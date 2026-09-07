"""Telegram adapter and pairing tests against a scripted HTTP transport.

No socket is opened and no request reaches api.telegram.org. These tests check
that the adapter's request and response handling matches the official
documentation recorded in `API_ASSUMPTIONS`; they are explicitly **not** a
verified live integration, and passing them clears no activation gate.
"""

import json
import os
import re
from pathlib import Path

import pytest

from kestrel.application import Lab, framework_root
from kestrel.contracts import canonical
from kestrel.fixtures import generate
from kestrel.messaging import (
    COMMANDS,
    Assistant,
    CommandProcessor,
    init_messaging,
    parse_command,
)
from kestrel.notifications import Channel, Destination, Exporter, Gateway, Grant
from kestrel.telegram import (
    ACTIVATION_ENV,
    API_ASSUMPTIONS,
    API_BASE,
    API_DOCUMENTATION,
    API_HOST,
    MAX_TEXT_CHARACTERS,
    FakeHttpTransport,
    HttpResponse,
    InboundPoller,
    Pairing,
    TelegramClient,
    TelegramError,
    TelegramTransport,
    TelegramTransportFailure,
    build_live_client,
    nonce_hash,
    read_credential,
    redact,
    require_activation,
    rotate_credential,
    start_link,
)

TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
CHAT = 555_000_111
USER = 42_000_777
NOW = 1_000_000.0


def ok(result):
    return HttpResponse(status=200, body=canonical({"ok": True, "result": result}),
                        final_url=API_BASE + "/botX/method")


def error(status, description, parameters=None):
    body = {"ok": False, "error_code": status, "description": description}
    if parameters:
        body["parameters"] = parameters
    return HttpResponse(status=status, body=canonical(body), final_url=API_BASE + "/x")


def client(script):
    return TelegramClient(TOKEN, http=FakeHttpTransport(script=script), channel="test")


BOT = {"id": 123456789, "is_bot": True, "first_name": "Kestrel", "username": "kestrel_test_bot"}
NO_WEBHOOK = {"url": "", "pending_update_count": 0, "has_custom_certificate": False}


DEFAULT_SENDER = {"id": USER, "is_bot": False, "username": "operator"}


def message(text, *, chat_type="private", sender=DEFAULT_SENDER, chat_id=CHAT,
            update_id=1, key="message"):
    body = {"message_id": 7, "date": 1_700_000_000,
            "chat": {"id": chat_id, "type": chat_type}, "text": text}
    if sender is not None:
        body["from"] = sender
    return {"update_id": update_id, key: body}


@pytest.fixture
def assistant(tmp_path):
    manifests = generate(tmp_path / "external-projects", framework_root())
    with Lab.initialize(tmp_path / "external-lab") as lab:
        record = lab.register(manifests[0], snapshot_dirty=True)
        lab.propose(record["project_id"], "Telegram fixture.")
        lab_root = lab.root
    root = tmp_path / "messaging"
    init_messaging(root, lab_root)
    with Assistant(root) as helper:
        helper.reconcile(now=NOW)
        yield helper, root, (root / "assistant-private" / "operator.token").read_text()


# --------------------------------------------------------------------------
# Request shape
# --------------------------------------------------------------------------

@pytest.mark.messaging("N-A12")
def test_requests_match_the_documented_url_method_and_body():
    http = FakeHttpTransport(script=[ok(BOT)])
    TelegramClient(TOKEN, http=http, channel="c").get_me()
    call = http.calls[0]
    assert call["url"] == f"{API_BASE}/bot{TOKEN}/getMe"
    assert call["method"] == "POST"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["headers"]["Content-Length"] == str(len(call["body"]))
    assert json.loads(call["body"]) == {}
    assert call["url"].startswith("https://")
    assert API_HOST in call["url"]


@pytest.mark.messaging("N-A12")
def test_send_message_disables_previews_and_never_offers_paid_options():
    http = FakeHttpTransport(script=[ok({"message_id": 9, "date": 1,
                                         "chat": {"id": CHAT, "type": "private"}})])
    TelegramClient(TOKEN, http=http).send_message(chat_id=CHAT, text="hello")
    body = json.loads(http.calls[0]["body"])
    assert body == {"chat_id": CHAT, "text": "hello",
                    "link_preview_options": {"is_disabled": True},
                    "disable_notification": False}
    for forbidden in ("parse_mode", "reply_markup", "star_count", "paid",
                      "business_connection_id", "reply_to_message_id"):
        assert forbidden not in body


@pytest.mark.messaging("N-A12")
def test_get_updates_requests_only_ordinary_messages():
    http = FakeHttpTransport(script=[ok([])])
    TelegramClient(TOKEN, http=http).get_updates(offset=41, timeout=25)
    body = json.loads(http.calls[0]["body"])
    assert body["allowed_updates"] == ["message"]
    assert body["offset"] == 41 and body["timeout"] == 25 and body["limit"] == 100
    assert http.calls[0]["max_bytes"] == 1024 * 1024
    assert http.calls[0]["timeout"] >= 35.0


