"""Offline authority, durable dispatch, and model-based controller acceptance."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from kestrel.controller import AuthorityError, BudgetError, Controller, ControllerError, StateError

TOKEN = "synthetic-development-operator-token-" * 2


def contract(*, attempts: int = 6, profile: str = "development") -> dict:
    return {
        "brief": {"original": "Does the synthetic treatment reduce error?"},
        "metric": "mean_squared_error",
        "selection_rule": "evaluate each declared candidate once",
        "profile": profile,
        "capabilities": ["execute"],
        "budget": {"attempts": attempts, "runtime_seconds": 60, "provider_calls": 0, "tokens": 0},
    }


def authorize(store: Controller, specification: dict | None = None) -> tuple[str, str]:
    campaign_id = store.propose(specification or contract())
    approval_id = store.approve(
        campaign_id,
        token=TOKEN,
        contract_digest=store.campaign(campaign_id)["digest"],
        principal="fixture-controller",
        expires_at=time.time() + 3600,
    )
    return campaign_id, approval_id


def reserve(store: Controller, task_id: str, approval_id: str, **kwargs) -> dict:
    return store.reserve(
        task_id,
        approval_id=approval_id,
        principal="fixture-controller",
        backend="synthetic-driver",
        available_capabilities={"development"},
        **kwargs,
    )


def inspect(status: str, **kwargs):
    return lambda label: {"backend_label": label, "status": status, **kwargs}


def succeeded(store: Controller, attempt: dict) -> dict:
    attempt = store.transition(attempt["id"], "RUNNING", fence=attempt["fence"])
    attempt = store.reconcile(attempt["id"], inspect("stopped"))
    attempt = store.transition(attempt["id"], "VERIFYING", fence=attempt["fence"])
    observation = hashlib.sha256(f"observation-{attempt['id']}".encode()).hexdigest()
    store.record_result(attempt["id"], {"observation_id": observation})
    return store.transition(attempt["id"], "SUCCEEDED", fence=attempt["fence"])


@pytest.fixture
def store(tmp_path):
    with Controller(
        tmp_path / "runtime" / "controller.sqlite", resource_capacity={"cpu": 1, "gpu": 0}
    ) as instance:
        instance.initialize_operator(TOKEN)
        yield instance


@pytest.mark.acceptance("A16")
def test_failed_retries_and_new_task_ids_share_immutable_campaign_budget(store):
    campaign_id, approval_id = authorize(store, contract(attempts=2))
    task = store.add_task(campaign_id, {"id": "failing", "max_attempts": 3})
    for _ in range(2):
        attempt = reserve(store, task, approval_id)
        store.transition(
            attempt["id"], "FAILED", fence=attempt["fence"], detail={"reason": "fixture failure"}
        )
        # A terminal label does not establish the job has actually stopped.
        with pytest.raises(StateError):
            reserve(store, task, approval_id)
        store.reconcile(attempt["id"], inspect("stopped"))
    assert store.budget_used(campaign_id)["attempts"] == 2
    assert store.budget_used(campaign_id)["runtime_seconds"] == 2
    for task_id in (task, store.add_task(campaign_id, {"id": "budget-laundering"})):
        with pytest.raises(BudgetError, match="Campaign budget"):
            reserve(store, task_id, approval_id)
    assert len(store.attempts(campaign_id)) == 2
    assert all(attempt["state"] == "FAILED" for attempt in store.attempts(campaign_id))


@pytest.mark.acceptance("A16")
def test_budget_reservation_is_atomic_across_two_controllers(store):
    campaign_id, approval_id = authorize(store, contract(attempts=1))
    one = store.add_task(campaign_id, {"id": "one"})
    two = store.add_task(campaign_id, {"id": "two"})
    with Controller(store.path) as other:
        attempt = reserve(store, one, approval_id)
        with pytest.raises(BudgetError):
            reserve(other, two, approval_id)
        assert other.attempts(campaign_id)[0]["id"] == attempt["id"]
    assert store.budget_used(campaign_id)["attempts"] == 1


@pytest.mark.acceptance("A16")
def test_concurrent_reservation_waits_for_transaction_then_rechecks_budget(store, monkeypatch):
    campaign_id, approval_id = authorize(store, contract(attempts=1))
    first = store.add_task(campaign_id, {"id": "concurrent-one"})
    second = store.add_task(campaign_id, {"id": "concurrent-two"})
    read_budget = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_finished = threading.Event()
    original_budget_used = Controller.budget_used
    outcomes = []

    def pause_first_after_read(instance, campaign):
        value = original_budget_used(instance, campaign)
        if threading.current_thread().name == "first-reservation":
            read_budget.set()
            assert release_first.wait(5)
        return value

    monkeypatch.setattr(Controller, "budget_used", pause_first_after_read)

    def launch(task_id, is_second=False):
        try:
            if is_second:
                second_started.set()
            with Controller(store.path) as connection:
                reserve(connection, task_id, approval_id)
            outcomes.append("reserved")
        except BudgetError:
            outcomes.append("denied")
        finally:
            if is_second:
                second_finished.set()

    thread_one = threading.Thread(target=launch, args=(first,), name="first-reservation")
    thread_two = threading.Thread(target=launch, args=(second, True), name="second-reservation")
    thread_one.start()
    try:
        assert read_budget.wait(5)
        thread_two.start()
        assert second_started.wait(5)
        # The first writer holds the transaction open after reading the cap.
        assert not second_finished.wait(0.05)
    finally:
        release_first.set()
        thread_one.join(5)
        if thread_two.ident is not None:
            thread_two.join(5)
    assert not thread_one.is_alive() and not thread_two.is_alive()
    assert sorted(outcomes) == ["denied", "reserved"]
    assert len(store.attempts(campaign_id)) == 1


@pytest.mark.acceptance("A16")
@pytest.mark.parametrize(
    "bad", [{"attempts": -1}, {"runtime_seconds": float("nan")}, {"tokens": True}, {"unknown": 1}]
)
def test_invalid_or_understated_reservation_cannot_bypass_caps(store, bad):
    campaign_id, approval_id = authorize(store)
    task = store.add_task(campaign_id, {"id": "bounded", "budget": {"runtime_seconds": 10}})
    with pytest.raises(ControllerError):
        reserve(store, task, approval_id, budget_request=bad)
    with pytest.raises(BudgetError, match="understate"):
        reserve(store, task, approval_id, budget_request={"runtime_seconds": 1})
    assert store.attempts(campaign_id) == []
    assert not any(store.resources_used().values())


@pytest.mark.acceptance("A18")
def test_unavailable_isolation_cannot_fallback_to_development(store):
    campaign_id, approval_id = authorize(store, contract(profile="isolated-local"))
    with pytest.raises(AuthorityError, match="security profile"):
        store.add_task(campaign_id, {"id": "downgrade", "profile": "development"})
    task = store.add_task(campaign_id, {"id": "isolated", "profile": "isolated-local"})
    with pytest.raises(AuthorityError, match="unavailable"):
        reserve(store, task, approval_id)
    assert store.attempts(campaign_id) == []


@pytest.mark.acceptance("A19")
def test_approval_requires_operator_and_exact_digest_policy_principal_and_expiry(
    store, monkeypatch
):
    campaign_id, approval_id = authorize(store)
    campaign = store.campaign(campaign_id)
    for changed in (
        {"token": "worker-provided-fake-approval"},
        {"contract_digest": "0" * 64},
        {"policy_version": "worker-policy"},
        {"expires_at": time.time() - 1},
        {"capabilities": ["execute", "new_privilege"]},
    ):
        arguments = {
            "token": TOKEN,
            "contract_digest": campaign["digest"],
            "expires_at": time.time() + 30,
        }
        with pytest.raises(AuthorityError):
            store.approve(campaign_id, **{**arguments, **changed})
    task = store.add_task(campaign_id, {"id": "approval-bound"})
    with pytest.raises(AuthorityError, match="principal"):
        store.reserve(
            task,
            approval_id=approval_id,
            principal="worker",
            backend="fixture",
            available_capabilities={"development"},
        )
    second_campaign, _ = authorize(store)
    second_task = store.add_task(second_campaign, {"id": "replay-other-campaign"})
    with pytest.raises(AuthorityError):
        reserve(store, second_task, approval_id)
    future = time.time() + 4000
    with monkeypatch.context() as patch:
        patch.setattr("kestrel.controller.time.time", lambda: future)
        with pytest.raises(AuthorityError, match="expired"):
            reserve(store, task, approval_id)
    assert store.attempts(campaign_id) == []


@pytest.mark.acceptance("A19")
def test_operator_cannot_be_rebootstrapped_or_policy_expanded_on_reopen(store):
    with pytest.raises(AuthorityError):
        store.initialize_operator("replacement-token-" * 4)
    with pytest.raises(AuthorityError, match="policy"):
        Controller(store.path, policy_version="worker-policy")
    with pytest.raises(AuthorityError, match="capacity"):
        Controller(store.path, resource_capacity={"cpu": 10})
    campaign_id, approval_id = authorize(store)
    task = store.add_task(campaign_id, {"id": "revoked"})
    store.revoke(approval_id, token=TOKEN)
    with pytest.raises(AuthorityError):
        reserve(store, task, approval_id)


@pytest.mark.acceptance("A19")
@pytest.mark.parametrize("change", ["revoke", "expire", "wrong-principal"])
def test_reservation_does_not_preserve_expired_or_revoked_launch_authority(
    store, monkeypatch, change
):
    campaign_id, approval_id = authorize(store)
    task = store.add_task(campaign_id, {"id": "reserved-before-authority-change"})
    attempt = reserve(store, task, approval_id, lease_seconds=7200)
    principal = "fixture-controller"
    if change == "revoke":
        store.revoke(approval_id, token=TOKEN)
    elif change == "expire":
        future = time.time() + 4000
        monkeypatch.setattr("kestrel.controller.time.time", lambda: future)
    else:
        principal = "worker"
    with pytest.raises(AuthorityError, match="Launch approval"):
        store.authorize_launch(attempt["id"], principal=principal)
    assert store.budget_used(campaign_id)["attempts"] == 1
    assert store.attempt(attempt["id"])["resources_held"]
    assert not any(
        event["kind"] == "attempt_launch_authorized" for event in store.events(campaign_id)
    )


@pytest.mark.acceptance("A32")
def test_launch_authority_consumed_and_recovered_by_same_never_launched_identity(store):
    campaign_id, approval_id = authorize(store)
    task = store.add_task(campaign_id, {"id": "launch-authorization-window"})
    attempt = reserve(store, task, approval_id)
    authorized = store.authorize_launch(attempt["id"], principal="fixture-controller")
    assert not authorized["launch_permitted"]
    with pytest.raises(StateError):
        store.authorize_launch(attempt["id"], principal="fixture-controller")
    recovered = store.reconcile(attempt["id"], inspect("absent", never_launched=True))
    assert recovered["launch_permitted"]
    store.authorize_launch(attempt["id"], principal="fixture-controller")
    assert len(store.attempts(campaign_id)) == 1


@pytest.mark.acceptance("A25")
def test_failed_abandoned_unfavorable_trials_and_choices_remain_in_ledger(store):
    campaign_id, approval_id = authorize(store)
    states = []
    for state in ("FAILED", "CANCELLED", "LOST", "SUCCEEDED"):
        task = store.add_task(campaign_id, {"id": state.lower()})
        attempt = reserve(store, task, approval_id)
        if state == "SUCCEEDED":
            succeeded(store, attempt)
        else:
            store.transition(
                attempt["id"],
                state,
                fence=attempt["fence"],
                detail={"reason": "recorded synthetic outcome"},
            )
            store.reconcile(attempt["id"], inspect("stopped"))
        states.append(state)
    store.record_selection(
        campaign_id,
        candidate_id="inferior-treatment",
        rationale="Predeclared candidate retained despite worse error",
        observation_ids=["negative-observation"],
    )
    assert [attempt["state"] for attempt in store.attempts(campaign_id)] == states
    assert store.budget_used(campaign_id)["attempts"] == 4
    assert any(event["kind"] == "candidate_selected" for event in store.events(campaign_id))
    with pytest.raises(sqlite3.IntegrityError, match="permanent"):
        store._db.execute("DELETE FROM attempts")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._db.execute("DELETE FROM events")


@pytest.mark.acceptance("A26")
def test_original_contract_is_immutable_and_frozen_changes_require_new_approval(store):
    original = contract()
    campaign_id, approval_id = authorize(store, original)
    original["metric"] = "builder-invented-metric"
    before = store.campaign(campaign_id)
    store.freeze(campaign_id)
    store.start_confirmation(campaign_id)
    assert store.campaign(campaign_id)["contract"]["metric"] == "mean_squared_error"
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store._db.execute(
            "UPDATE campaigns SET contract=? WHERE id=?", (json.dumps(original), campaign_id)
        )
    with pytest.raises(StateError, match="frozen"):
        store.record_selection(
            campaign_id, candidate_id="new-choice", rationale="After peeking", observation_ids=[]
        )
    amendment = store.amend(campaign_id, original)
    assert store.campaign(amendment)["digest"] != before["digest"]
    assert store.campaign(amendment)["parent_id"] == campaign_id
    task = store.add_task(amendment, {"id": "amended-work"})
    with pytest.raises(AuthorityError):
        reserve(store, task, approval_id)
    assert store.campaign(campaign_id)["digest"] == before["digest"]


@pytest.mark.acceptance("A23")
def test_negative_finding_completes_without_retry_until_win(store):
    campaign_id, approval_id = authorize(store)
    task = store.add_task(campaign_id, {"id": "inferior-treatment"})
    attempt = succeeded(store, reserve(store, task, approval_id))
    store.freeze(campaign_id)
    store.start_confirmation(campaign_id)
    store.complete(
        campaign_id,
        execution_status="succeeded",
        protocol_status="valid",
        finding="not_supported",
        evidence_ids=[attempt["result"]["observation_id"]],
    )
    outcome = store.campaign(campaign_id)["outcome"]
    assert outcome["execution_status"] == "succeeded"
    assert outcome["protocol_status"] == "valid"
    assert outcome["finding"] == "NOT_SUPPORTED"
    assert store.ready_tasks(campaign_id) == []
    assert len(store.attempts(campaign_id)) == 1
    with pytest.raises(StateError):
        store.add_task(campaign_id, {"id": "try-until-win"})


@pytest.mark.acceptance("A27")
def test_failed_execution_cannot_be_relabelled_success_by_attaching_a_report(store):
    campaign_id, approval_id = authorize(store)
    task = store.add_task(campaign_id, {"id": "execution-failed"})
    attempt = reserve(store, task, approval_id)
    store.transition(attempt["id"], "FAILED", fence=attempt["fence"])
    store.reconcile(attempt["id"], inspect("stopped"))
    store.freeze(campaign_id)
    # Shape-valid but unregistered, so the deeper state checks are what refuse it.
    invented = "c" * 64
    with pytest.raises(StateError, match="all declared tasks"):
        store.complete(
            campaign_id,
            execution_status="succeeded",
            protocol_status="valid",
            finding="NOT_SUPPORTED",
            evidence_ids=[invented],
        )
    with pytest.raises(StateError, match="cannot support"):
        store.complete(
            campaign_id,
            execution_status="failed",
            protocol_status="invalid",
            finding="SUPPORTED_IN_SCOPE",
            evidence_ids=[invented],
        )
    assert store.campaign(campaign_id)["state"] == "FROZEN"


@pytest.mark.acceptance("A27")
def test_unsuccessful_execution_cannot_certify_a_scientific_finding(store):
    """Independent audit regression: a substantive finding needs succeeded work,
    real evidence identities, and at least one verified observation."""
    campaign_id, approval_id = authorize(store)
    task = store.add_task(campaign_id, {"id": "abandoned"})
    attempt = reserve(store, task, approval_id)
    store.transition(attempt["id"], "LOST", fence=attempt["fence"])
    store.reconcile(attempt["id"], inspect("stopped"))
    store.freeze(campaign_id)
    digest = "d" * 64
    for execution_status in ("failed", "lost", "cancelled"):
        for finding in ("SUPPORTED_IN_SCOPE", "NOT_SUPPORTED"):
            with pytest.raises(StateError, match="Unsuccessful execution cannot"):
                store.complete(
                    campaign_id,
                    execution_status=execution_status,
                    protocol_status="valid",
                    finding=finding,
                    evidence_ids=[digest],
                )
    # Evidence references must be content addresses, not free text.
    for unshaped in ("invented-report", "", "C" * 64, "d" * 63, "g" * 64, digest + "0"):
        with pytest.raises(StateError, match="registered evidence references"):
            store.complete(
                campaign_id,
                execution_status="lost",
                protocol_status="incomplete",
                finding="INCONCLUSIVE",
                evidence_ids=[unshaped],
            )
    # A valid protocol needs a verified observation, not merely a stopped ledger.
    with pytest.raises(StateError, match="at least one verified succeeded attempt"):
        store.complete(
            campaign_id,
            execution_status="lost",
            protocol_status="valid",
            finding="INCONCLUSIVE",
            evidence_ids=[digest],
        )
    assert store.campaign(campaign_id)["state"] == "FROZEN"
    assert store.campaign(campaign_id)["outcome"] is None
    # The honest outcome for abandoned work remains available.
    store.complete(
        campaign_id,
        execution_status="lost",
        protocol_status="incomplete",
        finding="INCONCLUSIVE",
        evidence_ids=[digest],
    )
    assert store.campaign(campaign_id)["outcome"]["finding"] == "INCONCLUSIVE"


@pytest.mark.acceptance("A27")
def test_empty_campaign_cannot_declare_a_valid_protocol(store):
    """Independent audit regression: zero attempts is not a valid protocol."""
    campaign_id, _ = authorize(store)
    store.freeze(campaign_id)
    with pytest.raises(StateError, match="Unsuccessful execution cannot"):
        store.complete(
            campaign_id,
            execution_status="failed",
            protocol_status="valid",
            finding="SUPPORTED_IN_SCOPE",
            evidence_ids=["e" * 64],
        )
    with pytest.raises(StateError, match="at least one verified succeeded attempt"):
        store.complete(
            campaign_id,
            execution_status="failed",
            protocol_status="valid",
            finding="INCONCLUSIVE",
            evidence_ids=["e" * 64],
        )
    assert store.campaign(campaign_id)["outcome"] is None


class DurableTestDriver:
    """An explicit model driver, not evidence of process/container isolation."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(exist_ok=True)

    def launch(self, label: str) -> None:
        try:
            with (self.root / label).open("x") as marker:
                marker.write("running")
        except FileExistsError:
            pass

    def inspect(self, label: str) -> dict:
        path = self.root / label
        if not path.exists():
            return {"backend_label": label, "status": "absent", "never_launched": True}
        return {"backend_label": label, "status": path.read_text()}


