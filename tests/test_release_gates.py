"""Parser tests use manufactured XML; they do not certify the runtime itself."""

import hashlib
import importlib.util
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "kestrel_release_gates_tool", Path(__file__).resolve().parents[1] / "tools" / "release_gates.py"
)
assert _SPEC and _SPEC.loader
release_gates = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release_gates)


def specification(tmp_path, rows):
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps({
        "spec_version": "0.1",
        "status": "requirements_not_implementation_results",
        "tests": [{"id": identifier, "milestone": "M0", "gate": gate,
                   "requirement": f"fixture parser requirement {identifier}",
                   "verification": "parser correctness only"} for identifier, gate in rows],
    }))
    return path


def junit(tmp_path, cases, *, run_id="run-1", scope="core", exit_code=0,
          completed="2026-09-06T12:00:00+00:00", revision="source-revision-1",
          profile="development", runtime="macos-process", missing=()):
    path = tmp_path / f"{run_id}.xml"
    suite = ET.Element("testsuite", name="pytest", tests=str(len(cases)))
    properties = ET.SubElement(suite, "properties")
    metadata = {
        "kestrel_run_id": run_id, "kestrel_scope": scope,
        "kestrel_command": "python -m pytest --disable-socket",
        "kestrel_exit_code": str(exit_code), "kestrel_source_revision": revision,
        "kestrel_run_completed_at": completed, "kestrel_platform": "macOS test host",
        "kestrel_runtime_platform": runtime, "kestrel_profile": profile,
    }
    for name, value in metadata.items():
        if name not in missing:
            ET.SubElement(properties, "property", name=name, value=value)
    for name, acceptance, status in cases:
        case = ET.SubElement(suite, "testcase", classname="tests.synthetic_parser_fixture", name=name, time="0.01")
        case_properties = ET.SubElement(case, "properties")
        ET.SubElement(case_properties, "property", name="acceptance", value=acceptance)
        if status == "failed":
            ET.SubElement(case, "failure", message="actual fixture parser failure").text = "assertion detail"
        elif status == "skipped":
            ET.SubElement(case, "skipped", message="runtime unavailable")
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
    return path


def report(spec, files, **kwargs):
    return release_gates.build_report(spec, files, "source-revision-1", **kwargs)


def statuses(value):
    return {row["id"]: row["status"] for row in value["acceptance"]}


@pytest.mark.acceptance("A42")
@pytest.mark.acceptance("A46")
def test_report_requires_actual_evidence_for_every_requirement(tmp_path):
    spec = specification(tmp_path, [("A01", "core"), ("A12", "isolation"),
                                    ("A30", "live_agent"), ("A43", "gpu")])
    results = junit(tmp_path, [("core_import", "A01", "passed"),
                               ("isolation", "A12", "skipped")])
    value = report(spec, [results])
    assert statuses(value) == {"A01": "passed", "A12": "blocked", "A30": "not_run", "A43": "not_run"}
    assert value["gates"]["core"]["checks_satisfied"] is True
    assert value["gates"]["isolation"]["checks_satisfied"] is False
    assert value["gates"]["live_agent"]["checks_satisfied"] is False
    assert value["gates"]["gpu"]["checks_satisfied"] is False
    assert value["readiness"] == {"offline_pilot": True, "isolated_local_measurements": False,
                                  "live_agent_measurements": False, "gpu_measurements": False}
    assert value["deployment"]["authorized"] is False


@pytest.mark.acceptance("A46")
def test_missing_core_coverage_cannot_be_reported_ready(tmp_path):
    spec = specification(tmp_path, [("A01", "core"), ("A28", "core")])
    results = junit(tmp_path, [("imports", "A01", "passed")])
    value = report(spec, [results], blockers={"A28": "raw Codex and Claude captures unavailable"})
    assert statuses(value)["A28"] == "blocked"
    assert value["readiness"]["offline_pilot"] is False
    assert value["gates"]["core"]["unsatisfied"] == ["A28"]


