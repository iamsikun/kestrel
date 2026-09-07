"""Bounded read-only observation of the controller and evidence stores.

The messaging assistant must never open `Lab`, `Controller`, or `Artifacts`:
each of those constructors creates directories, runs DDL, sets write pragmas,
and (for the controller) opens a write transaction to seed metadata. These
readers open the same files with `mode=ro` and `query_only`, validate the schema
they expect, and issue only bounded `SELECT`s.

This is an application-level boundary, not an OS-enforced one. The deployment
profile is what actually denies writes; see `docs/MESSAGING_OPERATIONS.md`.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from kestrel.artifacts import ArtifactError, _safe_read
from kestrel.contracts import digest, parse_json
from kestrel.controller import BUDGET_KEYS, RESOURCE_KEYS, TERMINAL

CONTROLLER_SCHEMA_VERSION = 1
EVIDENCE_TABLES = frozenset({"artifacts", "productions", "edges", "events"})
MAX_ROWS = 1000
#: Attempt states that still hold or may still hold capacity.
UNSETTLED = frozenset({"STARTING", "RUNNING", "RECOVERING", "VERIFYING"})


class SourceUnavailable(ValueError):
    """A source database cannot be observed without weakening the boundary.

    Raised for a missing file, a redirected path, an unsupported schema, a lock
    that does not clear, or a WAL database whose shared-memory index cannot be
    mapped read-only. The caller reports a freshness gap; it never fabricates a
    result and never falls back to a writable handle.
    """


def _guard_dir(path: Path) -> Path:
    """Resolve the parent chain, then refuse a final component that redirects.

    Mirrors the developer lab rule in `application.Lab.__init__`: an ancestor
    that happens to be a symlink (a temporary directory, for example) is fine;
    a lab state path that itself redirects is not.
    """
    path = Path(path)
    target = path.parent.resolve() / path.name
    if target.is_symlink():
        raise SourceUnavailable("Source state paths must not redirect through symlinks")
    return target


def _guard_file(path: Path) -> Path:
    target = _guard_dir(path)
    for sidecar in ("-wal", "-shm"):
        if (target.parent / (target.name + sidecar)).is_symlink():
            raise SourceUnavailable("Source state paths must not redirect through symlinks")
    return target


def _connect(path: Path, *, timeout: float) -> sqlite3.Connection:
    path = _guard_file(path)
    if not path.is_file():
        raise SourceUnavailable(f"Source database is unavailable: {path.name}")
    try:
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=timeout)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        # Touch the header before returning: a read-only connection to a WAL
        # database still needs its shared-memory index, and that failure must
        # surface here rather than mid-briefing.
        connection.execute("PRAGMA user_version").fetchone()
    except sqlite3.Error as exc:
        raise SourceUnavailable(
            "Source database cannot be opened read-only; export an authority-owned "
            "snapshot instead of relaxing the reader"
        ) from exc
    return connection


class _Reader:
    def __init__(self, path: Path, *, timeout: float = 10.0) -> None:
        self.path = _guard_file(path)
        self._db = _connect(self.path, timeout=timeout)

    def close(self) -> None:
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _rows(self, sql: str, parameters: tuple = (), *, limit: int = MAX_ROWS) -> list[sqlite3.Row]:
        if not 1 <= limit <= 100_000:
            raise SourceUnavailable("Bounded source reads require a sane row limit")
        try:
            return self._db.execute(f"{sql} LIMIT ?", (*parameters, limit)).fetchall()
        except sqlite3.Error as exc:
            raise SourceUnavailable("Bounded source read failed") from exc

    def _row(self, sql: str, parameters: tuple = ()) -> sqlite3.Row | None:
        rows = self._rows(sql, parameters, limit=1)
        return rows[0] if rows else None


class ControllerSource(_Reader):
    """Read-only projection of the authoritative execution ledger."""

    def __init__(self, path: Path, *, timeout: float = 10.0) -> None:
        super().__init__(path, timeout=timeout)
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version != CONTROLLER_SCHEMA_VERSION:
            self.close()
            raise SourceUnavailable(
                f"Unsupported controller schema version {version}; expected "
                f"{CONTROLLER_SCHEMA_VERSION}"
            )

    # -- identity -----------------------------------------------------------

    def metadata(self, key: str) -> str | None:
        """Read one declarative metadata value. Operator secrets are refused."""
        if key in {"operator_hash", "operator_salt"}:
            raise SourceUnavailable("Operator credential material is not observable")
        row = self._row("SELECT value FROM metadata WHERE key=?", (key,))
        return row[0] if row else None

    def fingerprint(self) -> str | None:
        """Stable identity for this ledger, or None while it has no history.

        Bound to the first append-only event row. Triggers forbid updating or
        deleting events, so this cannot drift for a continuing database, and a
        restored or replaced database will not silently reuse a cursor. Paths
        and modification times are deliberately not consulted.
        """
        row = self._row("SELECT * FROM events WHERE sequence=(SELECT MIN(sequence) FROM events)")
        if row is None:
            return None
        return digest({"schema": CONTROLLER_SCHEMA_VERSION, "first_event": {
            "sequence": row["sequence"], "campaign_id": row["campaign_id"],
            "kind": row["kind"], "subject": row["subject"], "detail": row["detail"],
            "created_at": row["created_at"]}})

    def head_sequence(self) -> int:
        row = self._row("SELECT COALESCE(MAX(sequence),0) FROM events")
        return int(row[0]) if row else 0

    # -- feed ---------------------------------------------------------------

    def events(self, *, after: int = 0, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM events WHERE sequence>? ORDER BY sequence",
                          (after,), limit=limit)
        return [self._event(row) for row in rows]

    def events_since(self, created_after: float,
                     *, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
        """Events recorded after a wall-clock instant.

        Ledger timestamps are the controller's own audit times; they are not a
        guarantee about when work actually finished.
        """
        rows = self._rows("SELECT * FROM events WHERE created_at>? ORDER BY sequence",
                          (created_after,), limit=limit)
        return [self._event(row) for row in rows]

    def event(self, sequence: int) -> dict[str, Any] | None:
        row = self._row("SELECT * FROM events WHERE sequence=?", (sequence,))
        return self._event(row) if row else None

    @staticmethod
    def _event(row: sqlite3.Row) -> dict[str, Any]:
        return {"sequence": row["sequence"], "campaign_id": row["campaign_id"],
                "kind": row["kind"], "subject": row["subject"],
                "detail": parse_json(row["detail"]), "created_at": row["created_at"],
                "identity": hashlib.sha256(
                    digest({"sequence": row["sequence"], "kind": row["kind"],
                            "subject": row["subject"], "detail": row["detail"]}).encode()
                ).hexdigest()}

    # -- records ------------------------------------------------------------

    def campaign(self, campaign_id: str) -> dict[str, Any]:
        row = self._row("SELECT * FROM campaigns WHERE id=?", (campaign_id,))
        if row is None:
            raise SourceUnavailable("Unknown campaign")
        record = dict(row)
        record["contract"] = parse_json(record["contract"])
        record["outcome"] = parse_json(record["outcome"]) if record["outcome"] else None
        return record

    def campaign_ids(self, *, limit: int = MAX_ROWS) -> list[str]:
        return [row[0] for row in
                self._rows("SELECT id FROM campaigns ORDER BY created_at,id", limit=limit)]

    def tasks(self, campaign_id: str, *, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM tasks WHERE campaign_id=? ORDER BY created_at,id",
                          (campaign_id,), limit=limit)
        return [{**dict(row), "spec": parse_json(row["spec"])} for row in rows]

    def attempt(self, attempt_id: str) -> dict[str, Any]:
        row = self._row("SELECT * FROM attempts WHERE id=?", (attempt_id,))
        if row is None:
            raise SourceUnavailable("Unknown attempt")
        return self._attempt(row)

    def attempts(self, campaign_id: str, *, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM attempts WHERE campaign_id=? ORDER BY created_at,id",
                          (campaign_id,), limit=limit)
        return [self._attempt(row) for row in rows]

    def _attempt(self, row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        for key in ("budget", "resources"):
            record[key] = parse_json(record[key])
        for key in ("resources_held", "stopped_confirmed", "launch_permitted",
                    "independent_replicate"):
            record[key] = bool(record[key])
        result = self._row("SELECT record FROM results WHERE attempt_id=?", (record["id"],))
        record["result"] = parse_json(result[0]) if result else None
        diagnostic = self._row("SELECT record FROM diagnostics WHERE attempt_id=?",
                               (record["id"],))
        record["diagnostic"] = parse_json(diagnostic[0]) if diagnostic else None
        return record

    def selections(self, campaign_id: str, *, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM selections WHERE campaign_id=? ORDER BY created_at,id",
                          (campaign_id,), limit=limit)
        return [{**dict(row), "observation_ids": parse_json(row["observation_ids"])}
                for row in rows]

    def budget_used(self, campaign_id: str) -> dict[str, float]:
        """Charged reservations, not measured consumption."""
        result = dict.fromkeys(BUDGET_KEYS, 0.0)
        for row in self._rows("SELECT budget FROM attempts WHERE campaign_id=?", (campaign_id,)):
            for key, value in parse_json(row[0]).items():
                result[key] += value
        return result

    def resources_used(self) -> dict[str, float]:
        result = dict.fromkeys(RESOURCE_KEYS, 0.0)
        for row in self._rows("SELECT resources FROM attempts WHERE resources_held=1"):
            for key, value in parse_json(row[0]).items():
                result[key] += value
        return result

    def approvals(self, campaign_id: str | None = None,
                  *, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
        if campaign_id is None:
            rows = self._rows("SELECT * FROM approvals ORDER BY created_at,id", limit=limit)
        else:
            rows = self._rows("SELECT * FROM approvals WHERE campaign_id=? ORDER BY created_at,id",
                              (campaign_id,), limit=limit)
        return [{**dict(row), "capabilities": parse_json(row["capabilities"])} for row in rows]

    # -- clock-derived conditions (no source event announces these) ---------

    def usable_approvals(self, campaign_id: str, contract_digest: str,
                         *, now: float) -> list[dict[str, Any]]:
        policy = self.metadata("policy_version")
        return [a for a in self.approvals(campaign_id)
                if a["contract_digest"] == contract_digest
                and a["policy_version"] == policy
                and a["revoked_at"] is None
                and a["expires_at"] > now]

    def stale_leases(self, *, now: float, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
        settled = ",".join("?" * (len(TERMINAL) + 1))
        rows = self._rows(
            f"SELECT * FROM attempts WHERE lease_until<=? AND state NOT IN ({settled}) "
            "ORDER BY lease_until,id",
            (now, *sorted(TERMINAL), "RECOVERING"), limit=limit)
        return [self._attempt(row) for row in rows]

    def unsettled_attempts(self, *, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
        """Attempts that still hold capacity or have not confirmed a stop."""
        rows = self._rows(
            "SELECT * FROM attempts WHERE resources_held=1 ORDER BY created_at,id", limit=limit)
        return [self._attempt(row) for row in rows]

    def blocked_tasks(self, *, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM tasks WHERE state='BLOCKED' ORDER BY created_at,id",
                          limit=limit)
        return [{**dict(row), "spec": parse_json(row["spec"])} for row in rows]


class EvidenceSource(_Reader):
    """Read-only projection of the content-addressed evidence store."""

    def __init__(self, root: Path, *, timeout: float = 10.0,
                 max_artifact_bytes: int = 16 * 1024**2) -> None:
        self.root = _guard_dir(root)
        self.objects = _guard_dir(self.root / "objects")
        if not self.objects.is_dir():
            raise SourceUnavailable("Evidence object store is unavailable")
        self.max_artifact_bytes = max_artifact_bytes
        super().__init__(self.root / "evidence.sqlite", timeout=timeout)
        present = {row[0] for row in
                   self._rows("SELECT name FROM sqlite_master WHERE type='table'", limit=1000)}
        if not EVIDENCE_TABLES <= present:
            self.close()
            raise SourceUnavailable("Unsupported evidence store schema")

    def fingerprint(self) -> str | None:
        row = self._row("SELECT * FROM events WHERE id=(SELECT MIN(id) FROM events)")
        if row is None:
            return None
        return digest({"schema": "evidence-0.1", "first_event": {
            "id": row["id"], "digest": row["digest"], "kind": row["kind"],
            "reason": row["reason"], "created_at": row["created_at"]}})

    def head_event_id(self) -> int:
        row = self._row("SELECT COALESCE(MAX(id),0) FROM events")
        return int(row[0]) if row else 0

    def events(self, *, after: int = 0, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
        rows = self._rows("SELECT * FROM events WHERE id>? ORDER BY id", (after,), limit=limit)
        return [dict(row) for row in rows]

    def get(self, artifact: str) -> dict[str, Any]:
        row = self._row("SELECT * FROM artifacts WHERE digest=?", (artifact,))
        if row is None:
            raise ArtifactError("unknown artifact")
        record = dict(row)
        record["path"] = str(self.objects / artifact)
        record["retained"] = bool(record["retained"])
        record["lineage"] = [r[0] for r in self._rows(
            "SELECT parent FROM edges WHERE child=? ORDER BY parent", (artifact,))]
        record["productions"] = [dict(r) for r in self._rows(
            "SELECT producer,assurance FROM productions WHERE digest=? ORDER BY producer,assurance",
            (artifact,))]
        record["events"] = [dict(r) for r in self._rows(
            "SELECT kind,reason,created_at FROM events WHERE digest=? ORDER BY id", (artifact,))]
        return record

    def read(self, artifact: str) -> bytes:
        """Return verified bytes, raising rather than downgrading on a mismatch."""
        record = self.get(artifact)
        if record["status"] == "deleted":
            raise ArtifactError("artifact content deleted; tombstone retained")
        data = _safe_read(self.objects, artifact, self.max_artifact_bytes)
        if len(data) != record["size"] or hashlib.sha256(data).hexdigest() != artifact:
            raise ArtifactError("artifact hash mismatch")
        return data


class LabSources:
    """Both read-only feeds for one developer lab, opened together."""

    def __init__(self, lab_root: Path, *, timeout: float = 10.0) -> None:
        self.root = _guard_dir(lab_root)
        if not _guard_dir(self.root / "lab.json").is_file():
            raise SourceUnavailable("Not an initialized developer lab")
        runtime = _guard_dir(self.root / "runtime")
        self.controller = ControllerSource(runtime / "controller.sqlite", timeout=timeout)
        try:
            self.evidence = EvidenceSource(runtime / "artifacts", timeout=timeout)
        except BaseException:
            self.controller.close()
            raise

    def disclosure_classifications(self) -> list[str]:
        """Conservative lab-wide ceiling, including evidence without a campaign.

        Empty synthetic state is public_synthetic; missing or unknown labels are
        restricted. This does not infer a downgrade from a short/aggregate view.
        """
        labels = [self.controller.campaign(identity)["contract"].get("data_classification")
                  for identity in self.controller.campaign_ids()]
        labels.extend(row[0] for row in self.evidence._rows(
            "SELECT DISTINCT classification FROM artifacts"))
        return sorted({value if value in ("public_synthetic", "public", "restricted")
                       else "restricted" for value in labels} or {"public_synthetic"})

    def cutoffs(self) -> dict[str, Any]:
        """The per-feed cutoff vector. There is no global snapshot transaction."""
        return {"controller": {"source": self.controller.fingerprint(),
                               "sequence": self.controller.head_sequence()},
                "evidence": {"source": self.evidence.fingerprint(),
                             "event_id": self.evidence.head_event_id()}}

    def close(self) -> None:
        self.controller.close()
        self.evidence.close()

    def __enter__(self):
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