@pytest.mark.messaging("N-A12")
def test_only_the_four_permitted_methods_are_reachable():
    adapter = client([])
    for forbidden in ("setWebhook", "deleteWebhook", "sendPhoto", "sendDocument",
                      "getFile", "../getMe", "sendMessage/../setWebhook", "logOut"):
        with pytest.raises(TelegramError, match="outside the permitted adapter surface"):
            adapter._url(forbidden)
    for permitted in ("getMe", "getWebhookInfo", "sendMessage", "getUpdates"):
        assert adapter._url(permitted).startswith(f"{API_BASE}/bot")


@pytest.mark.messaging("N-A12")
def test_text_outside_the_documented_range_is_refused_before_a_request():
    http = FakeHttpTransport(script=[])
    adapter = TelegramClient(TOKEN, http=http)
    for rejected in ("", "x" * (MAX_TEXT_CHARACTERS + 1)):
        with pytest.raises(TelegramError, match="1-4096"):
            adapter.send_message(chat_id=CHAT, text=rejected)
    assert http.calls == []


# --------------------------------------------------------------------------
# Response handling
# --------------------------------------------------------------------------

@pytest.mark.messaging("N-A12")
def test_additive_unknown_fields_are_ignored_but_critical_shapes_are_refused():
    identity = client([ok({**BOT, "has_main_web_app": True, "future_field": [1, 2]})]).get_me()
    assert identity.id == BOT["id"] and identity.username == "kestrel_test_bot"
    with pytest.raises(TelegramError):
        client([ok({"id": "not-an-integer", "is_bot": True, "first_name": "x"})]).get_me()
    with pytest.raises(TelegramError, match="does not identify a bot"):
        client([ok({**BOT, "is_bot": False})]).get_me()


@pytest.mark.messaging("N-A12")
@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"result": 1}',
                                  b'{"ok": "yes"}', b'{"ok": true, "ok": false}'])
def test_a_malformed_envelope_is_refused(body):
    http = FakeHttpTransport(script=[HttpResponse(status=200, body=body)])
    with pytest.raises(TelegramError):
        TelegramClient(TOKEN, http=http).get_me()


@pytest.mark.messaging("N-A12")
def test_an_oversized_response_is_refused():
    http = FakeHttpTransport(script=[HttpResponse(status=200, body=b"x" * (300 * 1024))])
    with pytest.raises(TelegramTransportFailure, match="byte cap"):
        TelegramClient(TOKEN, http=http).get_me()


@pytest.mark.messaging("N-A12")
def test_a_response_from_another_origin_is_refused():
    http = FakeHttpTransport(script=[HttpResponse(
        status=200, body=canonical({"ok": True, "result": BOT}),
        final_url="https://evil.example/botX/getMe")])
    with pytest.raises(TelegramTransportFailure, match="another origin"):
        TelegramClient(TOKEN, http=http).get_me()


@pytest.mark.messaging("N-A12")
def test_flood_control_surfaces_the_servers_own_retry_delay():
    outcome = client([error(429, "Too Many Requests", {"retry_after": 17})]).send_message(
        chat_id=CHAT, text="hi")
    assert outcome["ok"] is False
    assert outcome["retry_after"] == 17.0
    assert outcome["status"] == 429


@pytest.mark.messaging("N-A12")
def test_a_successful_send_must_confirm_the_recipient():
    confirmed = client([ok({"message_id": 3, "date": 1,
                            "chat": {"id": CHAT, "type": "private"}})]).send_message(
        chat_id=CHAT, text="hi")
    assert confirmed["destination_confirmed"] is True
    elsewhere = client([ok({"message_id": 3, "date": 1,
                            "chat": {"id": 999, "type": "private"}})]).send_message(
        chat_id=CHAT, text="hi")
    assert elsewhere["destination_confirmed"] is False


@pytest.mark.messaging("N-A12")
def test_get_updates_refuses_an_unbounded_or_untyped_result():
    with pytest.raises(TelegramError, match="bounded update list"):
        client([ok({"not": "a list"})]).get_updates(offset=None)
    with pytest.raises(TelegramError, match="bounded update list"):
        client([ok([{"update_id": index} for index in range(101)])]).get_updates(offset=None)
    with pytest.raises(TelegramError, match="integer update_id"):
        client([ok([{"update_id": "one"}])]).get_updates(offset=None)


# --------------------------------------------------------------------------
# Credential protection
# --------------------------------------------------------------------------

@pytest.mark.messaging("N-A06")
def test_the_token_never_appears_in_logs_reprs_or_exceptions():
    adapter = client([error(401, f"Unauthorized for {TOKEN}"),
                      TelegramTransportFailure(f"connect failed to bot{TOKEN}",
                                               transmitted=False)])
    outcome = adapter.send_message(chat_id=CHAT, text="hi")
    assert TOKEN not in outcome["description"]
    assert "<redacted>" in outcome["description"]
    with pytest.raises(TelegramTransportFailure) as raised:
        adapter.get_me()
    assert TOKEN not in str(raised.value)
    assert TOKEN not in repr(adapter) and TOKEN not in str(adapter)
    for record in adapter.log:
        rendered = json.dumps(record.__dict__)
        assert TOKEN not in rendered
        assert "http" not in rendered and "bot1234" not in rendered
    assert {record.method for record in adapter.log} == {"sendMessage", "getMe"}
    assert [record.status_category for record in adapter.log] == \
        ["error_401", "transport_failure"]