@pytest.mark.acceptance("A46")
@pytest.mark.parametrize("status", ["failed", "skipped"])
def test_latest_test_failure_or_skip_never_reuses_old_green(tmp_path, status):
    spec = specification(tmp_path, [("A01", "core")])
    old = junit(tmp_path, [("same_test", "A01", "passed")], run_id="old")
    new = junit(tmp_path, [("same_test", "A01", status)], run_id="new", completed="2026-09-06T13:00:00Z", exit_code=1 if status == "failed" else 0)
    value = report(spec, [new, old])
    assert statuses(value)["A01"] == ("failed" if status == "failed" else "blocked")
    assert value["readiness"]["offline_pilot"] is False
    assert len(value["executions"]) == 2
    assert [run["run_id"] for run in value["executions"] if run["selected"]] == ["new"]


@pytest.mark.acceptance("A46")
def test_latest_partial_scope_cannot_reuse_deselected_test(tmp_path):
    spec = specification(tmp_path, [("A01", "core"), ("A02", "core")])
    old = junit(tmp_path, [("first", "A01", "passed"), ("deselected-later", "A02", "passed")], run_id="old")
    new = junit(tmp_path, [("first", "A01", "passed")], run_id="new", completed="2026-09-06T13:00:00Z")
    value = report(spec, [old, new])
    assert statuses(value) == {"A01": "passed", "A02": "not_run"}
    assert value["readiness"]["offline_pilot"] is False


def test_superseded_failed_command_does_not_hide_current_success(tmp_path):
    spec = specification(tmp_path, [("A01", "core")])
    old = junit(tmp_path, [("first", "A01", "failed")], run_id="old", exit_code=1)
    new = junit(tmp_path, [("first", "A01", "passed")], run_id="new", completed="2026-09-06T13:00:00Z")
    value = report(spec, [old, new])
    assert value["readiness"]["offline_pilot"] is True
    assert value["executions"][0]["exit_code"] == 1
    assert value["executions"][0]["selected"] is False


@pytest.mark.acceptance("A42")
def test_linux_docker_measurements_are_separate_from_host_and_deployment(tmp_path):
    spec = specification(tmp_path, [("A01", "core"), ("A12", "isolation")])
    core = junit(tmp_path, [("core_test", "A01", "passed")], run_id="core")
    isolation = junit(tmp_path, [("forbidden_read_denied", "A12", "passed")], run_id="isolation", scope="isolation", profile="isolated-local", runtime="linux-docker-vm")
    value = report(spec, [core, isolation])
    assert value["gates"]["isolation"]["checks_satisfied"] is True
    assert value["readiness"]["isolated_local_measurements"] is True
    assert value["executions"][1]["host_platform"] == "macOS test host"
    assert value["executions"][1]["runtime_platform"] == "linux-docker-vm"
    assert value["deployment"]["authorized"] is False


@pytest.mark.acceptance("A42")
def test_development_test_cannot_establish_linux_isolation(tmp_path):
    spec = specification(tmp_path, [("A12", "isolation")])
    result = junit(tmp_path, [("process_mock", "A12", "passed")])
    value = report(spec, [result])
    assert statuses(value)["A12"] == "blocked"
    assert "Linux isolated-local" in value["acceptance"][0]["reason"]


@pytest.mark.acceptance("A42")
def test_failed_isolation_preserves_successful_core_evidence(tmp_path):
    spec = specification(tmp_path, [("A01", "core"), ("A12", "isolation")])
    core = junit(tmp_path, [("imports", "A01", "passed")], run_id="core")
    isolation = junit(tmp_path, [("forbidden_access", "A12", "failed")], run_id="isolation", scope="isolation", exit_code=1, profile="isolated-local", runtime="linux-docker-vm")
    value = report(spec, [core, isolation])
    assert value["gates"]["core"]["checks_satisfied"] is True
    assert value["gates"]["isolation"]["status"] == "failed"
    assert value["readiness"]["offline_pilot"] is True
    assert value["readiness"]["isolated_local_measurements"] is False