@pytest.mark.acceptance("A32")
@pytest.mark.parametrize("crash_after_dispatch", [False, True])
def test_restart_reconciles_stable_identity_before_launch(store, tmp_path, crash_after_dispatch):
    campaign_id, approval_id = authorize(store)
    task = store.add_task(campaign_id, {"id": "crash-window"})
    attempt = reserve(store, task, approval_id)
    driver = DurableTestDriver(tmp_path / "backend-registry")
    if crash_after_dispatch:
        driver.launch(attempt["backend_label"])
    # A different connection has no in-memory launch/completion knowledge.
    with Controller(store.path) as recovered:
        reconciled = recovered.reconcile(attempt["id"], driver.inspect)
        assert reconciled["id"] == attempt["id"]
        assert reconciled["launch_permitted"] is not crash_after_dispatch
        if reconciled["launch_permitted"]:
            driver.launch(reconciled["backend_label"])
        # The driver is idempotent under repeated submission of the same label.
        driver.launch(reconciled["backend_label"])
        assert len(list(driver.root.iterdir())) == 1
        assert len(recovered.attempts(campaign_id)) == 1
        assert recovered.budget_used(campaign_id)["attempts"] == 1
        with pytest.raises(StateError):
            recovered.transition(attempt["id"], "RUNNING", fence=attempt["fence"])