@pytest.mark.messaging("N-A06")
def test_redaction_covers_the_token_and_its_numeric_prefix():
    scrubbed = redact(f"url https://api.telegram.org/bot{TOKEN}/sendMessage id 123456789",
                      (TOKEN,))
    assert TOKEN not in scrubbed and "123456789" not in scrubbed
    assert redact("bot987654321:BBBBBBBBBBBBBBBBBBBBBB", ()) == "bot<redacted>"


@pytest.mark.messaging("N-A06")
def test_a_credential_file_must_be_protected_and_well_formed(tmp_path):
    path = tmp_path / "bot.token"
    path.write_text(TOKEN)
    path.chmod(0o644)
    with pytest.raises(TelegramError, match="group or world readable"):
        read_credential(path)
    path.chmod(0o600)
    assert read_credential(path) == TOKEN
    path.write_text("not-a-token")
    with pytest.raises(TelegramError, match="documented token shape"):
        read_credential(path)
    missing = tmp_path / "absent"
    with pytest.raises(TelegramError, match="unavailable"):
        read_credential(missing)
    link = tmp_path / "link.token"
    link.symlink_to(path)
    with pytest.raises(TelegramError, match="unavailable"):
        read_credential(link)


@pytest.mark.messaging("N-A12")
def test_live_operations_fail_closed_without_the_activation_gate(tmp_path, monkeypatch):
    monkeypatch.delenv(ACTIVATION_ENV, raising=False)
    with pytest.raises(TelegramError, match="separate operator authorization"):
        require_activation("test operation")
    path = tmp_path / "bot.token"
    path.write_text(TOKEN)
    path.chmod(0o600)
    with pytest.raises(TelegramError, match="separate operator authorization"):
        build_live_client(path, channel="c")
    assert os.environ.get(ACTIVATION_ENV) is None


# --------------------------------------------------------------------------
# Pairing
# --------------------------------------------------------------------------

@pytest.mark.messaging("N-A07")
def test_pairing_builds_a_documented_deep_link_and_stores_only_the_nonce_hash(assistant):
    helper, _, _ = assistant
    adapter = client([ok(BOT), ok(NO_WEBHOOK)])
    begun = Pairing(helper, adapter).begin(now=NOW)
    nonce = begun["start_link"].split("start=")[1]
    assert begun["start_link"] == f"https://t.me/kestrel_test_bot?start={nonce}"
    assert re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", nonce)
    assert len(nonce) >= 20
    row = helper._db.execute("SELECT * FROM pairing_sessions").fetchone()
    assert row["nonce_hash"] == nonce_hash(nonce)
    assert nonce not in json.dumps(dict(row))
    assert begun["expires_at"] == NOW + 600.0


@pytest.mark.messaging("N-A07")
def test_an_existing_webhook_stops_pairing_without_changing_anything(assistant):
    helper, _, _ = assistant
    adapter = client([ok(BOT), ok({"url": "https://someone-else.example/hook",
                                   "pending_update_count": 4})])
    with pytest.raises(TelegramError, match="already has an outgoing webhook"):
        Pairing(helper, adapter).begin(now=NOW)
    assert helper._db.execute("SELECT COUNT(*) FROM pairing_sessions").fetchone()[0] == 0
    assert [call["url"].rsplit("/", 1)[-1] for call in adapter._http.calls] == \
        ["getMe", "getWebhookInfo"]


@pytest.mark.messaging("N-A07")
@pytest.mark.parametrize("update,reason", [
    (message("/start WRONGNONCE"), "nonce_mismatch"),
    (message("hello there"), "not_a_pairing_command"),
    (message("/start NONCE", chat_type="group"), "not_a_private_chat"),
    (message("/start NONCE", sender={"id": 9, "is_bot": True}), "bot_or_absent_sender"),
    (message("/start NONCE", sender=None), "bot_or_absent_sender"),
    (message("/start NONCE", key="edited_message"),
     "unsupported_update_kind:edited_message"),
    (message("/start NONCE", key="channel_post"), "unsupported_update_kind:channel_post"),
    (message("/start NONCE", key="business_message"),
     "unsupported_update_kind:business_message"),
    ({"update_id": 5}, "unsupported_update_kind:empty"),
    ({"no_update_id": 1}, "not_an_update"),
])
def test_only_a_matching_private_start_command_pairs(assistant, update, reason):
    helper, _, _ = assistant
    adapter = client([ok(BOT), ok(NO_WEBHOOK)])
    pairing = Pairing(helper, adapter)
    begun = pairing.begin(now=NOW)
    nonce = begun["start_link"].split("start=")[1]
    if isinstance(update, dict) and "message" in update:
        text = update["message"].get("text")
        if text and "NONCE" in text:
            update["message"]["text"] = text.replace("NONCE", nonce)
    else:
        for key in ("edited_message", "channel_post", "business_message"):
            if key in update and update[key].get("text"):
                update[key]["text"] = update[key]["text"].replace("NONCE", nonce)
    result = pairing.poll(now=NOW + 1, updates=[update])
    assert result["candidate"] is None
    assert reason in {row["reason"] for row in pairing.rejections()}
    # Rejected third-party content is not retained beyond a reason code.
    stored = json.dumps(pairing.rejections())
    assert "hello there" not in stored


