"""Counterexamples to the second audit's repairs, using external synthetic labs."""

import pytest

from kestrel.application import Lab, framework_root
from kestrel.contracts import canonical
from kestrel.controller import AuthorityError, StateError
from kestrel.fixtures import generate


@pytest.fixture
def pending_result(tmp_path, monkeypatch):
    with Lab.initialize(tmp_path / "lab") as lab:
        manifest = generate(tmp_path / "projects", framework_root())[0]
        project = lab.register(manifest, snapshot_dirty=True)
        campaign = lab.propose(project["project_id"], "Preserve the actual negative finding")
        approval = lab.approve(campaign, lab.controller.campaign(campaign)["digest"],
                               (lab.root / "operator.token").read_text())
        completion = {}
        with monkeypatch.context() as patch:
            patch.setattr(lab.controller, "complete", lambda _, **kw: completion.update(kw))
            lab.run(campaign, approval)
        yield lab, campaign, completion


@pytest.mark.acceptance("A27")
@pytest.mark.acceptance("A40")
@pytest.mark.parametrize("forgery", ["missing-evidence", "opposite-finding"])
def test_completion_resolves_evidence_and_matches_its_finding(pending_result, forgery):
    lab, campaign, completion = pending_result
    if forgery == "missing-evidence":
        completion["evidence_ids"] = ["f" * 64]
    else:
        completion["finding"] = "SUPPORTED_IN_SCOPE"
    with pytest.raises(ValueError):
        lab.controller.complete(campaign, **completion)
    assert lab.controller.campaign(campaign)["outcome"] is None


@pytest.mark.acceptance("A27")
@pytest.mark.acceptance("A40")
def test_report_rejects_inconsistent_legacy_outcome(pending_result):
    # Simulate a legacy/imported terminal row without weakening controller triggers.
    lab, campaign, completion = pending_result
    lab.controller.complete(campaign, **completion)
    original = lab.controller.campaign
    def legacy(identifier):
        value = original(identifier)
        value["outcome"]["finding"] = "SUPPORTED_IN_SCOPE"
        return value
    lab.controller.campaign = legacy
    report = lab.report(campaign)
    assert report["validity"] == "invalid"
    assert report["finding"] == "inconclusive"
    with pytest.raises(ValueError):
        lab.quantitative_claim(campaign, {"analysis": completion["evidence_ids"][0],
                                          "field": "difference", "value": 4})


@pytest.mark.acceptance("A26")
def test_controller_amendment_can_be_cancelled_and_exported_by_lab(pending_result, tmp_path):
    lab, parent, completion = pending_result
    lab.controller.complete(parent, **completion)
    revised = lab.controller.campaign(parent)["contract"]
    revised["brief"]["original"] = "Explicit amended synthetic question"
    child = lab.controller.amend(parent, revised)
    lab.controller.freeze(child)
    report = lab.cancel(child)
    assert report["execution"] == "cancelled"
    assert report["evidence"][0]["attributable"]
    assert lab.store.read(report["contract_digest"]) == canonical(revised)
    assert lab.export(child, tmp_path / "amendment.zip").is_file()
    assert lab.report(parent)["finding"] == "not_supported"


@pytest.mark.acceptance("A27")
def test_controller_without_evidence_store_cannot_certify(pending_result):
    lab, campaign, completion = pending_result
    from kestrel.controller import Controller
    with Controller(lab.controller.path) as detached:
        with pytest.raises(StateError):
            detached.complete(campaign, **completion)


@pytest.mark.acceptance("A26")
def test_amended_campaign_executes_only_under_its_new_approval(pending_result):
    lab, parent, completion = pending_result
    lab.controller.complete(parent, **completion)
    revised = lab.controller.campaign(parent)["contract"]
    revised["brief"]["original"] = "Repeat under an explicitly amended question"
    child = lab.controller.amend(parent, revised)
    originals = lab.controller.tasks(parent)
    identities = {task["id"]: f"amended-{index}-{child}" for index, task in enumerate(originals)}
    for task in originals:
        spec = task["spec"]
        spec.update(id=identities[task["id"]], campaign_id=child,
                    dependencies=[identities[d] for d in spec["dependencies"]])
        lab.controller.add_task(child, spec)
    lab.controller.freeze(child)
    old_approval = lab.controller.attempts(parent)[0]["approval_id"]
    with pytest.raises(AuthorityError):
        lab.run(child, old_approval)
    assert lab.controller.attempts(child) == []
    approval = lab.approve(child, lab.controller.campaign(child)["digest"],
                           (lab.root / "operator.token").read_text())
    report = lab.run(child, approval)
    assert report["finding"] == "not_supported"
    assert report["evidence"][0]["attributable"] and report["evidence"][0]["consistent"]
    assert len(report["attempts"]) == 3