@pytest.mark.acceptance("A32")
def test_missing_backend_without_proof_never_launched_does_not_repeat_same_attempt(store):
    campaign_id, approval_id = authorize(store)
    task = store.add_task(campaign_id, {"id": "uncertain-exit", "max_attempts": 2})
    attempt = reserve(store, task, approval_id)
    attempt = store.transition(attempt["id"], "RUNNING", fence=attempt["fence"])
    lost = store.reconcile(attempt["id"], inspect("absent"))
    assert lost["state"] == "LOST"
    assert lost["stopped_confirmed"]
    assert not lost["launch_permitted"]
    retry = reserve(store, task, approval_id)
    assert retry["id"] != lost["id"]
    assert store.budget_used(campaign_id)["attempts"] == 2


@pytest.mark.acceptance("A32")
def test_inconsistent_never_launched_claim_cannot_replay_observed_computation(store):
    campaign_id, approval_id = authorize(store)
    task = store.add_task(campaign_id, {"id": "observed-live-job"})
    attempt = reserve(store, task, approval_id)
    store.reconcile(attempt["id"], inspect("running"))
    with pytest.raises(StateError, match="previously observed"):
        store.reconcile(attempt["id"], inspect("absent", never_launched=True))
    assert not store.attempt(attempt["id"])["launch_permitted"]
    assert store.resources_used()["cpu"] == 1


