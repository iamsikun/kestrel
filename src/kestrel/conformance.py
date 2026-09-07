"""Explicit, authorized fixture conformance through the existing campaign path.

Boundary mutations are synthetic data supplied to trusted validators. Project
programs run only through the campaign's approved driver, never through imports
or a second unaccounted subprocess path.
"""

from __future__ import annotations

import copy
import hashlib
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from kestrel.artifacts import Artifacts, _relative
from kestrel.contracts import canonical, parse_json, validate_response
from kestrel.fixtures import is_trusted_workspace
from kestrel.projects import load_sidecar

if TYPE_CHECKING:
    from kestrel.application import Lab


def _response(payload: bytes, attempt_id: str, returncode: int = 0) -> None:
    response = validate_response(payload, attempt_id, returncode)
    for artifact in response.produced_artifacts:
        _relative(artifact.path)


def _rejection(name: str, action) -> dict:
    try:
        action()
    except (ValueError, OSError) as exc:
        return {"check": name, "status": "passed", "rejected_as": type(exc).__name__}
    return {"check": name, "status": "failed", "detail": "Invalid input was accepted"}


def _protocol_checks(payload: bytes, attempt_id: str) -> list[dict]:
    """Mutation probes use a real, previously validated fixture response."""
    _response(payload, attempt_id)
    original = parse_json(payload)
    checks = [{"check": "actual_response_schema", "status": "passed"}]
    changes = {
        "unknown_protocol": {"protocol_version": "999.0"},
        "wrong_attempt": {"attempt_id": "unrelated-conformance-attempt"},
        "missing_artifact": {"produced_artifacts": []},
        "unknown_critical_field": {"authorize_execution": True},
        "reported_failure": {"status": "failure"},
        "duplicate_artifact": {"produced_artifacts": original["produced_artifacts"] * 2},
    }
    for name, change in changes.items():
        altered = canonical({**original, **change})
        checks.append(_rejection(name, lambda data=altered: _response(data, attempt_id)))
    for name, data, code in (
        ("malformed_json", b"{invalid-json", 0),
        ("oversized_response", b" " * 65537, 0),
        ("nonzero_exit", payload, 1),
    ):
        checks.append(_rejection(name, lambda data=data, code=code: _response(data, attempt_id, code)))
    for path in ("../escape.json", "/escape.json", "a/../result.json", "a\\result.json",
                 "result.json.partial"):
        altered = copy.deepcopy(original)
        altered["produced_artifacts"][0]["path"] = path
        checks.append(_rejection(f"unsafe_path:{path}",
                                 lambda data=canonical(altered): _response(data, attempt_id)))
    return checks


def _ingestion_checks(runtime: Path) -> list[dict]:
    """Exercise filesystem validation with external synthetic scratch data."""
    with tempfile.TemporaryDirectory(prefix="conformance-", dir=runtime) as directory:
        scratch = Path(directory)
        output = scratch / "output"
        output.mkdir()
        (output / "result.json").write_bytes(b'{"synthetic_conformance":true}')
        (output / "alias.json").symlink_to("result.json")
        store = Artifacts(scratch / "store")
        try:
            def ingest(path="result.json", completed=True):
                return store.ingest(output, path, producer="conformance:synthetic-probe",
                                    completed=completed)

            valid = ingest()
            checks = [{"check": "completed_file_ingestion", "status": "passed",
                       "digest": valid["digest"]}]
            for name, path, completed in (
                ("unfinished_worker", "result.json", False),
                ("missing_output_file", "missing.json", True),
                ("symlink_output_file", "alias.json", True),
                ("traversing_output_file", "../outside.json", True),
            ):
                checks.append(_rejection(name, lambda path=path, completed=completed:
                                         ingest(path, completed)))
            (output / ".partial").touch()
            checks.append(_rejection("incomplete_output_marker", ingest))
            return checks
        finally:
            store.close()


