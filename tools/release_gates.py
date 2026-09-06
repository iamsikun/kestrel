#!/usr/bin/env python3
"""Build acceptance evidence from actual JUnit results, never declarations of PASS.

Pytest's suite properties must identify the command, actual exit code, source
revision, scope, run ID, platform and UTC completion time. Testcase properties
named ``acceptance`` map a test to one or more comma-separated requirement IDs.
Each newest run replaces its entire scope; omitted tests cannot inherit a pass
from an older run. This report is evidence bookkeeping, not operator authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import tempfile
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

MAX_JUNIT_BYTES = 16 * 1024**2
MAX_CASES = 100_000
SUITE_PROPERTIES = (
    "kestrel_run_id",
    "kestrel_scope",
    "kestrel_command",
    "kestrel_exit_code",
    "kestrel_source_revision",
    "kestrel_run_completed_at",
    "kestrel_platform",
)


class GateReportError(ValueError):
    """Malformed or ambiguous evidence cannot establish a release gate."""


def _fingerprint(path: Path, kind: str) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise GateReportError(f"{kind} input must be a regular file: {path}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while data := stream.read(1024**2):
            digest.update(data)
            size += len(data)
    return {"path": str(path.resolve()), "sha256": digest.hexdigest(), "size_bytes": size, "kind": kind}


def _properties(node: ET.Element) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for prop in node.findall("./properties/property"):
        name = prop.get("name", "")
        if not name:
            raise GateReportError("JUnit property is missing its name")
        result.setdefault(name, []).append(prop.get("value", prop.text or ""))
    return result


def _single(properties: dict[str, list[str]], name: str, diagnostics: list[str]) -> str | None:
    values = properties.get(name, [])
    if len(values) != 1 or not values[0].strip():
        diagnostics.append(f"missing or duplicate suite provenance: {name}")
        return None
    return values[0]


def _completed(value: str | None, diagnostics: list[str]) -> datetime:
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone missing")
        return parsed.astimezone(UTC)
    except ValueError:
        diagnostics.append("completion timestamp must contain an explicit UTC offset")
        # Unknown chronology must never select an earlier successful run instead.
        return datetime.max.replace(tzinfo=UTC)


def _read_junit(path: Path, requirements: set[str], expected_revision: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fingerprint = _fingerprint(path, "junit")
    if fingerprint["size_bytes"] > MAX_JUNIT_BYTES:
        raise GateReportError("JUnit report exceeds the parser byte limit")
    raw = path.read_bytes()
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise GateReportError("JUnit DTDs and entity declarations are prohibited")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise GateReportError("invalid JUnit XML") from exc
    if root.tag not in ("testsuite", "testsuites"):
        raise GateReportError("unsupported JUnit root")
    suites = [root] if root.tag == "testsuite" else list(root.findall("./testsuite"))
    if not suites or len(suites) > 1024:
        raise GateReportError("JUnit report has no bounded suite inventory")
    runs = []
    count = 0
    for index, suite in enumerate(suites):
        if suite.findall("./testsuite"):
            raise GateReportError("nested JUnit suites are unsupported")
        diagnostics: list[str] = []
        properties = _properties(suite)
        metadata = {name: _single(properties, name, diagnostics) for name in SUITE_PROPERTIES}
        completed = _completed(metadata["kestrel_run_completed_at"], diagnostics)
        try:
            exit_code = int(metadata["kestrel_exit_code"] or "")
            if not 0 <= exit_code <= 255:
                raise ValueError("invalid exit code")
        except ValueError:
            exit_code = None
            diagnostics.append("command exit status is missing or invalid")
        if metadata["kestrel_source_revision"] != expected_revision:
            diagnostics.append("execution source revision does not match the requested source revision")
        scope = metadata["kestrel_scope"] or suite.get("name", "unknown")
        run_id = metadata["kestrel_run_id"] or f"unverified:{fingerprint['sha256']}:{index}"
        runtime = properties.get("kestrel_runtime_platform", [None])
        profile = properties.get("kestrel_profile", [None])
        cases = []
        case_ids = set()
        for case in suite.findall("./testcase"):
            count += 1
            if count > MAX_CASES:
                raise GateReportError("JUnit testcase inventory exceeds its count limit")
            case_properties = _properties(case)
            node_ids = case_properties.get("kestrel_nodeid", [])
            fallback = f"{case.get('classname', '')}::{case.get('name', '')}"
            node_id = node_ids[0] if len(node_ids) == 1 else fallback
            if node_id in case_ids:
                diagnostics.append(f"duplicate testcase identity: {node_id}")
            case_ids.add(node_id)
            acceptance = set()
            for value in case_properties.get("acceptance", []):
                for identifier in re.split(r"[,\s]+", value.strip()):
                    if identifier:
                        if identifier not in requirements:
                            diagnostics.append(f"unknown acceptance requirement: {identifier}")
                        acceptance.add(identifier)
            outcomes = [child for child in case if child.tag in ("failure", "error", "skipped")]
            status = "passed"
            if any(child.tag in ("failure", "error") for child in outcomes):
                status = "failed"
            elif any(child.tag == "skipped" for child in outcomes):
                status = "skipped"
            try:
                seconds = float(case.get("time", "0"))
                if not 0 <= seconds < float("inf"):
                    raise ValueError("invalid duration")
            except ValueError:
                seconds = None
                diagnostics.append(f"invalid testcase duration: {node_id}")
            cases.append({
                "nodeid": node_id,
                "status": status,
                "acceptance": sorted(acceptance),
                "duration_seconds": seconds,
                "diagnostics": [{"kind": child.tag, "message": child.get("message", ""), "detail": (child.text or "")[:8192]} for child in outcomes],
                "properties": case_properties,
                "run_id": run_id,
                "scope": scope,
                "junit_sha256": fingerprint["sha256"],
            })
        try:
            declared = int(suite.get("tests", ""))
            if declared != len(cases):
                diagnostics.append("JUnit suite declared test count differs from actual testcase inventory")
        except ValueError:
            diagnostics.append("JUnit suite is missing its actual test count")
        if exit_code == 0 and any(case["status"] == "failed" for case in cases):
            diagnostics.append("command exit zero conflicts with a failed testcase")
        if exit_code == 1 and not any(case["status"] == "failed" for case in cases):
            diagnostics.append("pytest exit one lacks corresponding failure evidence")
        runs.append({
            "run_id": run_id,
            "scope": scope,
            "suite_name": suite.get("name", ""),
            "command": metadata["kestrel_command"],
            "exit_code": exit_code,
            "source_revision": metadata["kestrel_source_revision"],
            "completed_at": metadata["kestrel_run_completed_at"],
            "host_platform": metadata["kestrel_platform"],
            "runtime_platform": runtime[0] if len(runtime) == 1 else None,
            "profile": profile[0] if len(profile) == 1 else None,
            "diagnostics": diagnostics,
            "junit_sha256": fingerprint["sha256"],
            "junit_path": fingerprint["path"],
            "tests": cases,
            "selected": False,
            "_completed": completed,
        })
    return fingerprint, runs


def build_report(spec_path: Path, junit_paths: Sequence[Path], source_revision: str,
                 artifact_paths: Sequence[Path] = (),
                 blockers: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Derive measured gate state from complete, revision-bound pytest executions."""
    if not isinstance(source_revision, str) or not source_revision.strip():
        raise GateReportError("an explicit source revision is required")
    if not junit_paths:
        raise GateReportError("at least one actual JUnit report is required")
    spec_input = _fingerprint(spec_path, "acceptance_spec")
    try:
        spec = json.loads(spec_path.read_bytes())
        requirements = spec["tests"]
        identifiers = {row["id"] for row in requirements}
        if len(identifiers) != len(requirements) or not requirements:
            raise GateReportError("acceptance requirements must have unique IDs")
        for row in requirements:
            if set(row) != {"id", "milestone", "gate", "requirement", "verification"} or not all(isinstance(v, str) and v for v in row.values()):
                raise GateReportError("invalid acceptance requirement schema")
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise GateReportError("invalid acceptance specification") from exc
    blockers = dict(blockers or {})
    if set(blockers) - identifiers or any(not isinstance(reason, str) or not reason.strip() for reason in blockers.values()):
        raise GateReportError("explicit blockers must name existing requirements and provide reasons")
    inputs = [spec_input]
    runs = []
    for path in junit_paths:
        fingerprint, parsed = _read_junit(Path(path), identifiers, source_revision)
        inputs.append(fingerprint)
        runs.extend(parsed)
    inputs.extend(_fingerprint(Path(path), "artifact") for path in artifact_paths)
    latest_by_scope: dict[str, dict[str, Any]] = {}
    for run in runs:
        previous = latest_by_scope.get(run["scope"])
        if previous is None or run["_completed"] > previous["_completed"]:
            latest_by_scope[run["scope"]] = run
        elif run["_completed"] == previous["_completed"]:
            if run["run_id"] != previous["run_id"] or run["junit_sha256"] != previous["junit_sha256"]:
                raise GateReportError("ambiguous runs have equal completion times in one scope")
    selected_runs = list(latest_by_scope.values())
    selected_tests: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for run in selected_runs:
        run["selected"] = True
        for case in run["tests"]:
            old = selected_tests.get(case["nodeid"])
            if old is None or run["_completed"] > old[0]["_completed"]:
                selected_tests[case["nodeid"]] = (run, case)
            elif run["_completed"] == old[0]["_completed"] and case != old[1]:
                raise GateReportError("testcase appears in ambiguous simultaneous scopes")
    provenance_issues = [f"{run['scope']}/{run['run_id']}: {diagnostic}" for run in selected_runs for diagnostic in run["diagnostics"]]
    unsuccessful_commands = [f"{run['scope']}/{run['run_id']}: command exit status {run['exit_code']}" for run in selected_runs if run["exit_code"] != 0]
    acceptance = []
    for requirement in requirements:
        identifier = requirement["id"]
        evidence = [(run, case) for run, case in selected_tests.values() if identifier in case["acceptance"]]
        tests = [case for _, case in evidence]
        reason = None
        if any(case["status"] == "failed" for case in tests):
            status = "failed"
            reason = "one or more mapped acceptance tests failed"
        elif identifier in blockers:
            status = "blocked"
            reason = blockers[identifier]
        elif not tests:
            status = "not_run"
            reason = "no testcase from the latest runs provides this acceptance evidence"
        elif any(case["status"] == "skipped" for case in tests):
            status = "blocked"
            reason = "one or more required mapped tests were skipped; skipping is not a pass"
        elif any(run["diagnostics"] or run["exit_code"] not in (0, 1) for run, _ in evidence):
            status = "blocked"
            reason = "mapped tests lack valid execution provenance or their command was interrupted or unsuccessful"
        elif requirement["gate"] == "isolation" and not any(run["profile"] == "isolated-local" and str(run["runtime_platform"]).lower().startswith("linux") for run, _ in evidence):
            status = "blocked"
            reason = "Linux isolated-local runtime measurements are required; the host platform alone is insufficient"
        else:
            status = "passed"
        acceptance.append({**requirement, "status": status, "reason": reason, "tests": tests})
    gates = {}
    for name in sorted({row["gate"] for row in requirements}):
        rows = [row for row in acceptance if row["gate"] == name]
        counts = {status: sum(row["status"] == status for row in rows) for status in ("passed", "failed", "blocked", "not_run")}
        gate_ids = {row["id"] for row in rows}
        associated_runs = [run for run in selected_runs
                           if run["scope"] == name or (name == "core" and run["scope"] == "install")
                           or any(gate_ids.intersection(case["acceptance"]) for case in run["tests"])]
        integrity = [f"{run['scope']}/{run['run_id']}: {diagnostic}"
                     for run in associated_runs for diagnostic in run["diagnostics"]]
        for run in associated_runs:
            interrupted = run["exit_code"] not in (0, 1)
            untagged_failure = any(case["status"] == "failed" and not case["acceptance"] for case in run["tests"])
            own_scope = run["scope"] == name or (name == "core" and run["scope"] == "install")
            if interrupted or (own_scope and untagged_failure):
                integrity.append(f"{run['scope']}/{run['run_id']}: command did not complete the required checks")
        satisfied = all(row["status"] == "passed" for row in rows) and not integrity
        gates[name] = {"status": "passed" if satisfied else "failed" if counts["failed"] else "blocked",
                       "checks_satisfied": satisfied, "counts": counts,
                       "integrity_issues": integrity,
                       "requirements": [row["id"] for row in rows],
                       "unsatisfied": [row["id"] for row in rows if row["status"] != "passed"]}
    core = gates.get("core", {}).get("checks_satisfied", False)
    isolation = gates.get("isolation", {}).get("checks_satisfied", False)
    live_agent = gates.get("live_agent", {}).get("checks_satisfied", False)
    gpu = gates.get("gpu", {}).get("checks_satisfied", False)
    for run in runs:
        del run["_completed"]
        run["test_counts"] = {status: sum(case["status"] == status for case in run["tests"]) for status in ("passed", "failed", "skipped")}
    return {
        "schema_version": "0.1",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_revision": source_revision,
        "reporter": {"python": platform.python_version(), "host_platform": platform.platform()},
        "inputs": inputs,
        "executions": runs,
        "acceptance": acceptance,
        "gates": gates,
        "readiness": {"offline_pilot": core, "isolated_local_measurements": core and isolation,
                      "live_agent_measurements": core and isolation and live_agent,
                      "gpu_measurements": core and isolation and gpu},
        "integrity_issues": provenance_issues + unsuccessful_commands,
        "deployment": {"authorized": False, "reason": "Test evidence does not authorize controller deployment or permission expansion; separate operator review and approval remain required."},
        "limitations": [
            "Mapped test results establish only the checks those tests actually perform; a marker is not an independent scientific certification.",
            "Superseded runs remain in execution history but cannot supply missing tests in the newest run of the same scope.",
            "Linux runtime measurements on a macOS Docker VM are distinct from testing and authorizing the target Linux deployment.",
            "Artifact hashes establish content identity, not authenticity, operator authority, or scientific truth.",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--spec", type=Path, default=Path(__file__).resolve().parents[1] / "specs" / "acceptance.json")
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--artifact", action="append", type=Path, default=[])
    parser.add_argument("--blocker", action="append", default=[], metavar="A_ID=REASON")
    options = parser.parse_args(argv)
    blockers = {}
    for value in options.blocker:
        identifier, separator, reason = value.partition("=")
        if not separator or identifier in blockers:
            parser.error("blockers require unique A_ID=REASON values")
        blockers[identifier] = reason
    try:
        report = build_report(options.spec, options.junit, options.source_revision, options.artifact, blockers)
        if options.output.is_symlink():
            raise GateReportError("report destination cannot be a symlink")
        options.output.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".release-gates-", dir=options.output.parent)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, options.output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except (OSError, GateReportError) as exc:
        parser.exit(2, f"release gate evidence rejected: {exc}\n")
    print(json.dumps({"report": str(options.output.resolve()), "readiness": report["readiness"]}, sort_keys=True))
    return 0 if report["readiness"]["offline_pilot"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
