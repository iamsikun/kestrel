"""End-to-end counterexamples retained from the independent messaging audit.

All lab state is synthetic and external; transports never contact a provider.
These checks do not certify target-host identity/egress isolation.
"""
import sqlite3
from unittest.mock import patch

import pytest

from kestrel.application import Lab
from kestrel.artifacts import Artifacts
from kestrel.cli import main
from kestrel.messaging import Assistant, CommandProcessor, init_messaging
from kestrel.notifications import (
    Channel,
    Destination,
    DisclosureRefused,
    Exporter,
    FakeTransport,
    Gateway,
    Grant,
    render_notification,
)
from kestrel.telegram import InboundPoller

pytestmark = pytest.mark.messaging("N-A17")
NOW = 1_000_000.0


@pytest.fixture
def setup(tmp_path):
    with Lab.initialize(tmp_path / "lab") as lab:
        artifact = lab.store.put_bytes(b"synthetic audit content", producer="audit",
                                      classification="public_synthetic")
    root = tmp_path / "messaging"
    init_messaging(root, tmp_path / "lab")
    with Assistant(root) as assistant:
        assistant.reconcile(now=NOW)
        exporter = Exporter(assistant)
        token = (root / "assistant-private/operator.token").read_text()
        exporter.register_channel(Channel(channel_id="personal", version=1,
            destination=Destination(transport="fake", identity="4242")),
            operator_token=token, now=NOW)
        exporter.issue_grant(Grant(grant_id="notify_operator", version=1,
            principal="operator", channel_id="personal", allowed_conditions=[],
            expires_at=NOW + 200_000), operator_token=token, now=NOW)
        yield assistant, exporter, root, tmp_path / "lab", artifact


def update(identity, text, **extra):
    return {"update_id": identity, "message": {"message_id": identity, "date": int(NOW),
        "chat": {"id": 4242, "type": "private"}, "from": {"id": 4242, "is_bot": False},
        "text": text, **extra}}


def poller(gateway):
    return InboundPoller(gateway, None, bot_identity="12345", epoch=1,
                         chat_id=4242, user_id=4242)


def make_replies(assistant, count):
    for i in range(count):
        assistant.record_request(str(i), "help", {}, now=NOW,
            reply={"purpose": "reply:help", "payload": {
                "request": str(i), "detail": {"commands": "/help"}}})


@pytest.mark.parametrize("labels", [None, [], ["unknown"]])
def test_renderer_refuses_missing_or_unknown_provenance(labels):
    payload = {} if labels is None else {"classifications": labels}
    with pytest.raises(DisclosureRefused, match="provenance"):
        render_notification({"purpose": "backfill_summary", "payload": payload},
            grant=Grant(grant_id="g", version=1, principal="operator", channel_id="c",
                        allowed_conditions=[], expires_at=NOW+10),
            channel=Channel(channel_id="c", version=1,
                            destination=Destination(transport="fake", identity="4242")))


def test_classified_source_event_and_all_research_views_refuse_public_export(setup):
    a, e, root, lab, _ = setup
    store = Artifacts(lab / "runtime/artifacts")
    try:
        restricted = store.put_bytes(b"synthetic restricted fixture", producer="audit",
                                     classification="restricted")
        store.invalidate(restricted["digest"], "Synthetic correction")
    finally:
        store.close()
    # Remove the original public empty-state occurrence.
    a._db.execute("DELETE FROM intents")
    a.reconcile(now=NOW+1)
    due = a.set_schedule(now=NOW)["next_due"]
    a.reconcile(now=due+1)
    with Gateway(root) as g:
        p = poller(g)
        p.poll_once(now=due+1, updates=[update(1, "/brief"), update(2, "/inbox"),
                                      update(3, "/status missing")])
        CommandProcessor(a, p).process_pending(now=due+1)
    intents = a.intents()
    research = [i for i in intents if i["purpose"] != "backfill_summary"]
    assert research
    assert all("restricted" in i["payload"]["classifications"] for i in research)
    exported = e.export_pending(now=due+2)
    assert not exported["exported"]
    assert exported["refused"]


