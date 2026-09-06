#!/usr/bin/env python3
"""Validate the specification kit, not an implemented Kestrel runtime."""
from __future__ import annotations

import hashlib
import json
import sys
from graphlib import CycleError, TopologicalSorter
from pathlib import Path


def read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        acceptance = read_json(root / "specs/acceptance.json")
        milestones = read_json(root / "specs/milestones.json")
        manifest = read_json(root / "kit-manifest.json")
        tests = acceptance["tests"]
        rows = milestones["milestones"]
        test_ids = [row["id"] for row in tests]
        milestone_ids = [row["id"] for row in rows]
        if len(test_ids) != len(set(test_ids)):
            raise ValueError("Duplicate acceptance ID")
        if len(milestone_ids) != len(set(milestone_ids)):
            raise ValueError("Duplicate milestone ID")
        known = set(milestone_ids)
        graph = {}
        references = []
        for row in rows:
            deps = set(row["depends_on"])
            if not deps <= known:
                raise ValueError(f"Unknown dependency in {row['id']}")
            graph[row["id"]] = deps
            references.extend(row["acceptance_ids"])
        order = list(TopologicalSorter(graph).static_order())
        if sorted(references) != sorted(test_ids):
            raise ValueError("Acceptance references are missing, duplicated, or unknown")
        for test in tests:
            if test["milestone"] not in known:
                raise ValueError(f"Unknown milestone for {test['id']}")
            parent = next(row for row in rows if row["id"] == test["milestone"])
            if test["id"] not in parent["acceptance_ids"]:
                raise ValueError(f"Incorrect milestone mapping for {test['id']}")
            if test["gate"] not in {"core", "isolation", "live_agent", "gpu"}:
                raise ValueError(f"Unknown gate for {test['id']}")
        for entry in manifest["files"]:
            path = root / entry["path"]
            if not path.resolve().is_relative_to(root):
                raise ValueError("Manifest path escapes kit")
            payload = path.read_bytes()
            if len(payload) != entry["bytes"]:
                raise ValueError(f"Size mismatch: {entry['path']}")
            if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise ValueError(f"Hash mismatch: {entry['path']}")
        print(json.dumps({
            "kit_integrity": "passed",
            "specification_consistency": "passed",
            "files_checked": len(manifest["files"]),
            "acceptance_requirements": len(tests),
            "milestone_order": order,
            "framework_implemented": False,
            "note": "This verifies the supplied build specification only. It does not run framework acceptance tests.",
        }, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, CycleError) as exc:
        print(f"Specification-kit validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