@pytest.mark.acceptance("A33")
def test_expired_lease_and_terminal_status_keep_occupied_resources_until_driver_confirms(store):
    campaign_id, approval_id = authorize(store)
    task = store.add_task(campaign_id, {"id": "old-live-job"})
    other_task = store.add_task(campaign_id, {"id": "conflicting"})
    attempt = reserve(store, task, approval_id)
    store.recover_expired(now=attempt["lease_until"] + 1)
    assert store.resources_used()["cpu"] == 1
    assert store.attempt(attempt["id"])["state"] == "RECOVERING"
    with pytest.raises(StateError, match="fencing"):
        store.heartbeat(attempt["id"], fence=attempt["fence"])
    with pytest.raises(BudgetError, match="occupied"):
        reserve(store, other_task, approval_id)
    uncertain = store.reconcile(attempt["id"], inspect("unknown"))
    assert uncertain["resources_held"] and not uncertain["launch_permitted"]
    running = store.reconcile(attempt["id"], inspect("running"))
    terminal = store.transition(attempt["id"], "CANCELLED", fence=running["fence"])
    assert terminal["resources_held"]
    with pytest.raises(BudgetError):
        reserve(store, other_task, approval_id)
    stopped = store.reconcile(attempt["id"], inspect("stopped"))
    assert not stopped["resources_held"]
    assert stopped["state"] == "CANCELLED"
    assert reserve(store, other_task, approval_id)["resources_held"]