def test_actual_restricted_evidence_correction_is_never_declassified(setup):
    a, e, _, lab, _ = setup
    e.export_pending(now=NOW+1)
    store = Artifacts(lab / "runtime/artifacts")
    try:
        artifact = store.put_bytes(b"synthetic new restricted event", producer="audit",
                                   classification="restricted")
        store.invalidate(artifact["digest"], "Synthetic correction")
    finally:
        store.close()
    a.reconcile(now=NOW+2)
    intent = next(i for i in a.intents() if i["purpose"] == "item:evidence_correction")
    assert intent["payload"]["detail"]["classifications"] == ["restricted"]
    assert intent["payload"]["classifications"] == ["public_synthetic", "restricted"]
    assert not e.export_pending(now=NOW+3)["exported"]


def test_daily_and_requested_brief_reach_fake_transport_without_local_path(setup):
    a, e, root, lab, _ = setup
    a._db.execute("DELETE FROM intents")
    due = a.set_schedule(now=NOW)["next_due"]
    a.reconcile(now=due+1)
    with Gateway(root) as g:
        p = poller(g)
        p.poll_once(now=due+1, updates=[update(1, "/brief")])
        CommandProcessor(a, p).process_pending(now=due+1)
        outcome = e.export_pending(now=due+2)
        assert not outcome["refused"]
        assert len(outcome["exported"]) == 2
        g.intake(now=due+2)
        transport = FakeTransport()
        g.dispatch_once(transport, now=due+2)
        g.dispatch_once(transport, now=due+3)
        assert len(transport.sent) == 2
        for identity in outcome["exported"]:
            assert str(lab) not in g.read_envelope(identity)["text"]


def test_gateway_cli_never_opens_private_authority_paths(setup, monkeypatch):
    a, e, root, _, _ = setup
    for key, value in {"bot_id": 12345, "chat_id": 4242, "user_id": 4242, "epoch": 1}.items():
        a._set_meta(f"telegram_{key}", str(value))
    e.publish_routing()
    e.export_pending(now=NOW+1)
    private = root / "assistant-private"
    private.rename(root / "hidden-private")
    (root / "messaging.json").rename(root / "hidden-config")
    class OfflineClient:
        def get_updates(self, **kwargs):
            return []
    monkeypatch.setattr("kestrel.cli.build_live_client", lambda *args, **kw: OfflineClient())
    try:
        with patch("kestrel.cli.Assistant", side_effect=AssertionError("private authority opened")), \
                patch("time.time", return_value=NOW+2):
            for command in (["gateway", "intake"], ["gateway", "dispatch", "--transport", "fake"],
                            ["gateway", "health"], ["delivery", "list"],
                            ["inbound", "journal"], ["inbound", "poll", "--credential-file", "unused"]):
                assert main(["--messaging-root", str(root), "notify", *command]) == 0
    finally:
        (root / "hidden-private").rename(private)
        (root / "hidden-config").rename(root / "messaging.json")
    for path in (root / "notification-export").rglob("*.json"):
        assert path.stat().st_mode & 0o777 == 0o640
    assert (root / "telegram-runtime/gateway.sqlite").stat().st_mode & 0o777 == 0o660


def test_journal_failure_holds_cursor_then_recovers_without_losing_stop(setup):
    a, _, root, _, _ = setup
    with Gateway(root) as g:
        p = poller(g)
        p.poll_once(now=NOW, updates=[update(7, "/help")])
        g._db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON inbound_updates "
                      "WHEN NEW.update_id=9 BEGIN SELECT RAISE(ABORT, 'storage failure'); END")
        with pytest.raises(sqlite3.IntegrityError):
            p.poll_once(now=NOW+1, updates=[update(8, "/help"), update(9, "/stop"),
                                          update(10, "/help")])
        assert p.cursor()["offset"] == 8
        assert p.journal_size() == 2
        g._db.execute("DROP TRIGGER fail_insert")
        result = p.poll_once(now=NOW+2, updates=[update(8, "/help"), update(9, "/stop"),
                                               update(10, "/help")])
        assert result["duplicates"] == [8]
        assert p.cursor()["offset"] == 11
        CommandProcessor(a, p).process_pending(now=NOW+2)
        assert a.paused()


@pytest.mark.parametrize("marker", ["forward_origin", "forward_from", "forward_from_chat",
    "forward_sender_name", "forward_date", "is_automatic_forward"])
