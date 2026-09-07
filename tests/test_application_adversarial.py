"""Actual fixture jobs across application authority and crash boundaries.

All repositories, databases, and malicious artifacts are synthetic and generated
outside the framework checkout. These are development workflow tests, not an
assertion that a local process isolates adversarial candidate code.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from kestrel.application import Lab, framework_root
from kestrel.artifacts import ArtifactError
from kestrel.contracts import canonical
from kestrel.controller import AuthorityError
from kestrel.fixtures import generate


class SimulatedControllerCrash(BaseException):
    """Bypass ordinary workflow error handling after a durable commit."""


def file_identities(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def worker_attempts(attempts: list[dict]) -> list[dict]:
    return [attempt for attempt in attempts if attempt["backend"] == "development"]


def worker_results(results: list[dict]) -> list[dict]:
    return [result for result in results if "observation" in result["record"]]


@pytest.fixture
def prepared(tmp_path):
    manifests = generate(tmp_path / "external-projects", framework_root())
    original = tmp_path / "external-projects" / "numerical"
    original_identities = file_identities(original)
    lab = Lab.initialize(tmp_path / "external-lab")
    record = lab.register(manifests[0], snapshot_dirty=True)
    campaign = lab.propose(record["project_id"], "Preserve this original synthetic brief exactly.")
    approval = lab.approve(
        campaign,
        lab.controller.campaign(campaign)["digest"],
        (lab.root / "operator.token").read_text(),
    )
    root = lab.root
    lab.close()
    yield root, campaign, approval, original, original_identities
    # Tests may deliberately leave controller rows interrupted. The real
    # trusted fixture supervisor enforces its own bounded deadline; also wait
    # for every actual dispatch before temporary-directory cleanup.
    with Lab(root) as cleanup:
        for attempt in worker_attempts(cleanup.controller.attempts(campaign)):
            status = cleanup.driver.inspect(attempt["backend_label"])
            if status.state != "absent" and not status.stopped:
                status = cleanup.driver.wait(attempt["backend_label"], timeout=7)
            assert status.stopped, "Synthetic worker remained unconfirmed after its deadline"


@pytest.mark.acceptance("A03")
@pytest.mark.acceptance("A07")
@pytest.mark.acceptance("A22")
@pytest.mark.acceptance("A23")
def test_actual_offline_campaign_retains_negative_result_and_original_project(prepared):
    root, campaign, approval, original, before = prepared
    with Lab(root) as lab:
        report = lab.run(campaign, approval)
        assert report["state"] == "COMPLETE"
        assert report["execution"] == "succeeded"
        assert report["validity"] == "valid"
        assert report["finding"] == "not_supported"
        assert report["brief"]["original"] == "Preserve this original synthetic brief exactly."
        analysis = report["evidence"][0]["analysis"]
        # Targets are 2*x+1, baseline predicts exactly, treatment is +2 off.
        assert analysis["baseline"] == 0
        assert analysis["treatment"] == 4
        assert analysis["difference"] == 4
        for result in worker_results(lab.controller.results(campaign)):
            raw = json.loads(lab.store.read(result["record"]["raw"]))
            observation = json.loads(lab.store.read(result["record"]["observation"]))
            assert raw["self_reported_metric"] == -999
            assert observation["self_reported_metric_rejected"] is True
            assert observation["metric"] in {0, 4}
        assert report["budget_reserved"]["attempts"] == 3
        assert report["budget_reserved"]["provider_calls"] == 0
        assert len(report["attempts"]) == 3
        assert len(worker_attempts(report["attempts"])) == 2
        assert file_identities(original) == before


@pytest.mark.acceptance("A19")
def test_run_with_wrong_approval_dispatches_nothing(prepared):
    root, campaign, _, original, before = prepared
    with Lab(root) as lab:
        with pytest.raises(AuthorityError):
            lab.run(campaign, "worker-invented-approval")
        assert lab.controller.attempts(campaign) == []
        assert list(lab.driver.state_dir.iterdir()) == []
        assert file_identities(original) == before


@pytest.mark.acceptance("A38")
def test_real_ready_fixture_runs_while_independent_branch_is_blocked(prepared):
    root, campaign, approval, original, before = prepared
    with Lab(root) as lab:
        first, second = [task for task in lab.controller.tasks(campaign)
                         if task["spec"]["operation"] == "execute"]
        frozen_digest = lab.controller.campaign(campaign)["digest"]
        lab.controller.block_task(first["id"], "Synthetic input awaits an external decision")
        pending = lab.run(campaign, approval)
        assert pending["state"] == "CONFIRMING"
        assert pending["execution"] == "pending"
        assert pending["validity"] == "incomplete"
        assert lab.controller.task(first["id"])["state"] == "BLOCKED"
        assert lab.controller.task(second["id"])["state"] == "SUCCEEDED"
        assert len(pending["attempts"]) == 2
        completed, = worker_attempts(pending["attempts"])
        assert completed["task_id"] == second["id"]
        assert completed["stopped_confirmed"] and not completed["resources_held"]
        assert worker_results(lab.controller.results(campaign))[0]["record"]["raw"]
        assert pending["budget_reserved"]["attempts"] == 2
        assert completed["approval_id"] == approval
        assert lab.controller.campaign(campaign)["digest"] == frozen_digest
        lab.controller.unblock_task(first["id"])
        report = lab.run(campaign, approval)
        assert report["state"] == "COMPLETE"
        assert report["finding"] == "not_supported"
        assert len(report["attempts"]) == 3
        assert lab.controller.attempt(completed["id"]) == completed
        assert report["budget_reserved"]["attempts"] == 3
        assert file_identities(original) == before


@pytest.mark.acceptance("A10")
@pytest.mark.parametrize(
    "corruption", ["major-version", "attempt-id", "artifact-missing", "malformed-json"]
)
def test_corrupted_adapter_response_cannot_publish_a_verified_observation(
    prepared, monkeypatch, corruption
):
    root, campaign, approval, _, _ = prepared
    with Lab(root) as lab:
        original_wait = lab.driver.wait

        def corrupted_wait(job_id, timeout=125):
            status = original_wait(job_id, timeout)
            assert status.stopped and status.returncode == 0
            response = json.loads(status.stdout)
            if corruption == "major-version":
                response["protocol_version"] = "999.0"
            elif corruption == "attempt-id":
                response["attempt_id"] = "different-worker-attempt"
            elif corruption == "artifact-missing":
                response["produced_artifacts"] = []
            return replace(
                status,
                stdout=b"{not-json" if corruption == "malformed-json" else canonical(response),
            )

        monkeypatch.setattr(lab.driver, "wait", corrupted_wait)
        report = lab.run(campaign, approval)
        assert report["execution"] == "failed"
        assert report["validity"] == "invalid"
        assert report["finding"] == "inconclusive"
        assert report["assurance"] == "unverified"
        assert len(report["attempts"]) == 3
        assert all(attempt["state"] == "FAILED" for attempt in worker_attempts(report["attempts"]))
        assert worker_results(lab.controller.results(campaign)) == []
        assert not any(lab.controller.resources_used().values())


@pytest.mark.acceptance("A10")
@pytest.mark.acceptance("A11")
@pytest.mark.parametrize("corruption", ["missing", "partial", "symlink"])
def test_complete_response_with_unsafe_or_missing_file_fails_atomic_publication(
    prepared, monkeypatch, corruption
):
    root, campaign, approval, _, _ = prepared
    with Lab(root) as lab:
        original_wait = lab.driver.wait

        def corrupt_file_after_real_exit(job_id, timeout=125):
            status = original_wait(job_id, timeout)
            assert status.stopped and status.returncode == 0
            dispatch = json.loads((lab.driver.state_dir / job_id / "dispatch.json").read_text())
            output = Path(dispatch["workspace"]) / "output"
            result = output / "result.json"
            if result.exists():
                if corruption == "partial":
                    result.rename(output / "result.json.partial")
                elif corruption == "symlink":
                    result.rename(output / "payload.json")
                    result.symlink_to("payload.json")
                else:
                    result.unlink()
            return status

        monkeypatch.setattr(lab.driver, "wait", corrupt_file_after_real_exit)
        report = lab.run(campaign, approval)
        assert report["state"] == "COMPLETE"
        assert report["validity"] == "invalid"
        assert report["assurance"] == "unverified"
        assert worker_results(lab.controller.results(campaign)) == []
        assert all(attempt["state"] == "FAILED" for attempt in worker_attempts(report["attempts"]))


@pytest.mark.acceptance("A32")
@pytest.mark.parametrize("crash_after_dispatch", [False, True])
def test_controller_crash_before_or_after_real_dispatch_reuses_stable_attempt(
    prepared, monkeypatch, crash_after_dispatch
):
    root, campaign, approval, original, before = prepared
    with Lab(root) as first:
        real_launch = first.driver.launch

        def crash_window(spec):
            if crash_after_dispatch:
                real_launch(spec)
            raise SimulatedControllerCrash()

        with monkeypatch.context() as patch:
            patch.setattr(first.driver, "launch", crash_window)
            with pytest.raises(SimulatedControllerCrash):
                first.run(campaign, approval)
        attempts_before = worker_attempts(first.controller.attempts(campaign))
        assert len(attempts_before) == 1
        assert attempts_before[0]["state"] == "STARTING"
        existing_label = attempts_before[0]["backend_label"]
        assert (first.driver.state_dir / existing_label).exists() is crash_after_dispatch
    with Lab(root) as restored:
        launched_labels = []
        real_launch = restored.driver.launch

        def record_launch(spec):
            launched_labels.append(spec.attempt_id)
            return real_launch(spec)

        monkeypatch.setattr(restored.driver, "launch", record_launch)
        report = restored.run(campaign, approval)
        assert report["finding"] == "not_supported"
        assert report["execution"] == "succeeded"
        assert len(report["attempts"]) == 3
        assert worker_attempts(report["attempts"])[0]["id"] == attempts_before[0]["id"]
        assert (existing_label in launched_labels) is not crash_after_dispatch
        assert len(list(restored.driver.state_dir.glob("*/dispatch.json"))) == 2
        assert report["budget_reserved"]["attempts"] == 3
        assert file_identities(original) == before


@pytest.mark.acceptance("A32")
@pytest.mark.acceptance("A34")
def test_crash_after_verification_result_commit_completes_without_new_execution(
    prepared, monkeypatch
):
    root, campaign, approval, _, _ = prepared
    with Lab(root) as first:
        original_record = first.controller.record_result

        def commit_then_crash(attempt_id, record):
            original_record(attempt_id, record)
            if "observation" in record:
                raise SimulatedControllerCrash()

        with monkeypatch.context() as patch:
            patch.setattr(first.controller, "record_result", commit_then_crash)
            with pytest.raises(SimulatedControllerCrash):
                first.run(campaign, approval)
        attempts = worker_attempts(first.controller.attempts(campaign))
        identifiers = [attempt["id"] for attempt in attempts]
        assert len(identifiers) == 2
        assert attempts[0]["state"] == "VERIFYING"
        saved_result = attempts[0]["result"]
        assert saved_result is not None
    with Lab(root) as restored:

        def forbidden_duplicate_launch(_):
            pytest.fail("Already dispatched attempts must reconcile, not launch again")

        monkeypatch.setattr(restored.driver, "launch", forbidden_duplicate_launch)
        report = restored.run(campaign, approval)
        assert report["state"] == "COMPLETE"
        assert report["execution"] == "succeeded"
        assert report["finding"] == "not_supported"
        assert [attempt["id"] for attempt in worker_attempts(report["attempts"])] == identifiers
        assert worker_attempts(report["attempts"])[0]["result"] == saved_result
        assert all(attempt["state"] == "SUCCEEDED" for attempt in report["attempts"])
        assert report["budget_reserved"]["attempts"] == 3


@pytest.mark.acceptance("A19")
@pytest.mark.acceptance("A32")
def test_revoked_approval_cannot_launch_a_reserved_attempt_after_restart(prepared, monkeypatch):
    root, campaign, approval, _, _ = prepared
    with Lab(root) as first:
        with monkeypatch.context() as patch:
            patch.setattr(
                first.driver, "launch", lambda _: (_ for _ in ()).throw(SimulatedControllerCrash())
            )
            with pytest.raises(SimulatedControllerCrash):
                first.run(campaign, approval)
        first.controller.revoke(approval, token=(root / "operator.token").read_text())
        original_id = worker_attempts(first.controller.attempts(campaign))[0]["id"]
    with Lab(root) as restored:

        def forbidden_launch(_):
            pytest.fail("A revoked grant cannot authorize even a previously reserved launch")

        monkeypatch.setattr(restored.driver, "launch", forbidden_launch)
        with pytest.raises(AuthorityError):
            restored.run(campaign, approval)
        assert worker_attempts(restored.controller.attempts(campaign))[0]["id"] == original_id
        assert list(restored.driver.state_dir.iterdir()) == []


@pytest.mark.acceptance("A36")
def test_invalidated_evaluator_changes_completed_report_validity_without_erasing_history(prepared):
    root, campaign, approval, _, _ = prepared
    with Lab(root) as lab:
        initial = lab.run(campaign, approval)
        assert initial["validity"] == "valid"
        attempts_before = lab.controller.attempts(campaign)
        evaluator = lab.controller.campaign(campaign)["contract"]["evaluator"]
        lab.store.invalidate(evaluator, "Synthetic audit discovered an invalid instrument")
        report = lab.report(campaign)
        assert report["state"] == "COMPLETE"
        assert report["execution"] == "succeeded"
        assert report["validity"] == "invalid"
        assert report["finding"] == "inconclusive"
        assert report["assurance"] == "unverified"
        assert lab.controller.attempts(campaign) == attempts_before


@pytest.mark.acceptance("A39")
@pytest.mark.acceptance("A40")
def test_report_rejects_tampered_computed_artifact_instead_of_certifying_invented_number(prepared):
    root, campaign, approval, _, _ = prepared
    with Lab(root) as lab:
        report = lab.run(campaign, approval)
        analysis_identity = report["evidence"][0]["digest"]
        artifact = Path(lab.store.get(analysis_identity)["path"])
        invented = report["evidence"][0]["analysis"]
        invented["treatment"] = -999999.0
        invented["finding"] = "supported_in_scope"
        artifact.chmod(0o600)
        artifact.write_bytes(canonical(invented))
        with pytest.raises(ArtifactError, match="hash mismatch"):
            lab.report(campaign)
        with pytest.raises(ArtifactError, match="hash mismatch"):
            lab.export(campaign, root / "tampered-export")
        assert lab.controller.campaign(campaign)["outcome"]["finding"] == "NOT_SUPPORTED"


@pytest.mark.acceptance("A27")
@pytest.mark.acceptance("A40")
def test_borrowed_evidence_from_another_campaign_cannot_certify_a_report(tmp_path):
    """Independent audit regression: valid, independently recomputed bytes are
    not this campaign's evidence unless they are attributable to it."""
    manifests = generate(tmp_path / "external-projects", framework_root())
    with Lab.initialize(tmp_path / "external-lab") as lab:
        token = (lab.root / "operator.token").read_text()
        first = lab.register(manifests[0], snapshot_dirty=True)
        campaign_a = lab.propose(first["project_id"], "Establish real attributable evidence.")
        approval = lab.approve(campaign_a, lab.controller.campaign(campaign_a)["digest"], token)
        report_a = lab.run(campaign_a, approval)
        borrowed = report_a["evidence"][0]["digest"]
        assert report_a["validity"] == "valid"
        assert report_a["evidence"][0]["attributable"] is True
        assert lab.store.get(borrowed)["assurance"] == "independently_recomputed"

        second = lab.register(manifests[1], snapshot_dirty=True)
        campaign_b = lab.propose(second["project_id"], "Perform no work and cite campaign A.")
        for task in lab.controller.tasks(campaign_b):
            lab.controller.cancel_task(task["id"], "Audit regression: no work performed")
        lab.controller.complete(
            campaign_b,
            execution_status="cancelled",
            protocol_status="incomplete",
            finding="INCONCLUSIVE",
            evidence_ids=[borrowed],
        )
        forged = lab.report(campaign_b)
        assert forged["state"] == "COMPLETE"
        assert forged["attempts"] == []
        assert forged["evidence"][0]["attributable"] is False
        assert forged["validity"] == "invalid"
        assert forged["finding"] == "inconclusive"
        assert forged["assurance"] == "unverified"
        # The cited analysis still names the campaign that actually produced it.
        assert json.loads(lab.store.read(borrowed))["campaign_id"] == campaign_a
        with pytest.raises(ValueError):
            lab.quantitative_claim(campaign_b, {
                "analysis": borrowed, "field": "difference",
                "value": json.loads(lab.store.read(borrowed))["difference"],
            })
        # Campaign A's own certified claim is unaffected.
        assert lab.report(campaign_a)["validity"] == "valid"
        assert lab.quantitative_claim(campaign_a, {
            "analysis": borrowed, "field": "difference",
            "value": json.loads(lab.store.read(borrowed))["difference"],
        })["assurance"] == "independently_recomputed"