@pytest.mark.acceptance("A33")
def test_expiry_rejects_old_completion_even_before_recovery_scan(store, monkeypatch):
    campaign_id, approval_id = authorize(store)
    task = store.add_task(campaign_id, {"id": "expired-before-scan"})
    attempt = reserve(store, task, approval_id)
    with monkeypatch.context() as patch:
        patch.setattr("kestrel.controller.time.time", lambda: attempt["lease_until"] + 1)
        with pytest.raises(StateError, match="Expired lease"):
            store.transition(attempt["id"], "FAILED", fence=attempt["fence"])
    assert store.attempt(attempt["id"])["state"] == "STARTING"
    assert store.resources_used()["cpu"] == 1


@pytest.mark.acceptance("A33")
def test_contradicted_stop_proof_marks_capacity_occupied_again(store):
    campaign_id, approval_id = authorize(store)
    task = store.add_task(campaign_id, {"id": "inconsistent-driver"})
    attempt = succeeded(store, reserve(store, task, approval_id))
    assert store.resources_used()["cpu"] == 0
    with pytest.raises(StateError, match="capacity retained"):
        store.reconcile(attempt["id"], inspect("running"))
    assert store.resources_used()["cpu"] == 1
    assert store.attempt(attempt["id"])["state"] == "SUCCEEDED"
    assert not store.attempt(attempt["id"])["stopped_confirmed"]
    other = store.add_task(campaign_id, {"id": "new-job"})
    with pytest.raises(BudgetError):
        reserve(store, other, approval_id)


