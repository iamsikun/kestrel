import hashlib
from pathlib import Path

import pytest

from kestrel.application import Lab, demo, framework_root
from kestrel.artifacts import Artifacts
from kestrel.contracts import canonical
from kestrel.fixtures import generate


@pytest.mark.acceptance("A02")
@pytest.mark.acceptance("A03")
@pytest.mark.acceptance("A04")
@pytest.mark.acceptance("A23")
@pytest.mark.acceptance("A25")
@pytest.mark.acceptance("A27")
def test_real_offline_campaigns_and_negative_findings(tmp_path):
    result = demo(tmp_path / "demo")
    assert result["provider_calls"] == 0
    assert len(result["reports"]) == 2
    assert not Path(result["root"]).is_relative_to(framework_root())
    for report, difference in zip(result["reports"], [4.0, 2.0], strict=True):
        assert report["state"] == "COMPLETE"
        assert report["execution"] == "succeeded"
        assert report["validity"] == "valid"
        assert report["finding"] == "not_supported"
        assert report["assurance"] == "independently_recomputed"
        assert report["evidence"][0]["analysis"]["difference"] == difference
        assert len(report["attempts"]) == 3
        assert sum(a["backend"] == "development" for a in report["attempts"]) == 2
        assert sum(a["backend"] == "offline-agent" for a in report["attempts"]) == 1
        assert report["budget_reserved"]["attempts"] == 3
        assert report["budget_reserved"]["provider_calls"] == 0
        assert all(a["stopped_confirmed"] and not a["resources_held"] for a in report["attempts"])
    assert Path(result["evidence_packet"]).is_file()
    imported = Artifacts(tmp_path / "imported")
    try:
        records = imported.import_bundle(Path(result["evidence_packet"]))
        assert all(record["assurance"] == "imported" for record in records)
    finally:
        imported.close()


@pytest.mark.acceptance("A06")
def test_lab_and_generated_projects_cannot_be_inside_framework():
    with pytest.raises(ValueError):
        Lab.initialize(framework_root() / "forbidden-lab")
    with pytest.raises(ValueError):
        generate(framework_root() / "forbidden-projects", framework_root())


@pytest.mark.acceptance("A36")
def test_invalidation_reaches_report(tmp_path):
    result = demo(tmp_path / "demo")
    campaign_id = result["reports"][0]["campaign_id"]
    with Lab(Path(result["root"])) as lab:
        contract = lab.controller.campaign(campaign_id)["contract"]
        lab.store.invalidate(contract["evaluator"], "Synthetic evaluator regression found")
        report = lab.report(campaign_id)
        assert report["validity"] == "invalid"
        assert report["assurance"] == "unverified"
        assert report["finding"] == "inconclusive"
        assert report["evidence"][0]["analysis"]["validity"] == "invalid"


@pytest.mark.acceptance("A40")
def test_report_values_come_from_immutable_evidence(tmp_path):
    result = demo(tmp_path / "demo")
    report = result["reports"][0]
    evidence = report["evidence"][0]
    assert hashlib.sha256(canonical(evidence["analysis"])).hexdigest() == evidence["digest"]


@pytest.mark.acceptance("A39")
@pytest.mark.acceptance("A45")
def test_campaign_export_omits_unrelated_restricted_artifacts(tmp_path):
    result = demo(tmp_path / "demo")
    with Lab(Path(result["root"])) as lab:
        secret = lab.store.put_bytes(b"unrelated synthetic restricted sentinel", producer="other-project",
                                    classification="restricted")
        bundle = lab.export(result["reports"][0]["campaign_id"], tmp_path / "one-campaign.zip")
        imported = Artifacts(tmp_path / "isolated-import")
        try:
            records = imported.import_bundle(bundle)
            assert secret["digest"] not in {r["digest"] for r in records}
            assert result["reports"][1]["contract_digest"] not in {r["digest"] for r in records}
        finally:
            imported.close()


@pytest.mark.acceptance("A36")
@pytest.mark.acceptance("A39")
def test_invalidated_evidence_exports_as_stale_history(tmp_path):
    result = demo(tmp_path / "demo")
    campaign_id = result["reports"][0]["campaign_id"]
    with Lab(Path(result["root"])) as lab:
        contract = lab.controller.campaign(campaign_id)["contract"]
        lab.store.invalidate(contract["evaluator"], "Synthetic review found invalid evaluator")
        bundle = lab.export(campaign_id, tmp_path / "stale-history.zip")
        imported = Artifacts(tmp_path / "historical-import")
        try:
            records = imported.import_bundle(bundle)
            assert any(record["status"] == "stale" for record in records)
            assert imported.get(contract["evaluator"])["status"] == "invalid"
            assert all(record["assurance"] == "imported" for record in records)
        finally:
            imported.close()