@pytest.mark.messaging("N-A07")
def test_a_matching_nonce_yields_a_candidate_that_still_needs_local_confirmation(
        assistant):
    helper, root, token = assistant
    adapter = client([ok(BOT), ok(NO_WEBHOOK)])
    pairing = Pairing(helper, adapter)
    nonce = pairing.begin(now=NOW)["start_link"].split("start=")[1]
    found = pairing.poll(now=NOW + 1, updates=[message(f"/start {nonce}")])
    assert found["confirmation_required"] is True
    assert found["candidate"]["chat_id"] == CHAT
    assert found["candidate"]["user_id"] == USER
    # Identification alone approves nothing.
    assert Exporter(helper).active_grant(now=NOW + 1) is None
    confirmed = pairing.confirm(operator_token=token, now=NOW + 2)
    assert confirmed["chat_id"] == CHAT
    assert "grants no execution" in confirmed["authority"]
    grant, channel = Exporter(helper).active_grant(now=NOW + 3)
    assert channel.destination.transport == "telegram"
    assert channel.destination.identity == str(CHAT)
    assert grant.allowed_conditions == []
    assert helper._meta("telegram_chat_id") == str(CHAT)
    assert helper._meta("telegram_epoch") == "1"


@pytest.mark.messaging("N-A07")
def test_a_nonce_is_single_use_and_expires(assistant):
    helper, _, token = assistant
    adapter = client([ok(BOT), ok(NO_WEBHOOK)])
    pairing = Pairing(helper, adapter)
    nonce = pairing.begin(now=NOW)["start_link"].split("start=")[1]
    pairing.poll(now=NOW + 1, updates=[message(f"/start {nonce}")])
    pairing.confirm(operator_token=token, now=NOW + 2)
    with pytest.raises(TelegramError, match="No pairing session is open"):
        pairing.poll(now=NOW + 3, updates=[message(f"/start {nonce}")])
    with pytest.raises(TelegramError, match="No pairing candidate"):
        pairing.confirm(operator_token=token, now=NOW + 4)
    later = Pairing(helper, client([ok(BOT), ok(NO_WEBHOOK)]))
    later.begin(now=NOW + 10)
    with pytest.raises(TelegramError, match="expired"):
        later.poll(now=NOW + 10 + 601.0, updates=[])


@pytest.mark.messaging("N-A07")
def test_confirmation_requires_the_messaging_operator_credential(assistant):
    helper, _, token = assistant
    adapter = client([ok(BOT), ok(NO_WEBHOOK)])
    pairing = Pairing(helper, adapter)
    nonce = pairing.begin(now=NOW)["start_link"].split("start=")[1]
    pairing.poll(now=NOW + 1, updates=[message(f"/start {nonce}")])
    from kestrel.messaging import MessagingError
    with pytest.raises(MessagingError, match="authentication"):
        pairing.confirm(operator_token="wrong-token-value-that-is-long-enough-xxxx",
                        now=NOW + 2)
    assert Exporter(helper).active_grant(now=NOW + 2) is None
    assert pairing.confirm(operator_token=token, now=NOW + 3)["chat_id"] == CHAT


def test_a_deep_link_parameter_stays_inside_the_documented_format():
    assert start_link("kestrel_bot", "abc-DEF_123").endswith("?start=abc-DEF_123")
    for bad_user in ("no", "has space", "a" * 40, "bad!name"):
        with pytest.raises(TelegramError, match="username"):
            start_link(bad_user, "abc")
    for bad_nonce in ("", "x" * 65, "has space", "semi;colon"):
        with pytest.raises(TelegramError, match="deep-link parameter"):
            start_link("kestrel_bot", bad_nonce)


@pytest.mark.messaging("N-A07")
def test_credential_rotation_confirms_the_same_bot_or_demands_re_enrollment(assistant):
    helper, _, token = assistant
    pairing = Pairing(helper, client([ok(BOT), ok(NO_WEBHOOK)]))
    nonce = pairing.begin(now=NOW)["start_link"].split("start=")[1]
    pairing.poll(now=NOW + 1, updates=[message(f"/start {nonce}")])
    pairing.confirm(operator_token=token, now=NOW + 2)
    same = rotate_credential(helper, client([ok(BOT)]), now=NOW + 3)
    assert same["same_bot"] is True
    assert "cannot resolve an already uncertain send" in same["limitation"]
    other = rotate_credential(helper, client([ok({**BOT, "id": 999})]), now=NOW + 4)
    assert other["same_bot"] is False
    assert "re-enrollment" in other["required"]


# --------------------------------------------------------------------------
# Transport adapter into the delivery state machine
# --------------------------------------------------------------------------

