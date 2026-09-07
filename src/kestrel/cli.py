"""Local developer CLI; no deployed-controller or live-provider endpoint."""

from __future__ import annotations

import argparse
import datetime
import json
import platform
import sys
from pathlib import Path

from kestrel import __version__
from kestrel.application import Lab, demo
from kestrel.briefings import brief
from kestrel.conformance import run_conformance
from kestrel.contracts import parse_json
from kestrel.messaging import (
    Assistant,
    MessagingError,
    MilestoneDefinition,
    init_messaging,
)
from kestrel.projects import load_sidecar
from kestrel.runners import DriverError


def doctor() -> dict:
    return {"version": __version__, "python": platform.python_version(), "platform": platform.platform(),
            "core": "available; run verification suite for acceptance evidence",
            "development": "trusted generated fixtures only; no adversarial isolation",
            "isolated_local": "not enabled by developer CLI; actual runtime/audit gates required",
            "messaging": "briefings implemented read-only; Telegram delivery not activated",
            "live_agent": "blocked: no authorized provider/credential integration",
            "gpu": "blocked: no authorized target hardware validation",
            "deployment": "not authorized or implemented"}


def messaging_command(args) -> dict:
    """Local messaging control. Never opens Lab and never contacts a network.

    Each result states its authority: attention-only preference changes, local
    state changes, or operations that would need a separate operator grant.
    """
    if args.messaging_root is None:
        raise MessagingError("Supply --messaging-root PATH before a messaging command")
    if args.command == "notify" and args.action == "init":
        if args.lab is None:
            raise MessagingError("Supply --lab PATH so messaging knows which lab to observe")
        return {**init_messaging(args.messaging_root, args.lab, timezone=args.timezone,
                                 brief_local_time=args.brief_time),
                "authority": "local state only; no grant, credential or egress created"}
    with Assistant(args.messaging_root) as assistant:
        if args.command == "inbox":
            if args.action == "list":
                return {"items": assistant.inbox(include_resolved=args.all),
                        "authority": "local read only"}
            return {**assistant.item(args.reference), "authority": "local read only"}
        if args.action == "reconcile":
            return {**assistant.reconcile(now=parse_instant(args.now)),
                    "authority": "local projection; research state is read only"}
        if args.action == "status":
            return {**assistant.status(), "authority": "local read only"}
        if args.action == "watch":
            return {**assistant.watch(args.scope, route=args.route),
                    "authority": "local preference; confers no execution or send permission"}
        if args.action == "unwatch":
            return {**assistant.unwatch(args.scope), "authority": "local preference"}
        if args.action == "milestone":
            if args.target == "add":
                definition = MilestoneDefinition.model_validate(
                    parse_json(args.definition.read_bytes(), max_bytes=64 * 1024))
                return {**assistant.add_milestone(args.milestone_id, definition),
                        "authority": "local definition; a milestone never certifies evidence"}
            return {"milestones": assistant.milestones(),
                    "history": assistant.milestone_history(),
                    "authority": "local read only"}
        if args.action == "schedule":
            if args.target == "set":
                return {**assistant.set_schedule(local_time=args.local_time,
                                                 timezone=args.timezone),
                        "authority": "local preference"}
            return {"schedule": assistant.schedule(), "authority": "local read only"}
        if args.action == "source":
            if args.target == "rebind":
                return {**assistant.rebind_sources(confirm=args.confirm),
                        "authority": "local cursor state; no research record changes"}
            return {**assistant.snapshot_sources(),
                    "authority": "authority-side read of research databases"}
        if args.action == "ack":
            return {**assistant.acknowledge(args.reference), "authority": "attention only"}
        if args.action == "snooze":
            return {**assistant.snooze(args.reference, args.duration),
                    "authority": "attention only"}
        return {**assistant.pause(paused=args.action == "pause"),
                "authority": "local delivery preference; cannot revoke a remote credential"}


