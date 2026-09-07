"""CLI conformance uses approved external fixtures and real execution evidence."""

import hashlib
import json
from pathlib import Path

import pytest

from kestrel.application import Lab, framework_root
from kestrel.cli import main
from kestrel.conformance import _protocol_checks, run_conformance
from kestrel.contracts import canonical, parse_json
from kestrel.fixtures import generate


@pytest.fixture
def prepared(tmp_path):
    manifests = generate(tmp_path / "external-projects", framework_root())
    with Lab.initialize(tmp_path / "external-lab") as lab:
        for manifest in manifests:
            lab.register(manifest, snapshot_dirty=True)
    return tmp_path / "external-lab", manifests


def authorize(lab, project):
    campaign = lab.propose(project, "Run the finite synthetic adapter conformance operation.")
    approval = lab.approve(campaign, lab.controller.campaign(campaign)["digest"],
                           (lab.root / "operator.token").read_text())
    return campaign, approval


def identities(path):
    return {str(file.relative_to(path)): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in path.rglob("*") if file.is_file()}


def test_static_conformance_never_dispatches(prepared, capsys):
    root, manifests = prepared
    assert main(["project", "conformance", "--manifest", str(manifests[0])]) == 0
    assert json.loads(capsys.readouterr().out)["active_probe"].startswith("not run")
    assert not list((root / "runtime" / "jobs").iterdir())


@pytest.mark.acceptance("A03")
@pytest.mark.acceptance("A10")
@pytest.mark.acceptance("A11")
@pytest.mark.parametrize("index", [0, 1])
def test_active_cli_conformance_uses_approved_real_fixture_operations(prepared, capsys, index):
    root, manifests = prepared
    manifest = manifests[index]
    sidecar = parse_json(manifest.read_bytes())
    original = Path(sidecar["source"]["locator"])
    before = identities(original)
    with Lab(root) as lab:
        campaign, approval = authorize(lab, sidecar["project_id"])
    command = ["--lab", str(root), "project", "conformance", "--manifest", str(manifest),
               "--campaign", campaign, "--approval", approval]
    assert main(command) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["active_probe"] == "executed"
    assert result["status"] == "passed"
    assert result["scientific_certification"] is False
    assert result["adversarial_isolation"] is False
    assert len(result["worker_attempts"]) == 2
    checks = {check["check"] for check in result["checks"]}
    assert {"unknown_protocol", "reported_failure", "wrong_attempt", "nonzero_exit",
            "symlink_output_file", "traversing_output_file", "incomplete_output_marker"} <= checks
    assert all(check["status"] == "passed" for check in result["checks"])
    with Lab(root) as lab:
        stored = parse_json(lab.store.read(result["artifact_digest"]))
        assert stored["worker_attempts"] == result["worker_attempts"]
        attempts_before = lab.controller.attempts(campaign)
        reused = run_conformance(lab, manifest, campaign, approval)
        assert reused["active_probe"] == "verified_existing_execution"
        assert lab.controller.attempts(campaign) == attempts_before
    assert identities(original) == before


@pytest.mark.acceptance("A19")
def test_active_conformance_requires_complete_authority_and_matching_manifest(prepared, capsys):
    root, manifests = prepared
    with Lab(root) as lab:
        campaign, approval = authorize(lab, "numerical")
        with pytest.raises(ValueError, match="registered sidecar"):
            run_conformance(lab, manifests[1], campaign, approval)
        with pytest.raises(ValueError, match="Approval"):
            run_conformance(lab, manifests[0], campaign, "worker-invented-approval")
        assert lab.controller.attempts(campaign) == []
    assert main(["project", "conformance", "--manifest", str(manifests[0]),
                 "--campaign", campaign]) == 2
    assert "requires --lab" in capsys.readouterr().err
    assert not list((root / "runtime" / "jobs").iterdir())


@pytest.mark.acceptance("A18")
def test_active_conformance_cannot_execute_arbitrary_code_or_downgrade_profile(prepared):
    root, manifests = prepared
    with Lab(root) as lab:
        campaign, approval = authorize(lab, "numerical")
        with pytest.raises(PermissionError, match="isolated campaign"):
            run_conformance(lab, manifests[0], campaign, approval, profile="isolated-local")
        snapshot = Path(lab.projects.get("numerical")["snapshot_path"])
        worker = snapshot / "worker.py"
        worker.chmod(0o600)
        worker.write_text("raise RuntimeError('untrusted program must never execute')")
        with pytest.raises(PermissionError, match="isolated profile"):
            run_conformance(lab, manifests[0], campaign, approval)
        assert lab.controller.attempts(campaign) == []
        assert not list(lab.driver.state_dir.iterdir())


@pytest.mark.acceptance("A18")
def test_declared_custom_command_is_not_silently_replaced_by_fixture_command(tmp_path):
    manifest = generate(tmp_path / "external-projects", framework_root())[0]
    sidecar = parse_json(manifest.read_bytes())
    sidecar["adapter"]["commands"]["execute"] = ["python", "-c", "raise RuntimeError('trap')"]
    manifest.write_bytes(canonical(sidecar))
    with Lab.initialize(tmp_path / "lab") as lab:
        lab.register(manifest, snapshot_dirty=True)
        campaign, approval = authorize(lab, "numerical")
        with pytest.raises(PermissionError, match="exact trusted fixture command"):
            run_conformance(lab, manifest, campaign, approval)
        assert lab.controller.attempts(campaign) == []
        assert not list(lab.driver.state_dir.iterdir())


def test_protocol_mutations_detect_a_validator_that_accepts_failed_execution(monkeypatch):
    from kestrel import conformance

    payload = canonical({"protocol_version": "0.1", "attempt_id": "fixture-attempt",
                         "status": "success", "diagnostics": {}, "produced_artifacts": [
                             {"path": "result.json", "media_type": "application/json", "complete": True}]})
    actual_validator = conformance.validate_response
    monkeypatch.setattr(conformance, "validate_response",
                        lambda data, attempt, code: actual_validator(data, attempt, 0))
    checks = _protocol_checks(payload, "fixture-attempt")
    failed = [check["check"] for check in checks if check["status"] == "failed"]
    assert failed == ["nonzero_exit"]