def wire_delivery(tmp_path):
    manifests = generate(tmp_path / "projects", framework_root())
    with Lab.initialize(tmp_path / "lab") as lab:
        record = lab.register(manifests[0], snapshot_dirty=True)
        lab.propose(record["project_id"], "Transport fixture.")
        lab_root = lab.root
    root = tmp_path / "messaging"
    init_messaging(root, lab_root)
    token = (root / "assistant-private" / "operator.token").read_text()
    helper = Assistant(root)
    helper.reconcile(now=NOW)
    exporter = Exporter(helper)
    exporter.register_channel(
        Channel(channel_id="personal", version=1,
                destination=Destination(transport="telegram", identity=str(CHAT))),
        operator_token=token, now=NOW)
    exporter.issue_grant(Grant(grant_id="notify_operator", version=1, principal="operator",
                               channel_id="personal", allowed_conditions=[],
                               expires_at=NOW + 86_400.0),
                         operator_token=token, now=NOW)
    exporter.export_pending(now=NOW)
    return helper, root


@pytest.mark.messaging("N-A12")
@pytest.mark.parametrize("response,expected", [
    (lambda: ok({"message_id": 1, "date": 1, "chat": {"id": CHAT, "type": "private"}}),
     "accepted"),
    (lambda: error(429, "flood", {"retry_after": 12}), "flood_control"),
    (lambda: error(403, "bot was blocked by the user"), "rejected_permanent"),
    (lambda: error(400, "chat not found"), "rejected_permanent"),
    (lambda: error(500, "internal"), "uncertain"),
])
def test_api_outcomes_map_onto_the_delivery_state_machine(tmp_path, response, expected):
    helper, root = wire_delivery(tmp_path)
    transport = TelegramTransport(client=client([response()]), expected_chat_id=CHAT)
    with Gateway(root) as gateway:
        gateway.intake(now=NOW)
        gateway.dispatch_once(transport, now=NOW)
        states = {row["state"] for row in gateway.deliveries()}
    mapping = {"accepted": "ACCEPTED", "flood_control": "RETRY_WAIT",
               "rejected_permanent": "FAILED_FINAL", "uncertain": "UNCERTAIN"}
    assert mapping[expected] in states
    helper.close()


@pytest.mark.messaging("N-A04")
def test_a_failure_before_transmission_retries_but_after_it_stays_uncertain(tmp_path):
    helper, root = wire_delivery(tmp_path)
    before = TelegramTransport(
        client=client([TelegramTransportFailure("dns failure", transmitted=False)]),
        expected_chat_id=CHAT)
    with Gateway(root) as gateway:
        gateway.intake(now=NOW)
        gateway.dispatch_once(before, now=NOW)
        assert gateway.deliveries(state="RETRY_WAIT")
    after = TelegramTransport(
        client=client([TelegramTransportFailure("read timeout", transmitted=True)]),
        expected_chat_id=CHAT)
    with Gateway(root) as gateway:
        gateway._db.execute("UPDATE deliveries SET state='READY', next_eligible=0")
        Exporter(helper).refresh_permits(now=NOW + 1)
        gateway.dispatch_once(after, now=NOW + 1)
        assert gateway.deliveries(state="UNCERTAIN")
    helper.close()


@pytest.mark.messaging("N-A02")
def test_the_transport_refuses_an_envelope_addressed_to_another_chat(tmp_path):
    helper, root = wire_delivery(tmp_path)
    adapter = client([])
    transport = TelegramTransport(client=adapter, expected_chat_id=999)
    with Gateway(root) as gateway:
        gateway.intake(now=NOW)
        gateway.dispatch_once(transport, now=NOW)
        failed = gateway.deliveries(state="FAILED_FINAL")
    assert failed and "differs from the paired chat" in failed[0]["reason"]
    assert adapter._http.calls == []
    helper.close()


# --------------------------------------------------------------------------
# Documentation and offline discipline
# --------------------------------------------------------------------------

def test_recorded_api_assumptions_are_explicit_and_unverified_live():
    assert API_DOCUMENTATION["checked_on"] == "2026-09-07"
    assert API_DOCUMENTATION["live_integration_verified"] is False
    for key in ("base_url", "envelope", "retry_after", "text_limit", "webhook_exclusive",
                "offset_confirms", "retention", "idle_reset", "deep_link"):
        assert API_ASSUMPTIONS[key]
    assert "24 hours" in API_ASSUMPTIONS["retention"]
    assert "randomly" in API_ASSUMPTIONS["idle_reset"]


def test_the_adapter_adds_no_runtime_dependency():
    text = (Path(framework_root()) / "src" / "kestrel" / "telegram.py").read_text()
    for forbidden in ("import requests", "import httpx", "telebot", "aiogram",
                      "python-telegram-bot", "import aiohttp"):
        assert forbidden not in text
    dependencies = (Path(framework_root()) / "pyproject.toml").read_text()
    assert "telegram" not in dependencies.split("[dependency-groups]")[0]


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_routine_telegram_tests_open_no_socket():
    # The suite runs with --disable-socket; the standard transport is only
    # constructed behind the activation gate, which is never set here.
    import socket

    from pytest_socket import SocketBlockedError

    assert os.environ.get(ACTIVATION_ENV) in (None, "", "0")
    with pytest.raises(SocketBlockedError):
        socket.socket().connect((API_HOST, 443))