def test_resource_admission_checks_can_supplement_actual_linux_measurements(tmp_path):
    spec = specification(tmp_path, [("A15", "isolation")])
    core = junit(tmp_path, [("reject_unsupported_cap", "A15", "passed")], run_id="core")
    isolation = junit(tmp_path, [("actual_caps", "A15", "passed")], run_id="isolation", scope="isolation", profile="isolated-local", runtime="linux-docker-vm")
    value = report(spec, [core, isolation])
    assert statuses(value)["A15"] == "passed"
    assert len(value["acceptance"][0]["tests"]) == 2


@pytest.mark.parametrize("missing", release_gates.SUITE_PROPERTIES)
def test_missing_command_or_execution_provenance_fails_closed(tmp_path, missing):
    spec = specification(tmp_path, [("A01", "core")])
    result = junit(tmp_path, [("test", "A01", "passed")], missing=[missing])
    value = report(spec, [result])
    assert value["readiness"]["offline_pilot"] is False
    assert value["integrity_issues"]


def test_revision_mismatch_and_failed_command_are_not_passes(tmp_path):
    spec = specification(tmp_path, [("A01", "core")])
    result = junit(tmp_path, [("test", "A01", "passed")], revision="different-revision", exit_code=2)
    value = report(spec, [result])
    assert statuses(value)["A01"] == "blocked"
    assert any("source revision" in issue for issue in value["integrity_issues"])
    assert any("exit status 2" in issue for issue in value["integrity_issues"])


@pytest.mark.acceptance("A46")
def test_report_binds_input_hashes_and_preserves_actual_commands(tmp_path):
    spec = specification(tmp_path, [("A01", "core")])
    result = junit(tmp_path, [("test", "A01", "passed")])
    artifact = tmp_path / "fixture-wheel.whl"
    artifact.write_bytes(b"manufactured artifact for hashing parser test")
    value = report(spec, [result], artifact_paths=[artifact])
    assert len(value["inputs"]) == 3
    for row in value["inputs"]:
        assert row["sha256"] == hashlib.sha256(Path(row["path"]).read_bytes()).hexdigest()
        assert row["size_bytes"] == Path(row["path"]).stat().st_size
    assert value["executions"][0]["command"] == "python -m pytest --disable-socket"
    assert value["executions"][0]["exit_code"] == 0
    assert value["source_revision"] == "source-revision-1"


@pytest.mark.parametrize("attack", ["unknown-acceptance", "duplicate-test", "count", "zero-exit-failure", "bad-time"])
def test_inconsistent_junit_cannot_establish_readiness(tmp_path, attack):
    spec = specification(tmp_path, [("A01", "core")])
    cases = [("test", "A01", "passed")]
    if attack == "unknown-acceptance":
        cases.append(("unknown", "A99", "passed"))
    elif attack == "duplicate-test":
        cases.append(("test", "A01", "passed"))
    elif attack == "zero-exit-failure":
        cases = [("test", "A01", "failed")]
    result = junit(tmp_path, cases)
    if attack in ("count", "bad-time"):
        tree = ET.parse(result)
        if attack == "count":
            tree.getroot().set("tests", "9000")
        else:
            tree.find("./testcase").set("time", "nan")
        tree.write(result)
    value = report(spec, [result])
    assert value["readiness"]["offline_pilot"] is False
    assert value["integrity_issues"]


def test_entity_expansion_is_rejected(tmp_path):
    spec = specification(tmp_path, [("A01", "core")])
    result = tmp_path / "malicious.xml"
    result.write_text('<!DOCTYPE testsuite [<!ENTITY e "expanded">]><testsuite>&e;</testsuite>')
    with pytest.raises(release_gates.GateReportError, match="entity"):
        report(spec, [result])


def test_cli_writes_reviewable_blocked_report_with_nonzero_exit(tmp_path):
    spec = specification(tmp_path, [("A01", "core"), ("A28", "core")])
    result = junit(tmp_path, [("test", "A01", "passed")])
    output = tmp_path / "release.json"
    code = release_gates.main(["--spec", str(spec), "--junit", str(result),
                               "--output", str(output), "--source-revision", "source-revision-1",
                               "--blocker", "A28=raw provider conformance unavailable"])
    assert code == 1
    value = json.loads(output.read_bytes())
    assert value["readiness"]["offline_pilot"] is False
    assert statuses(value)["A28"] == "blocked"