def run_conformance(lab: Lab, manifest_path: Path, campaign_id: str, approval_id: str,
                    *, profile: str = "development") -> dict:
    """Run or inspect the exact approved fixture campaign, then test its boundary.

The campaign's existing budget and two candidate tasks bound the tiny operations.
Completed execution is explicitly reused, never counted as a fresh attempt.
"""
    manifest = load_sidecar(manifest_path)
    if profile != "development":
        raise PermissionError("Active isolated conformance requires an enabled isolated campaign application")
    campaign = lab.controller.campaign(campaign_id)
    contract = campaign["contract"]
    if contract["profile"] != "development":
        raise PermissionError("Requested campaign profile is unavailable; no development fallback")
    project = lab.projects.get(contract["project_id"])
    if manifest != project["manifest"]:
        raise ValueError("Conformance manifest does not match the campaign's registered sidecar")
    snapshot = Path(project["snapshot_path"])
    if not is_trusted_workspace(snapshot):
        raise PermissionError("Arbitrary project conformance requires an isolated profile; no subprocess fallback")
    expected_adapter = {
        "identity": hashlib.sha256((snapshot / "worker.py").read_bytes()).hexdigest(),
        "commands": {"execute": ["python", "-I", "-B", "worker.py"]},
    }
    if manifest["adapter"] != expected_adapter:
        raise PermissionError("Declared adapter is not the exact trusted fixture command; isolated profile required")
    if manifest["environment"] != {"kind": "development", "identity": "trusted-fixture-python"}:
        raise PermissionError("Declared environment is not the trusted fixture environment")
    before = {attempt["id"]: attempt for attempt in lab.controller.attempts(campaign_id)}
    report = lab.run(campaign_id, approval_id)
    if report["execution"] != "succeeded" or report["validity"] != "valid":
        raise ValueError("Tiny fixture execution did not produce a valid completed result")
    checks, receipts, worker_attempts = [], [], []
    for result in lab.controller.results(campaign_id):
        record = result["record"]
        if "observation" not in record:
            continue
        attempt = lab.controller.attempt(result["attempt_id"])
        receipt = parse_json(lab.store.read(record["execution"]))
        if (attempt["state"] != "SUCCEEDED" or not attempt["stopped_confirmed"]
                or not receipt["stopped"] or receipt["returncode"] != 0):
            raise ValueError("Conformance requires successful, stopped fixture execution")
        worker_attempts.append(attempt["id"])
        receipts.append(record["execution"])
        checks.extend({**check, "attempt_id": attempt["id"]} for check in
                      _protocol_checks(receipt["stdout"].encode(), attempt["id"]))
    if len(worker_attempts) != 2:
        raise ValueError("Fixture conformance requires the two predeclared candidate executions")
    checks.extend(_ingestion_checks(lab.root / "runtime"))
    if set(worker_attempts) - before.keys():
        execution_mode = "executed"
    elif any(before[identity]["state"] != "SUCCEEDED" for identity in worker_attempts):
        execution_mode = "reconciled_existing_execution"
    else:
        execution_mode = "verified_existing_execution"
    result = {
        "version": "0.1", "campaign_id": campaign_id,
        "contract_digest": campaign["digest"], "adapter_identity": manifest["adapter"]["identity"],
        "declared_command": manifest["adapter"]["commands"]["execute"],
        "static_sidecar_valid": True, "profile": profile, "adversarial_isolation": False,
        "active_probe": execution_mode,
        "worker_attempts": worker_attempts, "execution_receipts": receipts, "checks": checks,
        "status": "passed" if all(check["status"] == "passed" for check in checks) else "failed",
        "assurance": "traceable", "scientific_certification": False,
        "limitations": ["Exact public synthetic fixtures only", "Boundary probes use synthetic mutations",
                        "General adapter execution requires an isolated campaign application"],
    }
    artifact = lab.store.put_bytes(canonical(result), producer=f"conformance:{campaign_id}",
                                   media_type="application/json", lineage=receipts)
    if result["status"] != "passed":
        raise ValueError(f"Conformance boundary check failed; retained artifact {artifact['digest']}")
    return {**result, "artifact_digest": artifact["digest"]}