@pytest.mark.acceptance("A34")
def test_terminal_history_and_result_references_cannot_be_rewritten(store):
    campaign_id, approval_id = authorize(store)
    task = store.add_task(campaign_id, {"id": "terminal"})
    attempt = succeeded(store, reserve(store, task, approval_id))
    for next_state in ("RUNNING", "FAILED", "SUCCEEDED"):
        with pytest.raises(StateError):
            store.transition(attempt["id"], next_state, fence=attempt["fence"])
    with pytest.raises(StateError, match="immutable"):
        store.record_result(attempt["id"], {"observation_id": "fabricated-replacement"})
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store._db.execute("UPDATE attempts SET state='FAILED' WHERE id=?", (attempt["id"],))
    assert store.results(campaign_id) == [
        {"attempt_id": attempt["id"], "record": attempt["result"]}
    ]


@pytest.mark.acceptance("A35")
def test_cache_reuse_is_not_independent_replication_and_randomization_units_cannot_repeat(store):
    campaign_id, approval_id = authorize(store)
    task = store.add_task(
        campaign_id,
        {
            "id": "replicate-one",
            "independent_replicate": True,
            "randomization_identity": "synthetic-unit/seed-1",
        },
    )
    attempt = succeeded(store, reserve(store, task, approval_id))
    store.cache_result("recipe-digest", "artifact-digest", attempt["id"])
    for _ in range(2):
        reused = store.reuse_cached(campaign_id, "recipe-digest")
        assert not reused["independent_replicate"]
        assert reused["assurance"] == "reused"
    assert store.independent_replicates(campaign_id) == 1
    task = store.add_task(
        campaign_id,
        {
            "id": "same-seed-new-directory",
            "independent_replicate": True,
            "randomization_identity": "synthetic-unit/seed-1",
        },
    )
    with pytest.raises(ControllerError, match="Repeated randomization"):
        reserve(store, task, approval_id)
    task = store.add_task(
        campaign_id,
        {
            "id": "replicate-two",
            "independent_replicate": True,
            "randomization_identity": "synthetic-unit/seed-2",
        },
    )
    succeeded(store, reserve(store, task, approval_id))
    assert store.independent_replicates(campaign_id) == 2
    assert len(store.attempts(campaign_id)) == 2


@pytest.mark.acceptance("A37")
def test_backup_restore_preserves_interrupted_jobs_history_approvals_and_budget(store, tmp_path):
    campaign_id, approval_id = authorize(store)
    task = store.add_task(campaign_id, {"id": "backup-in-flight"})
    attempt = reserve(store, task, approval_id)
    attempt = store.transition(attempt["id"], "RUNNING", fence=attempt["fence"])
    before = store.events(campaign_id)
    backup_path = tmp_path / "backups" / "controller.sqlite"
    checksum = store.backup(backup_path)
    assert len(checksum) == 64
    with Controller.restore(backup_path, tmp_path / "restored" / "controller.sqlite") as restored:
        assert restored.attempt(attempt["id"])["backend_label"] == attempt["backend_label"]
        assert restored.events(campaign_id) == before
        assert restored.budget_used(campaign_id) == store.budget_used(campaign_id)
        assert restored.resources_used()["cpu"] == 1
        assert not restored.reconcile(attempt["id"], inspect("running"))["launch_permitted"]
    with pytest.raises(ControllerError, match="overwrite"):
        Controller.restore(backup_path, store.path)


@pytest.mark.acceptance("A38")
def test_blocked_branch_does_not_prevent_independent_authorized_progress(store):
    campaign_id, approval_id = authorize(store)
    blocked = store.add_task(campaign_id, {"id": "missing-input"})
    store.block_task(blocked, "Synthetic input not available")
    store.add_task(campaign_id, {"id": "dependent", "dependencies": [blocked]})
    independent = store.add_task(campaign_id, {"id": "independent"})
    assert [task["id"] for task in store.ready_tasks(campaign_id)] == [independent]
    assert succeeded(store, reserve(store, independent, approval_id))["state"] == "SUCCEEDED"
    assert store.task(blocked)["state"] == "BLOCKED"
    store.cancel_task(blocked, "Input acquisition remains unauthorized")
    assert store.task(blocked)["state"] == "CANCELLED"


