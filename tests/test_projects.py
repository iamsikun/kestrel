import hashlib
import json
import os
from pathlib import Path

import pytest

from kestrel.projects import ProjectError, Projects, load_sidecar


@pytest.fixture
def project(tmp_path):
    source = tmp_path / "external"
    source.mkdir()
    (source / "adapter.py").write_text("print('fixture')\n")
    (source / "config.json").write_text('{"offset":0}')
    lab = tmp_path / "lab"
    lab.mkdir()
    manifest = lab / "sidecar.json"
    value = {
        "protocol_version": "0.1", "project_id": "numerical",
        "source": {"kind": "directory", "locator": str(source)},
        "adapter": {"identity": hashlib.sha256((source / "adapter.py").read_bytes()).hexdigest(),
                    "commands": {"execute": ["python", "adapter.py"]}},
        "environment": {"kind": "development", "identity": "trusted-synthetic-v1"},
        "capabilities": {"exact_resume": False},
        "data_policy": {"classification": "public_synthetic", "external_model_access": False},
    }
    manifest.write_text(json.dumps(value))
    registry = Projects(Path(__file__).resolve().parents[1], lab, tmp_path / "runtime")
    yield registry, source, manifest, value
    registry.close()


@pytest.mark.acceptance("A05")
@pytest.mark.acceptance("A08")
def test_nonexecuting_complete_source_registration(project, tmp_path):
    registry, source, manifest, value = project
    sentinel = tmp_path / "must-not-exist"
    trap = f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\n"
    (source / "setup.py").write_text(trap)
    (source / "__init__.py").write_text(trap)
    (source / ".git" / "hooks").mkdir(parents=True)
    (source / ".git" / "hooks" / "post-checkout").write_text(trap)
    (source / ".git" / "config").write_text(f"[core]\nfsmonitor = touch {sentinel}\n[filter \"trap\"]\nclean = touch {sentinel}\n")
    (source / ".gitattributes").write_text("* filter=trap\n")
    (source / ".gitignore").write_text("ignored.dat\n")
    (source / "ignored.dat").write_bytes(b"ignored but fully accounted")
    (source / "untracked.txt").write_text("untracked")
    before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    with pytest.raises(ProjectError, match="explicitly"):
        registry.register(manifest)
    record = registry.register(manifest, snapshot_dirty=True)
    assert not sentinel.exists()
    assert before == {str(p.relative_to(source)): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    assert record["source_identity"]["excluded_metadata"] == [".git"]
    snapshot = Path(record["snapshot_path"])
    assert not (snapshot / ".git").exists()
    assert (snapshot / "ignored.dat").read_bytes() == b"ignored but fully accounted"
    assert (snapshot / "untracked.txt").read_text() == "untracked"
    reopened = Projects(registry.framework_root, registry.lab_root, registry.runtime_root)
    assert reopened.get(value["project_id"]) == record
    reopened.close()


def test_candidates_conflicting_file_remain_independent(project):
    registry, source, manifest, _ = project
    record = registry.register(manifest, snapshot_dirty=True)
    first = registry.candidate("numerical", "a", {"config.json": b'{"offset":1}'})
    second = registry.candidate("numerical", "b", {"config.json": b'{"offset":2}'})
    (first / "config.json").write_text("changed again")
    assert (second / "config.json").read_bytes() == b'{"offset":2}'
    assert (source / "config.json").read_bytes() == b'{"offset":0}'
    assert (Path(record["snapshot_path"]) / "config.json").read_bytes() == b'{"offset":0}'
    assert len({(p / "config.json").stat().st_ino for p in (first, second, source, Path(record["snapshot_path"]))}) == 4
    with pytest.raises(ProjectError, match="already exists"):
        registry.candidate("numerical", "a")


@pytest.mark.parametrize("overlap", ["same", "nested", "ancestor", "symlink"])
def test_reject_conflicting_roots(tmp_path, overlap):
    framework = tmp_path / "framework"
    framework.mkdir()
    lab = tmp_path / "lab"
    runtime = tmp_path / "runtime"
    if overlap == "same":
        runtime = framework
    elif overlap == "nested":
        runtime = framework / "state"
    elif overlap == "ancestor":
        runtime = tmp_path
    else:
        runtime.symlink_to(framework, target_is_directory=True)
    with pytest.raises(ProjectError, match="disjoint"):
        Projects(framework, lab, runtime)


def test_source_alias_into_framework_rejected_without_inspection(project):
    registry, source, manifest, value = project
    (source / "alias").symlink_to(registry.framework_root, target_is_directory=True)
    value["source"]["locator"] = str(source / "alias")
    manifest.write_text(json.dumps(value))
    with pytest.raises(ProjectError, match="disjoint"):
        registry.register(manifest, snapshot_dirty=True)


@pytest.mark.parametrize("attack", ["symlink", "fifo", "submodule", "lfs", "hardlink"])
def test_unsafe_sources_rejected(project, tmp_path, attack):
    registry, source, manifest, _ = project
    if attack == "symlink":
        (source / "escape").symlink_to(tmp_path)
    elif attack == "fifo":
        os.mkfifo(source / "fifo")
    elif attack == "submodule":
        (source / ".gitmodules").write_text("[submodule 'trap']")
    elif attack == "lfs":
        (source / "large.dat").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:unknown\n")
    else:
        os.link(source / "config.json", source / "linked")
    with pytest.raises(ProjectError):
        registry.register(manifest, snapshot_dirty=True)
    assert registry.db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0


@pytest.mark.acceptance("A08")
def test_source_changes_change_identity_and_snapshot_tampering_fails(project):
    registry, source, manifest, value = project
    first = registry.register(manifest, snapshot_dirty=True)
    (source / "config.json").write_text('{"offset":3}')
    (source / "new.txt").write_text("tracked or untracked bytes count")
    value["project_id"] = "numerical-two"
    manifest.write_text(json.dumps(value))
    second = registry.register(manifest, snapshot_dirty=True)
    assert first["source_digest"] != second["source_digest"]
    assert (Path(second["snapshot_path"]) / "config.json").read_text() == '{"offset":3}'
    assert (Path(second["snapshot_path"]) / "new.txt").read_text() == "tracked or untracked bytes count"
    stored = Path(first["snapshot_path"]) / "config.json"
    stored.chmod(0o644)
    stored.write_text("corrupt")
    with pytest.raises(ProjectError, match="integrity"):
        registry.candidate("numerical", "tampered")


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a/../escape", "a\\evil", ".git/config", "a.partial"])
def test_candidate_path_traversal_rejected(project, path):
    registry, _, manifest, _ = project
    registry.register(manifest, snapshot_dirty=True)
    with pytest.raises(ProjectError):
        registry.candidate("numerical", "bad", {path: b"bad"})


def test_sidecar_strict_unknown_fields_and_duplicates(project):
    _, _, manifest, value = project
    value["authority"] = "operator"
    manifest.write_text(json.dumps(value))
    with pytest.raises(ProjectError):
        load_sidecar(manifest)
    manifest.write_text('project_id: x\nproject_id: y\n')
    with pytest.raises(ProjectError, match="unique"):
        load_sidecar(manifest)
    manifest.write_text("a: &a [1]\nb: *a\n")
    with pytest.raises(ProjectError, match="aliases"):
        load_sidecar(manifest)


def test_unverified_revision_fails_closed(project):
    registry, _, manifest, value = project
    value["source"]["kind"] = "git"
    value["source"]["revision"] = "a" * 40
    manifest.write_text(json.dumps(value))
    with pytest.raises(ProjectError, match="unverified Git"):
        registry.register(manifest, snapshot_dirty=True)


def test_source_and_candidate_byte_limits(project):
    registry, source, manifest, _ = project
    registry.max_source_bytes = 64
    registry.register(manifest, snapshot_dirty=True)
    with pytest.raises(ProjectError, match="byte bound"):
        registry.candidate("numerical", "too-large", {"config.json": b"x" * 65})
    assert not (registry.candidates / "numerical" / "too-large").exists()


def test_empty_directories_are_preserved_and_identified(project):
    registry, source, manifest, value = project
    first = registry.register(manifest, snapshot_dirty=True)
    (source / "empty" / "nested").mkdir(parents=True)
    value["project_id"] = "with-empty-directory"
    manifest.write_text(json.dumps(value))
    second = registry.register(manifest, snapshot_dirty=True)
    assert second["source_digest"] != first["source_digest"]
    assert (Path(second["snapshot_path"]) / "empty" / "nested").is_dir()
    candidate = registry.candidate(value["project_id"], "a")
    assert (candidate / "empty" / "nested").is_dir()


def test_source_mutation_during_snapshot_aborts(project, monkeypatch):
    registry, source, manifest, _ = project
    scan = registry._scan
    calls = 0

    def mutating_scan(path):
        nonlocal calls
        calls += 1
        result = scan(path)
        if calls == 1:
            (source / "config.json").write_text("changed during snapshot")
        return result

    monkeypatch.setattr(registry, "_scan", mutating_scan)
    with pytest.raises(ProjectError, match="changed during snapshot"):
        registry.register(manifest, snapshot_dirty=True)
    assert registry.db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    assert list(registry.snapshots.iterdir()) == []


def test_empty_directory_count_is_bounded(project):
    registry, source, manifest, _ = project
    registry.max_files = 4
    for number in range(4):
        (source / f"directory-{number}").mkdir()
    with pytest.raises(ProjectError, match="entry count"):
        registry.register(manifest, snapshot_dirty=True)