# --------------------------------------------------------------------------
# Inbound polling and typed commands
# --------------------------------------------------------------------------

def wire_inbound(tmp_path, *, run=True):
    """A paired lab with a confirmed chat, a gateway and a poller."""
    manifests = generate(tmp_path / "projects", framework_root())
    with Lab.initialize(tmp_path / "lab") as lab:
        record = lab.register(manifests[0], snapshot_dirty=True)
        campaign = lab.propose(record["project_id"], "Inbound fixture.")
        if run:
            approval = lab.approve(campaign, lab.controller.campaign(campaign)["digest"],
                                   (lab.root / "operator.token").read_text())
            lab.run(campaign, approval)
        lab_root = lab.root
    root = tmp_path / "messaging"
    init_messaging(root, lab_root)
    token = (root / "assistant-private" / "operator.token").read_text()
    helper = Assistant(root)
    helper.reconcile(now=NOW)
    pairing = Pairing(helper, client([ok(BOT), ok(NO_WEBHOOK)]))
    nonce = pairing.begin(now=NOW)["start_link"].split("start=")[1]
    pairing.poll(now=NOW + 1, updates=[message(f"/start {nonce}")])
    pairing.confirm(operator_token=token, now=NOW + 2)
    gateway = Gateway(root, owner="poller")
    poller = InboundPoller(gateway, client([]), bot_identity=str(BOT["id"]), epoch=1,
                           chat_id=CHAT, user_id=USER)
    return helper, gateway, poller, root, campaign


def command(text, *, update_id=100, date=None, chat_id=CHAT, sender=DEFAULT_SENDER,
            key="message", chat_type="private"):
    return message(text, update_id=update_id, chat_id=chat_id, sender=sender, key=key,
                   chat_type=chat_type) | (
        {} if date is None else {"message": {**message(text, update_id=update_id,
                                                       chat_id=chat_id, sender=sender,
                                                       chat_type=chat_type)["message"],
                                             "date": date}})


@pytest.mark.messaging("N-A07")
def test_updates_are_journaled_before_the_offset_advances(tmp_path):
    helper, gateway, poller, _, _ = wire_inbound(tmp_path)
    assert poller.cursor() is None
    result = poller.poll_once(now=NOW + 10, updates=[command("/help", update_id=100)])
    assert result["accepted"] == 1 and result["offset_advanced"] is True
    assert poller.cursor()["offset"] == 101
    journaled = poller.pending()
    assert [row["record"]["text"] for row in journaled] == ["/help"]
    assert journaled[0]["processed_at"] is None
    gateway.close()
    helper.close()


@pytest.mark.messaging("N-A07")
def test_a_full_journal_holds_the_offset_instead_of_acknowledging_bytes(tmp_path,
                                                                       monkeypatch):
    helper, gateway, poller, _, _ = wire_inbound(tmp_path)
    monkeypatch.setattr("kestrel.telegram.MAX_JOURNAL_ROWS", 0)
    result = poller.poll_once(now=NOW + 10, updates=[command("/help")])
    assert result["offset_advanced"] is False
    assert "deliberately held" in result["reason"]
    assert gateway.gaps()[0]["kind"] == "inbound_journal_full"
    assert poller.pending() == []
    gateway.close()
    helper.close()


@pytest.mark.messaging("N-A07")
@pytest.mark.parametrize("update,reason", [
    (command("/help", chat_id=999), "foreign_chat"),
    (command("/help", sender={"id": 1, "is_bot": False}), "foreign_sender"),
    (command("/help", sender={"id": USER, "is_bot": True}), "bot_or_absent_sender"),
    (command("/help", sender=None), "bot_or_absent_sender"),
    (command("/help", chat_type="supergroup"), "not_a_private_chat"),
    (command("/help", key="edited_message"), "unsupported_update_kind:edited_message"),
    (command("/help", key="channel_post"), "unsupported_update_kind:channel_post"),
    (command("x" * 5000), "oversized_text"),
    (command("   "), "empty_text"),
    ({"update_id": 7}, "unsupported_update_kind:empty"),
])
def test_foreign_spoofed_and_unsupported_input_is_rejected_with_a_tombstone(
        tmp_path, update, reason):
    helper, gateway, poller, _, _ = wire_inbound(tmp_path)
    result = poller.poll_once(now=NOW + 10, updates=[update])
    assert result["accepted"] == 0 and result["rejected"] == 1
    assert poller.pending() == []
    assert reason in {row["record"].get("rejected") for row in poller.rejections()}
    # The offset still advances: the bytes were durably accounted for.
    assert result["offset_advanced"] is True
    gateway.close()
    helper.close()


