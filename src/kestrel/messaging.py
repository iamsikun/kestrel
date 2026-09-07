"""Durable assistant state: projection, attention, milestones and schedules.

The assistant keeps its own database. It reads research state read-only through
`sources`, derives conditions with a deterministic rule set, and records items,
revisions, attention and notification intents in one transaction per batch.

Nothing here executes research work, changes an approval, or sends anything. An
acknowledgement silences a reminder; it never resolves a blocker or certifies
evidence. Resolution is only ever read back from source state.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field

from kestrel.briefings import build_briefing, evidence_epoch, render_plain, safe_text
from kestrel.contracts import Identifier, StrictModel, canonical, digest, parse_json
from kestrel.controller import TERMINAL
from kestrel.notifications import AUTHORITY_SCHEMA
from kestrel.reporting import build_report
from kestrel.sources import LabSources, SourceUnavailable
from kestrel.telegram import PAIRING_SCHEMA

ASSISTANT_SCHEMA_VERSION = 1
CONFIG_VERSION = "0.1"
ITEM_REF = re.compile(r"^(M[0-9]{1,9})-r([0-9]{1,6})$")
ATTENTION_STATES = ("OPEN", "ACKNOWLEDGED", "SNOOZED", "RESOLVED", "SUPERSEDED")
ROUTES = ("critical", "timely", "digest", "inbox")

#: Salience route per derived condition. Routing is policy, not inference from
#: engagement, and integrity conditions can never be demoted below `timely`.
CONDITION_ROUTES = {
    "approval_needed": "timely",
    "approval_unusable": "timely",
    "blocked_work": "timely",
    "budget_exhausted": "timely",
    "uncertain_capacity": "timely",
    "expired_lease": "timely",
    "evidence_correction": "timely",
    "verification_failed": "timely",
    "source_gap": "timely",
    "milestone_reached": "timely",
    "milestone_reopened": "timely",
    "campaign_complete": "digest",
    "watched_campaign_complete": "timely",
    "watched_attempt_settled": "timely",
    "watched_task_settled": "timely",
}


class MessagingError(ValueError):
    """A messaging operation was refused. Inherits the CLI's exit code 2."""


# --------------------------------------------------------------------------
# Typed operator inputs. These are validated data, never executable rules.
# --------------------------------------------------------------------------

class MilestonePredicate(StrictModel):
    """One campaign clause. There is no expression, SQL, or 'queue empty' form."""

    campaign_id: Identifier
    terminal_state: Literal["COMPLETE"] = "COMPLETE"
    required_protocol: Literal["valid", "incomplete", "invalid"] = "valid"
    allowed_findings: list[Literal["supported_in_scope", "not_supported",
                                   "inconclusive"]] | None = None
    required_assurance: list[Literal["imported", "traceable", "independently_recomputed",
                                     "replicated"]] | None = None


class MilestoneDefinition(StrictModel):
    definition_version: Literal["0.1"] = "0.1"
    label: str
    predicates: list[MilestonePredicate] = Field(min_length=1, max_length=32)


class MessagingConfig(StrictModel):
    config_version: Literal["0.1"] = CONFIG_VERSION
    lab: str
    timezone: str = "UTC"
    brief_local_time: str = "08:00"
    quiet_start: str = "22:00"
    quiet_end: str = "08:00"
    #: Conditions explicitly allowed to interrupt quiet hours. Empty by default:
    #: an overnight bypass is an operator choice, never a default.
    quiet_hour_exceptions: list[str] = []
    batch_limit: int = 200
    projection_freshness_seconds: float = 900.0
    intent_expiry_seconds: dict[str, float] = {}

    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise MessagingError(f"Unknown IANA timezone: {self.timezone}") from exc


DEFAULT_EXPIRY = {"daily_brief": 86_400.0, "catch_up": 86_400.0, "backfill_summary": 86_400.0,
                  "critical": 86_400.0, "timely": 21_600.0, "reply": 300.0}


# --------------------------------------------------------------------------
# Local time handling
# --------------------------------------------------------------------------

def parse_local_time(value: str) -> datetime.time:
    try:
        hour, minute = (int(part) for part in value.split(":"))
        return datetime.time(hour, minute)
    except (TypeError, ValueError) as exc:
        raise MessagingError(f"Local time must be HH:MM, not {value!r}") from exc


def local_instant(date: datetime.date, moment: datetime.time, zone: ZoneInfo) -> float:
    """UTC epoch for a local wall-clock time, with explicit DST rules.

    An ambiguous time (a repeated hour) uses its *first* occurrence. A skipped
    time (a spring-forward gap) uses the next instant that actually exists,
    found by bisecting the offset change rather than by guessing an hour.
    """
    naive = datetime.datetime.combine(date, moment)
    first = naive.replace(tzinfo=zone, fold=0)
    second = naive.replace(tzinfo=zone, fold=1)
    if first.astimezone(datetime.UTC).astimezone(zone).replace(tzinfo=None) == naive:
        return first.timestamp()
    # Nonexistent local time: the transition lies between the two candidate
    # instants. Bisect to the second.
    low = min(first.timestamp(), second.timestamp())
    high = max(first.timestamp(), second.timestamp())
    base = datetime.datetime.fromtimestamp(low, datetime.UTC).astimezone(zone).utcoffset()
    while high - low > 1:
        middle = (low + high) // 2
        offset = datetime.datetime.fromtimestamp(middle, datetime.UTC).astimezone(
            zone).utcoffset()
        if offset == base:
            low = middle
        else:
            high = middle
    return float(high)


def local_date(instant: float, zone: ZoneInfo) -> str:
    return datetime.datetime.fromtimestamp(instant, datetime.UTC).astimezone(zone).date(
    ).isoformat()


def in_quiet_hours(instant: float, config: MessagingConfig) -> bool:
    zone = config.zone()
    now = datetime.datetime.fromtimestamp(instant, datetime.UTC).astimezone(zone).time()
    start = parse_local_time(config.quiet_start)
    end = parse_local_time(config.quiet_end)
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def next_waking_instant(instant: float, config: MessagingConfig) -> float:
    """The next local instant outside quiet hours. No automatic overnight bypass."""
    if not in_quiet_hours(instant, config):
        return instant
    zone = config.zone()
    end = parse_local_time(config.quiet_end)
    moment = datetime.datetime.fromtimestamp(instant, datetime.UTC).astimezone(zone)
    candidate = local_instant(moment.date(), end, zone)
    if candidate <= instant:
        candidate = local_instant(moment.date() + datetime.timedelta(days=1), end, zone)
    return candidate


# --------------------------------------------------------------------------
# Layout and configuration
# --------------------------------------------------------------------------

DIRECTORIES = ("assistant-private", "assistant-private/briefings", "notification-export",
               "notification-export/envelopes", "notification-export/permits",
               "telegram-runtime", "telegram-secrets")


def init_messaging(root: Path, lab: Path, *, timezone: str = "UTC",
                   brief_local_time: str = "08:00") -> dict[str, Any]:
    """Create the external messaging ownership domains and its configuration.

    Directories are separated so a deployment can own them under distinct
    identities. Creating them here does not install a service, grant egress, or
    provision any credential.
    """
    root = Path(root).absolute()
    lab = Path(lab).absolute()
    if root == lab or root.is_relative_to(lab) or lab.is_relative_to(root):
        raise MessagingError("Messaging state must be a separate ownership domain from the lab")
    if not (lab / "lab.json").is_file():
        raise MessagingError("Supply an initialized external developer lab")
    config = MessagingConfig(lab=str(lab), timezone=timezone,
                             brief_local_time=brief_local_time)
    config.zone()
    parse_local_time(brief_local_time)
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    for relative in DIRECTORIES:
        (root / relative).mkdir(mode=0o700)
    path = root / "messaging.json"
    with path.open("x") as stream:
        path.chmod(0o600)
        stream.write(canonical(config).decode())
    token = secrets.token_hex(32)
    token_path = root / "assistant-private" / "operator.token"
    with token_path.open("x") as stream:
        token_path.chmod(0o600)
        stream.write(token)
    with Assistant(root, config) as assistant:
        assistant.initialize_operator(token)
    return {"messaging_root": str(root), "lab": str(lab), "timezone": timezone,
            "directories": list(DIRECTORIES),
            "operator_token_file": str(token_path),
            "note": "Configuration only. No bot, credential, service, schedule daemon or "
                    "egress permission was created."}