@pytest.mark.acceptance("A44")
def test_worker_source_edits_do_not_grant_self_deployment_or_authority(store):
    campaign_id, _ = authorize(store)
    for operation in ("deploy_controller", "upgrade_controller", "edit_policy", "grant_authority"):
        with pytest.raises(AuthorityError):
            store.add_task(campaign_id, {"id": operation, "operation": operation})
    with pytest.raises(AuthorityError):
        store.request_upgrade(principal="worker", package_digest="worker-edited-source-digest")
    assert store.events()[-1]["kind"] == "controller_upgrade_denied"
    for capability in ("network", "live_provider", "private_data", "cloud"):
        with pytest.raises(AuthorityError):
            store.approve(
                campaign_id,
                token=TOKEN,
                contract_digest=store.campaign(campaign_id)["digest"],
                expires_at=time.time() + 60,
                capabilities=[capability],
            )


@pytest.mark.acceptance("A45")
def test_restricted_cross_project_memory_denied_and_decision_recorded(store):
    restricted = store.record_memory(
        "synthetic-a", {"kind": "claim", "text": "restricted synthetic sentinel"}
    )
    store.grant_project_access(
        token=TOKEN, principal="reader-b", project_id="synthetic-b", classifications=["restricted"]
    )
    with pytest.raises(AuthorityError, match="source permission"):
        store.retrieve_memory(restricted, principal="reader-b", requesting_project="synthetic-b")
    denial = store.events()[-1]
    assert denial["kind"] == "memory_retrieval_denied"
    assert "sentinel" not in json.dumps(denial)
    with pytest.raises(AuthorityError):
        store.grant_project_access(
            token="worker-token",
            principal="reader-b",
            project_id="synthetic-a",
            classifications=["restricted"],
        )
    store.grant_project_access(
        token=TOKEN, principal="reader-a", project_id="synthetic-a", classifications=["restricted"]
    )
    assert store.retrieve_memory(
        restricted, principal="reader-a", requesting_project="synthetic-a"
    )["text"].endswith("sentinel")
    public = store.record_memory(
        "synthetic-a",
        {"kind": "methodology", "text": "Check independent analytic oracles."},
        classification="public",
        shareable=True,
        deidentified=True,
        token=TOKEN,
    )
    assert (
        store.retrieve_memory(public, principal="reader-b", requesting_project="synthetic-b")[
            "kind"
        ]
        == "methodology"
    )
    with pytest.raises(AuthorityError):
        store.record_memory(
            "synthetic-a",
            {"kind": "raw_transcript"},
            classification="public",
            shareable=True,
            deidentified=True,
            token=TOKEN,
        )


@pytest.mark.acceptance("A45")
def test_source_grant_does_not_follow_a_principal_into_another_project(store):
    """Independent audit regression: a grant on the record's own project does not
    authorize pulling it into an unrelated project's context."""
    restricted = store.record_memory(
        "synthetic-a", {"kind": "claim", "text": "restricted synthetic sentinel"}
    )
    unshared_public = store.record_memory(
        "synthetic-a", {"kind": "claim", "text": "public but unshared sentinel"},
        classification="public",
    )
    store.grant_project_access(
        token=TOKEN,
        principal="reader-a",
        project_id="synthetic-a",
        classifications=["restricted", "public"],
    )
    # Same project: the grant applies.
    assert store.retrieve_memory(
        restricted, principal="reader-a", requesting_project="synthetic-a"
    )["text"].endswith("sentinel")
    # Another project: the same grant no longer applies to restricted material.
    with pytest.raises(AuthorityError, match="source permission or approved public"):
        store.retrieve_memory(
            restricted, principal="reader-a", requesting_project="synthetic-b"
        )
    denial = store.events()[-1]
    assert denial["kind"] == "memory_retrieval_denied"
    assert denial["detail"]["cross_project"] is True
    assert denial["detail"]["requesting_project"] == "synthetic-b"
    assert "approved public sharing" in denial["detail"]["reason"]
    assert "sentinel" not in json.dumps(denial)
    # Public alone is not a cross-project channel; it must be approved shareable.
    with pytest.raises(AuthorityError):
        store.retrieve_memory(
            unshared_public, principal="reader-a", requesting_project="synthetic-b"
        )
    # An absent requesting project is not a way to skip the check.
    for missing in (None, "", 0):
        with pytest.raises(ControllerError, match="explicit requesting project"):
            store.retrieve_memory(
                restricted, principal="reader-a", requesting_project=missing
            )
    shared = store.record_memory(
        "synthetic-a",
        {"kind": "methodology", "text": "Check independent analytic oracles."},
        classification="public",
        shareable=True,
        deidentified=True,
        token=TOKEN,
    )
    allowed = store.retrieve_memory(
        shared, principal="reader-a", requesting_project="synthetic-b"
    )
    assert allowed["kind"] == "methodology"
    decision = store.events()[-1]
    assert decision["kind"] == "memory_retrieval_allowed"
    assert decision["detail"]["cross_project"] is True
    assert decision["detail"]["reason"] == "approved public de-identified sharing"


