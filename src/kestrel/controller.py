"""Transactional, single-host authority and attempt ledger.

This module is trusted controller code. Its driver callbacks and principal names
come from the application, never from a worker response. A development operator
token authenticates local approval operations; a user-owned SQLite database is
not an isolation boundary or an authorization to deploy a controller.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class ControllerError(ValueError):
    """A requested controller operation violates a persisted invariant."""


class AuthorityError(ControllerError):
    """An operation lacks current, correctly scoped authority."""


class BudgetError(ControllerError):
    """A declared reservation exceeds an immutable campaign or resource cap."""


class StateError(ControllerError):
    """A transition, lease, dependency, or recovery precondition is not met."""


TERMINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "LOST"})
TRANSITIONS = {
    "STARTING": frozenset({"RUNNING", "RECOVERING", "FAILED", "CANCELLED", "LOST"}),
    "RUNNING": frozenset({"VERIFYING", "RECOVERING", "FAILED", "CANCELLED", "LOST"}),
    "RECOVERING": frozenset({"STARTING", "RUNNING", "VERIFYING", "FAILED", "CANCELLED", "LOST"}),
    "VERIFYING": frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "RECOVERING"}),
    **{state: frozenset() for state in TERMINAL},
}
_SHA256 = re.compile(r"[0-9a-f]{64}")
BUDGET_KEYS = frozenset({"attempts", "runtime_seconds", "provider_calls", "tokens"})
RESOURCE_KEYS = frozenset({"cpu", "gpu", "memory_mb", "storage_mb"})
PROHIBITED_ACTIONS = frozenset(
    {"deploy_controller", "upgrade_controller", "edit_policy", "grant_authority", "publish"}
)


def _json(value: Any) -> str:
    """Detach data and reject NaN, objects, and non-string mapping keys."""

    def validate(item: Any) -> None:
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                validate(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                validate(child)
            return
        raise ControllerError("Boundary objects must contain finite JSON values only")

    validate(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    # Import lazily to keep this store usable while schemas evolve. The contract
    # package defines canonical recipe/contract identities for the application.
    from kestrel.contracts import digest

    return digest(value)


def _numbers(value: Mapping[str, Any], allowed: frozenset[str]) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) - allowed:
        raise ControllerError("Unknown budget or resource dimension")
    result = {}
    for key, amount in value.items():
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount < 0:
            raise ControllerError("Reservations and limits must be finite nonnegative numbers")
        if key in {"attempts", "provider_calls", "tokens", "cpu", "gpu"} and amount != int(amount):
            raise ControllerError(f"{key} must be an integer")
        result[key] = float(amount)
    return result


def _positive_seconds(value: float) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ControllerError("Lease duration must be finite and positive")
    return float(value)


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS campaigns(
    id TEXT PRIMARY KEY, digest TEXT NOT NULL, contract TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'EXPLORING', parent_id TEXT REFERENCES campaigns(id),
    outcome TEXT, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals(
    id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
    contract_digest TEXT NOT NULL, policy_version TEXT NOT NULL,
    principal TEXT NOT NULL, capabilities TEXT NOT NULL, expires_at REAL NOT NULL,
    revoked_at REAL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks(
    id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
    spec TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'QUEUED', blocked_reason TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts(
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
    campaign_id TEXT NOT NULL REFERENCES campaigns(id), approval_id TEXT NOT NULL,
    principal TEXT NOT NULL, backend TEXT NOT NULL, backend_label TEXT UNIQUE NOT NULL,
    state TEXT NOT NULL, fence INTEGER NOT NULL, lease_until REAL NOT NULL,
    resources TEXT NOT NULL, budget TEXT NOT NULL, resources_held INTEGER NOT NULL,
    stopped_confirmed INTEGER NOT NULL DEFAULT 0, launch_permitted INTEGER NOT NULL,
    independent_replicate INTEGER NOT NULL DEFAULT 0,
    randomization_identity TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events(
    sequence INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT,
    kind TEXT NOT NULL, subject TEXT NOT NULL, detail TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS selections(
    id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
    candidate_id TEXT NOT NULL, rationale TEXT NOT NULL, observation_ids TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cache(
    recipe_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL,
    source_attempt_id TEXT NOT NULL REFERENCES attempts(id)
);
CREATE TABLE IF NOT EXISTS results(
    attempt_id TEXT PRIMARY KEY REFERENCES attempts(id), record TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS diagnostics(
    attempt_id TEXT PRIMARY KEY REFERENCES attempts(id), record TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reuses(
    id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaigns(id),
    recipe_id TEXT NOT NULL, artifact_id TEXT NOT NULL, source_attempt_id TEXT NOT NULL,
    independent_replicate INTEGER NOT NULL DEFAULT 0 CHECK(independent_replicate=0),
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memory(
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL, classification TEXT NOT NULL,
    content TEXT NOT NULL, shareable INTEGER NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS project_permissions(
    principal TEXT NOT NULL, project_id TEXT NOT NULL, classification TEXT NOT NULL,
    PRIMARY KEY(principal, project_id, classification)
);
CREATE TRIGGER IF NOT EXISTS campaign_contract_immutable
BEFORE UPDATE OF id,digest,contract,parent_id ON campaigns
BEGIN SELECT RAISE(ABORT, 'campaign contract is immutable'); END;
CREATE TRIGGER IF NOT EXISTS task_spec_immutable
BEFORE UPDATE OF id,campaign_id,spec ON tasks
BEGIN SELECT RAISE(ABORT, 'task specification is immutable'); END;
CREATE TRIGGER IF NOT EXISTS approval_scope_immutable
BEFORE UPDATE OF id,campaign_id,contract_digest,policy_version,principal,capabilities,expires_at,created_at ON approvals
BEGIN SELECT RAISE(ABORT, 'approval scope is immutable'); END;
CREATE TRIGGER IF NOT EXISTS completed_campaign_immutable
BEFORE UPDATE OF state,outcome ON campaigns WHEN OLD.state='COMPLETE'
BEGIN SELECT RAISE(ABORT, 'completed campaign outcome is immutable'); END;
CREATE TRIGGER IF NOT EXISTS attempt_identity_immutable
BEFORE UPDATE OF id,task_id,campaign_id,approval_id,principal,backend,backend_label,
 resources,budget,independent_replicate,randomization_identity,created_at ON attempts
BEGIN SELECT RAISE(ABORT, 'attempt identity and reservations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS terminal_attempt_immutable
BEFORE UPDATE OF state ON attempts
WHEN OLD.state IN ('SUCCEEDED','FAILED','CANCELLED','LOST') AND NEW.state != OLD.state
BEGIN SELECT RAISE(ABORT, 'terminal attempt state is immutable'); END;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS attempts_no_delete BEFORE DELETE ON attempts
BEGIN SELECT RAISE(ABORT, 'attempt history is permanent'); END;
CREATE TRIGGER IF NOT EXISTS selections_no_update BEFORE UPDATE ON selections
BEGIN SELECT RAISE(ABORT, 'selection history is permanent'); END;
CREATE TRIGGER IF NOT EXISTS selections_no_delete BEFORE DELETE ON selections
BEGIN SELECT RAISE(ABORT, 'selection history is permanent'); END;
CREATE TRIGGER IF NOT EXISTS results_no_update BEFORE UPDATE ON results
BEGIN SELECT RAISE(ABORT, 'verified result references are immutable'); END;
CREATE TRIGGER IF NOT EXISTS results_no_delete BEFORE DELETE ON results
BEGIN SELECT RAISE(ABORT, 'verified result references are permanent'); END;
CREATE TRIGGER IF NOT EXISTS diagnostics_no_update BEFORE UPDATE ON diagnostics
BEGIN SELECT RAISE(ABORT, 'diagnostics are immutable'); END;
CREATE TRIGGER IF NOT EXISTS diagnostics_no_delete BEFORE DELETE ON diagnostics
BEGIN SELECT RAISE(ABORT, 'diagnostics are immutable'); END;
"""


