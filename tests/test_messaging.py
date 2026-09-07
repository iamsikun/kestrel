"""Assistant projection, attention, milestone and schedule tests.

These exercise the offline authority side only. Nothing here sends a message,
contacts a network, or establishes that a deployed process boundary is enforced;
those are separate, currently unpassed gates.
"""

import datetime
import hashlib
import sqlite3
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from kestrel.application import Lab, framework_root
from kestrel.controller import Controller
from kestrel.fixtures import generate
from kestrel.messaging import (
    Assistant,
    MessagingError,
    MilestoneDefinition,
    in_quiet_hours,
    init_messaging,
    local_instant,
    next_waking_instant,
)
from kestrel.sources import LabSources

CHICAGO = ZoneInfo("America/Chicago")


class SimulatedProjectorCrash(BaseException):
    """Bypasses ordinary error handling, like a killed process would."""


def science_fingerprint(root: Path) -> str:
    """Scientific payloads under fixed identities, excluding messaging state."""
    parts = []
    for relative in ("runtime/controller.sqlite", "runtime/artifacts/evidence.sqlite"):
        database = root / relative
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
            for table in sorted(row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")):
                rows = sorted(repr(tuple(row)) for row in
                              connection.execute(f"SELECT * FROM {table}"))
                parts.append(f"{relative}:{table}:" + "|".join(rows))
    for artifact in sorted((root / "runtime" / "artifacts" / "objects").glob("*")):
        parts.append(hashlib.sha256(artifact.read_bytes()).hexdigest())
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def science_evidence_fingerprint(root: Path) -> str:
    parts = []
    database = root / "runtime" / "artifacts" / "evidence.sqlite"
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        for table in sorted(row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")):
            parts.extend(sorted(repr(tuple(row)) for row in
                                connection.execute(f"SELECT * FROM {table}")))
    for artifact in sorted((root / "runtime" / "artifacts" / "objects").glob("*")):
        parts.append(hashlib.sha256(artifact.read_bytes()).hexdigest())
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def build_lab(tmp_path, *, run: bool = True, index: int = 0):
    manifests = generate(tmp_path / "external-projects", framework_root())
    with Lab.initialize(tmp_path / "external-lab") as lab:
        record = lab.register(manifests[index], snapshot_dirty=True)
        campaign = lab.propose(record["project_id"], "Synthetic messaging fixture.")
        if run:
            approval = lab.approve(campaign, lab.controller.campaign(campaign)["digest"],
                                   (lab.root / "operator.token").read_text())
            lab.run(campaign, approval)
        return lab.root, campaign


@pytest.fixture
def assistant(tmp_path):
    root, campaign = build_lab(tmp_path)
    init_messaging(tmp_path / "messaging", root, timezone="America/Chicago")
    with Assistant(tmp_path / "messaging") as helper:
        yield helper, root, campaign


@pytest.fixture
def pending_assistant(tmp_path):
    root, campaign = build_lab(tmp_path, run=False, index=1)
    init_messaging(tmp_path / "messaging", root, timezone="America/Chicago")
    with Assistant(tmp_path / "messaging") as helper:
        yield helper, root, campaign


# --------------------------------------------------------------------------
# Projection determinism and replay
# --------------------------------------------------------------------------

@pytest.mark.messaging("N-A03")
def test_projection_never_changes_scientific_state(assistant):
    helper, root, _ = assistant
    before = science_fingerprint(root)
    for _ in range(3):
        helper.reconcile(now=1_000_000.0)
    assert science_fingerprint(root) == before
    assert helper.inbox()


@pytest.mark.messaging("N-A03")
def test_replaying_a_batch_creates_one_logical_item(assistant):
    helper, _, _ = assistant
    first = helper.reconcile(now=1_000_000.0)
    assert first["created"]
    for tick in (1_000_060.0, 1_000_120.0):
        again = helper.reconcile(now=tick)
        assert again["created"] == [] and again["revised"] == []
        assert again["intents"] == []
    references = [item["reference"] for item in helper.inbox()]
    assert len(references) == len(set(references)) == len(first["created"])


@pytest.mark.messaging("N-A03")
def test_first_projection_backfills_the_inbox_but_sends_one_summary(assistant):
    helper, _, _ = assistant
    result = helper.reconcile(now=1_000_000.0)
    assert len(result["created"]) >= 1
    intents = helper.intents()
    assert [intent["purpose"] for intent in intents] == ["backfill_summary"]
    assert intents[0]["payload"]["note"].startswith("Existing state was projected")


@pytest.mark.messaging("N-A03")
def test_an_empty_interval_produces_no_items_or_intents(assistant):
    helper, _, _ = assistant
    helper.reconcile(now=1_000_000.0)
    quiet = helper.reconcile(now=1_000_030.0)
    assert quiet["created"] == quiet["revised"] == quiet["resolved"] == []
    assert quiet["intents"] == []


@pytest.mark.messaging("N-A03")
def test_a_crash_inside_the_batch_commits_nothing(assistant, monkeypatch):
    helper, _, _ = assistant

    def crash(*_args, **_kwargs):
        raise SimulatedProjectorCrash("killed mid-batch")

    monkeypatch.setattr(Assistant, "_advance_schedule", crash)
    with pytest.raises(SimulatedProjectorCrash):
        helper.reconcile(now=1_000_000.0)
    assert helper.inbox() == []
    assert helper.intents() == []
    assert helper.bindings() == {}
    monkeypatch.undo()
    assert helper.reconcile(now=1_000_010.0)["created"]


# --------------------------------------------------------------------------
# Attention semantics
# --------------------------------------------------------------------------

@pytest.mark.messaging("N-A17")
def test_acknowledgement_silences_reminders_without_resolving_the_condition(
        pending_assistant):
    helper, root, campaign = pending_assistant
    helper.reconcile(now=1_000_000.0)  # backfill: one summary, not one alert per item
    with Lab(root) as lab:
        lab.controller.block_task(lab.controller.tasks(campaign)[0]["id"], "input missing")
    helper.reconcile(now=1_000_050.0)
    item = next(row for row in helper.inbox() if row["condition"] == "blocked_work")
    assert [intent["item_id"] for intent in helper.intents(state="PENDING")
            if intent["item_id"]] == [item["item_id"]]
    acknowledged = helper.acknowledge(item["reference"], now=1_000_100.0)
    assert acknowledged["resolved"] is False
    assert "does not resolve" in acknowledged["note"]
    assert helper.intents(state="SUPPRESSED")
    # The blocker is still open in source state and still in the inbox.
    refreshed = helper.item(item["reference"])
    assert refreshed["attention"] == "ACKNOWLEDGED"
    assert refreshed["resolved_at"] is None
    assert item["reference"] in {row["reference"] for row in helper.inbox()}
    with LabSources(root) as sources:
        assert sources.controller.tasks(campaign)[0]["state"] == "BLOCKED"


@pytest.mark.messaging("N-A17")
def test_resolving_the_source_condition_closes_the_item_and_its_reminders(
        pending_assistant):
    helper, root, campaign = pending_assistant
    helper.reconcile(now=1_000_000.0)
    item = next(row for row in helper.inbox() if row["condition"] == "approval_needed")
    with Lab(root) as lab:
        lab.approve(campaign, lab.controller.campaign(campaign)["digest"],
                    (lab.root / "operator.token").read_text())
    resolved = helper.reconcile(now=1_000_200.0)
    assert item["reference"] in resolved["resolved"]
    assert helper.item(item["reference"])["resolved_at"] == 1_000_200.0
    assert not [row for row in helper.inbox() if row["condition"] == "approval_needed"]
    assert all(intent["state"] != "PENDING"
               for intent in helper.intents() if intent["item_id"] == item["item_id"])


@pytest.mark.messaging("N-A17")
def test_a_stale_reference_cannot_acknowledge_newly_worsened_evidence(pending_assistant):
    helper, root, campaign = pending_assistant
    helper.reconcile(now=1_000_000.0)
    item = next(row for row in helper.inbox() if row["condition"] == "blocked_work"
                or row["condition"] == "approval_needed")
    with Lab(root) as lab:
        task = lab.controller.tasks(campaign)[0]
        lab.controller.block_task(task["id"], "dependency unavailable")
    helper.reconcile(now=1_000_100.0)
    stale = f"{item['item_id']}-r{item['revision'] + 5}"
    with pytest.raises(MessagingError, match="stale"):
        helper.acknowledge(stale, now=1_000_200.0)
    with pytest.raises(MessagingError, match="stale"):
        helper.snooze(stale, "1h", now=1_000_200.0)


@pytest.mark.messaging("N-A17")
def test_material_worsening_creates_a_new_revision_needing_fresh_attention(
        pending_assistant):
    helper, root, campaign = pending_assistant
    helper.reconcile(now=1_000_000.0)
    item = next(row for row in helper.inbox() if row["condition"] == "approval_needed")
    helper.acknowledge(item["reference"], now=1_000_050.0)
    with Lab(root) as lab:
        for task in lab.controller.tasks(campaign)[:1]:
            lab.controller.block_task(task["id"], "dependency unavailable")
    revised = helper.reconcile(now=1_000_100.0)
    blocked = [row for row in helper.inbox() if row["condition"] == "blocked_work"]
    assert blocked and blocked[0]["attention"] == "OPEN"
    assert revised["created"] or revised["revised"]


@pytest.mark.messaging("N-A17")
def test_snooze_requires_an_explicit_bounded_expiry(assistant):
    helper, _, _ = assistant
    helper.reconcile(now=1_000_000.0)
    reference = helper.inbox()[0]["reference"]
    deferred = helper.snooze(reference, "1h", now=1_000_000.0)
    assert deferred["attention"] == "SNOOZED"
    assert deferred["attention_until"] == 1_003_600.0
    for rejected in ("forever", "10s", "999d", ""):
        with pytest.raises(MessagingError):
            helper.snooze(reference, rejected, now=1_000_000.0)


@pytest.mark.messaging("N-A17")
def test_tomorrow_means_the_next_configured_local_brief_time(assistant):
    helper, _, _ = assistant
    helper.reconcile(now=1_000_000.0)
    reference = helper.inbox()[0]["reference"]
    noon = local_instant(datetime.date(2026, 6, 1), datetime.time(12, 0), CHICAGO)
    deferred = helper.snooze(reference, "tomorrow", now=noon)
    when = datetime.datetime.fromtimestamp(deferred["attention_until"],
                                           datetime.UTC).astimezone(CHICAGO)
    assert (when.date(), when.hour, when.minute) == (datetime.date(2026, 6, 2), 8, 0)


@pytest.mark.messaging("N-A17")
def test_a_repeated_request_identity_is_applied_once(assistant):
    helper, _, _ = assistant
    helper.reconcile(now=1_000_000.0)
    reference = helper.inbox()[0]["reference"]
    first = helper.acknowledge(reference, now=1_000_010.0, request_id="update:41")
    second = helper.acknowledge(reference, now=1_000_020.0, request_id="update:41")
    assert first == second
    actions = [row["action"] for row in helper.item(reference)["history"]]
    assert actions.count("acknowledge") == 1


@pytest.mark.messaging("N-A17")
def test_attention_history_is_append_only(assistant):
    helper, _, _ = assistant
    helper.reconcile(now=1_000_000.0)
    helper.acknowledge(helper.inbox()[0]["reference"], now=1_000_010.0)
    with pytest.raises(sqlite3.Error):
        helper._db.execute("UPDATE attention_history SET action='forged'")
    with pytest.raises(sqlite3.Error):
        helper._db.execute("DELETE FROM attention_history")


# --------------------------------------------------------------------------
# Milestones
# --------------------------------------------------------------------------

def milestone_for(campaign, **overrides):
    predicate = {"campaign_id": campaign, "terminal_state": "COMPLETE",
                 "required_protocol": "valid",
                 "required_assurance": ["independently_recomputed"], **overrides}
    return MilestoneDefinition(label="Comparison complete", predicates=[predicate])


@pytest.mark.messaging("N-A17")
def test_a_reached_milestone_is_announced_and_reopens_on_invalidation(assistant):
    helper, root, campaign = assistant
    helper.reconcile(now=1_000_000.0)
    helper.add_milestone("gate", milestone_for(campaign), now=1_000_010.0)
    reached = helper.reconcile(now=1_000_020.0)
    assert any(helper.item(ref)["condition"] == "milestone_reached"
               for ref in reached["created"])
    assert [row["state"] for row in helper.milestone_history()] == ["reached"]
    with Lab(root) as lab:
        evidence = lab.controller.campaign(campaign)["outcome"]["evidence_ids"][0]
        lab.store.invalidate(evidence, "evaluator withdrawn")
    reopened = helper.reconcile(now=1_000_030.0)
    assert reopened["created"] or reopened["revised"]
    assert [row["state"] for row in helper.milestone_history()] == ["reached", "reopened"]
    current = [row for row in helper.inbox() if row["condition"] == "milestone_reopened"]
    assert current and current[0]["detail"]["reached"] is False


@pytest.mark.messaging("N-A17")
def test_an_unreached_milestone_is_never_announced_as_project_completion(
        pending_assistant):
    helper, _, campaign = pending_assistant
    helper.add_milestone("gate", milestone_for(campaign), now=1_000_000.0)
    helper.reconcile(now=1_000_010.0)
    assert not [row for row in helper.inbox() if row["condition"].startswith("milestone_")]
    assert helper.milestone_history() == []


@pytest.mark.messaging("N-A17")
def test_a_milestone_predicate_cannot_carry_an_expression_or_queue_rule():
    for rejected in ({"campaign_id": "c1", "expression": "queue == 0"},
                     {"campaign_id": "c1", "sql": "SELECT 1"},
                     {"campaign_id": "c1", "terminal_state": "ANY"},
                     {"campaign_id": "c1", "required_protocol": "whatever"}):
        with pytest.raises(ValueError):
            MilestoneDefinition(label="bad", predicates=[rejected])
    with pytest.raises(ValueError):
        MilestoneDefinition(label="empty", predicates=[])


@pytest.mark.messaging("N-A17")
def test_changing_a_milestone_creates_a_new_version_and_keeps_the_old_one(assistant):
    helper, _, campaign = assistant
    helper.add_milestone("gate", milestone_for(campaign), now=1_000_000.0)
    second = helper.add_milestone(
        "gate", milestone_for(campaign, allowed_findings=["supported_in_scope"]),
        now=1_000_010.0)
    assert second["version"] == 2
    versions = [(row["version"], bool(row["active"])) for row in helper.milestones()]
    assert versions == [(1, False), (2, True)]
    with pytest.raises(sqlite3.Error):
        helper._db.execute("UPDATE milestones SET definition='{}' WHERE version=1")


@pytest.mark.messaging("N-A17")
def test_a_milestone_over_an_unknown_campaign_is_unsatisfied_not_reached(assistant):
    helper, root, _ = assistant
    definition = milestone_for("absent-campaign")
    with LabSources(root) as sources:
        evaluation = Assistant.evaluate_milestone(sources, definition, now=1_000_000.0)
    assert evaluation["reached"] is False
    assert evaluation["clauses"][0]["reason"] == "campaign_unavailable"


# --------------------------------------------------------------------------
# Schedules, DST, catch-up and clock changes
# --------------------------------------------------------------------------

@pytest.mark.messaging("N-A13")
def test_a_skipped_local_time_uses_the_next_instant_that_exists():
    instant = local_instant(datetime.date(2026, 3, 8), datetime.time(2, 30), CHICAGO)
    local = datetime.datetime.fromtimestamp(instant, datetime.UTC).astimezone(CHICAGO)
    assert (local.hour, local.minute) == (3, 0)
    earlier = datetime.datetime.fromtimestamp(instant - 1, datetime.UTC).astimezone(CHICAGO)
    assert (earlier.hour, earlier.minute) == (1, 59)


@pytest.mark.messaging("N-A13")
def test_an_ambiguous_local_time_uses_its_first_occurrence():
    instant = local_instant(datetime.date(2026, 11, 1), datetime.time(1, 30), CHICAGO)
    local = datetime.datetime.fromtimestamp(instant, datetime.UTC).astimezone(CHICAGO)
    assert (local.hour, local.minute) == (1, 30)
    assert local.utcoffset() == datetime.timedelta(hours=-5)
    later = datetime.datetime.fromtimestamp(instant + 3600, datetime.UTC).astimezone(CHICAGO)
    assert (later.hour, later.minute) == (1, 30)
    assert later.utcoffset() == datetime.timedelta(hours=-6)


@pytest.mark.messaging("N-A13")
def test_one_brief_per_scheduled_local_date(assistant):
    helper, _, _ = assistant
    start = local_instant(datetime.date(2026, 6, 1), datetime.time(7, 0), CHICAGO)
    helper.set_schedule(local_time="08:00", now=start)
    helper.reconcile(now=start)
    first_due = local_instant(datetime.date(2026, 6, 1), datetime.time(8, 0), CHICAGO)
    first = helper.reconcile(now=first_due + 60)
    assert [row["purpose"] for row in first["occurrences"]] == ["daily_brief"]
    assert first["occurrences"][0]["local_date"] == "2026-06-01"
    for later in (first_due + 120, first_due + 3600):
        assert helper.reconcile(now=later).get("occurrences") is None
    second_due = local_instant(datetime.date(2026, 6, 2), datetime.time(8, 0), CHICAGO)
    second = helper.reconcile(now=second_due + 60)
    assert [row["local_date"] for row in second["occurrences"]] == ["2026-06-02"]
    assert second["occurrences"][0]["missed_days"] == 0


@pytest.mark.messaging("N-A13")
def test_missed_days_produce_one_catch_up_rather_than_a_week_of_mornings(assistant):
    helper, _, _ = assistant
    start = local_instant(datetime.date(2026, 6, 1), datetime.time(7, 0), CHICAGO)
    helper.set_schedule(local_time="08:00", now=start)
    helper.reconcile(now=start)
    helper.reconcile(now=local_instant(datetime.date(2026, 6, 1),
                                       datetime.time(8, 1), CHICAGO))
    resumed = local_instant(datetime.date(2026, 6, 9), datetime.time(9, 0), CHICAGO)
    result = helper.reconcile(now=resumed)
    assert len(result["occurrences"]) == 1
    occurrence = result["occurrences"][0]
    assert occurrence["purpose"] == "catch_up"
    assert occurrence["missed_days"] == 7
    assert occurrence["local_date"] == "2026-06-09"
    intent = next(row for row in helper.intents() if row["purpose"] == "catch_up")
    assert intent["payload"]["missed_days"] == 7


@pytest.mark.messaging("N-A13")
def test_a_clock_rollback_cannot_repeat_a_delivered_occurrence(assistant):
    helper, _, _ = assistant
    start = local_instant(datetime.date(2026, 6, 1), datetime.time(7, 0), CHICAGO)
    helper.set_schedule(local_time="08:00", now=start)
    helper.reconcile(now=start)
    due = local_instant(datetime.date(2026, 6, 1), datetime.time(8, 0), CHICAGO)
    assert helper.reconcile(now=due + 60)["occurrences"]
    rolled_back = helper.reconcile(now=due - 86_400)
    assert rolled_back.get("occurrences") is None
    assert len({row["purpose"] for row in helper.intents()
                if row["purpose"] in ("daily_brief", "catch_up")}) == 1


@pytest.mark.messaging("N-A13")
def test_a_brief_crossing_a_dst_boundary_keeps_one_occurrence_per_local_date(assistant):
    helper, _, _ = assistant
    before = local_instant(datetime.date(2026, 3, 7), datetime.time(7, 0), CHICAGO)
    helper.set_schedule(local_time="02:30", now=before)
    helper.reconcile(now=before)
    across = local_instant(datetime.date(2026, 3, 8), datetime.time(9, 0), CHICAGO)
    result = helper.reconcile(now=across)
    assert len(result["occurrences"]) == 1
    assert result["occurrences"][0]["local_date"] == "2026-03-08"


@pytest.mark.messaging("N-A13")
def test_quiet_hours_defer_delivery_without_an_automatic_overnight_bypass(assistant):
    helper, _, _ = assistant
    midnight = local_instant(datetime.date(2026, 6, 2), datetime.time(0, 30), CHICAGO)
    assert in_quiet_hours(midnight, helper.config)
    helper.reconcile(now=midnight)
    for intent in helper.intents():
        release = datetime.datetime.fromtimestamp(intent["not_before"],
                                                  datetime.UTC).astimezone(CHICAGO)
        assert (release.hour, release.minute) == (8, 0)
    assert next_waking_instant(midnight, helper.config) > midnight


@pytest.mark.messaging("N-A13")
def test_a_briefing_record_is_retained_locally_with_its_coverage(assistant):
    helper, _, _ = assistant
    start = local_instant(datetime.date(2026, 6, 1), datetime.time(7, 0), CHICAGO)
    helper.set_schedule(local_time="08:00", now=start)
    helper.reconcile(now=start)
    due = local_instant(datetime.date(2026, 6, 1), datetime.time(8, 0), CHICAGO)
    helper.reconcile(now=due + 60)
    intent = next(row for row in helper.intents() if row["purpose"] == "daily_brief")
    briefing = helper.briefing(intent["payload"]["briefing"])
    assert briefing["content_digest"] == intent["payload"]["content_digest"]
    assert briefing["record"]["cutoffs"]["controller"]["sequence"] > 0
    assert (helper.private / "briefings" / f"{briefing['id']}.json").is_file()


# --------------------------------------------------------------------------
# Source continuity, gaps and grouping
# --------------------------------------------------------------------------

@pytest.mark.messaging("N-A18")
def test_a_replaced_source_database_pauses_projection_and_asks_for_a_rebind(
        assistant, tmp_path):
    helper, root, _ = assistant
    helper.reconcile(now=1_000_000.0)
    evidence_before = science_evidence_fingerprint(root)
    database = root / "runtime" / "controller.sqlite"
    for sidecar in ("", "-wal", "-shm"):
        Path(str(database) + sidecar).unlink(missing_ok=True)
    with Controller(database) as restored:
        restored.initialize_operator("x" * 64)
    paused = helper.reconcile(now=1_000_100.0)
    assert paused["paused"] is True
    assert paused["gaps"][0]["status"] == "rebind_required"
    assert helper.gaps()[0]["kind"] == "source_continuity"
    # Projection stays paused until the operator confirms which lab this is.
    assert helper.reconcile(now=1_000_200.0)["paused"] is True
    with pytest.raises(MessagingError, match="confirm"):
        helper.rebind_sources(confirm=False)
    assert helper.rebind_sources(confirm=True, now=1_000_300.0)["rebound"] is True
    assert helper.reconcile(now=1_000_400.0).get("paused") is not True
    assert helper.gaps() == []
    assert helper.bindings()["controller"]["epoch"] == 2
    # Replacing the ledger is the injected fault; the evidence store and its
    # stored bytes must be untouched by the assistant throughout.
    assert science_evidence_fingerprint(root) == evidence_before


@pytest.mark.messaging("N-A18")
def test_an_unavailable_source_reports_a_bounded_gap_without_mutating_anything(
        assistant):
    helper, root, _ = assistant
    helper.reconcile(now=1_000_000.0)
    database = root / "runtime" / "controller.sqlite"
    moved = database.with_suffix(".moved")
    database.rename(moved)
    result = helper.reconcile(now=1_000_100.0)
    assert result["paused"] is True
    assert result["gaps"][0]["kind"] == "source_unavailable"
    assert helper.gaps()[0]["kind"] == "source_unavailable"
    moved.rename(database)
    recovered = helper.reconcile(now=1_000_200.0)
    assert recovered.get("paused") is not True
    assert helper.gaps() == []


@pytest.mark.messaging("N-A18")
def test_a_truncated_event_history_is_treated_as_uncertain_continuity(assistant):
    helper, _, _ = assistant
    helper.reconcile(now=1_000_000.0)
    helper._db.execute("UPDATE source_bindings SET cursor=999999 WHERE feed='controller'")
    result = helper.reconcile(now=1_000_100.0)
    assert result["paused"] is True
    assert "beyond the source head" in result["gaps"][0]["detail"]


@pytest.mark.messaging("N-A08")
def test_one_invalidation_becomes_one_grouped_incident(assistant):
    helper, root, campaign = assistant
    helper.reconcile(now=1_000_000.0)
    with Lab(root) as lab:
        evidence = lab.controller.campaign(campaign)["outcome"]["evidence_ids"][0]
        ancestor = lab.store.get(evidence)["lineage"][0]
        affected = lab.store.invalidate(ancestor, "input dataset withdrawn")
    assert len(affected) > 1
    helper.reconcile(now=1_000_100.0)
    corrections = [row for row in helper.inbox()
                   if row["condition"] == "evidence_correction"]
    assert len(corrections) == 1
    assert corrections[0]["detail"]["affected"] == len(affected)


@pytest.mark.messaging("N-A08")
def test_many_held_attempts_become_one_incident_per_campaign(assistant, monkeypatch):
    helper, root, campaign = assistant
    with LabSources(root) as sources:
        attempts = sources.controller.attempts(campaign)
    held = [{**attempt, "resources_held": True, "state": "RUNNING",
             "stopped_confirmed": False} for attempt in attempts] * 20
    monkeypatch.setattr("kestrel.sources.ControllerSource.unsettled_attempts",
                        lambda self, **_: held)
    helper.reconcile(now=1_000_000.0)
    incidents = [row for row in helper.inbox() if row["condition"] == "uncertain_capacity"]
    assert len(incidents) == 1
    assert incidents[0]["detail"]["attempt_count"] == len(attempts)
    assert "no blind restart" in incidents[0]["detail"]["consequence"]


@pytest.mark.messaging("N-A11")
def test_status_reports_projection_lag_and_refuses_to_claim_health(assistant):
    helper, _, _ = assistant
    assert helper.status(now=1_000_000.0)["projection_stale"] is True
    helper.reconcile(now=1_000_000.0)
    fresh = helper.status(now=1_000_060.0)
    assert fresh["projection_stale"] is False
    assert fresh["projection_lag_seconds"] == 60.0
    stale = helper.status(now=1_000_000.0 + 4000)
    assert stale["projection_stale"] is True
    assert "cannot report its own outage" in fresh["limitation"]


@pytest.mark.messaging("N-A11")
def test_pause_is_a_local_preference_that_cannot_revoke_a_remote_credential(assistant):
    helper, _, _ = assistant
    paused = helper.pause(paused=True, now=1_000_000.0)
    assert helper.paused() is True
    assert "cannot revoke a bot token" in paused["note"]
    helper.pause(paused=False, now=1_000_010.0)
    assert helper.paused() is False


@pytest.mark.messaging("N-A10")
def test_messaging_state_lives_outside_the_lab_and_the_checkout(tmp_path):
    root, _ = build_lab(tmp_path, run=False)
    with pytest.raises(MessagingError):
        init_messaging(root / "messaging", root)
    with pytest.raises(MessagingError):
        init_messaging(tmp_path / "elsewhere", tmp_path / "absent-lab")
    created = init_messaging(tmp_path / "messaging", root)
    assert not Path(created["messaging_root"]).is_relative_to(framework_root())
    assert not Path(created["messaging_root"]).is_relative_to(root)
    assert (tmp_path / "messaging").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "messaging" / "messaging.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.messaging("N-A10")
def test_the_assistant_imports_no_provider_or_network_client():
    text = (Path(framework_root()) / "src" / "kestrel" / "messaging.py").read_text()
    for forbidden in ("import requests", "import httpx", "urllib.request", "import socket",
                      "openai", "anthropic", "torch"):
        assert forbidden not in text


@pytest.mark.messaging("N-A10")
def test_the_offline_pilot_is_unaffected_by_a_projecting_assistant(tmp_path):
    root, campaign = build_lab(tmp_path)
    with Lab(root) as lab:
        expected = lab.report(campaign)
    init_messaging(tmp_path / "messaging", root)
    with Assistant(tmp_path / "messaging") as helper:
        helper.reconcile(now=1_000_000.0)
        helper.reconcile(now=1_000_100.0)
    with Lab(root) as lab:
        assert lab.report(campaign) == expected
        assert lab.controller.budget_used(campaign) == expected["budget_reserved"]
        assert len(lab.controller.attempts(campaign)) == len(expected["attempts"])
        assert lab.controller.selections(campaign) if hasattr(
            lab.controller, "selections") else True


def test_an_unsupported_assistant_schema_is_refused(tmp_path):
    root, _ = build_lab(tmp_path, run=False)
    init_messaging(tmp_path / "messaging", root)
    with Assistant(tmp_path / "messaging"):
        pass
    database = tmp_path / "messaging" / "assistant-private" / "assistant.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(MessagingError, match="schema version"):
        Assistant(tmp_path / "messaging")


def test_item_references_bind_both_item_and_revision():
    assert Assistant.parse_reference("M42-r7") == ("M42", 7)
    for rejected in ("M42", "42-r1", "M42-r", "M42-r1x", "", "M42-r1; DROP TABLE items"):
        with pytest.raises(MessagingError):
            Assistant.parse_reference(rejected)


def test_a_watch_scope_must_be_one_of_the_supported_kinds(assistant):
    helper, _, campaign = assistant
    assert helper.watch(f"campaign:{campaign}", now=1.0)["version"] == 1
    assert helper.watch(f"campaign:{campaign}", now=2.0)["version"] == 2
    assert [row["scope"] for row in helper.subscriptions()] == [f"campaign:{campaign}"]
    for rejected in ("project:x", "campaign:", "campaign:../etc", "lab", ""):
        with pytest.raises(MessagingError):
            helper.watch(rejected, now=1.0)


@pytest.mark.messaging("N-A03")
def test_watching_a_campaign_promotes_its_completion_from_digest_to_timely(assistant):
    helper, _, campaign = assistant
    helper.reconcile(now=1_000_000.0)
    digest_item = next(row for row in helper.inbox()
                       if row["condition"] == "campaign_complete")
    assert digest_item["route"] == "digest"
    helper.watch(f"campaign:{campaign}", now=1_000_010.0)
    helper.reconcile(now=1_000_020.0)
    watched = [row for row in helper.inbox()
               if row["condition"] == "watched_campaign_complete"]
    assert watched and watched[0]["route"] == "timely"
    assert any(intent["payload"].get("condition") == "watched_campaign_complete"
               for intent in helper.intents())


@pytest.mark.messaging("N-A03")
def test_a_watched_task_names_its_attempts_and_a_watched_attempt_reports_execution(
        assistant):
    helper, root, campaign = assistant
    with LabSources(root) as sources:
        task = sources.controller.tasks(campaign)[0]
        attempt = sources.controller.attempts(campaign)[0]
    helper.watch(f"task:{task['id']}", now=1_000_000.0)
    helper.watch(f"attempt:{attempt['id']}", now=1_000_000.0)
    helper.reconcile(now=1_000_010.0)
    inbox = {row["condition"]: row for row in helper.inbox()}
    assert inbox["watched_task_settled"]["detail"]["attempts"]
    assert inbox["watched_task_settled"]["detail"]["attempt_count"] >= 1
    settled = inbox["watched_attempt_settled"]["detail"]
    assert settled["state"] in {"SUCCEEDED", "FAILED", "CANCELLED", "LOST"}
    assert "evaluation_recorded" in settled


def test_snapshot_export_is_authority_side_and_labelled_as_a_copy(assistant):
    helper, _, _ = assistant
    produced = helper.snapshot_sources(now=1_000_000.0)
    assert set(produced["databases"]) == {"controller", "evidence"}
    for path in produced["databases"].values():
        assert Path(path).is_file()
        assert Path(path).is_relative_to(helper.private)
    assert "no independent scientific assurance" in produced["limitation"]