@pytest.mark.messaging("N-A07")
def test_a_replayed_update_is_applied_once(tmp_path):
    helper, gateway, poller, _, _ = wire_inbound(tmp_path)
    poller.poll_once(now=NOW + 10, updates=[command("/stop", update_id=100)])
    processor = CommandProcessor(helper, poller)
    first = processor.process_pending(now=NOW + 11)
    assert [row["command"] for row in first["applied"]] == ["/stop"]
    assert helper.paused() is True
    # Telegram re-sends the same update after a restart before the offset moved.
    again = poller.poll_once(now=NOW + 12, updates=[command("/stop", update_id=100)])
    assert again["duplicates"] == [100]
    assert processor.process_pending(now=NOW + 13)["applied"] == []
    replies = [row for row in helper.intents() if row["purpose"].startswith("reply:")]
    assert len(replies) == 1
    gateway.close()
    helper.close()


@pytest.mark.messaging("N-A07")
def test_a_crash_between_processing_and_reply_delivery_does_not_reapply(tmp_path):
    helper, gateway, poller, root, _ = wire_inbound(tmp_path)
    poller.poll_once(now=NOW + 10, updates=[command("/stop", update_id=100)])
    processor = CommandProcessor(helper, poller)
    processor.process_pending(now=NOW + 11)
    # Simulate a restart that lost the gateway's processed marker.
    gateway._db.execute("UPDATE inbound_updates SET processed_at=NULL")
    replayed = processor.process_pending(now=NOW + 12)
    assert replayed["replayed"] and replayed["applied"] == []
    assert len([row for row in helper.intents()
                if row["purpose"].startswith("reply:")]) == 1
    assert helper.processed(f"{BOT['id']}:1:100")["kind"] == "stop"
    gateway.close()
    helper.close()


@pytest.mark.messaging("N-A07")
def test_a_stale_command_cannot_change_current_attention(tmp_path):
    helper, gateway, poller, _, _ = wire_inbound(tmp_path)
    reference = helper.inbox()[0]["reference"]
    stale = command(f"/ack {reference}", update_id=100, date=int(NOW))
    poller.poll_once(now=NOW + 5000, updates=[stale])
    outcome = CommandProcessor(helper, poller).process_pending(now=NOW + 5000)
    assert outcome["refused"][0]["reason"] == "stale_command"
    assert helper.item(reference)["attention"] == "OPEN"
    gateway.close()
    helper.close()


@pytest.mark.messaging("N-A07")
def test_a_stale_reference_is_refused_and_asks_for_a_refresh(tmp_path):
    helper, gateway, poller, _, _ = wire_inbound(tmp_path)
    reference = helper.inbox()[0]["reference"]
    item_id = reference.split("-")[0]
    poller.poll_once(now=NOW + 10,
                     updates=[command(f"/ack {item_id}-r9", update_id=100,
                                      date=int(NOW + 10))])
    outcome = CommandProcessor(helper, poller).process_pending(now=NOW + 11)
    assert outcome["refused"][0]["reason"] == "stale_reference"
    assert helper.item(reference)["attention"] == "OPEN"
    reply = next(row for row in helper.intents() if row["purpose"] == "reply:refused")
    assert reply["payload"]["detail"]["reason"] == "stale_reference"
    gateway.close()
    helper.close()


@pytest.mark.messaging("N-A07")
def test_the_authorized_commands_apply_only_attention_and_read_effects(tmp_path):
    helper, gateway, poller, _, campaign = wire_inbound(tmp_path)
    reference = helper.inbox()[0]["reference"]
    updates = [command("/help", update_id=1, date=int(NOW + 10)),
               command(f"/status {campaign}", update_id=2, date=int(NOW + 10)),
               command("/brief", update_id=3, date=int(NOW + 10)),
               command("/inbox", update_id=4, date=int(NOW + 10)),
               command(f"/snooze {reference} 1h", update_id=5, date=int(NOW + 10)),
               command("/stop", update_id=6, date=int(NOW + 10)),
               command("/resume", update_id=7, date=int(NOW + 10))]
    poller.poll_once(now=NOW + 10, updates=updates)
    outcome = CommandProcessor(helper, poller).process_pending(now=NOW + 11)
    assert [row["command"] for row in outcome["applied"]] == [
        "/help", "/status", "/brief", "/inbox", "/snooze", "/stop", "/resume"]
    assert helper.item(reference)["attention"] == "SNOOZED"
    assert helper.paused() is False
    purposes = {row["purpose"] for row in helper.intents()}
    assert {"reply:help", "reply:status", "reply:brief", "reply:inbox", "reply:snooze",
            "reply:stop", "reply:resume"} <= purposes
    gateway.close()
    helper.close()


@pytest.mark.messaging("N-A07")
def test_no_execution_or_policy_command_exists(tmp_path):
    helper, gateway, poller, _, campaign = wire_inbound(tmp_path)
    forbidden = [f"/approve {campaign}", f"/run {campaign}", f"/cancel {campaign}",
                 "/budget 1000", "/policy allow_all", "/execute rm -rf /",
                 "/grant network", "; DROP TABLE items"]
    poller.poll_once(now=NOW + 10,
                     updates=[command(text, update_id=200 + index, date=int(NOW + 10))
                              for index, text in enumerate(forbidden)])
    outcome = CommandProcessor(helper, poller).process_pending(now=NOW + 11)
    assert all(row["reason"] == "unsupported_request" for row in outcome["refused"])
    assert all(purpose == "reply:help" for purpose in
               [row["purpose"] for row in helper.intents()
                if row["purpose"].startswith("reply:")])
    for name in ("approve", "run", "cancel", "budget", "policy", "execute", "grant"):
        assert f"/{name}" not in COMMANDS
    gateway.close()
    helper.close()


