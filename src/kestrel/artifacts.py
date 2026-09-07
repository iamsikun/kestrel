"""Bounded artifact ingestion and portable evidence, without executing inputs.

The caller must establish worker termination before setting ``completed=True``.
Filesystem ownership and an isolated worker namespace protect the store in a
deployment; the development store does not claim to isolate hostile processes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


class ArtifactError(ValueError):
    """Untrusted content or an evidence operation violated a bound."""


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _relative(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ArtifactError("invalid relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ("", ".", "..") for p in value.split("/")):
        raise ArtifactError("unsafe relative path")
    if any(part.endswith((".partial", ".incomplete")) for part in path.parts):
        raise ArtifactError("incomplete output path")
    return path.parts


def _directory_fd(root: Path) -> int:
    """Open canonical directory components without following a swapped symlink."""
    if root.is_symlink():
        raise ArtifactError("symlink root")
    root = root.resolve(strict=True)
    fd = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _safe_read(root: Path, relative: str, maximum: int) -> bytes:
    parts = _relative(relative)
    try:
        fd = _directory_fd(root)
    except OSError as exc:
        raise ArtifactError("unsafe or missing output root") from exc
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise ArtifactError("artifact must be a regular file with one link")
            if before.st_size > maximum:
                raise ArtifactError("artifact exceeds byte limit")
            chunks = []
            size = 0
            while block := os.read(file_fd, min(65536, maximum + 1 - size)):
                chunks.append(block)
                size += len(block)
                if size > maximum:
                    raise ArtifactError("artifact exceeds byte limit")
            after = os.fstat(file_fd)
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            if any(getattr(before, f) != getattr(after, f) for f in fields):
                raise ArtifactError("artifact changed during ingestion")
            if size != after.st_size:
                raise ArtifactError("incomplete artifact read")
            return b"".join(chunks)
        finally:
            os.close(file_fd)
    except OSError as exc:
        raise ArtifactError(f"unsafe or missing artifact: {relative}") from exc
    finally:
        os.close(fd)


def _digest(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ArtifactError("invalid SHA-256 digest")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactError("duplicate JSON field")
        result[key] = value
    return result


class Artifacts:
    def __init__(self, root: Path, max_artifact_bytes: int = 16 * 1024**2,
                 max_total_bytes: int = 256 * 1024**2, max_artifacts: int = 4096):
        if max_artifact_bytes <= 0 or max_total_bytes <= 0 or not 1 <= max_artifacts <= 4096:
            raise ArtifactError("byte limits must be positive")
        if root.is_symlink():
            raise ArtifactError("symlink artifact root")
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.objects = self.root / "objects"
        self.objects.mkdir(exist_ok=True)
        if self.objects.is_symlink() or (self.root / "evidence.sqlite").is_symlink():
            raise ArtifactError("unsafe artifact store layout")
        self.max_artifact_bytes = max_artifact_bytes
        self.max_total_bytes = max_total_bytes
        self.max_artifacts = max_artifacts
        self.db = sqlite3.connect(self.root / "evidence.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS artifacts (
                digest TEXT PRIMARY KEY, size INTEGER NOT NULL, media_type TEXT NOT NULL,
                classification TEXT NOT NULL, assurance TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'valid', retained INTEGER NOT NULL,
                source_assurance TEXT
            );
            CREATE TABLE IF NOT EXISTS productions (
                digest TEXT NOT NULL REFERENCES artifacts, producer TEXT NOT NULL,
                assurance TEXT NOT NULL, UNIQUE(digest, producer, assurance)
            );
            CREATE TABLE IF NOT EXISTS edges (
                child TEXT REFERENCES artifacts, parent TEXT REFERENCES artifacts,
                PRIMARY KEY(child,parent)
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, digest TEXT NOT NULL REFERENCES artifacts,
                kind TEXT NOT NULL, reason TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
        """)

    def close(self) -> None:
        self.db.close()

    def ingest(self, output_root: Path, relative_path: str, *, producer: str,
               completed: bool = False, expected_digest: str | None = None,
               **metadata: Any) -> dict[str, Any]:
        if completed is not True:
            raise ArtifactError("worker completion has not been established")
        for marker in (".partial", ".incomplete", ".kestrel-incomplete"):
            if (output_root / marker).exists() or (output_root / marker).is_symlink():
                raise ArtifactError("output directory is incomplete")
        data = _safe_read(output_root, relative_path, self.max_artifact_bytes)
        calculated = hashlib.sha256(data).hexdigest()
        if expected_digest is not None and calculated != _digest(expected_digest):
            raise ArtifactError("worker digest mismatch")
        return self.put_bytes(data, producer=producer, **metadata)

    def put_bytes(self, data: bytes, *, producer: str,
                  media_type: str = "application/octet-stream",
                  lineage: Sequence[str] = (), classification: str = "public_synthetic",
                  assurance: str = "traceable", retain: bool = True,
                  historical: bool = False) -> dict[str, Any]:
        if not isinstance(data, bytes) or len(data) > self.max_artifact_bytes:
            raise ArtifactError("artifact exceeds byte limit or is not bytes")
        if not isinstance(producer, str) or not producer or len(producer) > 512:
            raise ArtifactError("invalid producer")
        if not isinstance(media_type, str) or not media_type or len(media_type) > 256:
            raise ArtifactError("invalid media type")
        if classification not in ("public_synthetic", "public", "restricted"):
            raise ArtifactError("invalid data classification")
        if assurance not in ("imported", "traceable", "independently_recomputed", "replicated"):
            raise ArtifactError("invalid assurance")
        if type(historical) is not bool or (historical and assurance != "traceable"):
            raise ArtifactError("historical export records cannot acquire scientific assurance")
        if type(retain) is not bool or isinstance(lineage, (str, bytes)) or len(lineage) > 1024:
            raise ArtifactError("invalid retention or lineage")
        digest = hashlib.sha256(data).hexdigest()
        parents = sorted({_digest(p) for p in lineage})
        if len(parents) > 1024 or digest in parents:
            raise ArtifactError("invalid lineage")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            stale_parent = False
            for parent in parents:
                record = self.get(parent)
                if record["classification"] == "restricted" and classification != "restricted":
                    raise ArtifactError("restricted parent data cannot lose its classification through lineage")
                if record["status"] != "valid":
                    if not historical or record["status"] == "deleted":
                        raise ArtifactError("cannot derive valid evidence from unavailable or invalid input")
                    stale_parent = True
                self.read(parent)
                reachable = self.db.execute("""WITH RECURSIVE ancestors(d) AS (
                    SELECT parent FROM edges WHERE child=? UNION
                    SELECT parent FROM edges JOIN ancestors ON child=ancestors.d)
                    SELECT 1 FROM ancestors WHERE d=?""", (parent, digest)).fetchone()
                if reachable:
                    raise ArtifactError("cyclic lineage")
            old = self.db.execute("SELECT * FROM artifacts WHERE digest=?", (digest,)).fetchone()
            if old:
                self.read(digest)
                if old["classification"] != classification:
                    raise ArtifactError("content classification conflict")
                original_parents = [row[0] for row in self.db.execute(
                    "SELECT parent FROM edges WHERE child=? ORDER BY parent", (digest,)
                )]
                if parents != original_parents:
                    raise ArtifactError("conflicting content provenance; create a distinct occurrence record")
            else:
                if self.db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] >= self.max_artifacts:
                    raise ArtifactError("artifact record count budget exhausted")
                total = self.db.execute("SELECT COALESCE(SUM(size),0) FROM artifacts WHERE status!='deleted'").fetchone()[0]
                if total + len(data) > self.max_total_bytes:
                    raise ArtifactError("artifact store byte budget exhausted")
                self._write_object(digest, data)
                self.db.execute("INSERT INTO artifacts(digest,size,media_type,classification,assurance,retained) VALUES(?,?,?,?,?,?)",
                                (digest, len(data), media_type, classification, assurance, int(retain)))
            self.db.execute("INSERT OR IGNORE INTO productions VALUES(?,?,?)", (digest, producer, assurance))
            self.db.executemany("INSERT OR IGNORE INTO edges VALUES(?,?)", [(digest, p) for p in parents])
            if stale_parent:
                affected = self.db.execute("""WITH RECURSIVE affected(d) AS (
                    SELECT ? UNION SELECT child FROM edges JOIN affected ON parent=affected.d)
                    SELECT d FROM affected""", (digest,)).fetchall()
                for row in affected:
                    self.db.execute("UPDATE artifacts SET status='stale' WHERE digest=? AND status='valid'", (row[0],))
                    self.db.execute("INSERT INTO events(digest,kind,reason) VALUES(?,'stale',?)",
                                    (row[0], "historical record references invalidated evidence"))
            if retain:
                self.db.execute("UPDATE artifacts SET retained=1 WHERE digest=?", (digest,))
        return self.get(digest)

    def _write_object(self, digest: str, data: bytes) -> None:
        target = self.objects / digest
        if target.exists() or target.is_symlink():
            if _safe_read(self.objects, digest, self.max_artifact_bytes) != data:
                raise ArtifactError("content store corruption")
            return
        fd, temporary = tempfile.mkstemp(prefix=".staging-", dir=self.objects)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), 0o444)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def get(self, digest: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM artifacts WHERE digest=?", (_digest(digest),)).fetchone()
        if row is None:
            raise ArtifactError("unknown artifact")
        result = dict(row)
        result["path"] = str(self.objects / digest)
        result["retained"] = bool(result["retained"])
        result["lineage"] = [r[0] for r in self.db.execute("SELECT parent FROM edges WHERE child=? ORDER BY parent", (digest,))]
        result["productions"] = [dict(r) for r in self.db.execute("SELECT producer,assurance FROM productions WHERE digest=? ORDER BY producer,assurance", (digest,))]
        result["events"] = [dict(r) for r in self.db.execute("SELECT kind,reason,created_at FROM events WHERE digest=? ORDER BY id", (digest,))]
        return result

    def read(self, digest: str) -> bytes:
        record = self.get(digest)
        if record["status"] == "deleted":
            raise ArtifactError("artifact content deleted; tombstone retained")
        data = _safe_read(self.objects, digest, self.max_artifact_bytes)
        if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != digest:
            raise ArtifactError("artifact hash mismatch")
        return data

    def verify(self, digest: str) -> bool:
        self.read(digest)
        return True

    def invalidate(self, digest: str, reason: str) -> list[str]:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4096:
            raise ArtifactError("invalidation requires a bounded reason")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.get(digest)
            rows = self.db.execute("""WITH RECURSIVE affected(d) AS (
                SELECT ? UNION SELECT child FROM edges JOIN affected ON parent=affected.d)
                SELECT d FROM affected ORDER BY d""", (digest,)).fetchall()
            for row in rows:
                status = "invalid" if row[0] == digest else "stale"
                self.db.execute("UPDATE artifacts SET status=? WHERE digest=? AND status NOT IN ('deleted','invalid')", (status, row[0]))
                self.db.execute("INSERT INTO events(digest,kind,reason) VALUES(?,?,?)", (row[0], status, reason))
        return [r[0] for r in rows]

    def set_retention(self, digest: str, retained: bool) -> None:
        self.get(digest)
        if type(retained) is not bool:
            raise ArtifactError("retention must be boolean")
        with self.db:
            self.db.execute("UPDATE artifacts SET retained=? WHERE digest=?", (int(retained), digest))

    def collect(self, digest: str, *, reason: str) -> None:
        """Delete only unretained, unreferenced content and preserve its tombstone."""
        if not reason:
            raise ArtifactError("deletion requires a reason")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            record = self.get(digest)
            if record["retained"] or self.db.execute("SELECT 1 FROM edges WHERE parent=?", (digest,)).fetchone():
                raise ArtifactError("required evidence cannot be collected")
            self.db.execute("UPDATE artifacts SET status='deleted' WHERE digest=?", (digest,))
            self.db.execute("INSERT INTO events(digest,kind,reason) VALUES(?,'deleted',?)", (digest, reason))
        (self.objects / digest).unlink(missing_ok=True)

    def export_bundle(self, destination: Path, digests: Sequence[str] | None = None) -> Path:
        pending = list(digests) if digests is not None else [r[0] for r in self.db.execute("SELECT digest FROM artifacts")]
        records: dict[str, dict[str, Any]] = {}
        while pending:
            digest = _digest(pending.pop())
            if digest not in records:
                record = self.get(digest)
                del record["path"]
                records[digest] = record
                pending.extend(record["lineage"])
        manifest_data = _json({"schema_version": "0.1", "artifacts": [records[d] for d in sorted(records)]})
        if len(manifest_data) > 4 * 1024**2:
            raise ArtifactError("bundle manifest exceeds byte budget")
        if destination.exists() or destination.is_symlink():
            raise ArtifactError("export destination already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".bundle-", dir=destination.parent)
        os.close(fd)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("manifest.json", manifest_data)
                for digest, record in sorted(records.items()):
                    if record["status"] != "deleted":
                        archive.writestr(f"objects/{digest}", self.read(digest))
            os.link(temporary, destination)
        finally:
            os.unlink(temporary)
        return destination

    def import_bundle(self, bundle: Path) -> list[dict[str, Any]]:
        """Verify the complete archive before publication; imported trust stays imported."""
        if bundle.is_symlink() or not bundle.is_file():
            raise ArtifactError("bundle must be a regular file")
        try:
            with zipfile.ZipFile(bundle) as archive:
                infos = archive.infolist()
                names = [i.filename for i in infos]
                if len(names) > 4097 or len(names) != len(set(names)) or "manifest.json" not in names:
                    raise ArtifactError("invalid archive inventory")
                if any(i.flag_bits & 1 for i in infos):
                    raise ArtifactError("encrypted evidence bundles are unsupported")
                if sum(i.file_size for i in infos) > self.max_total_bytes + 4 * 1024**2:
                    raise ArtifactError("bundle exceeds byte budget")
                manifest_info = archive.getinfo("manifest.json")
                if manifest_info.file_size > 4 * 1024**2:
                    raise ArtifactError("oversized bundle manifest")
                manifest = json.loads(archive.read("manifest.json"), object_pairs_hook=_unique_object)
                if set(manifest) != {"schema_version", "artifacts"} or manifest["schema_version"] != "0.1":
                    raise ArtifactError("unsupported bundle schema")
                rows = manifest["artifacts"]
                if not isinstance(rows, list) or len(rows) > 4096:
                    raise ArtifactError("invalid artifact inventory")
                records, contents = {}, {}
                for row in rows:
                    required = {"digest", "size", "media_type", "classification", "assurance", "status", "retained", "source_assurance", "lineage", "productions", "events"}
                    if not isinstance(row, dict) or set(row) != required:
                        raise ArtifactError("invalid evidence record")
                    digest = _digest(row["digest"])
                    if digest in records or type(row["size"]) is not int or not 0 <= row["size"] <= self.max_artifact_bytes:
                        raise ArtifactError("invalid artifact identity or size")
                    if row["status"] not in ("valid", "invalid", "stale", "deleted") or type(row["retained"]) is not bool:
                        raise ArtifactError("invalid evidence state")
                    if row["assurance"] not in ("imported", "traceable", "independently_recomputed", "replicated"):
                        raise ArtifactError("invalid imported assurance")
                    if row["source_assurance"] is not None and row["source_assurance"] not in ("imported", "traceable", "independently_recomputed", "replicated"):
                        raise ArtifactError("invalid source assurance")
                    if not isinstance(row["media_type"], str) or not 1 <= len(row["media_type"]) <= 256:
                        raise ArtifactError("invalid media type")
                    if row["classification"] not in ("public_synthetic", "public", "restricted"):
                        raise ArtifactError("invalid classification")
                    if not isinstance(row["lineage"], list) or len(row["lineage"]) > 1024:
                        raise ArtifactError("invalid lineage")
                    for parent in row["lineage"]:
                        _digest(parent)
                    if len(set(row["lineage"])) != len(row["lineage"]):
                        raise ArtifactError("duplicate lineage parent")
                    if not isinstance(row["productions"], list) or len(row["productions"]) > 4096:
                        raise ArtifactError("invalid imported productions")
                    for production in row["productions"]:
                        if not isinstance(production, dict) or set(production) != {"producer", "assurance"}:
                            raise ArtifactError("invalid imported production")
                        if not isinstance(production["producer"], str) or not 1 <= len(production["producer"]) <= 512 or production["assurance"] not in ("imported", "traceable", "independently_recomputed", "replicated"):
                            raise ArtifactError("invalid imported production fields")
                    if not isinstance(row["events"], list) or len(row["events"]) > 4096:
                        raise ArtifactError("invalid imported events")
                    for event in row["events"]:
                        if not isinstance(event, dict) or set(event) != {"kind", "reason", "created_at"} or any(not isinstance(v, str) for v in event.values()):
                            raise ArtifactError("invalid imported event")
                    _json(row)
                    records[digest] = row
                    if row["status"] != "deleted":
                        name = f"objects/{digest}"
                        info = archive.getinfo(name)
                        if info.file_size != row["size"]:
                            raise ArtifactError("artifact size mismatch")
                        data = archive.read(name)
                        if hashlib.sha256(data).hexdigest() != digest:
                            raise ArtifactError("artifact hash mismatch")
                        contents[digest] = data
                expected = {"manifest.json"} | {f"objects/{d}" for d in contents}
                if set(names) != expected:
                    raise ArtifactError("undeclared or unsafe archive entry")
                for digest, row in records.items():
                    if digest in row["lineage"] or not set(row["lineage"]) <= records.keys():
                        raise ArtifactError("incomplete or cyclic provenance")
                    if row["status"] == "valid" and any(records[p]["status"] != "valid" for p in row["lineage"]):
                        raise ArtifactError("valid evidence cannot depend on invalid or deleted evidence")
                self._check_acyclic(records)
        except (OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError, TypeError, RecursionError) as exc:
            raise ArtifactError("invalid evidence bundle") from exc
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            current = {r[0] for r in self.db.execute("SELECT digest FROM artifacts")}
            if current & records.keys():
                raise ArtifactError("import requires new identities; existing evidence is immutable")
            if len(current) + len(records) > self.max_artifacts:
                raise ArtifactError("artifact record count budget exhausted")
            total = self.db.execute("SELECT COALESCE(SUM(size),0) FROM artifacts WHERE status!='deleted'").fetchone()[0]
            if total + sum(len(data) for data in contents.values()) > self.max_total_bytes:
                raise ArtifactError("artifact store byte budget exhausted")
            for digest, row in records.items():
                if digest in contents:
                    self._write_object(digest, contents[digest])
                self.db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?)", (digest, row["size"], row["media_type"], row["classification"], "imported", row["status"], int(row["retained"]), row["assurance"]))
                self.db.execute("INSERT INTO productions VALUES(?,?,'imported')", (digest, "external-bundle"))
                self.db.execute("INSERT INTO events(digest,kind,reason) VALUES(?,'imported',?)", (digest, _json({"source_productions": row["productions"], "source_events": row["events"], "source_assurance": row["source_assurance"]}).decode()))
            for digest, row in records.items():
                self.db.executemany("INSERT INTO edges VALUES(?,?)", [(digest, p) for p in set(row["lineage"])])
        return [self.get(d) for d in sorted(records)]

    @staticmethod
    def _check_acyclic(records: dict[str, dict[str, Any]]) -> None:
        remaining = {d: set(row["lineage"]) for d, row in records.items()}
        while remaining:
            leaves = {d for d, parents in remaining.items() if not parents}
            if not leaves:
                raise ArtifactError("cyclic evidence lineage")
            remaining = {d: parents - leaves for d, parents in remaining.items() if d not in leaves}