def parse_instant(value: str | None) -> float | None:
    """Accept UTC epoch seconds or an explicit ISO-8601 instant."""
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        moment = datetime.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("--since needs epoch seconds or an ISO-8601 instant") from exc
    if moment.tzinfo is None:
        raise ValueError("--since ISO-8601 instants require an explicit UTC offset")
    return moment.timestamp()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="kestrel", description="Kestrel first-pilot developer runtime")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--lab", type=Path, help="external developer lab directory")
    root.add_argument("--messaging-root", type=Path,
                      help="external messaging state directory")
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
    conformance.add_argument("--campaign", help="run or inspect this approved fixture campaign")
    conformance.add_argument("--approval", help="approval bound to the supplied campaign")
    conformance.add_argument("--profile", choices=("development", "isolated-local"),
                             default="development")
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
    inbox = commands.add_parser("inbox").add_subparsers(dest="action", required=True)
    listing = inbox.add_parser("list")
    listing.add_argument("--all", action="store_true", help="include resolved items")
    inbox.add_parser("show").add_argument("reference")
    notify = commands.add_parser("notify").add_subparsers(dest="action", required=True)
    setup = notify.add_parser("init", help="create external messaging state; no service")
    setup.add_argument("--timezone", default="UTC", help="explicit IANA timezone")
    setup.add_argument("--brief-time", default="08:00", help="local HH:MM briefing time")
    reconcile = notify.add_parser("reconcile")
    reconcile.add_argument("--once", action="store_true", required=True)
    reconcile.add_argument("--now", help="deterministic observation instant")
    notify.add_parser("status")
    watch = notify.add_parser("watch")
    watch.add_argument("scope", help="campaign:ID, task:ID, attempt:ID or milestone:ID")
    watch.add_argument("--route", choices=("critical", "timely", "digest", "inbox"),
                       default="timely")
    notify.add_parser("unwatch").add_argument("scope")
    milestone = notify.add_parser("milestone").add_subparsers(dest="target", required=True)
    milestone_add = milestone.add_parser("add")
    milestone_add.add_argument("--id", dest="milestone_id", required=True)
    milestone_add.add_argument("--definition", type=Path, required=True,
                               help="typed predicate document; not an executable rule")
    milestone.add_parser("inspect")
    schedule = notify.add_parser("schedule").add_subparsers(dest="target", required=True)
    schedule.add_parser("show")
    schedule_set = schedule.add_parser("set")
    schedule_set.add_argument("--time", dest="local_time")
    schedule_set.add_argument("--timezone")
    source = notify.add_parser("source").add_subparsers(dest="target", required=True)
    source.add_parser("rebind").add_argument("--confirm", action="store_true")
    source.add_parser("snapshot")
    notify.add_parser("ack").add_argument("reference")
    snooze = notify.add_parser("snooze")
    snooze.add_argument("reference")
    snooze.add_argument("duration", help="30m, 1h, 2d or tomorrow")
    notify.add_parser("pause")
    notify.add_parser("resume")
    briefing = commands.add_parser("brief", help="read-only briefing from verified records")
    briefing.add_argument("--since", help="UTC epoch seconds or ISO-8601 instant")
    briefing.add_argument("--format", dest="form", choices=("json", "markdown", "plain"),
                          default="json")
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
            if args.campaign is None and args.approval is None:
                result = {"static_sidecar_valid": True, "manifest": load_sidecar(args.manifest),
                          "active_probe": "not run; supply --lab, --campaign and --approval for fixture checks"}
            else:
                if args.lab is None or args.campaign is None or args.approval is None:
                    raise ValueError("Active conformance requires --lab, --campaign and --approval")
                with Lab(args.lab) as lab:
                    result = run_conformance(lab, args.manifest, args.campaign, args.approval,
                                             profile=args.profile)
        elif args.command in ("notify", "inbox"):
            result = messaging_command(args)
        elif args.command == "brief":
            # Read-only path: never construct Lab, which would initialize
            # writable stores, a driver and agent state.
            if args.lab is None:
                raise ValueError("Supply --lab PATH before the command")
            result = brief(args.lab, since=parse_instant(args.since), form=args.form)
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
        print(result if isinstance(result, str)
              else json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, OSError, DriverError, KeyError) as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