@pytest.mark.messaging("N-A07")
def test_status_for_an_unknown_identifier_discloses_nothing(tmp_path):
    helper, gateway, poller, _, _ = wire_inbound(tmp_path)
    poller.poll_once(now=NOW + 10,
                     updates=[command("/status not-a-campaign", update_id=1,
                                      date=int(NOW + 10))])
    CommandProcessor(helper, poller).process_pending(now=NOW + 11)
    reply = next(row for row in helper.intents()
                 if row["purpose"].startswith("reply:status"))
    assert reply["purpose"] == "reply:status_unknown"
    assert reply["payload"]["detail"] == {}
    gateway.close()
    helper.close()


@pytest.mark.messaging("N-A18")
def test_an_idle_reset_rebases_the_cursor_instead_of_trusting_a_gap(tmp_path):
    helper, gateway, poller, _, _ = wire_inbound(tmp_path)
    poller.poll_once(now=NOW + 10, updates=[command("/help", update_id=900_000)])
    assert poller.cursor()["offset"] == 900_001
    # After the documented idle interval Telegram may restart identifiers.
    later = NOW + 10 + 8 * 86_400
    result = poller.poll_once(now=later, updates=[command("/help", update_id=5,
                                                          date=int(later))])
    assert result["mode"] == "rebase"
    assert poller.cursor()["offset"] == 6
    assert poller.cursor()["mode"] == "normal"
    # A numeric gap on its own never opened a loss gap.
    assert "missing_update" not in {gap["kind"] for gap in gateway.gaps(open_only=False)}
    gateway.close()
    helper.close()


@pytest.mark.messaging("N-A18")
def test_a_gap_longer_than_the_retention_window_reports_possible_lost_replies(tmp_path):
    helper, gateway, poller, _, _ = wire_inbound(tmp_path)
    poller.poll_once(now=NOW + 10, updates=[command("/help", update_id=1)])
    later = NOW + 10 + 30 * 3600
    poller.poll_once(now=later, updates=[])
    gap = next(row for row in gateway.gaps() if row["kind"] == "possible_inbound_loss")
    assert gap["detail"]["offline_seconds"] > 24 * 3600
    assert "Never assume the operator did not answer" in gap["detail"]["note"]
    gateway.close()
    helper.close()


@pytest.mark.messaging("N-A18")
def test_re_enrollment_starts_a_new_epoch_rather_than_reusing_a_cursor(tmp_path):
    helper, gateway, poller, root, _ = wire_inbound(tmp_path)
    poller.poll_once(now=NOW + 10, updates=[command("/help", update_id=500)])
    successor = InboundPoller(gateway, client([]), bot_identity=str(BOT["id"]), epoch=2,
                              chat_id=CHAT, user_id=USER)
    result = successor.poll_once(now=NOW + 20, updates=[command("/help", update_id=3,
                                                                date=int(NOW + 20))])
    assert result["mode"] == "rebase"
    assert successor.cursor()["epoch"] == 2
    assert {gap["kind"] for gap in gateway.gaps()} >= {"inbound_epoch_change"}
    # The earlier epoch's journal is retained and not reprocessed.
    assert len(successor.pending()) == 1
    gateway.close()
    helper.close()


@pytest.mark.messaging("N-A07")
def test_negative_offsets_and_destructive_dropping_are_never_used(tmp_path):
    helper, gateway, poller, _, _ = wire_inbound(tmp_path)
    poller.poll_once(now=NOW + 10, updates=[command("/help", update_id=42)])
    http = FakeHttpTransport(script=[ok([])])
    poller.client = TelegramClient(TOKEN, http=http)
    poller.poll_once(now=NOW + 11, timeout=0)
    body = json.loads(http.calls[0]["body"])
    assert body["offset"] == 43
    assert body["offset"] > 0
    assert "drop_pending_updates" not in body
    assert "delete_webhook" not in body
    gateway.close()
    helper.close()


def test_command_parsing_is_a_fixed_grammar():
    assert parse_command("/ack M12-r1") == ("/ack", ["M12-r1"])
    assert parse_command("/ACK@kestrel_bot M12-r1") == ("/ack", ["M12-r1"])
    assert parse_command("/snooze M12-r1 tomorrow") == ("/snooze", ["M12-r1", "tomorrow"])
    for rejected in ("", "hello", "/approve x", "/run", "/ack a b c",
                     "/ack " + "x" * 100, "/ack has space; rm -rf /", "/ack $(whoami)",
                     "/ack M12-r1' OR 1=1", "ack M12-r1"):
        assert parse_command(rejected) is None
    assert parse_command("/ack") == ("/ack", [])
