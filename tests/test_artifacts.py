import hashlib
import json
import os
import zipfile

import pytest

from kestrel.artifacts import ArtifactError, Artifacts


@pytest.fixture
def store(tmp_path):
    result = Artifacts(tmp_path / "store", max_artifact_bytes=4096, max_total_bytes=16384)
    yield result
    result.close()


def test_atomic_ingest_digests_and_producers(store, tmp_path):
    outputs = tmp_path / "output"
    outputs.mkdir()
    (outputs / "data.json").write_bytes(b'{"prediction":2}')
    with pytest.raises(ArtifactError, match="completion"):
        store.ingest(outputs, "data.json", producer="attempt-1")
    artifact = store.ingest(outputs, "data.json", producer="attempt-1", completed=True)
    assert artifact["digest"] == hashlib.sha256(b'{"prediction":2}').hexdigest()
    assert store.read(artifact["digest"]) == b'{"prediction":2}'
    (outputs / "data.json").write_bytes(b"changed")
    assert store.verify(artifact["digest"])
    store.put_bytes(b'{"prediction":2}', producer="attempt-2")
    assert [p["producer"] for p in store.get(artifact["digest"])["productions"]] == ["attempt-1", "attempt-2"]


@pytest.mark.parametrize("attack", ["traversal", "absolute", "symlink", "symlink-dir", "hardlink", "fifo", "partial", "marker", "missing", "digest", "oversize"])
def test_artifact_attacks_never_publish(store, tmp_path, attack):
    output = tmp_path / "output"
    output.mkdir()
    (tmp_path / "outside").write_text("outside")
    (output / "safe").write_text("ok")
    path = "safe"
    options = {}
    if attack == "traversal":
        path = "../outside"
    elif attack == "absolute":
        path = str(tmp_path / "outside")
    elif attack == "symlink":
        (output / "link").symlink_to(tmp_path / "outside")
        path = "link"
    elif attack == "symlink-dir":
        (output / "dir").symlink_to(tmp_path, target_is_directory=True)
        path = "dir/outside"
    elif attack == "hardlink":
        os.link(tmp_path / "outside", output / "link")
        path = "link"
    elif attack == "fifo":
        os.mkfifo(output / "pipe")
        path = "pipe"
    elif attack == "partial":
        (output / "data.partial").write_text("partial")
        path = "data.partial"
    elif attack == "marker":
        (output / ".kestrel-incomplete").touch()
    elif attack == "missing":
        path = "not-produced"
    elif attack == "digest":
        options["expected_digest"] = "0" * 64
    else:
        (output / "safe").write_bytes(b"x" * 4097)
    with pytest.raises(ArtifactError):
        store.ingest(output, path, producer="malicious-attempt", completed=True, **options)
    assert store.db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    assert list(store.objects.iterdir()) == []


def test_cas_tamper_detected(store):
    artifact = store.put_bytes(b"genuine", producer="evaluator")
    path = store.objects / artifact["digest"]
    path.chmod(0o644)
    path.write_bytes(b"tampered")
    with pytest.raises(ArtifactError, match="hash mismatch"):
        store.read(artifact["digest"])


def test_budget_includes_failed_duplicate_and_new_publications(store):
    store.max_total_bytes = 6
    first = store.put_bytes(b"1234", producer="one")
    store.put_bytes(b"1234", producer="retry")
    with pytest.raises(ArtifactError, match="budget"):
        store.put_bytes(b"567", producer="two")
    assert store.read(first["digest"]) == b"1234"
    assert store.db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1


def test_invalidation_reaches_observation_analysis_and_claim_without_deletion(store):
    evaluator = store.put_bytes(b"evaluator-v1", producer="operator")
    observation = store.put_bytes(b"loss=4", producer="evaluation", lineage=[evaluator["digest"]])
    analysis = store.put_bytes(b"difference=3", producer="analysis", lineage=[observation["digest"]])
    claim = store.put_bytes(b"treatment unsupported", producer="report", lineage=[analysis["digest"]])
    affected = store.invalidate(evaluator["digest"], "independent audit found evaluator defect")
    assert set(affected) == {x["digest"] for x in (evaluator, observation, analysis, claim)}
    assert store.get(evaluator["digest"])["status"] == "invalid"
    assert store.get(claim["digest"])["status"] == "stale"
    assert store.read(claim["digest"]) == b"treatment unsupported"
    with pytest.raises(ArtifactError, match="invalid input"):
        store.put_bytes(b"new claim", producer="report", lineage=[analysis["digest"]])