@pytest.mark.acceptance("A06")
def test_controller_database_symlink_is_rejected_without_touching_target(tmp_path):
    root = tmp_path / "lab"
    with Lab.initialize(root):
        pass
    database = root / "runtime" / "controller.sqlite"
    database.rename(root / "runtime" / "original.sqlite")
    target = tmp_path / "unrelated.sqlite"
    database.symlink_to(target)
    with pytest.raises(ValueError, match="symlinks"):
        Lab(root)
    assert not target.exists()


@pytest.mark.acceptance("A40")
def test_forged_analysis_hash_cannot_acquire_comparison_authority(tmp_path):
    result = demo(tmp_path / "demo")
    campaign_id = result["reports"][0]["campaign_id"]
    with Lab(Path(result["root"])) as lab:
        fake = lab.store.put_bytes(canonical({"baseline": 99, "treatment": 0, "difference": -99}),
                                  producer="worker:forged-analysis")
        with pytest.raises(ValueError, match="registered comparison"):
            lab.quantitative_claim(campaign_id, {"analysis": fake["digest"], "field": "difference", "value": -99})
        analysis = result["reports"][0]["evidence"][0]["digest"]
        assert lab.quantitative_claim(campaign_id, {"analysis": analysis, "field": "difference", "value": 4})["assurance"] == "independently_recomputed"


@pytest.mark.acceptance("A39")
def test_imported_artifacts_do_not_gain_assurance_from_restored_controller(tmp_path):
    from kestrel.controller import Controller

    result = demo(tmp_path / "source")
    campaign_id = result["reports"][0]["campaign_id"]
    backup = tmp_path / "controller-backup.sqlite"
    with Lab(Path(result["root"])) as source:
        packet = source.export(campaign_id, tmp_path / "packet.zip")
        source.controller.backup(backup)
    with Lab.initialize(tmp_path / "destination") as target:
        target.store.import_bundle(packet)
        target.controller.close()
        database = target.root / "runtime" / "controller.sqlite"
        database.rename(database.with_name("initial.sqlite"))
        target.controller = Controller.restore(backup, database)
        report = target.report(campaign_id)
        assert report["assurance"] == "imported"
        assert report["finding"] == "inconclusive"
        assert report["evidence"][0]["analysis"]["assurance"] == "imported"


@pytest.mark.acceptance("A39")
@pytest.mark.acceptance("A45")
def test_identical_results_do_not_merge_campaign_export_lineage(tmp_path):
    result = demo(tmp_path / "demo")
    first = result["reports"][0]
    with Lab(Path(result["root"])) as lab:
        second = lab.propose("numerical", "Unrelated synthetic brief that must stay in its own packet")
        contract = lab.controller.campaign(second)
        approval = lab.approve(second, contract["digest"], (lab.root / "operator.token").read_text())
        other = lab.run(second, approval)
        assert first["evidence"][0]["analysis"]["difference"] == other["evidence"][0]["analysis"]["difference"]
        bundle = lab.export(first["campaign_id"], tmp_path / "first-only.zip")
        imported = Artifacts(tmp_path / "imported-identical")
        try:
            records = imported.import_bundle(bundle)
            assert contract["digest"] not in {record["digest"] for record in records}
            for record in records:
                assert second.encode() not in imported.read(record["digest"])
        finally:
            imported.close()


@pytest.mark.acceptance("A25")
def test_cancel_unstarted_campaign_has_durable_cancelled_outcome(tmp_path):
    with Lab.initialize(tmp_path / "lab") as lab:
        manifest = generate(tmp_path / "projects", framework_root())[0]
        project = lab.register(manifest, snapshot_dirty=True)
        campaign = lab.propose(project["project_id"], "Cancel before authorizing any execution")
        report = lab.cancel(campaign)
        assert report["state"] == "COMPLETE"
        assert report["execution"] == "cancelled"
        assert report["validity"] == "incomplete"
        assert report["finding"] == "inconclusive"
        assert report["attempts"] == []
        assert all(task["state"] == "CANCELLED" for task in lab.controller.tasks(campaign))
