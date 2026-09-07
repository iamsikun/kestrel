"""Deterministic briefings built only from verified records.

Every rendered factual field resolves to a source reference or to a named
derivation listed in `DERIVATIONS`. No language model is involved and no text
is generated from free-form source strings. Fields with no producer are
reported as unavailable rather than as zero.
"""

from __future__ import annotations

import datetime
import time
import unicodedata
from typing import Any

from kestrel.artifacts import ArtifactError
from kestrel.contracts import digest
from kestrel.controller import TERMINAL
from kestrel.reporting import LIMITATIONS, build_report
from kestrel.sources import LabSources, SourceUnavailable

BRIEFING_VERSION = "0.1"

#: Named deterministic derivations a briefing is allowed to state.
DERIVATIONS = {
    "charged_reservations": "Sum of per-attempt reserved budgets; not measured consumption",
    "attempt_counts": "Count of attempt rows grouped by recorded state",
    "declared_plan": "Recipe, candidate and selection counts declared in the frozen contract",
    "awaiting_approval": "Campaign has non-terminal tasks and no unexpired, unrevoked "
                         "approval bound to its current contract digest",
    "uncertain_capacity": "Attempt still holds reservations without a confirmed stop",
    "evidence_correction": "Evidence store invalidation or staleness events in the interval",
}

#: Signals the runtime does not record. Stating them as zero would be a fabrication.
UNAVAILABLE = (
    "Measured wall-clock runtime, CPU, memory and GPU utilisation",
    "Monetary cost and provider token spend",
    "Host telemetry, disk pressure and external contention",
    "Literature coverage and related-work novelty",
    "Operator reading, acknowledgement or device delivery",
)

_MAX_LABEL = 200


def evidence_epoch(created_at: str) -> float | None:
    """Normalise the evidence store's UTC text timestamp to an epoch.

    The controller records `REAL` epochs while the evidence store records
    `CURRENT_TIMESTAMP` text at one-second resolution. Ordering still uses each
    feed's own key; this conversion only supports interval filtering.
    """
    try:
        moment = datetime.datetime.strptime(str(created_at), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None
    return moment.replace(tzinfo=datetime.UTC).timestamp()


def safe_text(value: Any, *, limit: int = _MAX_LABEL) -> str:
    """Bound and neutralise a source-authored string before it is rendered.

    Project titles and briefs are untrusted input. Control characters, bidi
    overrides and line breaks are removed so a record cannot forge structure in
    a rendered message. This is containment, not proof that arbitrary text is
    safe to publish externally; the disclosure profile decides that separately.
    """
    text = value if isinstance(value, str) else str(value)
    cleaned = "".join(
        " " if character in "\r\n\t" else character
        for character in text
        if unicodedata.category(character) not in {"Cc", "Cf", "Cs", "Co", "Cn"}
        or character in "\r\n\t"
    )
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit] + ("…" if len(cleaned) > limit else "")


def _campaign_summary(sources: LabSources, campaign_id: str, *, now: float) -> dict[str, Any]:
    """One campaign's verified view, or a bounded integrity item on failure."""
    campaign = sources.controller.campaign(campaign_id)
    contract = campaign["contract"]
    attempts = sources.controller.attempts(campaign_id)
    tasks = sources.controller.tasks(campaign_id)
    states: dict[str, int] = {}
    for attempt in attempts:
        states[attempt["state"]] = states.get(attempt["state"], 0) + 1
    task_states: dict[str, int] = {}
    for task in tasks:
        task_states[task["state"]] = task_states.get(task["state"], 0) + 1
    summary: dict[str, Any] = {
        "campaign_id": campaign_id,
        "contract_digest": campaign["digest"],
        "state": campaign["state"],
        "kind": contract.get("kind"),
        "project_id": safe_text(contract.get("project_id", "")),
        "interpretation": safe_text(contract.get("brief", {}).get("interpretation", "")),
        "declared_plan": {
            "recipes": len(contract.get("recipes", [])),
            "baseline_recipes": 1 if contract.get("baseline") else 0,
            "treatment_candidates": len(contract.get("candidates", [])),
            "selections_recorded": len(sources.controller.selections(campaign_id)),
            "attempts_recorded": len(attempts),
        },
        "attempt_states": dict(sorted(states.items())),
        "task_states": dict(sorted(task_states.items())),
        "charged_reservations": sources.controller.budget_used(campaign_id),
        "declared_budget": contract.get("budget"),
        "approval": {"usable": len(sources.controller.usable_approvals(
            campaign_id, campaign["digest"], now=now)),
            "recorded": len(sources.controller.approvals(campaign_id))},
    }
    try:
        report = build_report(campaign_id, campaign, sources.evidence, attempts,
                              summary["charged_reservations"])
        summary["outcome"] = {axis: report[axis] for axis in
                              ("execution", "validity", "finding", "assurance")}
        summary["evidence"] = [{"digest": item["digest"], "status": item["status"],
                                "assurance": item["assurance"],
                                "attributable": item["attributable"],
                                "consistent": item["consistent"]}
                               for item in report["evidence"]]
        summary["verification"] = "recomputed"
    except (ArtifactError, ValueError) as exc:
        # A verification failure is an integrity item, never a cached green result.
        summary["outcome"] = {"execution": "unavailable", "validity": "unavailable",
                              "finding": "unavailable", "assurance": "unverified"}
        summary["evidence"] = []
        summary["verification"] = "failed"
        summary["verification_error"] = f"{type(exc).__name__}: {safe_text(exc)}"
    return summary