class Controller:
    """One durable store; transactions also serialize independent connections.

    Capacity is application admission accounting, not a runtime CPU/RAM quota.
    Actual enforcement and profile capability probing belong to the job driver.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        policy_version: str = "development-v1",
        resource_capacity: Mapping[str, Any] | None = None,
    ) -> None:
        if Path(path).is_symlink():
            raise ControllerError("Controller database cannot be a symlink")
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self._db.close()
            raise ControllerError("Unsupported controller database schema")
        self._db.executescript(SCHEMA)
        self._db.execute("PRAGMA user_version=1")
        requested_capacity = _numbers(
            resource_capacity if resource_capacity is not None else {"cpu": 2, "gpu": 0},
            RESOURCE_KEYS,
        )
        with self._transaction():
            for key, value in (
                ("policy_version", policy_version),
                ("profile", "development"),
                ("resource_capacity", _json(requested_capacity)),
            ):
                self._db.execute("INSERT OR IGNORE INTO metadata VALUES (?,?)", (key, value))
            if self._meta("policy_version") != policy_version:
                raise AuthorityError(
                    "Changing deployed policy is not a controller constructor operation"
                )
            self.capacity = json.loads(self._meta("resource_capacity"))
            if resource_capacity is not None and self.capacity != requested_capacity:
                raise AuthorityError("Persisted capacity cannot be expanded by reopening the store")
        self.policy_version = policy_version

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Controller:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        else:
            self._db.execute("COMMIT")

    def _meta(self, key: str) -> str:
        row = self._db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        if row is None:
            raise AuthorityError(f"Controller metadata is missing: {key}")
        return row[0]

    def _event(self, campaign: str | None, kind: str, subject: str, detail: Any) -> None:
        self._db.execute(
            "INSERT INTO events(campaign_id,kind,subject,detail,created_at) VALUES (?,?,?,?,?)",
            (campaign, kind, subject, _json(detail), time.time()),
        )

    def initialize_operator(self, token: str) -> None:
        """Bootstrap a developer lab once, never an unattended deployment grant.

        The application writes this high entropy token outside the source tree
        and never mounts it into workers. This function does not create workers,
        deploy services, or grant live-provider access.
        """
        if type(token) is not str or len(token) < 32:
            raise AuthorityError("The development operator token must have at least 32 characters")
        with self._transaction():
            if self._db.execute("SELECT 1 FROM metadata WHERE key='operator_hash'").fetchone():
                raise AuthorityError("An initialized operator cannot be replaced by bootstrap")
            salt = secrets.token_hex(32)
            hashed = hashlib.sha256((salt + token).encode()).hexdigest()
            self._db.executemany(
                "INSERT INTO metadata VALUES (?,?)",
                [("operator_salt", salt), ("operator_hash", hashed)],
            )
            self._event(
                None, "development_operator_initialized", "operator", {"profile": "development"}
            )

    def _operator(self, token: str) -> None:
        if type(token) is not str:
            raise AuthorityError("Operator authentication required")
        expected = self._meta("operator_hash")
        actual = hashlib.sha256((self._meta("operator_salt") + token).encode()).hexdigest()
        if not hmac.compare_digest(expected, actual):
            raise AuthorityError("Operator authentication failed")

    def propose(self, contract: dict[str, Any], *, parent_id: str | None = None) -> str:
        encoded = _json(contract)
        if type(contract) is not dict or "budget" not in contract:
            raise ControllerError("A campaign needs an explicit immutable budget")
        budget = _numbers(contract["budget"], BUDGET_KEYS)
        if set(budget) != BUDGET_KEYS or budget["attempts"] < 1:
            raise ControllerError(
                "Specify attempts, runtime_seconds, provider_calls, and tokens caps"
            )
        campaign_id = uuid.uuid4().hex
        with self._transaction():
            if parent_id is not None:
                self.campaign(parent_id)
            self._db.execute(
                "INSERT INTO campaigns(id,digest,contract,parent_id,created_at) VALUES (?,?,?,?,?)",
                (campaign_id, _digest(contract), encoded, parent_id, time.time()),
            )
            self._event(campaign_id, "campaign_proposed", campaign_id, {"parent_id": parent_id})
        return campaign_id

    def campaign(self, campaign_id: str) -> dict[str, Any]:
        row = self._db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if row is None:
            raise ControllerError("Unknown campaign")
        result = dict(row)
        result["contract"] = json.loads(result["contract"])
        result["outcome"] = json.loads(result["outcome"]) if result["outcome"] else None
        return result

    def approve(
        self,
        campaign_id: str,
        *,
        token: str,
        contract_digest: str,
        principal: str = "developer",
        expires_at: float,
        capabilities: Iterable[str] = ("execute",),
        policy_version: str | None = None,
    ) -> str:
        self._operator(token)
        grants = sorted(set(capabilities))
        if not principal or not grants or any(type(grant) is not str for grant in grants):
            raise AuthorityError("A grant needs a principal and capability scope")
        if set(grants) & PROHIBITED_ACTIONS:
            raise AuthorityError(
                "Self-deployment and authority changes are outside campaign capabilities"
            )
        # The first pilot's bootstrap cannot approve paid or networked campaigns.
        if set(grants) & {"live_provider", "network", "private_data", "cloud"}:
            raise AuthorityError(
                "Development bootstrap cannot authorize live or private-data actions"
            )
        if (
            type(expires_at) not in (int, float)
            or not math.isfinite(expires_at)
            or expires_at <= time.time()
        ):
            raise AuthorityError("Approval expiry must be in the future")
        version = policy_version if policy_version is not None else self.policy_version
        approval_id = uuid.uuid4().hex
        with self._transaction():
            campaign = self.campaign(campaign_id)
            if campaign["digest"] != contract_digest or version != self._meta("policy_version"):
                raise AuthorityError(
                    "Approval must match the exact contract digest and active policy"
                )
            if campaign["state"] == "COMPLETE":
                raise StateError("A completed campaign cannot receive execution authority")
            if set(grants) - set(campaign["contract"].get("capabilities", ["execute"])):
                raise AuthorityError("Approval cannot expand the contract's capability envelope")
            self._db.execute(
                "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    approval_id,
                    campaign_id,
                    contract_digest,
                    version,
                    principal,
                    _json(grants),
                    expires_at,
                    None,
                    time.time(),
                ),
            )
            self._event(
                campaign_id,
                "approval_granted",
                approval_id,
                {"principal": principal, "capabilities": grants},
            )
        return approval_id

    def revoke(self, approval_id: str, *, token: str) -> None:
        self._operator(token)
        with self._transaction():
            row = self._db.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if row is None:
                raise AuthorityError("Unknown approval")
            self._db.execute(
                "UPDATE approvals SET revoked_at=? WHERE id=?", (time.time(), approval_id)
            )
            self._event(row["campaign_id"], "approval_revoked", approval_id, {})

    def add_task(self, campaign_id: str, task: dict[str, Any]) -> str:
        task = json.loads(_json(task))
        if type(task) is not dict:
            raise ControllerError("Task must be a JSON object")
        task_id = task.setdefault("id", uuid.uuid4().hex)
        task.setdefault("dependencies", [])
        task.setdefault("max_attempts", 1)
        task.setdefault("profile", "development")
        task.setdefault("operation", "execute")
        task.setdefault("required_capabilities", [])
        task.setdefault("budget", {"runtime_seconds": 1, "provider_calls": 0, "tokens": 0})
        task.setdefault("resources", {"cpu": 1})
        if type(task_id) is not str or not task_id:
            raise ControllerError("Task ID must be nonempty text")
        if type(task["max_attempts"]) is not int or task["max_attempts"] < 1:
            raise ControllerError("max_attempts must be a positive integer")
        if task["profile"] not in {"development", "isolated-local", "hardened"}:
            raise ControllerError("Unknown requested execution profile")
        if type(task["operation"]) is not str or task["operation"] in PROHIBITED_ACTIONS:
            raise AuthorityError("Task operation is not a campaign-authorizable action")
        for key in ("dependencies", "required_capabilities"):
            if type(task[key]) is not list or any(type(value) is not str for value in task[key]):
                raise ControllerError(f"{key} must be a list of strings")
        _numbers(task["budget"], BUDGET_KEYS)
        _numbers(task["resources"], RESOURCE_KEYS)
        with self._transaction():
            campaign = self.campaign(campaign_id)
            if campaign["state"] == "COMPLETE":
                raise StateError("Completed campaigns cannot add work")
            if task["profile"] != campaign["contract"].get("profile", "development"):
                raise AuthorityError("Task cannot change the contract's requested security profile")
            for dependency in task["dependencies"]:
                row = self._db.execute(
                    "SELECT campaign_id FROM tasks WHERE id=?", (dependency,)
                ).fetchone()
                if row is None or row[0] != campaign_id or dependency == task_id:
                    raise StateError("Dependencies must be earlier tasks in the same campaign")
            self._db.execute(
                "INSERT INTO tasks(id,campaign_id,spec,created_at) VALUES (?,?,?,?)",
                (task_id, campaign_id, _json(task), time.time()),
            )
            self._event(campaign_id, "task_proposed", task_id, {"spec": task})
        return task_id

    def task(self, task_id: str) -> dict[str, Any]:
        row = self._db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ControllerError("Unknown task")
        result = dict(row)
        result["spec"] = json.loads(result["spec"])
        return result

    def ready_tasks(self, campaign_id: str) -> list[dict[str, Any]]:
        campaign = self.campaign(campaign_id)
        if campaign["state"] == "COMPLETE":
            return []
        result = []
        for row in self._db.execute(
            "SELECT id FROM tasks WHERE campaign_id=? AND state='QUEUED' ORDER BY created_at,id",
            (campaign_id,),
        ).fetchall():
            task = self.task(row[0])
            if all(self.task(dep)["state"] == "SUCCEEDED" for dep in task["spec"]["dependencies"]):
                if not self._db.execute(
                    "SELECT 1 FROM attempts WHERE task_id=? AND (resources_held=1 OR state NOT IN ('SUCCEEDED','FAILED','CANCELLED','LOST'))",
                    (task["id"],),
                ).fetchone():
                    result.append(task)
        return result

    def tasks(self, campaign_id: str) -> list[dict[str, Any]]:
        self.campaign(campaign_id)
        return [
            self.task(row[0])
            for row in self._db.execute(
                "SELECT id FROM tasks WHERE campaign_id=? ORDER BY created_at,id", (campaign_id,)
            ).fetchall()
        ]

    def cancel_task(self, task_id: str, reason: str) -> None:
        with self._transaction():
            task = self.task(task_id)
            if task["state"] not in {"QUEUED", "BLOCKED"}:
                raise StateError("Active attempts require driver cancellation and reconciliation")
            if self._db.execute(
                "SELECT 1 FROM attempts WHERE task_id=? AND resources_held=1", (task_id,)
            ).fetchone():
                raise StateError(
                    "An older uncertain job still requires cancellation and reconciliation"
                )
            self._db.execute(
                "UPDATE tasks SET state='CANCELLED',blocked_reason=? WHERE id=?", (reason, task_id)
            )
            self._event(task["campaign_id"], "task_cancelled", task_id, {"reason": reason})

    def block_task(self, task_id: str, reason: str) -> None:
        if not reason:
            raise StateError("Blocked work needs a recorded reason")
        with self._transaction():
            task = self.task(task_id)
            if task["state"] != "QUEUED":
                raise StateError("Only queued tasks can be blocked")
            self._db.execute(
                "UPDATE tasks SET state='BLOCKED',blocked_reason=? WHERE id=?", (reason, task_id)
            )
            self._event(task["campaign_id"], "task_blocked", task_id, {"reason": reason})

    def unblock_task(self, task_id: str) -> None:
        with self._transaction():
            task = self.task(task_id)
            if task["state"] != "BLOCKED":
                raise StateError("Task is not blocked")
            self._db.execute(
                "UPDATE tasks SET state='QUEUED',blocked_reason=NULL WHERE id=?", (task_id,)
            )
            self._event(task["campaign_id"], "task_unblocked", task_id, {})

    def budget_used(self, campaign_id: str) -> dict[str, float]:
        self.campaign(campaign_id)
        result = dict.fromkeys(BUDGET_KEYS, 0.0)
        for row in self._db.execute(
            "SELECT budget FROM attempts WHERE campaign_id=?", (campaign_id,)
        ):
            for key, value in json.loads(row[0]).items():
                result[key] += value
        return result

    def resources_used(self) -> dict[str, float]:
        result = dict.fromkeys(RESOURCE_KEYS, 0.0)
        for row in self._db.execute("SELECT resources FROM attempts WHERE resources_held=1"):
            for key, value in json.loads(row[0]).items():
                result[key] += value
        return result

    def reserve(
        self,
        task_id: str,
        *,
        approval_id: str,
        principal: str,
        backend: str,
        available_capabilities: Iterable[str],
        required_capabilities: Iterable[str] = (),
        resource_request: Mapping[str, Any] | None = None,
        budget_request: Mapping[str, Any] | None = None,
        lease_seconds: float = 60,
    ) -> dict[str, Any]:
        """Atomically authorize, charge worst-case budget and reserve capacity.

        Driver capabilities are supplied by trusted application probing. Passing
        a worker's claimed capability list here would violate this API boundary.
        """
        lease_seconds = _positive_seconds(lease_seconds)
        with self._transaction():
            task = self.task(task_id)
            campaign = self.campaign(task["campaign_id"])
            if task_id not in {ready["id"] for ready in self.ready_tasks(task["campaign_id"])}:
                raise StateError("Task is not ready or its previous job remains uncertain")
            spec = task["spec"]
            approval = self._db.execute(
                "SELECT * FROM approvals WHERE id=?", (approval_id,)
            ).fetchone()
            if approval is None or any(
                (
                    approval["campaign_id"] != campaign["id"],
                    approval["contract_digest"] != campaign["digest"],
                    approval["policy_version"] != self._meta("policy_version"),
                    approval["principal"] != principal,
                    approval["expires_at"] <= time.time(),
                    approval["revoked_at"] is not None,
                )
            ):
                raise AuthorityError(
                    "Approval is absent, expired, revoked, or bound to different inputs/principal"
                )
            needed = set(spec["required_capabilities"]) | set(required_capabilities)
            grants = set(json.loads(approval["capabilities"]))
            if (needed | {spec["operation"]}) - grants:
                raise AuthorityError("Approval does not grant all task capabilities")
            if set(spec["required_capabilities"]) & PROHIBITED_ACTIONS:
                raise AuthorityError("Workers cannot upgrade runtime or policy")
            available = set(available_capabilities)
            if spec["profile"] not in available or needed - available:
                raise AuthorityError(
                    "Requested execution profile or enforcement capability unavailable"
                )
            previous = self._db.execute(
                "SELECT COUNT(*) FROM attempts WHERE task_id=?", (task_id,)
            ).fetchone()[0]
            if previous >= spec["max_attempts"]:
                raise BudgetError("Task retry cap exhausted")
            declared_budget = _numbers(spec["budget"], BUDGET_KEYS)
            budget = _numbers(
                budget_request if budget_request is not None else declared_budget, BUDGET_KEYS
            )
            budget["attempts"] = max(1.0, budget.get("attempts", 1.0))
            resources = _numbers(
                resource_request if resource_request is not None else spec["resources"],
                RESOURCE_KEYS,
            )
            if any(budget.get(key, 0) < value for key, value in declared_budget.items()):
                raise BudgetError("Reservation cannot understate declared task budget")
            if any(resources.get(key, 0) < value for key, value in spec["resources"].items()):
                raise BudgetError("Reservation cannot understate declared task resources")
            used = self.budget_used(campaign["id"])
            if any(
                used[key] + value > campaign["contract"]["budget"].get(key, 0)
                for key, value in budget.items()
            ):
                raise BudgetError("Campaign budget exhausted, including all previous attempts")
            occupied = self.resources_used()
            if any(
                occupied[key] + value > self.capacity.get(key, 0)
                for key, value in resources.items()
            ):
                raise BudgetError("Resource capacity is occupied or unsupported")
            independent = spec.get("independent_replicate", False)
            if type(independent) is not bool:
                raise ControllerError("independent_replicate must be a boolean")
            randomization = spec.get("randomization_identity")
            if independent:
                if type(randomization) is not str or not randomization:
                    raise ControllerError(
                        "Independent replication requires a declared randomization identity"
                    )
                if self._db.execute(
                    "SELECT 1 FROM attempts WHERE campaign_id=? AND independent_replicate=1 AND randomization_identity=?",
                    (campaign["id"], randomization),
                ).fetchone():
                    raise ControllerError(
                        "Repeated randomization identity is not an independent replicate"
                    )
            attempt_id = uuid.uuid4().hex
            now = time.time()
            self._db.execute(
                "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    attempt_id,
                    task_id,
                    campaign["id"],
                    approval_id,
                    principal,
                    backend,
                    f"kestrel-{attempt_id}",
                    "STARTING",
                    1,
                    now + lease_seconds,
                    _json(resources),
                    _json(budget),
                    1,
                    0,
                    1,
                    int(independent),
                    randomization,
                    now,
                    now,
                ),
            )
            self._db.execute("UPDATE tasks SET state='ACTIVE' WHERE id=?", (task_id,))
            self._event(
                campaign["id"],
                "attempt_reserved",
                attempt_id,
                {
                    "budget": budget,
                    "resources": resources,
                    "backend_label": f"kestrel-{attempt_id}",
                },
            )
        return self.attempt(attempt_id)

    def attempt(self, attempt_id: str) -> dict[str, Any]:
        row = self._db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None:
            raise ControllerError("Unknown attempt")
        result = dict(row)
        for key in ("budget", "resources"):
            result[key] = json.loads(result[key])
        for key in (
            "resources_held",
            "stopped_confirmed",
            "launch_permitted",
            "independent_replicate",
        ):
            result[key] = bool(result[key])
        record = self._db.execute(
            "SELECT record FROM results WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        result["result"] = json.loads(record[0]) if record else None
        diagnostic = self._db.execute(
            "SELECT record FROM diagnostics WHERE attempt_id=?", (attempt_id,)
        ).fetchone()
        result["diagnostic"] = json.loads(diagnostic[0]) if diagnostic else None
        return result

    def authorize_launch(self, attempt_id: str, *, principal: str = "developer") -> dict[str, Any]:
        """Recheck current authority immediately before a driver's external launch.

        The reservation's original approval remains authoritative after restart.
        Its expiry or revocation cannot be bypassed by passing a different grant
        to the run command. Existing computation can still be reconciled and
        evaluated without authorizing a new launch.
        """
        with self._transaction():
            attempt = self.attempt(attempt_id)
            if (
                attempt["state"] not in {"STARTING", "RECOVERING"}
                or not attempt["launch_permitted"]
            ):
                raise StateError("Launch requires a reserved or positively reconciled attempt")
            if attempt["lease_until"] <= time.time():
                raise StateError("Expired launch lease requires reconciliation")
            campaign = self.campaign(attempt["campaign_id"])
            approval = self._db.execute(
                "SELECT * FROM approvals WHERE id=?", (attempt["approval_id"],)
            ).fetchone()
            if approval is None or any(
                (
                    approval["campaign_id"] != campaign["id"],
                    approval["contract_digest"] != campaign["digest"],
                    approval["policy_version"] != self._meta("policy_version"),
                    approval["principal"] != principal,
                    attempt["principal"] != principal,
                    approval["expires_at"] <= time.time(),
                    approval["revoked_at"] is not None,
                )
            ):
                raise AuthorityError(
                    "Launch approval is expired, revoked, or bound to different inputs/principal"
                )
            self._db.execute(
                "UPDATE attempts SET launch_permitted=0,updated_at=? WHERE id=?",
                (time.time(), attempt_id),
            )
            self._event(
                campaign["id"],
                "attempt_launch_authorized",
                attempt_id,
                {"approval_id": approval["id"], "principal": principal, "fence": attempt["fence"]},
            )
        return self.attempt(attempt_id)

    def _fenced(self, attempt_id: str, fence: int) -> dict[str, Any]:
        attempt = self.attempt(attempt_id)
        if type(fence) is not int or attempt["fence"] != fence:
            raise StateError("Stale controller lease fencing token")
        return attempt

    def transition(
        self, attempt_id: str, state: str, *, fence: int, detail: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        with self._transaction():
            attempt = self._fenced(attempt_id, fence)
            if attempt["lease_until"] <= time.time():
                raise StateError(
                    "Expired lease requires reconciliation before completion or transition"
                )
            if state not in TRANSITIONS[attempt["state"]]:
                raise StateError(f"Illegal attempt transition {attempt['state']} -> {state}")
            if state in {"VERIFYING", "SUCCEEDED"} and not attempt["stopped_confirmed"]:
                raise StateError(
                    "Driver must confirm candidate stopped before verification/success"
                )
            if state == "RUNNING" and attempt["stopped_confirmed"]:
                raise StateError("A confirmed stopped attempt cannot resume computation")
            self._db.execute(
                "UPDATE attempts SET state=?,launch_permitted=0,updated_at=? WHERE id=?",
                (state, time.time(), attempt_id),
            )
            if state in TERMINAL:
                task = self.task(attempt["task_id"])
                total = self._db.execute(
                    "SELECT COUNT(*) FROM attempts WHERE task_id=?", (task["id"],)
                ).fetchone()[0]
                next_state = state
                if state in {"FAILED", "LOST"} and total < task["spec"]["max_attempts"]:
                    next_state = "QUEUED"
                self._db.execute("UPDATE tasks SET state=? WHERE id=?", (next_state, task["id"]))
            self._event(
                attempt["campaign_id"],
                "attempt_transition",
                attempt_id,
                {"from": attempt["state"], "to": state, "fence": fence, "detail": detail or {}},
            )
        return self.attempt(attempt_id)

    def heartbeat(
        self, attempt_id: str, *, fence: int, lease_seconds: float = 60
    ) -> dict[str, Any]:
        lease_seconds = _positive_seconds(lease_seconds)
        with self._transaction():
            attempt = self._fenced(attempt_id, fence)
            if attempt["state"] in TERMINAL or attempt["lease_until"] <= time.time():
                raise StateError("Expired or terminal lease requires reconciliation")
            self._db.execute(
                "UPDATE attempts SET lease_until=?,updated_at=? WHERE id=?",
                (time.time() + lease_seconds, time.time(), attempt_id),
            )
        return self.attempt(attempt_id)

    def recover_expired(self, *, now: float | None = None) -> list[dict[str, Any]]:
        audit_time = time.time() if now is None else now
        result = []
        with self._transaction():
            rows = self._db.execute(
                "SELECT id FROM attempts WHERE lease_until<=? AND state NOT IN ('SUCCEEDED','FAILED','CANCELLED','LOST','RECOVERING')",
                (audit_time,),
            ).fetchall()
            for row in rows:
                attempt = self.attempt(row[0])
                self._db.execute(
                    "UPDATE attempts SET state='RECOVERING',fence=fence+1,launch_permitted=0,updated_at=? WHERE id=?",
                    (time.time(), attempt["id"]),
                )
                self._event(
                    attempt["campaign_id"],
                    "lease_expired",
                    attempt["id"],
                    {
                        "previous_state": attempt["state"],
                        "resources_retained": attempt["resources_held"],
                    },
                )
                result.append(self.attempt(attempt["id"]))
        return result

    def reconcile(
        self,
        attempt_id: str,
        inspect_job: Callable[[str], dict[str, Any]],
        *,
        lease_seconds: float = 60,
    ) -> dict[str, Any]:
        """Inspect a stable backend identity before any restart or release.

        ``absent`` must be authoritative driver knowledge, not a missing local
        PID alone. Unknown job ownership/state retains resources. The callback
        executes outside the SQLite transaction, followed by a fencing check.
        """
        lease_seconds = _positive_seconds(lease_seconds)
        original = self.attempt(attempt_id)
        inspection = json.loads(_json(inspect_job(original["backend_label"])))
        if (
            type(inspection) is not dict
            or inspection.get("backend_label") != original["backend_label"]
        ):
            raise StateError("Driver inspection does not match stable backend identity")
        status = inspection.get("status")
        if status not in {"running", "stopped", "absent", "unknown"}:
            raise StateError("Driver returned an unknown reconciliation status")
        inconsistent_driver = False
        with self._transaction():
            attempt = self._fenced(attempt_id, original["fence"])
            state = attempt["state"]
            held = attempt["resources_held"]
            stopped = attempt["stopped_confirmed"]
            launch = False
            if status == "absent" and inspection.get("never_launched") is True:
                observed_running = False
                for row in self._db.execute(
                    "SELECT kind,detail FROM events WHERE subject=?", (attempt_id,)
                ):
                    detail = json.loads(row["detail"])
                    if (row["kind"] == "attempt_transition" and detail.get("to") == "RUNNING") or (
                        row["kind"] == "attempt_reconciled"
                        and detail.get("inspection", {}).get("status") == "running"
                    ):
                        observed_running = True
                        break
                if observed_running:
                    raise StateError(
                        "Driver cannot claim a previously observed job was never launched"
                    )
            if (
                status == "absent"
                and state not in TERMINAL
                and not stopped
                and inspection.get("never_launched") is True
            ):
                # No persisted launch result: reclaim this SAME reservation and
                # attempt. Idempotent driver launch must use backend_label.
                if state not in {"STARTING", "RECOVERING"}:
                    state = "RECOVERING"
                launch = True
            elif status in {"stopped", "absent"}:
                stopped, held = True, False
                if state == "STARTING":
                    state = "RECOVERING"
                if status == "absent" and state not in TERMINAL:
                    state = "LOST"
                    task = self.task(attempt["task_id"])
                    count = self._db.execute(
                        "SELECT COUNT(*) FROM attempts WHERE task_id=?", (task["id"],)
                    ).fetchone()[0]
                    task_state = "QUEUED" if count < task["spec"]["max_attempts"] else "LOST"
                    self._db.execute(
                        "UPDATE tasks SET state=? WHERE id=?", (task_state, task["id"])
                    )
            elif status == "running":
                if stopped:
                    # A contradicted stop proof must conservatively occupy the
                    # resource again, even when the driver has violated its
                    # own identity contract. Raising before recording this
                    # would leave capacity falsely available.
                    inconsistent_driver = True
                    stopped = False
                if state not in TERMINAL:
                    state = "RECOVERING" if inconsistent_driver else "RUNNING"
                held = True
            elif state not in TERMINAL:
                state = "RECOVERING"
            self._db.execute(
                "UPDATE attempts SET state=?,fence=fence+1,lease_until=?,resources_held=?,stopped_confirmed=?,launch_permitted=?,updated_at=? WHERE id=?",
                (
                    state,
                    time.time() + lease_seconds,
                    int(held),
                    int(stopped),
                    int(launch),
                    time.time(),
                    attempt_id,
                ),
            )
            self._event(
                attempt["campaign_id"],
                "attempt_reconciled",
                attempt_id,
                {
                    "inspection": inspection,
                    "launch_same_attempt_permitted": launch,
                    "resources_held": held,
                    "driver_identity_inconsistent": inconsistent_driver,
                },
            )
        if inconsistent_driver:
            raise StateError(
                "A supposedly stopped job is running; capacity retained pending driver repair"
            )
        return self.attempt(attempt_id)

    def attempts(self, campaign_id: str) -> list[dict[str, Any]]:
        return [
            self.attempt(row[0])
            for row in self._db.execute(
                "SELECT id FROM attempts WHERE campaign_id=? ORDER BY created_at,id", (campaign_id,)
            ).fetchall()
        ]

    def events(self, campaign_id: str | None = None) -> list[dict[str, Any]]:
        if campaign_id is None:
            rows = self._db.execute("SELECT * FROM events ORDER BY sequence").fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM events WHERE campaign_id=? ORDER BY sequence", (campaign_id,)
            ).fetchall()
        return [{**dict(row), "detail": json.loads(row["detail"])} for row in rows]

    def freeze(self, campaign_id: str) -> None:
        self._campaign_transition(campaign_id, "EXPLORING", "FROZEN")

    def start_confirmation(self, campaign_id: str) -> None:
        self._campaign_transition(campaign_id, "FROZEN", "CONFIRMING")

    def _campaign_transition(self, campaign_id: str, previous: str, state: str) -> None:
        with self._transaction():
            if self.campaign(campaign_id)["state"] != previous:
                raise StateError(f"Campaign must be {previous} before {state}")
            self._db.execute("UPDATE campaigns SET state=? WHERE id=?", (state, campaign_id))
            self._event(
                campaign_id, "campaign_transition", campaign_id, {"from": previous, "to": state}
            )

    def amend(self, campaign_id: str, contract: dict[str, Any]) -> str:
        if self.campaign(campaign_id)["digest"] == _digest(contract):
            raise StateError("An amendment must change the contract identity")
        return self.propose(contract, parent_id=campaign_id)

    def complete(
        self,
        campaign_id: str,
        *,
        execution_status: str,
        protocol_status: str,
        finding: str,
        evidence_ids: list[str],
    ) -> None:
        finding = finding.upper()
        if execution_status not in {"succeeded", "failed", "cancelled", "lost"}:
            raise StateError("Explicit execution outcome required")
        if protocol_status not in {"valid", "invalid", "incomplete"}:
            raise StateError("Explicit protocol outcome required")
        if finding not in {"SUPPORTED_IN_SCOPE", "NOT_SUPPORTED", "INCONCLUSIVE", "INVALID"}:
            raise StateError("Explicit scoped scientific finding required")
        if protocol_status != "valid" and finding in {"SUPPORTED_IN_SCOPE", "NOT_SUPPORTED"}:
            raise StateError(
                "Invalid or incomplete protocol cannot support a scientific conclusion"
            )
        # An execution that did not succeed is an execution outcome, not a scientific
        # one. Failed, lost and cancelled work stays inconclusive; it is never
        # relabelled as a supported or refuted claim.
        if execution_status != "succeeded" and finding in {"SUPPORTED_IN_SCOPE", "NOT_SUPPORTED"}:
            raise StateError(
                "Unsuccessful execution cannot support or refute a scientific conclusion"
            )
        if (
            type(evidence_ids) is not list
            or not evidence_ids
            or any(type(value) is not str or not _SHA256.fullmatch(value) for value in evidence_ids)
        ):
            raise StateError("Completion requires registered evidence references")
        with self._transaction():
            campaign = self.campaign(campaign_id)
            if campaign["state"] not in {"FROZEN", "CONFIRMING"}:
                raise StateError("Complete only a frozen scientific contract")
            permitted_findings = campaign["contract"].get("accepted_findings")
            if (
                protocol_status == "valid"
                and permitted_findings is not None
                and finding.lower() not in permitted_findings
            ):
                raise StateError("Finding is outside the immutable contract's accepted outcomes")
            if self._db.execute(
                "SELECT 1 FROM attempts WHERE campaign_id=? AND (resources_held=1 OR state NOT IN ('SUCCEEDED','FAILED','CANCELLED','LOST'))",
                (campaign_id,),
            ).fetchone():
                raise StateError("Unfinished or uncertain attempts prevent completion")
            if self._db.execute(
                "SELECT 1 FROM tasks WHERE campaign_id=? AND state NOT IN ('SUCCEEDED','FAILED','CANCELLED','LOST')",
                (campaign_id,),
            ).fetchone():
                raise StateError("Unfinished tasks prevent completion")
            if execution_status == "succeeded":
                tasks = self.tasks(campaign_id)
                if not tasks or any(task["state"] != "SUCCEEDED" for task in tasks):
                    raise StateError("Successful execution requires all declared tasks to succeed")
                if self._db.execute(
                    "SELECT 1 FROM attempts a LEFT JOIN results r ON a.id=r.attempt_id WHERE a.campaign_id=? AND a.state='SUCCEEDED' AND r.attempt_id IS NULL",
                    (campaign_id,),
                ).fetchone():
                    raise StateError(
                        "Successful execution requires recorded verified result references"
                    )
            # A valid protocol needs at least one verified observation. Declaring
            # validity over an empty or entirely unverified ledger is not a finding.
            if protocol_status == "valid" and not self._db.execute(
                "SELECT 1 FROM attempts a JOIN results r ON a.id=r.attempt_id WHERE a.campaign_id=? AND a.state='SUCCEEDED'",
                (campaign_id,),
            ).fetchone():
                raise StateError(
                    "A valid protocol requires at least one verified succeeded attempt"
                )
            outcome = {
                "execution_status": execution_status,
                "protocol_status": protocol_status,
                "finding": finding,
                "evidence_ids": evidence_ids,
            }
            self._db.execute(
                "UPDATE campaigns SET state='COMPLETE',outcome=? WHERE id=?",
                (_json(outcome), campaign_id),
            )
            self._event(campaign_id, "campaign_complete", campaign_id, outcome)

    def record_selection(
        self, campaign_id: str, *, candidate_id: str, rationale: str, observation_ids: list[str]
    ) -> str:
        with self._transaction():
            if self.campaign(campaign_id)["state"] != "EXPLORING":
                raise StateError(
                    "Selection is frozen; change it with an explicit contract amendment"
                )
            if not candidate_id or not rationale:
                raise StateError("Selection requires candidate identity and rationale")
            selection_id = uuid.uuid4().hex
            self._db.execute(
                "INSERT INTO selections VALUES (?,?,?,?,?,?)",
                (
                    selection_id,
                    campaign_id,
                    candidate_id,
                    rationale,
                    _json(observation_ids),
                    time.time(),
                ),
            )
            self._event(
                campaign_id,
                "candidate_selected",
                selection_id,
                {
                    "candidate_id": candidate_id,
                    "rationale": rationale,
                    "observation_ids": observation_ids,
                },
            )
        return selection_id

    def record_result(self, attempt_id: str, record: dict[str, Any]) -> None:
        """Persist trusted ingestion/evaluation references, never certify a worker score."""
        encoded = _json(record)
        with self._transaction():
            attempt = self.attempt(attempt_id)
            if (
                attempt["state"] not in {"VERIFYING", "SUCCEEDED"}
                or not attempt["stopped_confirmed"]
            ):
                raise StateError(
                    "Result references require a stopped attempt in verification or success"
                )
            prior = self._db.execute(
                "SELECT record FROM results WHERE attempt_id=?", (attempt_id,)
            ).fetchone()
            if prior is not None:
                if prior[0] != encoded:
                    raise StateError(
                        "A recorded result is immutable; re-evaluation creates new evidence"
                    )
                return
            self._db.execute(
                "INSERT INTO results VALUES (?,?,?)", (attempt_id, encoded, time.time())
            )
            self._event(attempt["campaign_id"], "result_recorded", attempt_id, {"record": record})

    def record_diagnostic(self, attempt_id: str, record: dict[str, Any], *, fence: int) -> None:
        """Retain failed-output evidence separately from successful result publication."""
        encoded = _json(record)
        with self._transaction():
            attempt = self._fenced(attempt_id, fence)
            if not attempt["stopped_confirmed"] or attempt["state"] != "VERIFYING":
                raise StateError("Diagnostics require a stopped attempt being verified")
            prior = self._db.execute("SELECT record FROM diagnostics WHERE attempt_id=?", (attempt_id,)).fetchone()
            if prior:
                if prior[0] != encoded:
                    raise StateError("Recorded diagnostics are immutable")
                return
            self._db.execute("INSERT INTO diagnostics VALUES (?,?,?)", (attempt_id, encoded, time.time()))
            self._event(attempt["campaign_id"], "diagnostic_recorded", attempt_id, {"record": record})

    def results(self, campaign_id: str) -> list[dict[str, Any]]:
        return [
            {"attempt_id": row[0], "record": json.loads(row[1])}
            for row in self._db.execute(
                "SELECT r.attempt_id,r.record FROM results r JOIN attempts a ON a.id=r.attempt_id WHERE a.campaign_id=? ORDER BY r.created_at,r.attempt_id",
                (campaign_id,),
            ).fetchall()
        ]

    def cache_result(self, recipe_id: str, artifact_id: str, attempt_id: str) -> None:
        with self._transaction():
            attempt = self.attempt(attempt_id)
            if attempt["state"] != "SUCCEEDED" or not attempt["stopped_confirmed"]:
                raise StateError("Only completed verified attempts are cache eligible")
            self._db.execute(
                "INSERT INTO cache VALUES (?,?,?)", (recipe_id, artifact_id, attempt_id)
            )
            self._event(
                attempt["campaign_id"],
                "cache_registered",
                recipe_id,
                {"artifact_id": artifact_id, "source_attempt_id": attempt_id},
            )

    def reuse_cached(self, campaign_id: str, recipe_id: str) -> dict[str, Any]:
        with self._transaction():
            if self.campaign(campaign_id)["state"] == "COMPLETE":
                raise StateError("Completed campaigns cannot acquire new evidence")
            row = self._db.execute("SELECT * FROM cache WHERE recipe_id=?", (recipe_id,)).fetchone()
            if row is None:
                raise StateError("No eligible cached result")
            reuse_id = uuid.uuid4().hex
            self._db.execute(
                "INSERT INTO reuses VALUES (?,?,?,?,?,?,?)",
                (
                    reuse_id,
                    campaign_id,
                    recipe_id,
                    row["artifact_id"],
                    row["source_attempt_id"],
                    0,
                    time.time(),
                ),
            )
            record = {
                **dict(row),
                "id": reuse_id,
                "independent_replicate": False,
                "assurance": "reused",
            }
            self._event(campaign_id, "cache_reused", reuse_id, record)
        return record

    def independent_replicates(self, campaign_id: str) -> int:
        return self._db.execute(
            "SELECT COUNT(*) FROM attempts WHERE campaign_id=? AND state='SUCCEEDED' AND stopped_confirmed=1 AND independent_replicate=1",
            (campaign_id,),
        ).fetchone()[0]

    def backup(self, destination: str | Path) -> str:
        destination = Path(destination).resolve()
        if destination == self.path or destination.exists():
            raise ControllerError("Backup must use a new external destination")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(destination) as target:
            self._db.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ControllerError("Backup integrity check failed")
        return hashlib.sha256(destination.read_bytes()).hexdigest()

    @staticmethod
    def restore(backup: str | Path, destination: str | Path) -> Controller:
        source = Path(backup).resolve()
        destination = Path(destination).resolve()
        if destination.exists():
            raise ControllerError("Restore cannot overwrite controller state")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
            if original.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ControllerError("Backup integrity check failed")
            with sqlite3.connect(destination) as target:
                original.backup(target)
            version = original.execute(
                "SELECT value FROM metadata WHERE key='policy_version'"
            ).fetchone()[0]
        return Controller(destination, policy_version=version)

    def request_upgrade(self, *, principal: str, package_digest: str) -> None:
        with self._transaction():
            self._event(
                None,
                "controller_upgrade_denied",
                principal,
                {
                    "package_digest": package_digest,
                    "reason": "separate authorized operator review and deployment required",
                },
            )
        raise AuthorityError("Campaign workers cannot deploy runtime or policy changes")

    def grant_project_access(
        self, *, token: str, principal: str, project_id: str, classifications: Iterable[str]
    ) -> None:
        self._operator(token)
        classes = set(classifications)
        if not classes or classes - {"public", "restricted"}:
            raise AuthorityError("Unknown or absent data classification")
        with self._transaction():
            self._db.executemany(
                "INSERT OR IGNORE INTO project_permissions VALUES (?,?,?)",
                [(principal, project_id, classification) for classification in classes],
            )
            self._event(
                None,
                "project_access_granted",
                principal,
                {"project_id": project_id, "classifications": sorted(classes)},
            )

    def record_memory(
        self,
        project_id: str,
        content: dict[str, Any],
        *,
        classification: str = "restricted",
        shareable: bool = False,
        deidentified: bool = False,
        token: str | None = None,
    ) -> str:
        if (
            type(content) is not dict
            or type(shareable) is not bool
            or type(deidentified) is not bool
        ):
            raise ControllerError(
                "Memory requires a JSON record and explicit boolean sharing controls"
            )
        if classification not in {"public", "restricted"}:
            raise ControllerError("Unknown memory classification")
        if shareable:
            self._operator(token)
            if (
                classification != "public"
                or not deidentified
                or content.get("kind") not in {"methodology", "claim"}
            ):
                raise AuthorityError(
                    "Shared memory must be approved public, de-identified methodology or claims"
                )
        record_id = uuid.uuid4().hex
        with self._transaction():
            self._db.execute(
                "INSERT INTO memory VALUES (?,?,?,?,?,?)",
                (
                    record_id,
                    project_id,
                    classification,
                    _json(content),
                    int(shareable),
                    time.time(),
                ),
            )
            self._event(
                None,
                "memory_recorded",
                record_id,
                {
                    "project_id": project_id,
                    "classification": classification,
                    "shareable": shareable,
                },
            )
        return record_id

    def retrieve_memory(
        self, record_id: str, *, principal: str, requesting_project: str
    ) -> dict[str, Any]:
        """Authorize against the source project AND the asking project's context.

        A grant on the record's own project does not follow its principal into an
        unrelated campaign. Approved public de-identified records are the only
        cross-project channel, so restricted material cannot travel that way.
        """
        if type(requesting_project) is not str or not requesting_project:
            raise ControllerError("Memory retrieval requires an explicit requesting project")
        allowed = False
        content = None
        cross_project = None
        reason = "unknown memory record"
        with self._transaction():
            row = self._db.execute("SELECT * FROM memory WHERE id=?", (record_id,)).fetchone()
            if row is not None:
                permission = self._db.execute(
                    "SELECT 1 FROM project_permissions WHERE principal=? AND project_id=? AND classification=?",
                    (principal, row["project_id"], row["classification"]),
                ).fetchone()
                cross_project = requesting_project != row["project_id"]
                approved_public = row["classification"] == "public" and bool(row["shareable"])
                allowed = (bool(permission) and not cross_project) or approved_public
                if allowed:
                    content = json.loads(row["content"])
                    reason = (
                        "approved public de-identified sharing"
                        if cross_project
                        else "source project permission"
                    )
                elif cross_project:
                    reason = "cross-project retrieval requires approved public sharing"
                else:
                    reason = "principal lacks source project permission"
            self._event(
                None,
                "memory_retrieval_allowed" if allowed else "memory_retrieval_denied",
                record_id,
                {
                    "principal": principal,
                    "requesting_project": requesting_project,
                    "cross_project": cross_project,
                    "reason": reason,
                },
            )
        if not allowed:
            raise AuthorityError(
                "Memory retrieval lacks source permission or approved public sharing"
            )
        return content


Store = Controller
