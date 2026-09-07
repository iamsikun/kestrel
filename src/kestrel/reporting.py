"""One verified interpretation of a campaign outcome, shared by every surface.

`Lab.report` and the read-only briefing path both call this module. There is
deliberately no second, weaker reading of the same records: a message may not
present a conclusion that the authoritative report would refuse.

Verification here is bookkeeping over recorded evidence. It does not establish
that a scientific claim is correct.
"""

from __future__ import annotations

from typing import Any, Protocol

from kestrel.contracts import outcome_matches, parse_json

LIMITATIONS = (
    "Trusted synthetic fixtures only; no adversarial isolation",
    "Finite-domain oracle; no population inference",
    "No live provider, GPU or deployed-controller assurance",
)


class EvidenceReader(Protocol):
    """The minimum evidence access a report needs.

    Satisfied by the writable `Artifacts` store and by the read-only
    `sources.EvidenceSource`. `read` must re-verify content bytes and raise
    rather than downgrade when they no longer match their digest.
    """

    def get(self, digest: str) -> dict[str, Any]: ...

    def read(self, digest: str) -> bytes: ...


def verify_outcome_evidence(
    campaign_id: str, campaign: dict[str, Any], reader: EvidenceReader
) -> tuple[list[dict[str, Any]], str, str]:
    """Recheck every cited artifact and derive campaign validity and assurance."""
    outcome = campaign["outcome"]
    evidence: list[dict[str, Any]] = []
    validity = "incomplete"
    assurance = "unverified"
    if outcome:
        for identity in outcome["evidence_ids"]:
            record = reader.get(identity)
            analysis = parse_json(reader.read(identity))
            # Valid, independently recomputed bytes are not this campaign's
            # evidence unless they are actually attributable to it. Borrowed
            # analysis from another campaign cannot certify this one.
            if type(analysis) is not dict:
                raise ValueError("Outcome evidence must contain a structured outcome")
            attributable = (analysis.get("campaign_id") == campaign_id
                            and campaign["digest"] in record["lineage"])
            consistent = outcome_matches(analysis, execution=outcome["execution_status"],
                                         validity=outcome["protocol_status"],
                                         finding=outcome["finding"])
            analysis["assurance"] = record["assurance"]
            if record["status"] != "valid" or not attributable or not consistent:
                analysis = {**analysis, "validity": "invalid", "finding": "inconclusive",
                            "assurance": "unverified"}
            evidence.append({"digest": identity, "status": record["status"],
                             "assurance": record["assurance"], "analysis": analysis,
                             "attributable": attributable, "consistent": consistent})
        validity = (outcome["protocol_status"]
                    if all(e["status"] == "valid" and e["attributable"] and e["consistent"]
                           for e in evidence)
                    else "invalid")
        if validity == "valid":
            assurance = ("independently_recomputed"
                         if all(e["assurance"] == "independently_recomputed" for e in evidence)
                         else "imported" if any(e["assurance"] == "imported" for e in evidence)
                         else "traceable")
    return evidence, validity, assurance


def build_report(
    campaign_id: str,
    campaign: dict[str, Any],
    reader: EvidenceReader,
    attempts: list[dict[str, Any]],
    budget_reserved: dict[str, float],
) -> dict[str, Any]:
    """Assemble the authoritative report shape from already-read records."""
    outcome = campaign["outcome"]
    evidence, validity, assurance = verify_outcome_evidence(campaign_id, campaign, reader)
    execution_only = campaign["contract"].get("contract_version") == "execution-1"
    report = {"campaign_id": campaign_id, "contract_digest": campaign["digest"],
            "brief": campaign["contract"].get("brief", {}), "state": campaign["state"],
            "execution": outcome["execution_status"] if outcome else "pending",
            "validity": validity,
            "finding": outcome["finding"].lower()
            if outcome and assurance == "independently_recomputed" else "inconclusive",
            "assurance": assurance,
            "profile": "development", "adversarial_isolation": False,
            "attempts": attempts,
            "budget_reserved": budget_reserved, "evidence": evidence,
            "limitations": list(LIMITATIONS)}

    if execution_only:
        report.update(profile="isolated-local", scientific_outcome="not_evaluated",
                      metrics_assurance="self_reported", limitations=[
                          "Project outputs are self-reported; no scientific evaluation",
                          "Linux container profile; deployment verification remains separate",
                          "Writable workspace storage is advisory; no live provider or GPU assurance"])
    return report
