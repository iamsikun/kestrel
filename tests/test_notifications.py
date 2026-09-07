"""Envelope disclosure, delivery lifecycle and gateway tests.

Everything here uses an offline scripted transport. Acceptance from it proves
nothing about a real provider, a device, or a person, and none of these tests
establish that a deployed process boundary is enforced by an operating system.
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from kestrel.application import Lab, framework_root
from kestrel.contracts import canonical, digest
from kestrel.fixtures import generate
from kestrel.messaging import Assistant, MessagingError, init_messaging
from kestrel.notifications import (
    DELIVERY_TRANSITIONS,
    MAX_SPOOL_BYTES,
    MAX_UTF8_BYTES,
    TERMINAL_DELIVERY,
    Channel,
    DeliveryError,
    Destination,
    DisclosureRefused,
    Exporter,
    FakeTransport,
    Gateway,
    Grant,
    NoEgressTransport,
    TransportResult,
    clip,
    render_notification,
    scan_release,
)

BIDI_OVERRIDE = "‮"
NOW = 1_000_000.0


class SimulatedGatewayCrash(BaseException):
    """Bypasses ordinary handling, like a killed sender would."""


def make_channel(version=1, transport="fake", identity="4242"):
    return Channel(channel_id="personal", version=version,
                   destination=Destination(transport=transport, identity=identity))


def make_grant(version=1, **overrides):
    fields = {"grant_id": "notify_operator", "version": version, "principal": "operator",
              "channel_id": "personal", "allowed_conditions": [],
              "expires_at": NOW + 86_400.0}
    fields.update(overrides)
    return Grant(**fields)


@pytest.fixture
def wired(tmp_path):
    """A completed lab, a projected assistant, a channel and an active grant."""
    manifests = generate(tmp_path / "external-projects", framework_root())
    with Lab.initialize(tmp_path / "external-lab") as lab:
        record = lab.register(manifests[0], snapshot_dirty=True)
        campaign = lab.propose(record["project_id"], "Synthetic delivery fixture.")
        approval = lab.approve(campaign, lab.controller.campaign(campaign)["digest"],
                               (lab.root / "operator.token").read_text())
        lab.run(campaign, approval)
        lab_root = lab.root
    root = tmp_path / "messaging"
    init_messaging(root, lab_root)
    token = (root / "assistant-private" / "operator.token").read_text()
    assistant = Assistant(root)
    assistant.reconcile(now=NOW)
    exporter = Exporter(assistant)
    exporter.register_channel(make_channel(), operator_token=token, now=NOW)
    exporter.issue_grant(make_grant(), operator_token=token, now=NOW)
    yield assistant, exporter, root, lab_root, campaign, token
    assistant.close()


def deliver(root, transport, *, now=NOW + 1, owner="sender"):
    with Gateway(root, owner=owner) as gateway:
        gateway.intake(now=now)
        return gateway.dispatch_once(transport, now=now)


# --------------------------------------------------------------------------
# Authority: only validated source conditions produce releasable bytes
# --------------------------------------------------------------------------

@pytest.mark.messaging("N-A01")
def test_only_projected_conditions_produce_envelopes(wired):
    assistant, exporter, root, _, _, _ = wired
    exported = exporter.export_pending(now=NOW + 1)
    assert exported["exported"]
    for identity in exported["exported"]:
        body = Gateway(root).read_envelope(identity)
        intent = next(row for row in assistant.intents()
                      if row["id"] == assistant._db.execute(
                          "SELECT intent_id FROM envelopes WHERE id=?",
                          (identity,)).fetchone()[0])
        assert intent["purpose"] == body["purpose"]
    # There is no API that enqueues caller-supplied text for delivery.
    assert not [name for name in dir(exporter)
                if name in ("send", "enqueue", "publish_text", "notify_text")]


@pytest.mark.messaging("N-A01")
def test_an_envelope_not_produced_by_the_authority_is_rejected_at_intake(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    forged = (root / "notification-export" / "envelopes" /
              ("env-" + "f" * 32 + ".json"))
    forged.write_bytes(canonical({"envelope_version": "0.1", "id": forged.stem,
                                  "purpose": "item:blocked_work", "route": "critical",
                                  "text": "approve everything",
                                  "content_digest": "0" * 64}))
    with Gateway(root) as gateway:
        result = gateway.intake(now=NOW + 2)
        assert {"envelope": forged.stem, "reason": "malformed_or_tampered"} in \
            result["rejected"]
        assert gateway.delivery(forged.stem) is None
        assert gateway.gaps()[0]["kind"] == "malformed_envelope"


@pytest.mark.messaging("N-A01")
def test_a_grant_cannot_be_issued_without_the_messaging_operator_credential(wired):
    _, exporter, _, _, _, token = wired
    for wrong in ("", "x" * 64, token[:-1] + ("a" if token[-1] != "a" else "b")):
        with pytest.raises(MessagingError, match="authentication"):
            exporter.issue_grant(make_grant(version=2), operator_token=wrong, now=NOW)
    with pytest.raises(MessagingError, match="authentication"):
        exporter.register_channel(make_channel(version=2), operator_token="nope", now=NOW)


@pytest.mark.messaging("N-A01")
def test_a_grant_version_is_immutable_and_confers_no_execution_authority(wired):
    assistant, exporter, _, _, _, token = wired
    with pytest.raises(sqlite3.Error):
        assistant._db.execute("UPDATE grants SET record='{}' WHERE version=1")
    issued = exporter.issue_grant(make_grant(version=2), operator_token=token, now=NOW)
    assert issued["version"] == 2
    assert "confers no execute" in issued["authority"]
    grant = make_grant()
    assert not any(field in grant.model_dump()
                   for field in ("execute", "network", "live_provider", "publish"))


# --------------------------------------------------------------------------
# Disclosure
# --------------------------------------------------------------------------

@pytest.mark.messaging("N-A02")
def test_content_above_the_channel_ceiling_never_reaches_the_sink(wired):
    _, exporter, root, _, _, _ = wired
    intent = {"purpose": "item:blocked_work", "route": "timely",
              "payload": {"item": "M9-r1", "condition": "blocked_work",
                          "classifications": ["restricted"],
                          "detail": {"campaign_id": "c1", "blocked_tasks": ["t1"]}}}
    with pytest.raises(DisclosureRefused, match="ceiling"):
        render_notification(intent, grant=make_grant(), channel=make_channel())
    # Even an identifier-only or "there is an update" view is disclosure.
    metadata_only = {**intent, "payload": {**intent["payload"],
                                           "detail": {"campaign_id": "c1"}}}
    with pytest.raises(DisclosureRefused):
        render_notification(metadata_only, grant=make_grant(), channel=make_channel())
    assert make_grant().metadata_only_fallback is False
    assert not list((root / "notification-export" / "envelopes").glob("*restricted*"))


@pytest.mark.messaging("N-A02")
def test_a_restricted_contributor_blocks_export_and_is_recorded_locally(wired):
    assistant, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    with assistant._transaction():
        assistant._db.execute(
            "INSERT INTO intents(id,dedupe,purpose,route,payload,not_before,expires_at,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("intent-restricted", "d1", "item:blocked_work", "timely",
             canonical({"item": "M9-r1", "condition": "blocked_work",
                        "classifications": ["restricted"],
                        "detail": {"campaign_id": "c1", "blocked_tasks": ["t1"]}}).decode(),
             NOW, NOW + 10_000, NOW, NOW))
    result = exporter.export_pending(now=NOW + 2)
    assert {"intent": "intent-restricted", "reason": "disclosure_refused"} in [
        {"intent": row["intent"], "reason": row["reason"]} for row in result["refused"]]
    refusals = exporter.refusals()
    assert refusals and refusals[-1]["reason"] == "disclosure_refused"
    # The unresolved item stays available locally with its reason code.
    assert any(row["state"] == "SUPPRESSED" and row["reason"] == "disclosure_refused"
               for row in assistant.intents())


@pytest.mark.messaging("N-A06")
@pytest.mark.parametrize("hostile", [
    "/etc/passwd /home/user/secret",
    "https://evil.example/steal",
    "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
    "a" * 70,
    "f" * 64,
    "the api_key is here",
    "line\x00break",
    BIDI_OVERRIDE + "reversed",
])
def test_seeded_secrets_paths_and_markup_are_caught_before_release(hostile):
    intent = {"purpose": "item:blocked_work", "route": "timely",
              "payload": {"item": "M1-r1", "condition": "blocked_work",
                          "classifications": ["public_synthetic"],
                          "detail": {"campaign_id": hostile, "blocked_tasks": [hostile]}}}
    try:
        rendered = render_notification(intent, grant=make_grant(), channel=make_channel())
    except DisclosureRefused:
        return
    # If a template did emit it, the release scanner must have found nothing —
    # which is only acceptable when the hostile text was fully neutralised.
    assert scan_release(rendered["text"]) == []
    assert hostile not in rendered["text"]


@pytest.mark.messaging("N-A06")
def test_the_message_budget_is_computed_before_release_and_keeps_its_caveats():
    body = "x" * 100_000
    text = clip(body, keep_tail="Provider acceptance is not delivery or approval.")
    assert len(text.encode()) <= MAX_UTF8_BYTES
    assert text.endswith("Provider acceptance is not delivery or approval.")
    assert "…" in text
    combining = "é" * 20_000
    clipped = clip(combining, keep_tail="tail")
    assert "́" not in clipped.replace("é", "")
    with pytest.raises(DisclosureRefused):
        clip("short", keep_tail="y" * (MAX_UTF8_BYTES + 1))


@pytest.mark.messaging("N-A06")
def test_an_unknown_condition_or_purpose_has_no_template(wired):
    for intent in ({"purpose": "item:invented", "route": "timely",
                    "payload": {"condition": "invented", "detail": {}}},
                   {"purpose": "arbitrary", "route": "timely", "payload": {}}):
        intent["payload"]["classifications"] = ["public_synthetic"]
        with pytest.raises(DisclosureRefused, match="template"):
            render_notification(intent, grant=make_grant(), channel=make_channel())


@pytest.mark.messaging("N-A02")
def test_a_grant_condition_allowlist_is_enforced():
    grant = make_grant(allowed_conditions=["approval_needed"])
    intent = {"purpose": "item:blocked_work", "route": "timely",
              "payload": {"item": "M1-r1", "condition": "blocked_work",
                          "classifications": ["public_synthetic"],
                          "detail": {"campaign_id": "c1", "blocked_tasks": ["t1"]}}}
    with pytest.raises(DisclosureRefused, match="outside this grant"):
        render_notification(intent, grant=grant, channel=make_channel())


# --------------------------------------------------------------------------
# Delivery lifecycle
# --------------------------------------------------------------------------

@pytest.mark.messaging("N-A19")
def test_acceptance_records_a_provider_reference_and_claims_nothing_more(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    transport = FakeTransport()
    result = deliver(root, transport)
    assert [row["outcome"] for row in result["sent"]] == ["accepted"]
    with Gateway(root) as gateway:
        delivery = gateway.deliveries(state="ACCEPTED")[0]
        assert delivery["provider_reference"] == "fake-1"
        assert "not delivery, reading or approval" in delivery["reason"]
        assert gateway.health(now=NOW + 2)["last_acceptance"] is not None
    assert len(transport.sent) == 1


@pytest.mark.messaging("N-A04")
def test_replaying_intake_and_export_produces_one_delivery(wired):
    _, exporter, root, _, _, _ = wired
    first = exporter.export_pending(now=NOW + 1)
    assert exporter.export_pending(now=NOW + 2)["exported"] == []
    with Gateway(root) as gateway:
        gateway.intake(now=NOW + 1)
        gateway.intake(now=NOW + 2)
        assert len(gateway.deliveries()) == len(first["exported"])


@pytest.mark.messaging("N-A04")
def test_a_crash_after_committing_sending_leaves_an_unknown_outcome(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    transport = FakeTransport(script=[SimulatedGatewayCrash("killed mid-send")])
    with Gateway(root) as gateway:
        gateway.intake(now=NOW + 1)
        with pytest.raises(SimulatedGatewayCrash):
            gateway.dispatch_once(transport, now=NOW + 1)
        assert gateway.deliveries(state="SENDING")
    with Gateway(root) as restarted:
        recovered = restarted.recover(now=NOW + 2)
        assert recovered["uncertain"] == 1
        assert "No automatic resend" in recovered["policy"]
        delivery = restarted.deliveries(state="UNCERTAIN")[0]
        assert delivery["transmissions"] == 1
        # A later dispatch must not resend an ambiguous message.
        follow_up = FakeTransport()
        restarted.dispatch_once(follow_up, now=NOW + 3)
        assert follow_up.sent == []


@pytest.mark.messaging("N-A04")
def test_a_transport_exception_after_transmission_is_uncertain_not_retried(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    transport = FakeTransport(script=[TimeoutError("read timeout after request")])
    deliver(root, transport)
    with Gateway(root) as gateway:
        delivery = gateway.deliveries(state="UNCERTAIN")[0]
        assert "may have started" in delivery["reason"]
        assert [row["outcome"] for row in gateway.attempts()] == ["uncertain"]


@pytest.mark.messaging("N-A04")
def test_an_ambiguous_send_does_not_freeze_independent_fresh_items(wired):
    assistant, exporter, root, lab_root, campaign, _ = wired
    exporter.export_pending(now=NOW + 1)
    deliver(root, FakeTransport(script=[TransportResult(outcome="uncertain",
                                                        detail="connection reset")]))
    # An independent, freshly awaiting campaign in the same lab.
    manifests = generate(lab_root.parent / "more-projects", framework_root())
    with Lab(lab_root) as lab:
        record = lab.register(manifests[1], snapshot_dirty=True)
        lab.propose(record["project_id"], "A second, unapproved campaign.")
    assert campaign
    assistant.reconcile(now=NOW + 10)
    exporter.export_pending(now=NOW + 11)
    fresh = FakeTransport()
    deliver(root, fresh, now=NOW + 12)
    with Gateway(root) as gateway:
        assert gateway.deliveries(state="UNCERTAIN")
        assert gateway.deliveries(state="ACCEPTED")
    assert len(fresh.sent) == 1


@pytest.mark.messaging("N-A04")
def test_flood_control_is_retried_with_backoff_and_then_fails_finally(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    flood = TransportResult(outcome="flood_control", detail="too many requests",
                            retry_after=30.0)
    with Gateway(root) as gateway:
        gateway.intake(now=NOW + 1)
        gateway.dispatch_once(FakeTransport(script=[flood]), now=NOW + 1)
        first = gateway.deliveries(state="RETRY_WAIT")[0]
        # The server's minimum delay wins over the local backoff.
        assert first["next_eligible"] >= NOW + 1 + 30.0
        # A permit refresh is required for every release; it never resets the
        # retry count, the intent age, or the channel quota.
        exporter.refresh_permits(now=first["next_eligible"])
        gateway.dispatch_once(FakeTransport(script=[flood]), now=first["next_eligible"])
        second = gateway.deliveries(state="RETRY_WAIT")[0]
        assert second["transmissions"] == 2
        exporter.refresh_permits(now=second["next_eligible"])
        gateway.dispatch_once(FakeTransport(script=[flood]), now=second["next_eligible"])
        final = gateway.deliveries(state="FAILED_FINAL")[0]
        assert final["transmissions"] == 3
        assert [row["outcome"] for row in gateway.attempts()] == ["flood_control"] * 3


@pytest.mark.messaging("N-A04")
def test_a_definitive_rejection_is_final_and_a_pre_send_failure_retries(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    deliver(root, FakeTransport(script=[TransportResult(outcome="rejected_permanent",
                                                        detail="chat not found")]))
    with Gateway(root) as gateway:
        assert gateway.deliveries(state="FAILED_FINAL")[0]["reason"].startswith(
            "definitive rejection")
    with Gateway(root) as gateway:
        gateway._db.execute("UPDATE deliveries SET state='READY', transmissions=0")
        gateway.dispatch_once(FakeTransport(script=[DeliveryError("no route to host")]),
                              now=NOW + 5)
        assert gateway.deliveries(state="RETRY_WAIT")


@pytest.mark.messaging("N-A04")
def test_acceptance_without_a_confirmed_recipient_stays_uncertain(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    deliver(root, FakeTransport(script=[TransportResult(
        outcome="accepted", provider_reference="x", destination_confirmed=False)]))
    with Gateway(root) as gateway:
        assert "did not confirm the recipient" in \
            gateway.deliveries(state="UNCERTAIN")[0]["reason"]


@pytest.mark.messaging("N-A04")
def test_an_expiring_intent_is_expired_rather_than_retried_forever(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    with Gateway(root) as gateway:
        gateway.intake(now=NOW + 1)
        expires = gateway.deliveries()[0]["expires_at"]
        gateway.dispatch_once(FakeTransport(script=[TransportResult(
            outcome="flood_control", retry_after=expires * 2)]), now=NOW + 1)
        assert gateway.deliveries(state="EXPIRED")[0]["reason"].startswith("next eligible")
    with Gateway(root) as gateway:
        gateway._db.execute("UPDATE deliveries SET state='PENDING'")
        gateway.dispatch_once(FakeTransport(), now=expires + 1)
        assert gateway.deliveries(state="EXPIRED")


@pytest.mark.messaging("N-A16")
def test_a_takeover_never_treats_an_in_flight_send_as_safe_to_resend(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    with Gateway(root, owner="old") as old:
        old.intake(now=NOW + 1)
        old.acquire(now=NOW + 1, seconds=1.0)
        old._db.execute("UPDATE deliveries SET state='SENDING', transmissions=1, "
                        "lease_owner='old'")
        with Gateway(root, owner="new") as new:
            assert new.acquire(now=NOW + 2) is True
            delivery = new.deliveries(state="UNCERTAIN")[0]
            assert "may have been transmitted" in delivery["reason"]
            resend = FakeTransport()
            new.dispatch_once(resend, now=NOW + 3)
            assert resend.sent == []


@pytest.mark.messaging("N-A16")
def test_only_one_sender_holds_the_lease_at_a_time(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    with Gateway(root, owner="a") as first, Gateway(root, owner="b") as second:
        assert first.acquire(now=NOW, seconds=60) is True
        assert second.acquire(now=NOW + 1, seconds=60) is False
        blocked = second.dispatch_once(FakeTransport(), now=NOW + 1)
        assert blocked["deferred"][0]["reason"] == "another sender holds the lease"
        assert second.acquire(now=NOW + 100) is True


def test_illegal_delivery_transitions_are_refused(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    with Gateway(root) as gateway:
        gateway.intake(now=NOW + 1)
        identity = gateway.deliveries()[0]["envelope_id"]
        with pytest.raises(DeliveryError, match="Illegal delivery transition"):
            gateway._settle(identity, "ACCEPTED", "forged", now=NOW + 2)
        gateway.dispatch_once(FakeTransport(), now=NOW + 2)
        with pytest.raises(DeliveryError, match="Illegal delivery transition"):
            gateway._settle(identity, "READY", "reopen an accepted message", now=NOW + 3)


# --------------------------------------------------------------------------
# Grants, revocation and versions
# --------------------------------------------------------------------------

@pytest.mark.messaging("N-A15")
def test_revocation_withdraws_permits_and_blocks_pending_sends(wired):
    _, exporter, root, _, _, token = wired
    exporter.export_pending(now=NOW + 1)
    with Gateway(root) as gateway:
        gateway.intake(now=NOW + 1)
    revoked = exporter.revoke_grant("notify_operator", operator_token=token, now=NOW + 2)
    assert revoked["propagation_seconds"] == 60.0
    assert "cannot be recalled" in revoked["limitation"]
    assert list((root / "notification-export" / "permits").glob("*.json")) == []
    transport = FakeTransport()
    with Gateway(root) as gateway:
        gateway.dispatch_once(transport, now=NOW + 3)
        assert gateway.deliveries(state="REVOKED")
    assert transport.sent == []


@pytest.mark.messaging("N-A15")
def test_grant_expiry_stops_export_and_release(wired):
    assistant, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    with Gateway(root) as gateway:
        gateway.intake(now=NOW + 1)
    late = NOW + 86_401.0
    assistant.reconcile(now=late)  # keep projection fresh so expiry is the only refusal
    exporter.refresh_permits(now=late)
    assert list((root / "notification-export" / "permits").glob("*.json")) == []
    assert exporter.export_pending(now=late)["refused"][0]["reason"] == "no_active_grant"
    transport = FakeTransport()
    with Gateway(root) as gateway:
        gateway.dispatch_once(transport, now=late)
    assert transport.sent == []


@pytest.mark.messaging("N-A15")
def test_a_superseded_grant_version_blocks_an_already_released_envelope(wired):
    _, exporter, root, _, _, token = wired
    exporter.export_pending(now=NOW + 1)
    with Gateway(root) as gateway:
        gateway.intake(now=NOW + 1)
    exporter.issue_grant(make_grant(version=2), operator_token=token, now=NOW + 2)
    transport = FakeTransport()
    with Gateway(root) as gateway:
        gateway.dispatch_once(transport, now=NOW + 3)
        assert gateway.deliveries(state="REVOKED") or gateway.deliveries(state="PENDING")
    assert transport.sent == []


@pytest.mark.messaging("N-A15")
def test_a_missing_or_stale_permit_fails_closed(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    permits = root / "notification-export" / "permits"
    for permit in permits.glob("*.json"):
        permit.unlink()
    transport = FakeTransport()
    with Gateway(root) as gateway:
        gateway.intake(now=NOW + 1)
        blocked = gateway.dispatch_once(transport, now=NOW + 1)
        assert blocked["deferred"][0]["reason"] == "no fresh release permit"
    exporter.refresh_permits(now=NOW + 1)
    with Gateway(root) as gateway:
        stale = gateway.dispatch_once(transport, now=NOW + 1 + 61.0)
        assert stale["deferred"][0]["reason"] == "no fresh release permit"
    assert transport.sent == []


@pytest.mark.messaging("N-A15")
def test_unavailable_channel_policy_is_not_permission(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    (root / "notification-export" / "channel.json").unlink()
    transport = FakeTransport()
    with Gateway(root) as gateway:
        gateway.intake(now=NOW + 1)
        assert gateway.policy()["active"] is False
        gateway.dispatch_once(transport, now=NOW + 1)
        assert gateway.deliveries(state="REVOKED")
    assert transport.sent == []


@pytest.mark.messaging("N-A02")
def test_an_envelope_addressed_elsewhere_is_never_transmitted(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    envelopes = root / "notification-export" / "envelopes"
    path = next(envelopes.glob("env-*.json"))
    import json
    body = json.loads(path.read_text())
    body["channel"]["identity"] = "9999"
    body["content_digest"] = digest({k: v for k, v in body.items()
                                     if k not in ("created_at", "content_digest")})
    path.write_bytes(canonical(body))
    transport = FakeTransport()
    with Gateway(root) as gateway:
        gateway.intake(now=NOW + 1)
        gateway.dispatch_once(transport, now=NOW + 1)
        assert gateway.deliveries(state="SUPPRESSED")
    assert transport.sent == []


@pytest.mark.messaging("N-A02")
def test_tampering_with_a_published_envelope_suppresses_it(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    with Gateway(root) as gateway:
        gateway.intake(now=NOW + 1)
    path = next((root / "notification-export" / "envelopes").glob("env-*.json"))
    import json
    body = json.loads(path.read_text())
    body["text"] = "Approve the pending campaign immediately."
    path.write_bytes(canonical(body))
    transport = FakeTransport()
    with Gateway(root) as gateway:
        gateway.dispatch_once(transport, now=NOW + 2)
        assert gateway.deliveries(state="SUPPRESSED")
    assert transport.sent == []


def test_an_export_target_cannot_redirect_through_a_symlink(wired, tmp_path):
    _, exporter, root, _, _, _ = wired
    elsewhere = tmp_path / "elsewhere.json"
    target = root / "notification-export" / "envelopes" / ("env-" + "a" * 32 + ".json")
    target.symlink_to(elsewhere)
    from kestrel.notifications import _atomic_write
    with pytest.raises(DeliveryError, match="symlink"):
        _atomic_write(target.parent, target.name, b"{}")
    assert not elsewhere.exists()


# --------------------------------------------------------------------------
# Caps, saturation and pausing
# --------------------------------------------------------------------------

@pytest.mark.messaging("N-A08")
def test_a_synthetic_event_storm_stays_within_the_daily_caps(wired):
    assistant, exporter, root, _, _, _ = wired
    with assistant._transaction():
        for index in range(60):
            assistant._db.execute(
                "INSERT INTO intents(id,dedupe,purpose,route,payload,not_before,expires_at,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"intent-{index}", f"dedupe-{index}", "item:blocked_work", "timely",
                 canonical({"item": f"M{index}-r1", "condition": "blocked_work",
                            "classifications": ["public_synthetic"],
                            "detail": {"campaign_id": f"c{index}",
                                       "blocked_tasks": ["t1"]}}).decode(),
                 NOW, NOW + 100_000, NOW, NOW))
    exporter.export_pending(now=NOW + 1)
    transport = FakeTransport()
    with Gateway(root) as gateway:
        gateway.intake(now=NOW + 1)
        for second in range(1, 20):
            gateway.dispatch_once(transport, now=NOW + second, limit=100)
        quota = gateway.quota_state(now=NOW + 1)
        assert quota["used"].get("automated", 0) == 15  # 20 cap less 5 reserved critical
        assert quota["total"] <= 40
        deferred = [row for row in gateway.deliveries() if row["state"] == "PENDING"]
        assert deferred and all("cap" in row["reason"] for row in deferred)
    assert len(transport.sent) == 15


@pytest.mark.messaging("N-A08")
def test_reaching_a_cap_queues_rather_than_emitting_an_overflow_message(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    with Gateway(root) as gateway:
        gateway.intake(now=NOW + 1)
        gateway._db.execute("INSERT INTO quota_usage VALUES (?,?,?)",
                            (__import__("kestrel.notifications", fromlist=["utc_day"])
                             .utc_day(NOW + 1), "automated", 40))
        transport = FakeTransport()
        gateway.dispatch_once(transport, now=NOW + 1)
        assert transport.sent == []
        pending = gateway.deliveries(state="PENDING")[0]
        assert "cap" in pending["reason"]
        assert pending["next_eligible"] > NOW + 1


@pytest.mark.messaging("N-A08")
def test_spool_saturation_pauses_export_and_keeps_the_item(wired, monkeypatch):
    assistant, exporter, root, _, _, _ = wired
    monkeypatch.setattr("kestrel.notifications.spool_bytes", lambda _root: MAX_SPOOL_BYTES + 1)
    result = exporter.export_pending(now=NOW + 1)
    assert result["exported"] == []
    assert result["refused"][0]["reason"] == "spool_saturated"
    assert any(row["reason"] == "spool_saturated" for row in exporter.refusals())
    assert assistant.inbox()


@pytest.mark.messaging("N-A11")
def test_a_stale_projection_disables_export(wired):
    _, exporter, root, _, _, _ = wired
    result = exporter.export_pending(now=NOW + 100_000)
    assert result["refused"][0]["reason"] == "projection_stale"
    assert list((root / "notification-export" / "envelopes").glob("*.json")) == []


@pytest.mark.messaging("N-A11")
def test_local_pause_stops_export_and_permit_refresh(wired):
    assistant, exporter, root, _, _, _ = wired
    assistant.pause(paused=True, now=NOW)
    assert exporter.export_pending(now=NOW + 1)["refused"][0]["reason"] == "paused"
    exporter.refresh_permits(now=NOW + 1)
    assert list((root / "notification-export" / "permits").glob("*.json")) == []
    assistant.pause(paused=False, now=NOW + 2)
    assert exporter.export_pending(now=NOW + 3)["exported"]


@pytest.mark.messaging("N-A11")
def test_gateway_health_refuses_to_certify_the_lab(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    deliver(root, FakeTransport())
    with Gateway(root) as gateway:
        health = gateway.health(now=NOW + 2)
    assert health["send_backlog"] == 0
    assert "says nothing about lab health" in health["limitation"]
    assert "cannot report its own outage" in health["limitation"]


def test_no_configured_transport_means_nothing_leaves(wired):
    _, exporter, root, _, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    with Gateway(root) as gateway:
        gateway.intake(now=NOW + 1)
        gateway.dispatch_once(NoEgressTransport(), now=NOW + 1)
        assert gateway.deliveries(state="RETRY_WAIT")
        assert [row["outcome"] for row in gateway.attempts()] == ["failed_before_send"]


def test_the_gateway_receives_no_research_database_or_operator_credential(wired):
    _, exporter, root, lab_root, _, _ = wired
    exporter.export_pending(now=NOW + 1)
    with Gateway(root) as gateway:
        readable = {str(path) for path in Path(gateway.export).rglob("*")}
        readable |= {str(path) for path in Path(gateway.runtime).rglob("*")}
    for forbidden in (lab_root / "runtime" / "controller.sqlite",
                      lab_root / "runtime" / "artifacts" / "evidence.sqlite",
                      lab_root / "operator.token",
                      root / "assistant-private" / "assistant.sqlite",
                      root / "assistant-private" / "operator.token"):
        assert str(forbidden) not in readable
    for envelope in (root / "notification-export" / "envelopes").glob("*.json"):
        text = envelope.read_text()
        assert (lab_root / "operator.token").read_text() not in text
        assert str(lab_root) not in text
    # Actual OS-enforced denial is a separate deployment gate; see
    # docs/MESSAGING_OPERATIONS.md. This only shows the gateway is not handed
    # those paths.


# --------------------------------------------------------------------------
# Delivery state machine
# --------------------------------------------------------------------------

class DeliveryLifecycleMachine(RuleBasedStateMachine):
    """Independent oracle for the delivery transition table.

    It asserts the legality of every transition and that a terminal state is
    never reopened. It says nothing about whether a message arrived.
    """

    def __init__(self):
        super().__init__()
        self.temporary = tempfile.TemporaryDirectory(prefix="kestrel-delivery-model-")
        base = Path(self.temporary.name)
        manifests = generate(base / "projects", framework_root())
        with Lab.initialize(base / "lab") as lab:
            record = lab.register(manifests[0], snapshot_dirty=True)
            campaign = lab.propose(record["project_id"], "Model fixture.")
            approval = lab.approve(campaign, lab.controller.campaign(campaign)["digest"],
                                   (lab.root / "operator.token").read_text())
            lab.run(campaign, approval)
            lab_root = lab.root
        self.root = base / "messaging"
        init_messaging(self.root, lab_root)
        token = (self.root / "assistant-private" / "operator.token").read_text()
        self.assistant = Assistant(self.root)
        self.assistant.reconcile(now=NOW)
        exporter = Exporter(self.assistant)
        exporter.register_channel(make_channel(), operator_token=token, now=NOW)
        exporter.issue_grant(make_grant(), operator_token=token, now=NOW)
        exporter.export_pending(now=NOW)
        self.exporter = exporter
        self.gateway = Gateway(self.root, owner="model")
        self.gateway.intake(now=NOW)
        self.identity = self.gateway.deliveries()[0]["envelope_id"]
        self.now = NOW
        self.seen = ["PENDING"]

    @rule(outcome=st.sampled_from(["accepted", "flood_control", "rejected_permanent",
                                   "failed_before_send", "uncertain"]),
          confirmed=st.booleans())
    def attempt_send(self, outcome, confirmed):
        self.now += 120.0
        before = self.gateway.delivery(self.identity)["state"]
        self.exporter.refresh_permits(now=self.now)
        self.gateway.dispatch_once(
            FakeTransport(script=[TransportResult(outcome=outcome,
                                                  provider_reference="ref",
                                                  destination_confirmed=confirmed)]),
            now=self.now)
        after = self.gateway.delivery(self.identity)["state"]
        if after != before:
            assert before not in TERMINAL_DELIVERY, f"{before} reopened as {after}"
        self.seen.append(after)

    @rule()
    def take_over(self):
        self.now += 5.0
        with Gateway(self.root, owner="other") as rival:
            rival.acquire(now=self.now)
        assert self.gateway.delivery(self.identity)["state"] != "SENDING"

    @rule()
    def restart(self):
        self.now += 5.0
        self.gateway.recover(now=self.now)

    @invariant()
    def state_is_reachable_and_attempts_are_bounded(self):
        delivery = self.gateway.delivery(self.identity)
        assert delivery["state"] in DELIVERY_TRANSITIONS
        assert delivery["transmissions"] <= 3
        assert len(self.gateway.attempts(self.identity)) == delivery["transmissions"]
        if delivery["state"] == "ACCEPTED":
            assert delivery["provider_reference"] is not None

    def teardown(self):
        self.gateway.close()
        self.assistant.close()
        self.temporary.cleanup()


TestDeliveryLifecycle = DeliveryLifecycleMachine.TestCase
TestDeliveryLifecycle.settings = settings(max_examples=15, stateful_step_count=12,
                                          deadline=None)
TestDeliveryLifecycle = pytest.mark.messaging("N-A04")(TestDeliveryLifecycle)