def test_forwarded_stop_is_journaled_as_rejection_and_never_applied(setup, marker):
    a, _, root, _, _ = setup
    with Gateway(root) as g:
        p = poller(g)
        result = p.poll_once(now=NOW, updates=[update(8, "/stop", **{marker: {}})])
        assert result["rejected"] == 1
        assert p.cursor()["offset"] == 9
        assert p.rejections()[0]["record"]["rejected"] == "forwarded_message"
        CommandProcessor(a, p).process_pending(now=NOW)
        assert not a.paused()


def test_two_instances_with_same_label_cannot_acquire_the_same_lease(setup):
    _, _, root, _, _ = setup
    with Gateway(root, owner="cli") as g1, Gateway(root, owner="cli") as g2:
        assert g1.owner != g2.owner
        assert g1.acquire(now=NOW)
        assert not g2.acquire(now=NOW)


def test_nested_sender_cannot_send_during_active_request_even_after_lease_expiry(setup):
    _, e, root, _, _ = setup
    e.export_pending(now=NOW+1)
    with Gateway(root, owner="cli") as g1, Gateway(root, owner="cli") as g2:
        g1.intake(now=NOW+2)
        original = g1._transmit
        nested = []
        def interleave(*args, **kwargs):
            nested.append(g2.dispatch_once(FakeTransport(), now=NOW+100))
            return original(*args, **kwargs)
        with patch.object(g1, "_transmit", side_effect=interleave):
            result = g1.dispatch_once(FakeTransport(), now=NOW+3)
        assert len(result["sent"]) == 1
        assert not nested[0]["sent"]
        assert g1.deliveries()[0]["transmissions"] == 1


def test_permit_never_outlives_grant_and_expired_grant_blocks_send(setup):
    _, e, root, _, _ = setup
    token = (root / "assistant-private/operator.token").read_text()
    e.issue_grant(Grant(grant_id="notify_operator", version=2, principal="operator",
        channel_id="personal", allowed_conditions=[], expires_at=NOW+10),
        operator_token=token, now=NOW)
    ids = e.export_pending(now=NOW+1)["exported"]
    with Gateway(root) as g:
        assert g.permit(ids[0], now=NOW+2)["expires_at"] == NOW+10
        g.intake(now=NOW+2)
        assert not g.dispatch_once(FakeTransport(), now=NOW+11)["sent"]


@pytest.mark.parametrize("change", ["stale", "source_changed", "unavailable", "unbound"])
def test_refresh_withdraws_permits_when_projection_cannot_authorize(setup, change):
    a, e, root, lab, artifact = setup
    e.export_pending(now=NOW+1)
    now = NOW+2
    if change == "stale":
        now = NOW+1000
    elif change == "source_changed":
        store = Artifacts(lab / "runtime/artifacts")
        try:
            store.invalidate(artifact["digest"], "Synthetic source change")
        finally:
            store.close()
    elif change == "unavailable":
        (lab / "lab.json").rename(lab / "hidden-lab.json")
    else:
        a._db.execute("UPDATE source_bindings SET status='rebind_required'")
    result = e.refresh_permits(now=now)
    assert result["issued"] == []
    with Gateway(root) as g:
        g.intake(now=now)
        assert not g.dispatch_once(FakeTransport(), now=now+1)["sent"]


@pytest.mark.parametrize("kinds", [["critical"]*20+["automated"]*15,
                                    ["automated"]*20+["critical"]*20])
def test_combined_automated_cap_holds_in_both_orders(setup, kinds):
    _, _, root, _, _ = setup
    with Gateway(root) as g:
        with g._transaction():
            results = [g._charge(kind, {}, now=NOW) for kind in kinds]
        assert sum(result is None for result in results) == 20
        assert g.quota_state(now=NOW)["total"] == 20