def test_retention_preserves_required_ancestors_and_tombstones(store):
    source = store.put_bytes(b"input", producer="input", retain=False)
    claim = store.put_bytes(b"claim", producer="report", lineage=[source["digest"]])
    with pytest.raises(ArtifactError, match="required"):
        store.collect(source["digest"], reason="quota")
    with pytest.raises(ArtifactError, match="required"):
        store.collect(claim["digest"], reason="quota")
    scratch = store.put_bytes(b"scratch", producer="diagnostic", retain=False)
    store.collect(scratch["digest"], reason="expired diagnostic retention")
    assert store.get(scratch["digest"])["status"] == "deleted"
    assert store.get(scratch["digest"])["events"][-1]["reason"] == "expired diagnostic retention"
    with pytest.raises(ArtifactError, match="tombstone"):
        store.read(scratch["digest"])


def test_export_import_preserves_history_but_never_promotes_trust(store, tmp_path):
    source = store.put_bytes(b"input", producer="input")
    claim = store.put_bytes(b"result", producer="independent-evaluator", lineage=[source["digest"]], assurance="independently_recomputed")
    store.invalidate(source["digest"], "bad data")
    scratch = store.put_bytes(b"scratch", producer="test", retain=False)
    store.collect(scratch["digest"], reason="expired")
    bundle = store.export_bundle(tmp_path / "bundle.zip")
    restored = Artifacts(tmp_path / "restored")
    imported = restored.import_bundle(bundle)
    assert len(imported) == 3
    record = restored.get(claim["digest"])
    assert record["assurance"] == "imported"
    assert record["source_assurance"] == "independently_recomputed"
    assert record["status"] == "stale"
    assert record["lineage"] == [source["digest"]]
    assert restored.get(scratch["digest"])["status"] == "deleted"
    assert "bad data" in restored.get(source["digest"])["events"][0]["reason"]
    restored.close()


@pytest.mark.parametrize("attack", ["bytes", "extra", "missing", "cycle", "unknown-field", "size", "media-type", "events", "productions"])
def test_tampered_bundle_is_rejected_before_any_publication(store, tmp_path, attack):
    artifact = store.put_bytes(b"evidence", producer="evaluation")
    bundle = store.export_bundle(tmp_path / "bundle.zip")
    with zipfile.ZipFile(bundle) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    if attack == "bytes":
        entries[f"objects/{artifact['digest']}"] = b"tampered"
    elif attack == "extra":
        entries["../escape"] = b"bad"
    elif attack == "missing":
        del entries[f"objects/{artifact['digest']}"]
    else:
        manifest = json.loads(entries["manifest.json"])
        row = manifest["artifacts"][0]
        if attack == "cycle":
            row["lineage"] = [row["digest"]]
        elif attack == "unknown-field":
            row["operator_approval"] = True
        elif attack == "media-type":
            row["media_type"] = {"not": "a string"}
        elif attack == "events":
            row["events"] = [{"inject": "authority"}]
        elif attack == "productions":
            row["productions"] = [{"producer": "liar", "assurance": "self-approved"}]
        else:
            row["size"] = 10**12
        entries["manifest.json"] = json.dumps(manifest).encode()
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    restored = Artifacts(tmp_path / "restored")
    with pytest.raises(ArtifactError):
        restored.import_bundle(bad)
    assert restored.db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    assert list(restored.objects.iterdir()) == []
    restored.close()


def test_existing_import_cannot_change_existing_certification(store, tmp_path):
    record = store.put_bytes(b"verified", producer="evaluator", assurance="independently_recomputed")
    bundle = store.export_bundle(tmp_path / "bundle.zip")
    with pytest.raises(ArtifactError, match="existing evidence"):
        store.import_bundle(bundle)
    assert store.get(record["digest"])["assurance"] == "independently_recomputed"


def test_unknown_lineage_is_rejected_before_publication(store):
    with pytest.raises(ArtifactError, match="unknown artifact"):
        store.put_bytes(b"invented claim", producer="report", lineage=["0" * 64])
    assert store.db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0


def test_missing_output_root_rejected(store, tmp_path):
    with pytest.raises(ArtifactError, match="output root"):
        store.ingest(tmp_path / "missing", "file", producer="attempt", completed=True)


def test_artifact_count_is_bounded_even_for_tiny_artifacts(store):
    store.max_artifacts = 1
    store.put_bytes(b"", producer="empty")
    with pytest.raises(ArtifactError, match="count budget"):
        store.put_bytes(b"a", producer="one")


def test_duplicate_json_fields_in_bundle_rejected(store, tmp_path):
    bundle = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", '{"schema_version":"0.1","schema_version":"9.0","artifacts":[]}')
    with pytest.raises(ArtifactError, match="duplicate JSON"):
        store.import_bundle(bundle)