class AttemptReferenceMachine(RuleBasedStateMachine):
    """Independent transition oracle plus resource/fencing invariants."""

    def __init__(self):
        super().__init__()
        self.temporary = tempfile.TemporaryDirectory(prefix="kestrel-controller-model-")
        self.store = Controller(
            Path(self.temporary.name) / "state.sqlite", resource_capacity={"cpu": 1}
        )
        self.store.initialize_operator(TOKEN)
        self.campaign_id, approval_id = authorize(self.store)
        task = self.store.add_task(self.campaign_id, {"id": "model-task"})
        attempt = reserve(self.store, task, approval_id)
        self.attempt_id = attempt["id"]
        self.state = "STARTING"
        self.held = True
        self.stopped = False
        self.fence = 1

    @rule(
        target_state=st.sampled_from(
            [
                "STARTING",
                "RUNNING",
                "VERIFYING",
                "RECOVERING",
                "SUCCEEDED",
                "FAILED",
                "CANCELLED",
                "LOST",
            ]
        )
    )
    def move(self, target_state):
        allowed_pairs = {
            ("STARTING", "RUNNING"),
            ("STARTING", "RECOVERING"),
            ("STARTING", "FAILED"),
            ("STARTING", "CANCELLED"),
            ("STARTING", "LOST"),
            ("RUNNING", "VERIFYING"),
            ("RUNNING", "RECOVERING"),
            ("RUNNING", "FAILED"),
            ("RUNNING", "CANCELLED"),
            ("RUNNING", "LOST"),
            ("RECOVERING", "STARTING"),
            ("RECOVERING", "RUNNING"),
            ("RECOVERING", "VERIFYING"),
            ("RECOVERING", "FAILED"),
            ("RECOVERING", "CANCELLED"),
            ("RECOVERING", "LOST"),
            ("VERIFYING", "SUCCEEDED"),
            ("VERIFYING", "FAILED"),
            ("VERIFYING", "CANCELLED"),
            ("VERIFYING", "RECOVERING"),
        }
        allowed = (self.state, target_state) in allowed_pairs
        if target_state in {"VERIFYING", "SUCCEEDED"} and not self.stopped:
            allowed = False
        if target_state == "RUNNING" and self.stopped:
            allowed = False
        if allowed:
            self.store.transition(self.attempt_id, target_state, fence=self.fence)
            self.state = target_state
        else:
            with pytest.raises(StateError):
                self.store.transition(self.attempt_id, target_state, fence=self.fence)

    @rule(status=st.sampled_from(["stopped", "running", "unknown"]))
    def reconcile(self, status):
        if status == "running" and self.stopped:
            with pytest.raises(StateError):
                self.store.reconcile(self.attempt_id, inspect(status))
            self.stopped = False
            self.held = True
            self.fence += 1
            if self.state not in {"SUCCEEDED", "FAILED", "CANCELLED", "LOST"}:
                self.state = "RECOVERING"
            return
        self.store.reconcile(self.attempt_id, inspect(status))
        terminal = self.state in {"SUCCEEDED", "FAILED", "CANCELLED", "LOST"}
        if status == "stopped":
            self.held = False
            self.stopped = True
            if self.state == "STARTING":
                self.state = "RECOVERING"
        elif status == "running":
            self.held = True
            if not terminal:
                self.state = "RUNNING"
        elif not terminal:
            self.state = "RECOVERING"
        self.fence += 1

    @rule()
    def expire(self):
        self.store.recover_expired(now=self.store.attempt(self.attempt_id)["lease_until"] + 1)
        if self.state not in {"SUCCEEDED", "FAILED", "CANCELLED", "LOST", "RECOVERING"}:
            self.state = "RECOVERING"
            self.fence += 1

    @rule()
    def stale_heartbeat(self):
        with pytest.raises(StateError):
            self.store.heartbeat(self.attempt_id, fence=self.fence - 1)

    @invariant()
    def persisted_state_matches_independent_model(self):
        attempt = self.store.attempt(self.attempt_id)
        assert attempt["state"] == self.state
        assert attempt["fence"] == self.fence
        assert attempt["resources_held"] == self.held
        assert attempt["stopped_confirmed"] == self.stopped
        assert self.store.resources_used()["cpu"] == int(self.held)
        assert self.store.budget_used(self.campaign_id)["attempts"] == 1
        assert len(self.store.attempts(self.campaign_id)) == 1

    def teardown(self):
        self.store.close()
        self.temporary.cleanup()


TestAttemptStateMachine = AttemptReferenceMachine.TestCase
TestAttemptStateMachine.settings = settings(max_examples=50, stateful_step_count=50, deadline=None)
TestAttemptStateMachine = pytest.mark.acceptance("A34")(TestAttemptStateMachine)
