"""Local developer CLI; no deployed-controller or live-provider endpoint."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

from kestrel import __version__
from kestrel.application import Lab, demo
from kestrel.projects import load_sidecar
from kestrel.runners import DriverError


def doctor() -> dict:
    return {"version": __version__, "python": platform.python_version(), "platform": platform.platform(),
            "core": "available; run verification suite for acceptance evidence",
            "development": "trusted generated fixtures only; no adversarial isolation",
            "isolated_local": "not enabled by developer CLI; actual runtime/audit gates required",
            "live_agent": "blocked: no authorized provider/credential integration",
            "gpu": "blocked: no authorized target hardware validation",
            "deployment": "not authorized or implemented"}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="kestrel", description="Kestrel first-pilot developer runtime")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--lab", type=Path, help="external developer lab directory")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    demonstration = commands.add_parser("demo")
    demonstration.add_argument("--offline", action="store_true", required=True)
    demonstration.add_argument("--output", type=Path, help="new external lab directory")
    lab = commands.add_parser("lab").add_subparsers(dest="action", required=True)
    lab.add_parser("init").add_argument("path", type=Path)
    project = commands.add_parser("project").add_subparsers(dest="action", required=True)
    register = project.add_parser("register")
    register.add_argument("--manifest", type=Path, required=True)
    register.add_argument("--snapshot-dirty", action="store_true",
                          help="explicit complete snapshot including ignored/untracked files")
    project.add_parser("validate").add_argument("project")
    conformance = project.add_parser("conformance")
    conformance.add_argument("--manifest", type=Path, required=True)
    campaign = commands.add_parser("campaign").add_subparsers(dest="action", required=True)
    propose = campaign.add_parser("propose")
    propose.add_argument("--project", required=True)
    propose.add_argument("--brief", type=Path, required=True)
    for action in ("inspect", "status", "cancel", "report", "approve", "run"):
        action_parser = campaign.add_parser(action)
        action_parser.add_argument("campaign")
        if action == "approve":
            action_parser.add_argument("--digest", required=True)
            action_parser.add_argument("--operator-token-file", type=Path, required=True)
        if action == "run":
            action_parser.add_argument("--approval", required=True)
    evidence = commands.add_parser("evidence").add_subparsers(dest="action", required=True)
    export = evidence.add_parser("export")
    export.add_argument("campaign")
    export.add_argument("--output", type=Path, required=True)
    import_parser = evidence.add_parser("import")
    import_parser.add_argument("bundle", type=Path)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            result = doctor()
        elif args.command == "demo":
            result = demo(args.output)
        elif args.command == "lab":
            with Lab.initialize(args.path) as lab:
                result = {"lab": str(lab.root), "profile": "development",
                          "operator_token_file": str(lab.root / "operator.token")}
        elif args.command == "project" and args.action == "conformance":
            result = {"static_sidecar_valid": True, "manifest": load_sidecar(args.manifest),
                      "active_probe": "not run; registration does not grant execution"}
        else:
            if args.lab is None:
                raise ValueError("Supply --lab PATH before the command")
            with Lab(args.lab) as lab:
                if args.command == "project":
                    if args.action == "register":
                        result = lab.register(args.manifest, snapshot_dirty=args.snapshot_dirty)
                    else:
                        result = lab.projects.get(args.project)
                        lab.projects._verify_snapshot(Path(result["snapshot_path"]),
                                                      result["source_identity"]["files"],
                                                      result["source_identity"]["directories"])
                elif args.command == "campaign":
                    if args.action == "propose":
                        campaign_id = lab.propose(args.project, args.brief.read_text())
                        result = lab.controller.campaign(campaign_id)
                    elif args.action == "approve":
                        result = {"approval_id": lab.approve(args.campaign, args.digest,
                                  args.operator_token_file.read_text().strip())}
                    elif args.action == "run":
                        result = lab.run(args.campaign, args.approval)
                    elif args.action == "cancel":
                        result = lab.cancel(args.campaign)
                    elif args.action == "inspect":
                        result = lab.controller.campaign(args.campaign)
                    else:
                        result = lab.report(args.campaign)
                elif args.action == "export":
                    result = {"bundle": str(lab.export(args.campaign, args.output))}
                else:
                    result = {"artifacts": lab.store.import_bundle(args.bundle),
                              "assurance": "imported; not independently executed"}
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, OSError, DriverError, KeyError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