def load_config(root: Path) -> MessagingConfig:
    path = Path(root).absolute() / "messaging.json"
    if path.is_symlink() or not path.is_file():
        raise MessagingError("Messaging configuration is unavailable; run notify init")
    return MessagingConfig.model_validate(parse_json(path.read_bytes(), max_bytes=64 * 1024))


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_bindings(
    feed TEXT PRIMARY KEY, fingerprint TEXT, epoch INTEGER NOT NULL,
    cursor INTEGER NOT NULL, last_identity TEXT, status TEXT NOT NULL,
    updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS items(
    id TEXT PRIMARY KEY, item_key TEXT UNIQUE NOT NULL, scope TEXT NOT NULL,
    condition TEXT NOT NULL, route TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS item_revisions(
    item_id TEXT NOT NULL REFERENCES items(id), revision INTEGER NOT NULL,
    semantic TEXT NOT NULL, payload TEXT NOT NULL, salience TEXT NOT NULL,
    attention TEXT NOT NULL DEFAULT 'OPEN', attention_until REAL,
    resolved_at REAL, first_observed REAL NOT NULL, last_observed REAL NOT NULL,
    cutoffs TEXT NOT NULL, PRIMARY KEY(item_id, revision));
CREATE TABLE IF NOT EXISTS attention_history(
    id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, revision INTEGER NOT NULL,
    action TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL,
    request_id TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS subscriptions(
    id TEXT PRIMARY KEY, scope TEXT NOT NULL, version INTEGER NOT NULL,
    route TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS milestones(
    id TEXT NOT NULL, version INTEGER NOT NULL, label TEXT NOT NULL,
    definition TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
    PRIMARY KEY(id, version));
CREATE TRIGGER IF NOT EXISTS milestone_definition_immutable
BEFORE UPDATE OF id,version,label,definition ON milestones
BEGIN SELECT RAISE(ABORT, 'a milestone version is immutable; register a new version'); END;
CREATE TABLE IF NOT EXISTS milestone_history(
    id INTEGER PRIMARY KEY AUTOINCREMENT, milestone_id TEXT NOT NULL,
    version INTEGER NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL,
    created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS schedules(
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, timezone TEXT NOT NULL,
    local_time TEXT NOT NULL, preference_version INTEGER NOT NULL,
    next_due REAL NOT NULL, last_local_date TEXT, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS occurrences(
    id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, purpose TEXT NOT NULL,
    local_date TEXT NOT NULL, due_at REAL NOT NULL, preference_version INTEGER NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(schedule_id, purpose, local_date, preference_version));
CREATE TABLE IF NOT EXISTS briefings(
    id TEXT PRIMARY KEY, occurrence_id TEXT, content_digest TEXT NOT NULL,
    record TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS intents(
    id TEXT PRIMARY KEY, dedupe TEXT UNIQUE NOT NULL, purpose TEXT NOT NULL,
    route TEXT NOT NULL, item_id TEXT, revision INTEGER, occurrence_id TEXT,
    payload TEXT NOT NULL, not_before REAL NOT NULL, expires_at REAL NOT NULL,
    state TEXT NOT NULL DEFAULT 'PENDING', reason TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS processed_requests(
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, result TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS gaps(
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, detail TEXT NOT NULL,
    opened_at REAL NOT NULL, closed_at REAL);
CREATE TRIGGER IF NOT EXISTS attention_history_append_only
BEFORE UPDATE ON attention_history
BEGIN SELECT RAISE(ABORT, 'attention history is permanent'); END;
CREATE TRIGGER IF NOT EXISTS attention_history_no_delete
BEFORE DELETE ON attention_history
BEGIN SELECT RAISE(ABORT, 'attention history is permanent'); END;
CREATE TRIGGER IF NOT EXISTS milestone_history_append_only
BEFORE UPDATE ON milestone_history
BEGIN SELECT RAISE(ABORT, 'milestone history is permanent'); END;
CREATE TRIGGER IF NOT EXISTS revision_identity_immutable
BEFORE UPDATE OF item_id,revision,semantic,payload,first_observed ON item_revisions
BEGIN SELECT RAISE(ABORT, 'a recorded revision is immutable'); END;
"""


class Assistant:
    """The offline authority side of messaging. Owns only its own database."""

    def __init__(self, root: Path, config: MessagingConfig | None = None) -> None:
        self.root = Path(root).absolute()
        self.config = config or load_config(self.root)
        self.private = self.root / "assistant-private"
        if not self.private.is_dir() or self.private.is_symlink():
            raise MessagingError("Assistant private directory is unavailable")
        path = self.private / "assistant.sqlite"
        if path.is_symlink():
            raise MessagingError("Assistant state must not redirect through a symlink")
        self._db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._migrate()

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, ASSISTANT_SCHEMA_VERSION):
            self._db.close()
            raise MessagingError(f"Unsupported assistant schema version {version}")
        self._db.executescript(SCHEMA)
        self._db.executescript(AUTHORITY_SCHEMA)
        self._db.executescript(PAIRING_SCHEMA)
        self._db.execute(f"PRAGMA user_version={ASSISTANT_SCHEMA_VERSION}")
        with self._transaction():
            self._db.execute("INSERT OR IGNORE INTO meta VALUES ('item_counter','0')")
            self._db.execute("INSERT OR IGNORE INTO meta VALUES ('paused','0')")

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Assistant:
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

    def _meta(self, key: str, default: str | None = None) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def _set_meta(self, key: str, value: str) -> None:
        self._db.execute("INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE "
                         "SET value=excluded.value", (key, value))

    def initialize_operator(self, token: str) -> None:
        """Bootstrap the messaging operator credential exactly once.

        This demonstrates service-grant policy in a developer lab. It is not a
        deployed service identity, and it is deliberately distinct from the
        lab's campaign operator token so notification authority and execution
        authority cannot be confused.
        """
        if type(token) is not str or len(token) < 32:
            raise MessagingError("The messaging operator token needs at least 32 characters")
        with self._transaction():
            if self._meta("operator_hash") is not None:
                raise MessagingError("An initialized messaging operator cannot be replaced")
            salt = secrets.token_hex(32)
            self._set_meta("operator_salt", salt)
            self._set_meta("operator_hash",
                           hashlib.sha256((salt + token).encode()).hexdigest())

    def authenticate_operator(self, token: str) -> None:
        expected = self._meta("operator_hash")
        salt = self._meta("operator_salt")
        if type(token) is not str or expected is None or salt is None:
            raise MessagingError("Messaging operator authentication is unavailable")
        actual = hashlib.sha256((salt + token).encode()).hexdigest()
        if not hmac.compare_digest(expected, actual):
            raise MessagingError("Messaging operator authentication failed")

    def sources(self) -> LabSources:
        return LabSources(Path(self.config.lab))

    # ------------------------------------------------------------------
    # Subscriptions, milestones, schedules
    # ------------------------------------------------------------------

    def watch(self, scope: str, *, route: str = "timely", now: float | None = None) -> dict:
        now = time.time() if now is None else now
        if route not in ROUTES:
            raise MessagingError(f"Unknown route: {route}")
        if not re.fullmatch(r"(campaign|task|attempt|milestone):[A-Za-z0-9][A-Za-z0-9_.-]{0,100}",
                            scope):
            raise MessagingError("A watch scope is campaign:, task:, attempt: or milestone:")
        with self._transaction():
            row = self._db.execute("SELECT * FROM subscriptions WHERE scope=? ORDER BY version "
                                   "DESC LIMIT 1", (scope,)).fetchone()
            version = (row["version"] + 1) if row else 1
            identity = f"sub-{uuid.uuid4().hex}"
            self._db.execute("INSERT INTO subscriptions VALUES (?,?,?,?,1,?)",
                             (identity, scope, version, route, now))
            if row:
                self._db.execute("UPDATE subscriptions SET active=0 WHERE id=?", (row["id"],))
        return {"subscription_id": identity, "scope": scope, "version": version, "route": route}

    def unwatch(self, scope: str) -> dict:
        with self._transaction():
            changed = self._db.execute("UPDATE subscriptions SET active=0 WHERE scope=? "
                                       "AND active=1", (scope,)).rowcount
        return {"scope": scope, "deactivated": changed}

    def subscriptions(self, *, active_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM subscriptions" + (" WHERE active=1" if active_only else "")
        return [dict(row) for row in self._db.execute(sql + " ORDER BY scope,version")]

    def add_milestone(self, identity: str, definition: MilestoneDefinition,
                      *, now: float | None = None) -> dict:
        """Register a new milestone version. Changing a definition never edits one."""
        now = time.time() if now is None else now
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,60}", identity):
            raise MessagingError("A milestone needs a bounded identifier")
        if not definition.predicates:
            raise MessagingError("A milestone needs at least one explicit campaign predicate")
        with self._transaction():
            row = self._db.execute("SELECT MAX(version) FROM milestones WHERE id=?",
                                   (identity,)).fetchone()
            version = (row[0] or 0) + 1
            self._db.execute("UPDATE milestones SET active=0 WHERE id=?", (identity,))
            self._db.execute("INSERT INTO milestones VALUES (?,?,?,?,1,?)",
                             (identity, version, definition.label,
                              canonical(definition).decode(), now))
        return {"milestone_id": identity, "version": version, "label": definition.label,
                "predicates": len(definition.predicates)}

    def milestones(self) -> list[dict]:
        return [{**dict(row), "definition": parse_json(row["definition"])}
                for row in self._db.execute(
                    "SELECT * FROM milestones ORDER BY id, version")]

    def milestone_history(self) -> list[dict]:
        return [{**dict(row), "detail": parse_json(row["detail"])}
                for row in self._db.execute("SELECT * FROM milestone_history ORDER BY id")]

    def set_schedule(self, *, local_time: str | None = None, timezone: str | None = None,
                     now: float | None = None) -> dict:
        """Create or re-version the daily briefing schedule."""
        now = time.time() if now is None else now
        config = self.config
        if timezone is not None:
            config = config.model_copy(update={"timezone": timezone})
            config.zone()
        moment = parse_local_time(local_time or config.brief_local_time)
        zone = config.zone()
        row = self._db.execute("SELECT * FROM schedules WHERE id='daily_brief'").fetchone()
        preference_version = (row["preference_version"] + 1) if row else 1
        today = datetime.datetime.fromtimestamp(now, datetime.UTC).astimezone(zone).date()
        due = local_instant(today, moment, zone)
        if due <= now:
            due = local_instant(today + datetime.timedelta(days=1), moment, zone)
        with self._transaction():
            self._db.execute(
                "INSERT INTO schedules VALUES ('daily_brief','daily_brief',?,?,?,?,?,1) "
                "ON CONFLICT(id) DO UPDATE SET timezone=excluded.timezone, "
                "local_time=excluded.local_time, "
                "preference_version=excluded.preference_version, "
                "next_due=excluded.next_due, active=1",
                (config.timezone, moment.strftime("%H:%M"), preference_version, due,
                 row["last_local_date"] if row else None))
        return {"schedule_id": "daily_brief", "timezone": config.timezone,
                "local_time": moment.strftime("%H:%M"),
                "preference_version": preference_version, "next_due": due}

    def schedule(self) -> dict | None:
        row = self._db.execute("SELECT * FROM schedules WHERE id='daily_brief'").fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Source binding
    # ------------------------------------------------------------------

    def bindings(self) -> dict[str, dict]:
        return {row["feed"]: dict(row)
                for row in self._db.execute("SELECT * FROM source_bindings")}

    def _bind(self, feed: str, fingerprint: str | None, *, now: float,
              epoch: int | None = None) -> None:
        existing = self.bindings().get(feed)
        self._db.execute(
            "INSERT INTO source_bindings VALUES (?,?,?,0,NULL,'bound',?) "
            "ON CONFLICT(feed) DO UPDATE SET fingerprint=excluded.fingerprint, "
            "epoch=excluded.epoch, cursor=0, last_identity=NULL, status='bound', "
            "updated_at=excluded.updated_at",
            (feed, fingerprint,
             epoch if epoch is not None else ((existing["epoch"] + 1) if existing else 1), now))

    def _check_binding(self, feed: str, source: Any, *, now: float) -> dict[str, Any]:
        """Confirm continuity, or demand an explicit operator rebind.

        A replaced, restored or truncated source is reported as an uncertain
        continuity gap. Identity is never inferred from a path or a file
        modification time, and a cursor is never silently reset.
        """
        fingerprint = source.fingerprint()
        head = source.head_sequence() if feed == "controller" else source.head_event_id()
        binding = self.bindings().get(feed)
        if binding is None:
            return {"feed": feed, "status": "unbound", "fingerprint": fingerprint, "head": head}
        if binding["status"] != "bound":
            return {"feed": feed, "status": binding["status"], "fingerprint": fingerprint,
                    "head": head, "detail": "Explicit operator rebinding is required"}
        if fingerprint is not None and binding["fingerprint"] not in (None, fingerprint):
            return {"feed": feed, "status": "rebind_required", "fingerprint": fingerprint,
                    "head": head, "detail": "Source identity changed; continuity is uncertain"}
        if binding["cursor"] > head:
            return {"feed": feed, "status": "rebind_required", "fingerprint": fingerprint,
                    "head": head, "detail": "Recorded cursor is beyond the source head"}
        if binding["last_identity"] and feed == "controller":
            observed = source.event(binding["cursor"])
            if observed is None or observed["identity"] != binding["last_identity"]:
                return {"feed": feed, "status": "rebind_required", "fingerprint": fingerprint,
                        "head": head,
                        "detail": "Last consumed event no longer matches its recorded identity"}
        return {"feed": feed, "status": "bound", "fingerprint": fingerprint, "head": head,
                "cursor": binding["cursor"], "epoch": binding["epoch"]}

    def rebind_sources(self, *, confirm: bool = False, now: float | None = None) -> dict:
        """Open a new source epoch after a restore. Requires explicit confirmation."""
        if not confirm:
            raise MessagingError(
                "Rebinding discards cursor continuity; pass --confirm after checking that "
                "this is the intended lab")
        now = time.time() if now is None else now
        with self.sources() as src:
            controller = src.controller.fingerprint()
            evidence = src.evidence.fingerprint()
        with self._transaction():
            self._bind("controller", controller, now=now)
            self._bind("evidence", evidence, now=now)
            self._db.execute("UPDATE gaps SET closed_at=? WHERE kind='source_continuity' "
                             "AND closed_at IS NULL", (now,))
        return {"rebound": True, "epochs": {feed: row["epoch"]
                                            for feed, row in self.bindings().items()},
                "note": "Historical items are retained; a fresh backfill summary follows."}

    def snapshot_sources(self, *, now: float | None = None) -> dict:
        """Authority-side snapshot for hosts where read-only WAL access is denied.

        This uses the online backup API from the messaging side and produces a
        consistent copy inside the assistant's own directory. It is a fallback
        for an enforcement profile that forbids mapping the shared-memory index,
        not a way to relax the reader.
        """
        now = time.time() if now is None else now
        lab = Path(self.config.lab)
        target = self.private / "source-snapshot"
        target.mkdir(exist_ok=True)
        produced = {}
        for name, relative in (("controller", "runtime/controller.sqlite"),
                               ("evidence", "runtime/artifacts/evidence.sqlite")):
            origin = lab / relative
            if origin.is_symlink() or not origin.is_file():
                raise MessagingError(f"Source database is unavailable: {relative}")
            destination = target / f"{name}.sqlite"
            with sqlite3.connect(f"{origin.as_uri()}?mode=ro", uri=True) as source, \
                    sqlite3.connect(destination) as copy:
                source.backup(copy)
            produced[name] = str(destination)
        return {"snapshot_at": now, "databases": produced,
                "limitation": "A snapshot is a point-in-time copy; it is not a live feed and "
                              "carries no independent scientific assurance."}

    # ------------------------------------------------------------------
    # Observation: deterministic rules over current source state
    # ------------------------------------------------------------------

    def _observe(self, src: LabSources, *, now: float) -> dict[str, Any]:
        conditions: list[dict[str, Any]] = []
        watched = {row["scope"]: row for row in self.subscriptions()}
        cutoffs = src.cutoffs()
        classes: dict[str, str] = {}
        for campaign_id in src.controller.campaign_ids():
            contract = src.controller.campaign(campaign_id)["contract"]
            classes[campaign_id] = contract.get("data_classification", "restricted")
            conditions.extend(self._campaign_conditions(src, campaign_id, now=now,
                                                        watched=watched))
        conditions.extend(self._capacity_conditions(src, now=now))
        conditions.extend(self._evidence_conditions(src))
        conditions.extend(self._milestone_conditions(src, now=now))
        for condition in conditions:
            # Every view inherits the classification of each contributing
            # source. Aggregation and identifiers do not declassify anything.
            contributing = condition["payload"].get("campaign_ids") or []
            campaign_id = condition["payload"].get("campaign_id")
            if campaign_id:
                contributing = [*contributing, campaign_id]
            inherited = [classes.get(identity, "restricted") for identity in contributing]
            condition["payload"]["classifications"] = sorted(
                set(inherited or condition["payload"].get("classifications")
                    or ["public_synthetic"]))
        return {"cutoffs": cutoffs, "conditions": conditions}

    def _campaign_conditions(self, src: LabSources, campaign_id: str, *, now: float,
                             watched: dict[str, Any]) -> list[dict[str, Any]]:
        campaign = src.controller.campaign(campaign_id)
        contract = campaign["contract"]
        tasks = src.controller.tasks(campaign_id)
        attempts = src.controller.attempts(campaign_id)
        unfinished = [task for task in tasks if task["state"] not in TERMINAL]
        blocked = [task for task in tasks if task["state"] == "BLOCKED"]
        charged = src.controller.budget_used(campaign_id)
        usable = src.controller.usable_approvals(campaign_id, campaign["digest"], now=now)
        recorded = src.controller.approvals(campaign_id)
        scope = f"campaign:{campaign_id}"
        found: list[dict[str, Any]] = []
        if unfinished and not usable:
            condition = "approval_unusable" if recorded else "approval_needed"
            found.append({"key": f"{scope}|{condition}", "scope": scope,
                          "condition": condition,
                          "payload": {"campaign_id": campaign_id,
                                      "contract_digest": campaign["digest"],
                                      "unfinished_tasks": len(unfinished),
                                      "recorded_approvals": len(recorded)},
                          "salience": "awaiting_approval"})
        if blocked:
            found.append({"key": f"{scope}|blocked_work", "scope": scope,
                          "condition": "blocked_work",
                          "payload": {"campaign_id": campaign_id,
                                      "blocked_tasks": sorted(t["id"] for t in blocked),
                                      "reasons": sorted({safe_text(t["blocked_reason"] or "")
                                                         for t in blocked}),
                                      "eligible_elsewhere": len(unfinished) - len(blocked)},
                          "salience": "blocked_work"})
        declared = contract.get("budget") or {}
        exhausted = sorted(key for key, limit in declared.items()
                           if isinstance(limit, (int, float)) and limit > 0
                           and charged.get(key, 0) >= limit)
        if exhausted and unfinished:
            found.append({"key": f"{scope}|budget_exhausted", "scope": scope,
                          "condition": "budget_exhausted",
                          "payload": {"campaign_id": campaign_id, "exhausted": exhausted,
                                      "charged": charged, "declared": declared},
                          "salience": "charged_reservations"})
        if campaign["state"] == "COMPLETE":
            report = self._safe_report(src, campaign_id, campaign, attempts, charged)
            condition = ("watched_campaign_complete" if scope in watched
                         else "campaign_complete")
            found.append({"key": f"{scope}|{condition}", "scope": scope,
                          "condition": condition,
                          "payload": {"campaign_id": campaign_id, "outcome": report["outcome"],
                                      "verification": report["verification"],
                                      "attempts_charged": len(attempts),
                                      "treatment_candidates": len(contract.get("candidates", [])),
                                      "baseline_recipes": 1 if contract.get("baseline") else 0},
                          "salience": "verified_outcome"})
            if report["verification"] == "failed":
                found.append({"key": f"{scope}|verification_failed", "scope": scope,
                              "condition": "verification_failed",
                              "payload": {"campaign_id": campaign_id,
                                          "detail": report["error"]},
                              "salience": "evidence_correction"})
        for attempt in attempts:
            attempt_scope = f"attempt:{attempt['id']}"
            if attempt_scope in watched and attempt["state"] in TERMINAL:
                found.append({"key": f"{attempt_scope}|settled", "scope": attempt_scope,
                              "condition": "watched_attempt_settled",
                              "payload": {"attempt_id": attempt["id"],
                                          "campaign_id": campaign_id,
                                          "task_id": attempt["task_id"],
                                          "state": attempt["state"],
                                          "stopped_confirmed": attempt["stopped_confirmed"],
                                          "evaluation_recorded": attempt["result"] is not None},
                              "salience": "watched_run"})
        for task in tasks:
            task_scope = f"task:{task['id']}"
            if task_scope in watched and task["state"] in TERMINAL:
                task_attempts = [a for a in attempts if a["task_id"] == task["id"]]
                found.append({"key": f"{task_scope}|settled", "scope": task_scope,
                              "condition": "watched_task_settled",
                              "payload": {"task_id": task["id"], "campaign_id": campaign_id,
                                          "state": task["state"],
                                          "attempts": sorted(a["id"] for a in task_attempts),
                                          "attempt_count": len(task_attempts)},
                              "salience": "watched_run"})
        return found

    @staticmethod
    def _safe_report(src: LabSources, campaign_id: str, campaign: dict,
                     attempts: list[dict], charged: dict) -> dict[str, Any]:
        try:
            report = build_report(campaign_id, campaign, src.evidence, attempts, charged)
            return {"verification": "recomputed", "error": None,
                    "outcome": {axis: report[axis] for axis in
                                ("execution", "validity", "finding", "assurance")},
                    "report": report}
        except (ValueError, OSError) as exc:
            return {"verification": "failed",
                    "error": f"{type(exc).__name__}: {safe_text(exc)}",
                    "outcome": {"execution": "unavailable", "validity": "unavailable",
                                "finding": "unavailable", "assurance": "unverified"},
                    "report": None}

    @staticmethod
    def _capacity_conditions(src: LabSources, *, now: float) -> list[dict[str, Any]]:
        """One grouped incident per campaign, never one alert per attempt."""
        holding: dict[str, dict[str, dict]] = {}
        for attempt in src.controller.unsettled_attempts():
            if attempt["state"] in TERMINAL and attempt["stopped_confirmed"]:
                continue
            holding.setdefault(attempt["campaign_id"], {})[attempt["id"]] = attempt
        expired: dict[str, dict[str, dict]] = {}
        for attempt in src.controller.stale_leases(now=now):
            expired.setdefault(attempt["campaign_id"], {})[attempt["id"]] = attempt
        found = []
        for campaign_id, group in sorted(holding.items()):
            found.append({"key": f"campaign:{campaign_id}|uncertain_capacity",
                          "scope": f"campaign:{campaign_id}",
                          "condition": "uncertain_capacity",
                          "payload": {"campaign_id": campaign_id,
                                      "attempts": sorted(group),
                                      "attempt_count": len(group),
                                      "states": sorted({a["state"]
                                                        for a in group.values()}),
                                      "consequence": "Reconciliation is required before any "
                                                     "retry; no blind restart is proposed."},
                          "salience": "uncertain_capacity"})
        for campaign_id, group in sorted(expired.items()):
            found.append({"key": f"campaign:{campaign_id}|expired_lease",
                          "scope": f"campaign:{campaign_id}", "condition": "expired_lease",
                          "payload": {"campaign_id": campaign_id,
                                      "attempts": sorted(group),
                                      "attempt_count": len(group),
                                      "consequence": "Lease expiry does not confirm that the "
                                                     "job stopped."},
                          "salience": "uncertain_capacity"})
        return found

    @staticmethod
    def _evidence_conditions(src: LabSources) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        for event in src.evidence.events():
            if event["kind"] not in {"invalid", "stale", "deleted"}:
                continue
            try:
                classification = src.evidence.get(event["digest"])["classification"]
            except ValueError:
                classification = "restricted"
            if groups and groups[-1]["reason"] == event["reason"] \
                    and groups[-1]["last_event_id"] + 1 == event["id"]:
                groups[-1]["last_event_id"] = event["id"]
                groups[-1]["affected"] += 1
                groups[-1]["classifications"] = sorted(
                    {*groups[-1]["classifications"], classification})
                continue
            groups.append({"reason": safe_text(event["reason"]), "kind": event["kind"],
                           "first_event_id": event["id"], "last_event_id": event["id"],
                           "affected": 1, "classifications": [classification],
                           "recorded_at": evidence_epoch(event["created_at"])})
        return [{"key": f"evidence:{group['first_event_id']}|evidence_correction",
                 "scope": f"evidence:{group['first_event_id']}",
                 "condition": "evidence_correction", "payload": group,
                 "salience": "evidence_correction"} for group in groups]

    def _milestone_conditions(self, src: LabSources, *, now: float) -> list[dict[str, Any]]:
        found = []
        for record in self.milestones():
            if not record["active"]:
                continue
            definition = MilestoneDefinition.model_validate(record["definition"])
            evaluation = self.evaluate_milestone(src, definition, now=now)
            condition = "milestone_reached" if evaluation["reached"] else "milestone_reopened"
            history = self._db.execute(
                "SELECT state FROM milestone_history WHERE milestone_id=? AND version=? "
                "ORDER BY id DESC LIMIT 1", (record["id"], record["version"])).fetchone()
            previously = history[0] == "reached" if history else False
            if not evaluation["reached"] and not previously:
                # Never announce an unreached milestone, and never infer that a
                # project is finished from an empty queue.
                continue
            scope = f"milestone:{record['id']}"
            found.append({"key": f"{scope}|{condition}", "scope": scope,
                          "condition": condition,
                          "payload": {"milestone_id": record["id"],
                                      "version": record["version"],
                                      "label": safe_text(record["label"]),
                                      "reached": evaluation["reached"],
                                      "campaign_ids": sorted(
                                          clause["campaign_id"]
                                          for clause in evaluation["clauses"]),
                                      "clauses": evaluation["clauses"]},
                          "salience": "milestone",
                          "milestone_state": "reached" if evaluation["reached"] else "reopened"})
        return found

    @staticmethod
    def evaluate_milestone(src: LabSources, definition: MilestoneDefinition,
                           *, now: float) -> dict[str, Any]:
        """Evaluate a finite conjunction of explicit campaign predicates."""
        clauses = []
        for predicate in definition.predicates:
            try:
                campaign = src.controller.campaign(predicate.campaign_id)
            except SourceUnavailable:
                clauses.append({"campaign_id": predicate.campaign_id, "satisfied": False,
                                "reason": "campaign_unavailable"})
                continue
            report = Assistant._safe_report(
                src, predicate.campaign_id, campaign,
                src.controller.attempts(predicate.campaign_id),
                src.controller.budget_used(predicate.campaign_id))
            outcome = report["outcome"]
            reasons = []
            if campaign["state"] != predicate.terminal_state:
                reasons.append("state")
            if outcome["validity"] != predicate.required_protocol:
                reasons.append("protocol")
            if predicate.allowed_findings is not None \
                    and outcome["finding"] not in predicate.allowed_findings:
                reasons.append("finding")
            if predicate.required_assurance is not None \
                    and outcome["assurance"] not in predicate.required_assurance:
                reasons.append("assurance")
            clauses.append({"campaign_id": predicate.campaign_id, "satisfied": not reasons,
                            "unsatisfied": reasons, "observed": outcome,
                            "verification": report["verification"]})
        return {"reached": all(clause["satisfied"] for clause in clauses),
                "clauses": clauses, "evaluated_at": now}

    # ------------------------------------------------------------------
    # Reconciliation
    # ------------------------------------------------------------------

    def reconcile(self, *, now: float | None = None) -> dict[str, Any]:
        """Advance cursors, project conditions, and record intents atomically."""
        now = time.time() if now is None else now
        result: dict[str, Any] = {"observed_at": now, "gaps": [], "created": [],
                                  "revised": [], "resolved": [], "intents": [],
                                  "cursors": {}}
        try:
            with self.sources() as src:
                checks = {feed: self._check_binding(
                    feed, src.controller if feed == "controller" else src.evidence, now=now)
                    for feed in ("controller", "evidence")}
                blocking = [check for check in checks.values()
                            if check["status"] == "rebind_required"]
                if blocking:
                    self._open_gap("source_continuity", blocking, now=now)
                    result["gaps"] = blocking
                    result["paused"] = True
                    return result
                backfill = any(check["status"] == "unbound" for check in checks.values())
                observation = self._observe(src, now=now)
                events = src.controller.events(
                    after=checks["controller"].get("cursor", 0),
                    limit=self.config.batch_limit)
        except SourceUnavailable as exc:
            gap = {"kind": "source_unavailable", "detail": safe_text(exc)}
            self._open_gap("source_unavailable", [gap], now=now)
            result["gaps"] = [gap]
            result["paused"] = True
            return result
        with self._transaction():
            self._db.execute("UPDATE gaps SET closed_at=? WHERE kind IN "
                             "('source_unavailable','source_continuity') AND closed_at IS NULL",
                             (now,))
            for feed, check in checks.items():
                if check["status"] == "unbound":
                    self._bind(feed, check["fingerprint"], now=now, epoch=1)
            self._apply(observation, events=events, checks=checks, now=now,
                        backfill=backfill, result=result)
        return result

    def _apply(self, observation: dict, *, events: list[dict], checks: dict,
               now: float, backfill: bool, result: dict) -> None:
        observed = {condition["key"]: condition for condition in observation["conditions"]}
        stored = {row["item_key"]: dict(row)
                  for row in self._db.execute("SELECT * FROM items")}
        cutoffs = canonical(observation["cutoffs"]).decode()
        new_revisions: list[tuple[str, int, dict]] = []
        for key, condition in sorted(observed.items()):
            semantic = digest({"condition": condition["condition"],
                               "scope": condition["scope"], "payload": condition["payload"]})
            item = stored.get(key)
            if item is None:
                item_id = self._next_item_id()
                route = CONDITION_ROUTES.get(condition["condition"], "inbox")
                self._db.execute("INSERT INTO items VALUES (?,?,?,?,?,?)",
                                 (item_id, key, condition["scope"], condition["condition"],
                                  route, now))
                self._write_revision(item_id, 1, condition, semantic, cutoffs, now)
                new_revisions.append((item_id, 1, condition))
                result["created"].append(f"{item_id}-r1")
                continue
            item_id = item["id"]
            latest = self._latest_revision(item_id)
            if latest["semantic"] == semantic and latest["attention"] != "RESOLVED":
                self._db.execute("UPDATE item_revisions SET last_observed=? WHERE item_id=? "
                                 "AND revision=?", (now, item_id, latest["revision"]))
                continue
            revision = latest["revision"] + 1
            self._write_revision(item_id, revision, condition, semantic, cutoffs, now)
            new_revisions.append((item_id, revision, condition))
            result["revised"].append(f"{item_id}-r{revision}")
        for key, item in sorted(stored.items()):
            if key in observed:
                continue
            latest = self._latest_revision(item["id"])
            if latest is None or latest["attention"] == "RESOLVED":
                continue
            self._db.execute("UPDATE item_revisions SET attention='RESOLVED', resolved_at=? "
                             "WHERE item_id=? AND revision=?",
                             (now, item["id"], latest["revision"]))
            self._history(item["id"], latest["revision"], "resolved",
                          "condition no longer present in source state", "projector", None, now)
            self._suppress_intents(item["id"], "resolved before delivery", now)
            result["resolved"].append(f"{item['id']}-r{latest['revision']}")
        for item_id, revision, condition in new_revisions:
            if condition["condition"].startswith("milestone_"):
                self._db.execute("INSERT INTO milestone_history"
                                 "(milestone_id,version,state,detail,created_at) "
                                 "VALUES (?,?,?,?,?)",
                                 (condition["payload"]["milestone_id"],
                                  condition["payload"]["version"],
                                  condition["milestone_state"],
                                  canonical(condition["payload"]).decode(), now))
            # A superseded revision's pending notification must not be delivered.
            self._suppress_intents(item_id, "superseded by a newer revision", now,
                                   below_revision=revision)
        if backfill:
            self._create_intent(purpose="backfill_summary", route="timely",
                                payload={"items": len(new_revisions),
                                         "note": "Existing state was projected into the local "
                                                 "inbox; historical events are not replayed."},
                                now=now, result=result)
        else:
            for item_id, revision, condition in new_revisions:
                route = CONDITION_ROUTES.get(condition["condition"], "inbox")
                if route in ("critical", "timely"):
                    self._create_intent(purpose=f"item:{condition['condition']}", route=route,
                                        payload={"item": f"{item_id}-r{revision}",
                                                 "condition": condition["condition"],
                                                 "scope": condition["scope"],
                                                 "detail": condition["payload"]},
                                        now=now, result=result, item_id=item_id,
                                        revision=revision)
        self._advance_schedule(now=now, result=result)
        for feed, check in checks.items():
            if feed == "controller":
                # Advance only over events actually consumed in this batch, and
                # retain the identity of the last one so a replaced database
                # cannot silently continue the cursor.
                cursor = events[-1]["sequence"] if events else check.get("cursor", 0)
                identity = events[-1]["identity"] if events else None
            else:
                # Evidence conditions are recomputed from current state each
                # pass, so its cursor tracks the observed head.
                cursor, identity = check["head"], None
            self._db.execute(
                "UPDATE source_bindings SET cursor=?, last_identity=COALESCE(?,last_identity), "
                "updated_at=? WHERE feed=?", (cursor, identity, now, feed))
            result["cursors"][feed] = cursor

    def _next_item_id(self) -> str:
        counter = int(self._meta("item_counter", "0")) + 1
        self._set_meta("item_counter", str(counter))
        return f"M{counter}"

    def _write_revision(self, item_id: str, revision: int, condition: dict, semantic: str,
                        cutoffs: str, now: float) -> None:
        self._db.execute(
            "INSERT INTO item_revisions(item_id,revision,semantic,payload,salience,"
            "first_observed,last_observed,cutoffs) VALUES (?,?,?,?,?,?,?,?)",
            (item_id, revision, semantic, canonical(condition["payload"]).decode(),
             condition["salience"], now, now, cutoffs))
        self._history(item_id, revision, "observed", condition["condition"], "projector",
                      None, now)

    def _latest_revision(self, item_id: str) -> dict | None:
        row = self._db.execute("SELECT * FROM item_revisions WHERE item_id=? "
                               "ORDER BY revision DESC LIMIT 1", (item_id,)).fetchone()
        return dict(row) if row else None

    def _history(self, item_id: str, revision: int, action: str, reason: str, actor: str,
                 request_id: str | None, now: float) -> None:
        self._db.execute("INSERT INTO attention_history"
                         "(item_id,revision,action,reason,actor,request_id,created_at) "
                         "VALUES (?,?,?,?,?,?,?)",
                         (item_id, revision, action, safe_text(reason), actor, request_id, now))

    def _suppress_intents(self, item_id: str, reason: str, now: float,
                          *, below_revision: int | None = None) -> None:
        sql = ("UPDATE intents SET state='SUPPRESSED', reason=?, updated_at=? "
               "WHERE item_id=? AND state IN ('PENDING','READY','RETRY_WAIT')")
        parameters: tuple = (reason, now, item_id)
        if below_revision is not None:
            sql += " AND revision<?"
            parameters = (*parameters, below_revision)
        self._db.execute(sql, parameters)

    def _create_intent(self, *, purpose: str, route: str, payload: dict, now: float,
                       result: dict, item_id: str | None = None, revision: int | None = None,
                       occurrence_id: str | None = None,
                       respect_quiet_hours: bool = True) -> str | None:
        dedupe = digest({"purpose": purpose, "route": route, "item": item_id,
                         "revision": revision, "occurrence": occurrence_id,
                         "payload": payload})
        if self._db.execute("SELECT 1 FROM intents WHERE dedupe=?", (dedupe,)).fetchone():
            return None
        default = (DEFAULT_EXPIRY["reply"] if purpose.startswith("reply:")
                   else DEFAULT_EXPIRY.get(purpose, DEFAULT_EXPIRY.get(route, 21_600.0)))
        expiry = self.config.intent_expiry_seconds.get(purpose, default)
        not_before = now
        condition = payload.get("condition")
        if respect_quiet_hours and (route != "critical"
                                    or condition not in self.config.quiet_hour_exceptions):
            # A requested reply is not deferred: the operator just asked.
            not_before = next_waking_instant(now, self.config)
        identity = f"intent-{uuid.uuid4().hex}"
        self._db.execute(
            "INSERT INTO intents(id,dedupe,purpose,route,item_id,revision,occurrence_id,"
            "payload,not_before,expires_at,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (identity, dedupe, purpose, route, item_id, revision, occurrence_id,
             canonical(payload).decode(), not_before, now + expiry, now, now))
        result["intents"].append(identity)
        return identity

    # ------------------------------------------------------------------
    # Schedules
    # ------------------------------------------------------------------

    def _advance_schedule(self, *, now: float, result: dict) -> None:
        row = self._db.execute("SELECT * FROM schedules WHERE id='daily_brief' "
                               "AND active=1").fetchone()
        if row is None or row["next_due"] > now:
            return
        zone = ZoneInfo(row["timezone"])
        moment = parse_local_time(row["local_time"])
        due_dates = []
        cursor = row["next_due"]
        while cursor <= now and len(due_dates) <= 400:
            date = datetime.datetime.fromtimestamp(cursor, datetime.UTC).astimezone(
                zone).date()
            due_dates.append((date, cursor))
            cursor = local_instant(date + datetime.timedelta(days=1), moment, zone)
        last_seen = row["last_local_date"]
        # A system clock rollback must not re-emit an occurrence already produced.
        due_dates = [(date, instant) for date, instant in due_dates
                     if last_seen is None or date.isoformat() > last_seen]
        if not due_dates:
            self._db.execute("UPDATE schedules SET next_due=? WHERE id='daily_brief'",
                             (cursor,))
            return
        # One occurrence per run: a single catch-up replaces a week of mornings.
        date, instant = due_dates[-1]
        purpose = "daily_brief" if len(due_dates) == 1 else "catch_up"
        occurrence = f"occ-{uuid.uuid4().hex}"
        try:
            self._db.execute("INSERT INTO occurrences VALUES (?,?,?,?,?,?,?)",
                             (occurrence, "daily_brief", purpose, date.isoformat(), instant,
                              row["preference_version"], now))
        except sqlite3.IntegrityError:
            self._db.execute("UPDATE schedules SET next_due=?, last_local_date=? "
                             "WHERE id='daily_brief'", (cursor, date.isoformat()))
            return
        briefing = self._record_briefing(occurrence, now=now,
                                         missed_days=len(due_dates) - 1)
        self._db.execute("UPDATE schedules SET next_due=?, last_local_date=? "
                         "WHERE id='daily_brief'", (cursor, date.isoformat()))
        self._create_intent(purpose=purpose, route="timely",
                            payload={"occurrence": occurrence, "local_date": date.isoformat(),
                                     "briefing": briefing["id"],
                                     "content_digest": briefing["content_digest"],
                                     "missed_days": len(due_dates) - 1,
                                     "summary": briefing["summary"]},
                            now=now, result=result, occurrence_id=occurrence)
        result.setdefault("occurrences", []).append(
            {"id": occurrence, "purpose": purpose, "local_date": date.isoformat(),
             "missed_days": len(due_dates) - 1})

    def _record_briefing(self, occurrence: str | None, *, now: float,
                         missed_days: int = 0) -> dict[str, Any]:
        with self.sources() as src:
            record = build_briefing(src, now=now, label=str(self.config.lab))
        if missed_days:
            record["coverage"]["catch_up_days"] = missed_days
        identity = f"brief-{uuid.uuid4().hex}"
        self._db.execute("INSERT INTO briefings VALUES (?,?,?,?,?)",
                         (identity, occurrence, record["content_digest"],
                          canonical(record).decode(), now))
        path = self.private / "briefings" / f"{identity}.json"
        path.write_bytes(canonical(record))
        return {"id": identity, "content_digest": record["content_digest"],
                "summary": render_plain(record), "path": str(path)}

    def briefing(self, identity: str) -> dict:
        row = self._db.execute("SELECT * FROM briefings WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise MessagingError("Unknown briefing")
        return {**dict(row), "record": parse_json(row["record"], max_bytes=8 * 1024**2)}

    # ------------------------------------------------------------------
    # Inbox and attention
    # ------------------------------------------------------------------

    @staticmethod
    def parse_reference(reference: str) -> tuple[str, int]:
        match = ITEM_REF.match(str(reference).strip())
        if not match:
            raise MessagingError("An item reference binds item and revision, such as M12-r1")
        return match.group(1), int(match.group(2))

    def inbox(self, *, include_resolved: bool = False, limit: int = 100) -> list[dict]:
        rows = self._db.execute(
            "SELECT i.id AS item_id, i.item_key, i.scope, i.condition, i.route, "
            "r.revision, r.semantic, r.payload, r.salience, r.attention, r.attention_until, "
            "r.resolved_at, r.first_observed, r.last_observed "
            "FROM items i JOIN item_revisions r ON r.item_id=i.id "
            "WHERE r.revision=(SELECT MAX(revision) FROM item_revisions WHERE item_id=i.id) "
            "ORDER BY r.last_observed DESC, i.id LIMIT ?", (limit,)).fetchall()
        items = []
        for row in rows:
            if not include_resolved and row["attention"] == "RESOLVED":
                continue
            items.append({"reference": f"{row['item_id']}-r{row['revision']}",
                          "item_id": row["item_id"], "revision": row["revision"],
                          "scope": row["scope"], "condition": row["condition"],
                          "route": row["route"], "attention": row["attention"],
                          "attention_until": row["attention_until"],
                          "salience": row["salience"],
                          "first_observed": row["first_observed"],
                          "last_observed": row["last_observed"],
                          "detail": parse_json(row["payload"])})
        return items

    def item(self, reference: str) -> dict:
        item_id, revision = self.parse_reference(reference)
        row = self._db.execute(
            "SELECT * FROM item_revisions WHERE item_id=? AND revision=?",
            (item_id, revision)).fetchone()
        if row is None:
            raise MessagingError(f"Unknown item reference {reference}")
        item = self._db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        latest = self._latest_revision(item_id)
        history = [dict(entry) for entry in self._db.execute(
            "SELECT * FROM attention_history WHERE item_id=? ORDER BY id", (item_id,))]
        return {"reference": reference, "item_id": item_id, "revision": revision,
                "scope": item["scope"], "condition": item["condition"],
                "route": item["route"], "attention": row["attention"],
                "attention_until": row["attention_until"], "resolved_at": row["resolved_at"],
                "detail": parse_json(row["payload"]), "cutoffs": parse_json(row["cutoffs"]),
                "latest_revision": latest["revision"], "stale": latest["revision"] != revision,
                "history": history}

    def _mutate_attention(self, reference: str, *, action: str, actor: str, now: float,
                          request_id: str | None, until: float | None = None,
                          reply: dict | None = None) -> dict:
        item_id, revision = self.parse_reference(reference)
        with self._transaction():
            if request_id and self._db.execute(
                    "SELECT 1 FROM processed_requests WHERE id=?", (request_id,)).fetchone():
                row = self._db.execute("SELECT result FROM processed_requests WHERE id=?",
                                       (request_id,)).fetchone()
                return parse_json(row[0])
            current = self._latest_revision(item_id)
            if current is None:
                raise MessagingError(f"Unknown item {item_id}")
            if current["revision"] != revision:
                # A stale reference must never acknowledge newly worsened evidence.
                raise MessagingError(
                    f"{reference} is stale; the current revision is "
                    f"{item_id}-r{current['revision']}. Refresh the inbox and refer to it "
                    "explicitly.")
            if current["attention"] == "RESOLVED":
                raise MessagingError(f"{reference} is already resolved in source state")
            state = "ACKNOWLEDGED" if action == "acknowledge" else "SNOOZED"
            self._db.execute("UPDATE item_revisions SET attention=?, attention_until=? "
                             "WHERE item_id=? AND revision=?",
                             (state, until, item_id, revision))
            reason = ("acknowledged; the underlying condition is unchanged"
                      if action == "acknowledge" else f"snoozed until {until}")
            self._history(item_id, revision, action, reason, actor, request_id, now)
            self._suppress_intents(item_id, f"{action} by {actor}", now)
            result = {"reference": reference, "attention": state, "attention_until": until,
                      "resolved": False,
                      "note": "Attention state only. This does not resolve the condition, "
                              "approve work, or certify evidence."}
            if reply is not None:
                self._create_intent(purpose=reply["purpose"], route="timely",
                                    payload=reply["payload"], now=now,
                                    result={"intents": []}, item_id=item_id,
                                    revision=revision, respect_quiet_hours=False)
            if request_id:
                self._db.execute("INSERT INTO processed_requests VALUES (?,?,?,?)",
                                 (request_id, action, canonical(result).decode(), now))
        return result

    def acknowledge(self, reference: str, *, actor: str = "operator",
                    now: float | None = None, request_id: str | None = None,
                    reply: dict | None = None) -> dict:
        return self._mutate_attention(reference, action="acknowledge", actor=actor,
                                      now=time.time() if now is None else now,
                                      request_id=request_id, reply=reply)

    def snooze(self, reference: str, duration: str, *, actor: str = "operator",
               now: float | None = None, request_id: str | None = None,
               reply: dict | None = None) -> dict:
        now = time.time() if now is None else now
        return self._mutate_attention(reference, action="snooze", actor=actor, now=now,
                                      request_id=request_id, reply=reply,
                                      until=self.resolve_deferral(duration, now=now))

    def record_request(self, request_id: str, kind: str, result: dict, *, now: float,
                       reply: dict | None = None,
                       mutation: tuple[str, bool] | None = None) -> dict:
        """Commit one command's effect, its reply intent and its request id together.

        A restart between the inbound journal, this commit, and delivery of the
        answer can never lose a committed command or apply it twice. Whether the
        answer actually reached the operator remains uncertain.
        """
        with self._transaction():
            existing = self._db.execute("SELECT result FROM processed_requests WHERE id=?",
                                        (request_id,)).fetchone()
            if existing:
                return parse_json(existing[0])
            if mutation is not None:
                self._set_meta("paused", "1" if mutation[1] else "0")
                self._set_meta("paused_at", str(now))
            if reply is not None:
                self._create_intent(purpose=reply["purpose"], route="timely",
                                    payload=reply["payload"], now=now,
                                    result={"intents": []}, respect_quiet_hours=False)
            self._db.execute("INSERT INTO processed_requests VALUES (?,?,?,?)",
                             (request_id, kind, canonical(result).decode(), now))
        return result

    def processed(self, request_id: str) -> dict | None:
        row = self._db.execute("SELECT * FROM processed_requests WHERE id=?",
                               (request_id,)).fetchone()
        return {**dict(row), "result": parse_json(row["result"])} if row else None

    def resolve_deferral(self, duration: str, *, now: float) -> float:
        """`1h`, `30m`, `2d`, or `tomorrow` meaning the next configured brief time."""
        text = str(duration).strip().lower()
        if text == "tomorrow":
            zone = self.config.zone()
            moment = parse_local_time(self.config.brief_local_time)
            today = datetime.datetime.fromtimestamp(now, datetime.UTC).astimezone(zone).date()
            candidate = local_instant(today, moment, zone)
            if candidate <= now:
                candidate = local_instant(today + datetime.timedelta(days=1), moment, zone)
            return candidate
        match = re.fullmatch(r"(\d{1,4})([mhd])", text)
        if not match:
            raise MessagingError("Defer by 30m, 1h, 2d, or tomorrow")
        scale = {"m": 60, "h": 3600, "d": 86_400}[match.group(2)]
        seconds = int(match.group(1)) * scale
        if not 60 <= seconds <= 30 * 86_400:
            raise MessagingError("A deferral runs from one minute to thirty days")
        return now + seconds

    # ------------------------------------------------------------------
    # Intents, gaps and status
    # ------------------------------------------------------------------

    def intents(self, *, state: str | None = None, limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM intents"
        parameters: tuple = ()
        if state:
            sql += " WHERE state=?"
            parameters = (state,)
        rows = self._db.execute(sql + " ORDER BY created_at,id LIMIT ?",
                                (*parameters, limit)).fetchall()
        return [{**dict(row), "payload": parse_json(row["payload"])} for row in rows]

    def expire_intents(self, *, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._transaction():
            return self._db.execute(
                "UPDATE intents SET state='EXPIRED', reason='intent expiry elapsed', "
                "updated_at=? WHERE expires_at<=? AND state IN "
                "('PENDING','READY','RETRY_WAIT')", (now, now)).rowcount

    def _open_gap(self, kind: str, detail: Any, *, now: float) -> None:
        with self._transaction():
            existing = self._db.execute(
                "SELECT 1 FROM gaps WHERE kind=? AND closed_at IS NULL", (kind,)).fetchone()
            if not existing:
                self._db.execute("INSERT INTO gaps(kind,detail,opened_at) VALUES (?,?,?)",
                                 (kind, canonical(detail).decode(), now))
            if kind == "source_continuity":
                # Projection stays paused until an operator confirms which lab
                # this is. A restored database is not silently continued.
                for entry in detail:
                    self._db.execute(
                        "UPDATE source_bindings SET status='rebind_required', updated_at=? "
                        "WHERE feed=?", (now, entry.get("feed", "")))

    def gaps(self, *, open_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM gaps" + (" WHERE closed_at IS NULL" if open_only else "")
        return [{**dict(row), "detail": parse_json(row["detail"])}
                for row in self._db.execute(sql + " ORDER BY id")]

    def pause(self, *, paused: bool, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        with self._transaction():
            self._set_meta("paused", "1" if paused else "0")
            self._set_meta("paused_at", str(now))
        return {"paused": paused, "at": now,
                "note": "Local delivery preference only. This cannot revoke a bot token at "
                        "the provider or recall an accepted message."}

    def paused(self) -> bool:
        return self._meta("paused", "0") == "1"

    def status(self, *, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        bindings = self.bindings()
        last_projection = max((row["updated_at"] for row in bindings.values()), default=None)
        lag = None if last_projection is None else now - last_projection
        return {
            "observed_at": now,
            "paused": self.paused(),
            "bindings": bindings,
            "projection_lag_seconds": lag,
            "projection_stale": lag is None or lag > self.config.projection_freshness_seconds,
            "open_items": len(self.inbox()),
            "pending_intents": len(self.intents(state="PENDING")),
            "open_gaps": self.gaps(),
            "schedule": self.schedule(),
            "limitation": "A process cannot report its own outage. Timely detection needs an "
                          "independently authorized watchdog in another failure domain.",
        }


def json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Unserializable messaging value: {type(value).__name__}")


def dumps(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False, default=json_default)


# --------------------------------------------------------------------------
# Inbound commands
# --------------------------------------------------------------------------

#: The complete authorized vocabulary. There is deliberately no approve, run,
#: cancel, budget or policy command anywhere in this table.
COMMANDS = ("/help", "/status", "/brief", "/inbox", "/ack", "/snooze", "/stop", "/resume")
ATTENTION_COMMANDS = frozenset({"/ack", "/snooze", "/stop", "/resume"})
#: A stale command must not silently change current preferences.
COMMAND_FRESHNESS_SECONDS = 900.0
MAX_COMMAND_ARGUMENTS = 2
_ARGUMENT = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def parse_command(text: str) -> tuple[str, list[str]] | None:
    """Parse the fixed grammar. Inbound text is data, never an instruction."""
    parts = str(text).strip().split()
    if not parts:
        return None
    head = parts[0].split("@", 1)[0].lower()
    if head not in COMMANDS:
        return None
    arguments = parts[1:]
    if len(arguments) > MAX_COMMAND_ARGUMENTS:
        return None
    if any(not _ARGUMENT.match(argument) for argument in arguments):
        return None
    return head, arguments


class CommandProcessor:
    """Validates journaled inbound updates and applies bounded effects.

    The gateway's journal is untrusted. Its compromise can affect attention and
    forge transport-level acknowledgement within these limits; it cannot obtain
    scientific review status or any research execution authority.
    """

    def __init__(self, assistant: Assistant, poller: Any) -> None:
        self.assistant = assistant
        self.poller = poller

    def request_id(self, record: dict) -> str:
        return f"{self.poller.bot_identity}:{self.poller.epoch}:{record['update_id']}"

    def process_pending(self, *, now: float | None = None,
                        limit: int = 50) -> dict[str, Any]:
        now = time.time() if now is None else now
        applied, refused, replayed = [], [], []
        for entry in self.poller.pending(limit=limit):
            record = entry["record"]
            identity = self.request_id(record)
            existing = self.assistant.processed(identity)
            if existing is not None:
                replayed.append(identity)
                self.poller.mark_processed(record["update_id"], now=now)
                continue
            outcome = self._handle(record, identity=identity, now=now)
            self.poller.mark_processed(record["update_id"], now=now)
            (applied if outcome.get("applied") else refused).append(
                {"request": identity, **outcome})
        return {"applied": applied, "refused": refused, "replayed": replayed,
                "observed_at": now}

    def _refuse(self, record: dict, identity: str, reason: str, *, now: float) -> dict:
        result = {"applied": False, "reason": reason, "command": record.get("command")}
        self.assistant.record_request(
            identity, "refused", result, now=now,
            reply={"purpose": "reply:refused",
                   "payload": {"request": identity, "detail": {"reason": reason}}})
        return result

    def _handle(self, record: dict, *, identity: str, now: float) -> dict[str, Any]:
        parsed = parse_command(record["text"])
        if parsed is None:
            # Unsupported prose gets a bounded help response, nothing more.
            result = {"applied": False, "reason": "unsupported_request"}
            self.assistant.record_request(
                identity, "help", result, now=now,
                reply={"purpose": "reply:help",
                       "payload": {"request": identity,
                                   "detail": {"commands": ", ".join(COMMANDS)}}})
            return result
        command, arguments = parsed
        record = {**record, "command": command}
        age = now - float(record.get("date", now))
        if command in ATTENTION_COMMANDS and age > COMMAND_FRESHNESS_SECONDS:
            return self._refuse(record, identity, "stale_command", now=now)
        if command == "/help":
            result = {"applied": True, "command": command}
            self.assistant.record_request(
                identity, "help", result, now=now,
                reply={"purpose": "reply:help",
                       "payload": {"request": identity,
                                   "detail": {"commands": ", ".join(COMMANDS)}}})
            return result
        if command == "/status":
            return self._status(record, identity, arguments, now=now)
        if command == "/brief":
            return self._brief(record, identity, now=now)
        if command == "/inbox":
            return self._inbox(record, identity, now=now)
        if command in ("/stop", "/resume"):
            paused = command == "/stop"
            result = {"applied": True, "command": command, "paused": paused}
            self.assistant.record_request(
                identity, command.strip("/"), result, now=now,
                mutation=("paused", paused),
                reply={"purpose": f"reply:{command.strip('/')}",
                       "payload": {"request": identity, "detail": {"paused": paused}}})
            return result
        if not arguments:
            return self._refuse(record, identity, "missing_item_reference", now=now)
        reference = arguments[0]
        try:
            self.assistant.parse_reference(reference)
        except MessagingError:
            return self._refuse(record, identity, "malformed_item_reference", now=now)
        try:
            if command == "/ack":
                applied = self.assistant.acknowledge(
                    reference, actor="telegram", now=now, request_id=identity,
                    reply={"purpose": "reply:ack",
                           "payload": {"request": identity,
                                       "detail": {"reference": reference}}})
            else:
                if len(arguments) < 2:
                    return self._refuse(record, identity, "missing_deferral", now=now)
                applied = self.assistant.snooze(
                    reference, arguments[1], actor="telegram", now=now,
                    request_id=identity,
                    reply={"purpose": "reply:snooze",
                           "payload": {"request": identity,
                                       "detail": {"reference": reference}}})
        except MessagingError as exc:
            reason = ("stale_reference" if "stale" in str(exc)
                      else "already_resolved" if "resolved" in str(exc)
                      else "rejected")
            return self._refuse(record, identity, reason, now=now)
        return {"applied": True, "command": command, "reference": reference,
                "attention": applied["attention"]}

    def _status(self, record: dict, identity: str, arguments: list[str], *,
                now: float) -> dict[str, Any]:
        detail: dict[str, Any] = {}
        purpose = "reply:status_unknown"
        if arguments:
            try:
                with self.assistant.sources() as src:
                    campaign = src.controller.campaign(arguments[0])
                    report = Assistant._safe_report(
                        src, arguments[0], campaign,
                        src.controller.attempts(arguments[0]),
                        src.controller.budget_used(arguments[0]))
                detail = {"campaign_id": arguments[0], "state": campaign["state"],
                          **report["outcome"]}
                purpose = "reply:status"
            except (SourceUnavailable, ValueError):
                # An unknown identifier reveals nothing about other projects.
                detail = {}
        result = {"applied": True, "command": "/status", "known": purpose == "reply:status"}
        self.assistant.record_request(
            identity, "status", result, now=now,
            reply={"purpose": purpose, "payload": {"request": identity, "detail": detail}})
        return result

    def _brief(self, record: dict, identity: str, *, now: float) -> dict[str, Any]:
        with self.assistant.sources() as src:
            briefing = build_briefing(src, now=now, label=str(self.assistant.config.lab))
        result = {"applied": True, "command": "/brief",
                  "content_digest": briefing["content_digest"]}
        self.assistant.record_request(
            identity, "brief", result, now=now,
            reply={"purpose": "reply:brief",
                   "payload": {"request": identity,
                               "detail": {"summary": render_plain(briefing)}}})
        return result

    def _inbox(self, record: dict, identity: str, *, now: float) -> dict[str, Any]:
        items = self.assistant.inbox(limit=5)
        detail = ({"count": len(items),
                   "items": [f"{row['reference']} {row['condition']}" for row in items]}
                  if items else {})
        purpose = "reply:inbox" if items else "reply:inbox_empty"
        result = {"applied": True, "command": "/inbox", "count": len(items)}
        self.assistant.record_request(
            identity, "inbox", result, now=now,
            reply={"purpose": purpose, "payload": {"request": identity, "detail": detail}})
        return result
