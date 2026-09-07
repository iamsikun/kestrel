"""Non-executing external sidecars and complete, explicitly requested snapshots.

No Git process is invoked: repository configuration, hooks and filters are data.
The first pilot supports complete working-directory snapshots with an explicit
``snapshot_dirty=True`` opt-in. Clean revision resolution is not advertised.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .artifacts import ArtifactError, _directory_fd, _json, _relative, _safe_read


class ProjectError(ValueError):
    """A source or sidecar cannot be safely registered."""


class _SidecarLoader(yaml.SafeLoader):
    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            raise ProjectError("sidecar aliases are unsupported")
        return super().compose_node(parent, index)

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[str, Any]:
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ProjectError("sidecar keys must be unique strings")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _keys(value: Any, required: set[str], optional: set[str] | None = None) -> None:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - required - (optional or set()):
        raise ProjectError("missing or unknown sidecar fields")


def _slug(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}", value):
        raise ProjectError("invalid identifier")
    return value


def _disjoint(*roots: Path) -> None:
    for i, left in enumerate(roots):
        for right in roots[i + 1:]:
            if left == right or left.is_relative_to(right) or right.is_relative_to(left):
                raise ProjectError("framework, lab, runtime and source roots must be disjoint")


def load_sidecar(path: Path) -> dict[str, Any]:
    try:
        raw = _safe_read(path.parent, path.name, 1024 * 1024)
        value = yaml.load(raw, Loader=_SidecarLoader)
        _keys(value, {"protocol_version", "project_id", "source", "adapter", "environment", "capabilities", "data_policy"})
        if value["protocol_version"] != "0.1":
            raise ProjectError("unsupported protocol version")
        _slug(value["project_id"])
        source = value["source"]
        _keys(source, {"kind", "locator"}, {"revision"})
        if source["kind"] not in ("directory", "git") or not isinstance(source["locator"], str) or not Path(source["locator"]).is_absolute():
            raise ProjectError("source must have an absolute external directory locator")
        if "revision" in source and (not isinstance(source["revision"], str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", source["revision"])):
            raise ProjectError("unresolved source revision")
        adapter = value["adapter"]
        _keys(adapter, {"identity", "commands"})
        if not isinstance(adapter["identity"], str) or not re.fullmatch(r"[0-9a-f]{64}", adapter["identity"]):
            raise ProjectError("adapter identity must be a resolved SHA-256 digest")
        commands = adapter["commands"]
        if not isinstance(commands, dict) or not commands or len(commands) > 32:
            raise ProjectError("adapter commands must be a bounded mapping")
        for operation, argv in commands.items():
            _slug(operation)
            if not isinstance(argv, list) or not 1 <= len(argv) <= 64 or any(not isinstance(a, str) or not a or len(a) > 4096 or "\x00" in a for a in argv):
                raise ProjectError("commands must be bounded argument vectors")
        environment = value["environment"]
        _keys(environment, {"kind", "identity"})
        if environment["kind"] not in ("development", "image") or not isinstance(environment["identity"], str) or not 1 <= len(environment["identity"]) <= 512:
            raise ProjectError("invalid resolved environment")
        if environment["kind"] == "image" and not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", environment["identity"]):
            raise ProjectError("container image must be pinned by digest")
        if not isinstance(value["capabilities"], dict) or len(value["capabilities"]) > 64 or any(type(v) is not bool for v in value["capabilities"].values()):
            raise ProjectError("capabilities must be boolean declarations")
        policy = value["data_policy"]
        _keys(policy, {"classification", "external_model_access"})
        if policy["classification"] not in ("public_synthetic", "public", "restricted") or type(policy["external_model_access"]) is not bool:
            raise ProjectError("invalid data policy")
        return value
    except (yaml.YAMLError, UnicodeDecodeError, ArtifactError, OSError, RecursionError) as exc:
        raise ProjectError("invalid sidecar") from exc


class Projects:
    def __init__(self, framework_root: Path, lab_root: Path, runtime_root: Path,
                 max_source_bytes: int = 64 * 1024**2, max_files: int = 4096):
        self.framework_root = framework_root.resolve()
        self.lab_root = lab_root.resolve()
        self.runtime_root = runtime_root.resolve()
        _disjoint(self.framework_root, self.lab_root, self.runtime_root)
        if max_source_bytes <= 0 or max_files <= 0:
            raise ProjectError("source limits must be positive")
        self.max_source_bytes = max_source_bytes
        self.max_files = max_files
        self.lab_root.mkdir(parents=True, exist_ok=True)
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.snapshots = self.runtime_root / "snapshots"
        self.candidates = self.runtime_root / "candidates"
        for path in (self.snapshots, self.candidates):
            if path.is_symlink():
                raise ProjectError("unsafe runtime layout")
            path.mkdir(exist_ok=True)
        if (self.lab_root / "projects.sqlite").is_symlink():
            raise ProjectError("unsafe registry layout")
        self.db = sqlite3.connect(self.lab_root / "projects.sqlite")
        self.db.execute("CREATE TABLE IF NOT EXISTS projects (project_id TEXT PRIMARY KEY, record TEXT NOT NULL)")

    def close(self) -> None:
        self.db.close()

    def register(self, manifest: Path, snapshot_dirty: bool = False) -> dict[str, Any]:
        manifest = Path(manifest)
        canonical_manifest = manifest.resolve(strict=True)
        if not canonical_manifest.is_relative_to(self.lab_root):
            raise ProjectError("sidecars must reside in the external lab root")
        value = load_sidecar(manifest)
        source = Path(value["source"]["locator"]).resolve(strict=True)
        _disjoint(self.framework_root, self.lab_root, self.runtime_root, source)
        if not source.is_dir():
            raise ProjectError("source locator must be a directory")
        if snapshot_dirty is not True:
            raise ProjectError("clean revision resolution is unavailable; explicitly request a complete dirty snapshot")
        if "revision" in value["source"]:
            raise ProjectError("explicit directory snapshots cannot assert an unverified Git revision")
        existing = self.db.execute("SELECT record FROM projects WHERE project_id=?", (value["project_id"],)).fetchone()
        if existing:
            raise ProjectError("project identifier already registered; use a new baseline identity")
        entries, content, excluded, directories = self._scan(source)
        identity = {"format": "kestrel-source-v0.1", "files": entries,
                    "directories": directories, "excluded_metadata": excluded}
        source_digest = hashlib.sha256(_json(identity)).hexdigest()
        destination = self.snapshots / source_digest
        temporary = Path(tempfile.mkdtemp(prefix=".snapshot-", dir=self.snapshots))
        try:
            for directory in directories:
                (temporary / directory).mkdir(parents=True, exist_ok=True)
            for entry in entries:
                target = temporary / entry["path"]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content[entry["path"]])
                target.chmod(0o555 if entry["executable"] else 0o444)
            checked_entries, _, checked_excluded, checked_directories = self._scan(source)
            if (checked_entries, checked_excluded, checked_directories) != (entries, excluded, directories):
                raise ProjectError("source changed during snapshot; registration aborted")
            if destination.exists():
                self._verify_snapshot(destination, entries, directories)
            else:
                os.rename(temporary, destination)
            record = {"project_id": value["project_id"], "source_digest": source_digest,
                      "snapshot_path": str(destination), "manifest": value,
                      "source_root": str(source), "snapshot_mode": "complete_explicit",
                      "source_identity": identity}
            with self.db:
                self.db.execute("INSERT INTO projects VALUES(?,?)", (value["project_id"], _json(record).decode()))
            return record
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _scan(self, source: Path) -> tuple[list[dict[str, Any]], dict[str, bytes], list[str], list[str]]:
        entries, content, excluded, directories = [], {}, [], []
        total = 0
        count = 0

        def visit(fd: int, prefix: str) -> None:
            nonlocal total, count
            with os.scandir(fd) as children:
                names = []
                for child in children:
                    count += 1
                    if count > self.max_files:
                        raise ProjectError("source entry count exceeds bound")
                    names.append(child.name)
            for name in sorted(names):
                relative = f"{prefix}/{name}" if prefix else name
                if len(relative) > 1024 or len(Path(relative).parts) > 32:
                    raise ProjectError("source path exceeds bound")
                mode = os.stat(name, dir_fd=fd, follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    raise ProjectError("source symlinks are unsupported")
                if relative == ".git":
                    if not stat.S_ISDIR(mode):
                        raise ProjectError("worktree/submodule metadata is unsupported")
                    excluded.append(relative)
                    continue
                if name in (".git", ".gitmodules"):
                    raise ProjectError("nested repositories and submodules are unsupported")
                if stat.S_ISDIR(mode):
                    directory_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    directories.append(relative)
                    try:
                        visit(directory_fd, relative)
                    finally:
                        os.close(directory_fd)
                elif stat.S_ISREG(mode):
                    try:
                        data = _safe_read(source, relative, self.max_source_bytes - total)
                    except (ArtifactError, OSError) as exc:
                        raise ProjectError("unsafe or oversized source file") from exc
                    if data.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
                        raise ProjectError("unresolved Git LFS reference")
                    total += len(data)
                    if len(entries) >= self.max_files:
                        raise ProjectError("source file count exceeds bound")
                    entry = {"path": relative, "digest": hashlib.sha256(data).hexdigest(),
                             "size": len(data), "executable": bool(mode & 0o111)}
                    entries.append(entry)
                    content[relative] = data
                else:
                    raise ProjectError("source contains a special file")
        try:
            fd = _directory_fd(source)
            try:
                visit(fd, "")
            finally:
                os.close(fd)
        except (OSError, ArtifactError) as exc:
            raise ProjectError("source changed or contains unsafe paths") from exc
        entries.sort(key=lambda entry: entry["path"])
        if not entries:
            raise ProjectError("source is empty")
        return entries, content, excluded, sorted(directories)

    def get(self, project_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT record FROM projects WHERE project_id=?", (_slug(project_id),)).fetchone()
        if row is None:
            raise ProjectError("unknown project")
        return json.loads(row[0])

    def _verify_snapshot(self, path: Path, entries: list[dict[str, Any]], directories: list[str]) -> None:
        actual, _, excluded, actual_directories = self._scan(path)
        if actual != entries or excluded or actual_directories != directories:
            raise ProjectError("source snapshot integrity mismatch")

    def candidate(self, project_id: str, name: str,
                  changes: dict[str, bytes] | None = None) -> Path:
        record = self.get(project_id)
        name = _slug(name)
        source = Path(record["snapshot_path"])
        entries = record["source_identity"]["files"]
        directories = record["source_identity"]["directories"]
        self._verify_snapshot(source, entries, directories)
        changes = changes or {}
        if not isinstance(changes, dict) or len(changes) > self.max_files:
            raise ProjectError("candidate changes must be a bounded mapping")
        for relative, data in changes.items():
            try:
                parts = _relative(relative)
            except ArtifactError as exc:
                raise ProjectError("unsafe candidate path") from exc
            if ".git" in parts or ".gitmodules" in parts or not isinstance(data, bytes):
                raise ProjectError("unsupported candidate change")
        parent = self.candidates / project_id
        parent.mkdir(exist_ok=True)
        if parent.is_symlink():
            raise ProjectError("unsafe candidate root")
        destination = parent / name
        if destination.exists() or destination.is_symlink():
            raise ProjectError("candidate already exists")
        temporary = Path(tempfile.mkdtemp(prefix=".candidate-", dir=parent))
        total = 0
        try:
            for directory in directories:
                (temporary / directory).mkdir(parents=True, exist_ok=True)
            files = {entry["path"]: entry for entry in entries}
            if len(files.keys() | changes.keys()) > self.max_files:
                raise ProjectError("candidate file count exceeds bound")
            for relative in sorted(files.keys() | changes.keys()):
                data = changes[relative] if relative in changes else _safe_read(source, relative, self.max_source_bytes)
                total += len(data)
                if total > self.max_source_bytes:
                    raise ProjectError("candidate source byte bound exceeded")
                target = temporary / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o755 if files.get(relative, {}).get("executable") else 0o644)
            os.rename(temporary, destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return destination