def test_pacing_and_reply_minute_cap_persist_across_gateway_restarts(setup):
    a, e, root, _, _ = setup
    a._db.execute("DELETE FROM intents")
    make_replies(a, 7)
    e.export_pending(now=NOW+1)
    transport = FakeTransport()
    for second in range(1, 8):
        with Gateway(root) as g:
            g.intake(now=NOW+second)
            assert len(g.dispatch_once(transport, now=NOW+second)["sent"]) <= 1
            assert not g.dispatch_once(transport, now=NOW+second)["sent"]
    assert len(transport.sent) == 5
    e.refresh_permits(now=NOW+70)
    with Gateway(root) as g:
        assert len(g.dispatch_once(transport, now=NOW+70)["sent"]) == 1


def test_dispatch_rechecks_authority_between_messages(setup):
    a, e, root, _, _ = setup
    a._db.execute("DELETE FROM intents")
    make_replies(a, 2)
    e.export_pending(now=NOW+1)
    times = iter([NOW+2, NOW+2, NOW+80])
    with Gateway(root) as g:
        g.intake(now=NOW+2)
        transport = FakeTransport()
        assert len(g.dispatch_once(transport, clock=lambda: next(times))["sent"]) == 1


def test_existing_restricted_source_classifies_initial_backfill(tmp_path):
    lab_root = tmp_path / "lab"
    with Lab.initialize(lab_root) as lab:
        artifact = lab.store.put_bytes(b"synthetic classified baseline", producer="audit",
                                       classification="restricted")
        lab.store.invalidate(artifact["digest"], "Synthetic baseline correction")
    root = tmp_path / "messaging"
    init_messaging(root, lab_root)
    with Assistant(root) as a:
        a.reconcile(now=NOW)
        intent = next(i for i in a.intents() if i["purpose"] == "backfill_summary")
        assert intent["payload"]["classifications"] == ["restricted"]
        with pytest.raises(DisclosureRefused, match="ceiling"):
            render_notification(intent, grant=Grant(grant_id="g", version=1,
                principal="operator", channel_id="c", allowed_conditions=[], expires_at=NOW+10),
                channel=Channel(channel_id="c", version=1,
                    destination=Destination(transport="fake", identity="4242")))


def test_refresh_never_reauthorizes_legacy_intent_without_provenance(setup):
    a, e, root, _, _ = setup
    ids = e.export_pending(now=NOW+1)["exported"]
    a._db.execute("UPDATE intents SET payload='{}'")
    assert e.refresh_permits(now=NOW+2)["issued"] == []
    with Gateway(root) as g:
        assert g.permit(ids[0], now=NOW+2) is None


def test_permit_is_clamped_to_projection_freshness_boundary(setup):
    a, e, root, _, _ = setup
    ids = e.export_pending(now=NOW+1)["exported"]
    deadline = NOW + a.config.projection_freshness_seconds
    e.refresh_permits(now=deadline-1)
    with Gateway(root) as g:
        assert g.permit(ids[0], now=deadline-1)["expires_at"] == deadline
        assert g.permit(ids[0], now=deadline) is None


def test_dispatch_rechecks_revocation_after_a_previous_send(setup):
    a, e, root, _, _ = setup
    a._db.execute("DELETE FROM intents")
    make_replies(a, 2)
    e.export_pending(now=NOW+1)
    token = (root / "assistant-private/operator.token").read_text()
    transport = FakeTransport()
    original = transport.send
    def revoke_after_send(body):
        result = original(body)
        e.revoke_grant("notify_operator", operator_token=token, now=NOW+2)
        return result
    times = iter([NOW+2, NOW+2, NOW+3])
    with Gateway(root) as g, patch.object(transport, "send", side_effect=revoke_after_send):
        g.intake(now=NOW+2)
        result = g.dispatch_once(transport, clock=lambda: next(times))
        assert len(result["sent"]) == 1
        assert result["terminal"][0]["state"] == "REVOKED"


def test_candidate_changed_after_selection_is_not_claimed_or_charged(setup):
    _, e, root, _, _ = setup
    e.export_pending(now=NOW+1)
    with Gateway(root) as g:
        g.intake(now=NOW+2)
        original = g.deliveries
        def change_after_selection():
            rows = original()
            g._db.execute("UPDATE deliveries SET state='ACCEPTED'")
            return rows
        with patch.object(g, "deliveries", side_effect=change_after_selection):
            assert not g.dispatch_once(FakeTransport(), now=NOW+3)["sent"]
        assert g.quota_state(now=NOW+3)["total"] == 0
        assert g.attempts() == []