def _awaiting_you(summary: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    unfinished = sum(count for state, count in summary["task_states"].items()
                     if state not in TERMINAL)
    if unfinished and summary["approval"]["usable"] == 0:
        items.append({
            "condition": "approval_needed" if not summary["approval"]["recorded"]
            else "approval_unusable",
            "campaign_id": summary["campaign_id"],
            "derivation": "awaiting_approval",
            "unfinished_tasks": unfinished,
            "consequence": "These tasks cannot be reserved until an approval binds the "
                           "current contract digest.",
        })
    blocked = summary["task_states"].get("BLOCKED", 0)
    if blocked:
        items.append({"condition": "blocked_work", "campaign_id": summary["campaign_id"],
                      "derivation": "attempt_counts", "blocked_tasks": blocked,
                      "eligible_elsewhere": unfinished - blocked,
                      "consequence": "Independently eligible work can still proceed."})
    declared = summary.get("declared_budget") or {}
    charged = summary["charged_reservations"]
    exhausted = sorted(key for key, limit in declared.items()
                       if isinstance(limit, (int, float)) and charged.get(key, 0) >= limit > 0)
    if exhausted and unfinished:
        items.append({"condition": "budget_exhausted", "campaign_id": summary["campaign_id"],
                      "derivation": "charged_reservations", "exhausted": exhausted,
                      "consequence": "Remaining tasks need an amended budget; reservations "
                                     "are charged, not measured."})
    if summary["verification"] == "failed":
        items.append({"condition": "evidence_integrity", "campaign_id": summary["campaign_id"],
                      "derivation": "evidence_correction",
                      "detail": summary["verification_error"],
                      "consequence": "This campaign's recorded outcome cannot be presented "
                                     "as verified."})
    return items


def _integrity(sources: LabSources, *, since: float | None, now: float,
               summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for attempt in sources.controller.unsettled_attempts():
        if attempt["state"] in TERMINAL and attempt["stopped_confirmed"]:
            continue
        items.append({"condition": "uncertain_capacity", "derivation": "uncertain_capacity",
                      "campaign_id": attempt["campaign_id"], "attempt_id": attempt["id"],
                      "attempt_state": attempt["state"],
                      "stopped_confirmed": attempt["stopped_confirmed"],
                      "resources": attempt["resources"],
                      "consequence": "Reconciliation is required before any retry; a blind "
                                     "restart is not proposed."})
    for attempt in sources.controller.stale_leases(now=now):
        items.append({"condition": "expired_lease", "derivation": "uncertain_capacity",
                      "campaign_id": attempt["campaign_id"], "attempt_id": attempt["id"],
                      "attempt_state": attempt["state"], "lease_until": attempt["lease_until"],
                      "consequence": "Lease expiry does not confirm the job stopped."})
    # Evidence invalidation is a separate store with its own event order.
    corrections: list[dict[str, Any]] = []
    for event in sources.evidence.events():
        if event["kind"] not in {"invalid", "stale", "deleted"}:
            continue
        recorded = evidence_epoch(event["created_at"])
        if since is not None and recorded is not None and recorded <= since:
            continue
        if corrections and corrections[-1]["reason"] == event["reason"] \
                and corrections[-1]["last_event_id"] + 1 == event["id"]:
            corrections[-1]["last_event_id"] = event["id"]
            corrections[-1]["affected"] += 1
            continue
        corrections.append({"condition": "evidence_correction",
                            "derivation": "evidence_correction",
                            "kind": event["kind"], "reason": safe_text(event["reason"]),
                            "first_event_id": event["id"], "last_event_id": event["id"],
                            "affected": 1, "recorded_at": event["created_at"]})
    items.extend(corrections)
    for summary in summaries:
        if summary["verification"] == "failed":
            items.append({"condition": "verification_failed",
                          "derivation": "evidence_correction",
                          "campaign_id": summary["campaign_id"],
                          "detail": summary["verification_error"]})
    return items


def build_briefing(sources: LabSources, *, since: float | None = None,
                   now: float | None = None, label: str = "lab") -> dict[str, Any]:
    """Assemble one briefing record. Read-only; mutates no research state."""
    now = time.time() if now is None else now
    cutoffs = sources.cutoffs()
    changes = (sources.controller.events_since(since) if since is not None
               else sources.controller.events())
    changed_campaigns = sorted({event["campaign_id"] for event in changes
                                if event["campaign_id"]})
    summaries = [_campaign_summary(sources, campaign_id, now=now)
                 for campaign_id in sources.controller.campaign_ids()]
    awaiting = [item for summary in summaries for item in _awaiting_you(summary)]
    active = {
        "campaign_states": {},
        "attempt_states": {},
        "task_states": {},
        "capacity_held": sources.controller.resources_used(),
    }
    for summary in summaries:
        active["campaign_states"][summary["state"]] = \
            active["campaign_states"].get(summary["state"], 0) + 1
        for state, count in summary["attempt_states"].items():
            active["attempt_states"][state] = active["attempt_states"].get(state, 0) + count
        for state, count in summary["task_states"].items():
            active["task_states"][state] = active["task_states"].get(state, 0) + count
    for key in ("campaign_states", "attempt_states", "task_states"):
        active[key] = dict(sorted(active[key].items()))
    outcomes = [{"campaign_id": summary["campaign_id"], "state": summary["state"],
                 "outcome": summary["outcome"], "declared_plan": summary["declared_plan"],
                 "evidence": summary["evidence"], "verification": summary["verification"]}
                for summary in summaries if summary["state"] == "COMPLETE"]
    record = {
        "briefing_version": BRIEFING_VERSION,
        "lab": safe_text(label),
        "coverage": {"since": since, "until": now,
                     "changed_events": len(changes),
                     "changed_campaigns": changed_campaigns,
                     "complete_history": since is None},
        "cutoffs": cutoffs,
        "freshness": {
            "controller_last_event_at": max((event["created_at"] for event in changes),
                                            default=None),
            "observed_at": now,
            "note": "Per-feed cutoffs; the two stores commit independently and no global "
                    "snapshot transaction is claimed.",
        },
        "sections": {
            "awaiting_you": awaiting,
            "active_work": active,
            "reservations": [{"campaign_id": summary["campaign_id"],
                              "charged": summary["charged_reservations"],
                              "declared": summary["declared_budget"],
                              "derivation": "charged_reservations"}
                             for summary in summaries],
            "verified_outcomes": outcomes,
            "integrity": _integrity(sources, since=since, now=now, summaries=summaries),
        },
        "campaigns": [{"campaign_id": s["campaign_id"], "state": s["state"],
                       "project_id": s["project_id"], "kind": s["kind"],
                       "interpretation": s["interpretation"],
                       "contract_digest": s["contract_digest"],
                       "changed_in_interval": s["campaign_id"] in set(changed_campaigns)}
                      for s in summaries],
        "derivations": dict(sorted(DERIVATIONS.items())),
        "unavailable": list(UNAVAILABLE),
        "limitations": list(LIMITATIONS),
    }
    record["content_digest"] = briefing_digest(record)
    return record


def briefing_digest(record: dict[str, Any]) -> str:
    """Semantic identity of a briefing, excluding presentation-only fields.

    Wall-clock observation time is deliberately outside the digest so that two
    renderings of the same source state deduplicate instead of re-notifying.
    """
    semantic = {key: value for key, value in record.items()
                if key not in {"content_digest", "freshness"}}
    semantic["coverage"] = {key: value for key, value in record["coverage"].items()
                            if key != "until"}
    return digest(semantic)


# --------------------------------------------------------------------------
# Rendering. Templates are fixed; no source string becomes structure.
# --------------------------------------------------------------------------

def _counts(mapping: dict[str, Any]) -> str:
    return ", ".join(f"{name.lower()} {value:g}" if isinstance(value, float)
                     else f"{name.lower()} {value}"
                     for name, value in mapping.items()) or "none recorded"


def render_markdown(record: dict[str, Any]) -> str:
    sections = record["sections"]
    lines = [f"# Kestrel briefing · {record['lab']}", ""]
    coverage = record["coverage"]
    span = "complete recorded history" if coverage["complete_history"] \
        else f"since {coverage['since']:.0f}"
    lines += [f"Coverage: {span}; {coverage['changed_events']} ledger events in interval.",
              f"Controller cutoff sequence {record['cutoffs']['controller']['sequence']}; "
              f"evidence cutoff event {record['cutoffs']['evidence']['event_id']}.", ""]
    lines += ["## Needs you", ""]
    if not sections["awaiting_you"]:
        lines.append("- Nothing is awaiting you in the recorded state.")
    for item in sections["awaiting_you"]:
        lines.append(f"- **{item['condition']}** · campaign `{item['campaign_id']}` — "
                     f"{item['consequence']}")
    lines += ["", "## Active work", "",
              f"- Campaigns: {_counts(sections['active_work']['campaign_states'])}",
              f"- Tasks: {_counts(sections['active_work']['task_states'])}",
              f"- Attempts: {_counts(sections['active_work']['attempt_states'])}",
              f"- Capacity held: {_counts(sections['active_work']['capacity_held'])}", ""]
    lines += ["## Charged reservations", "",
              "Reserved worst case per attempt, not measured consumption.", ""]
    for row in sections["reservations"]:
        lines.append(f"- `{row['campaign_id']}`: {_counts(row['charged'])}"
                     + (f" of declared {_counts(row['declared'])}" if row["declared"] else ""))
    lines += ["", "## Verified outcomes", ""]
    if not sections["verified_outcomes"]:
        lines.append("- No campaign has completed.")
    for row in sections["verified_outcomes"]:
        outcome = row["outcome"]
        plan = row["declared_plan"]
        lines.append(
            f"- `{row['campaign_id']}`: execution {outcome['execution']}; protocol "
            f"{outcome['validity']}; finding {outcome['finding']}; evidence "
            f"{outcome['assurance']}. {plan['baseline_recipes']} baseline recipe, "
            f"{plan['treatment_candidates']} treatment candidate, "
            f"{plan['selections_recorded']} recorded selection, "
            f"{plan['attempts_recorded']} charged attempt(s).")
    lines += ["", "## Integrity", ""]
    if not sections["integrity"]:
        lines.append("- No anomalies recorded in this interval. This is not a statement "
                     "that all systems are healthy.")
    for item in sections["integrity"]:
        detail = item.get("detail") or item.get("reason") or item.get("attempt_id") or ""
        lines.append(f"- **{item['condition']}** {detail}".rstrip())
    lines += ["", "## Not available", ""]
    lines += [f"- {text}" for text in record["unavailable"]]
    lines += ["", "## Limitations", ""]
    lines += [f"- {text}" for text in record["limitations"]]
    lines += ["", f"Briefing identity `{record['content_digest'][:16]}`."]
    return "\n".join(lines) + "\n"


def render_plain(record: dict[str, Any], *, include_lab: bool = True) -> str:
    """Short plain-text view. The full record stays in the local inbox."""
    sections = record["sections"]
    awaiting = len(sections["awaiting_you"])
    integrity = len(sections["integrity"])
    complete = len(sections["verified_outcomes"])
    parts = [(f"Kestrel briefing for {record['lab']}." if include_lab
              else "Kestrel briefing."),
             f"Needs you: {awaiting}. Completed campaigns: {complete}. "
             f"Integrity items: {integrity}."]
    for item in sections["awaiting_you"][:3]:
        parts.append(f"- {item['condition']}: campaign {item['campaign_id']}.")
    for row in sections["verified_outcomes"][:3]:
        outcome = row["outcome"]
        parts.append(f"- {row['campaign_id']}: execution {outcome['execution']}; protocol "
                     f"{outcome['validity']}; finding {outcome['finding']}; evidence "
                     f"{outcome['assurance']}.")
    parts.append(f"Data through controller sequence "
                 f"{record['cutoffs']['controller']['sequence']}. "
                 f"Item {record['content_digest'][:12]}.")
    return "\n".join(parts)


def brief(lab_root, *, since: float | None = None, now: float | None = None,
          form: str = "json") -> dict[str, Any] | str:
    """Open both read-only feeds, build one briefing, and render it."""
    with LabSources(lab_root) as sources:
        record = build_briefing(sources, since=since, now=now, label=str(lab_root))
    if form == "json":
        return record
    if form == "markdown":
        return render_markdown(record)
    if form == "plain":
        return render_plain(record)
    raise SourceUnavailable(f"Unsupported briefing format: {form}")
