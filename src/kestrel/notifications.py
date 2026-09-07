"""Approved envelopes, delivery lifecycle, and the gateway that sends them.

Two sides live here and they trust each other asymmetrically.

The **authority side** (`Exporter`) holds grants, channels and the private
inbox. It renders allowlisted typed fields through fixed templates, checks
disclosure before anything leaves, and publishes immutable envelopes plus
short-lived release permits into an export directory.

The **gateway side** (`Gateway`) sees only that directory, its own journal, and
a transport credential. It cannot read a controller or evidence database, an
artifact, a project workspace, or an operator token. It runs the delivery state
machine and enforces quotas again for itself.

`ACCEPTED` means a transport accepted the request. It never means the message
was delivered to a device, read, acted on, reviewed, or authorized.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from kestrel.contracts import Identifier, StrictModel, canonical, digest, parse_json

ENVELOPE_VERSION = "0.1"
GATEWAY_SCHEMA_VERSION = 1

#: Local rendering ceiling. Telegram's own `sendMessage` limit is larger; the
#: stricter local cap leaves room for identity and freshness lines.
MAX_UTF16_UNITS = 3000
MAX_UTF8_BYTES = 16 * 1024
MAX_ENVELOPE_BYTES = 64 * 1024
#: Total bytes the messaging spool and gateway journal may occupy.
MAX_SPOOL_BYTES = 64 * 1024**2

PERMIT_LIFETIME = 60.0
PERMIT_REFRESH = 5.0
MAX_TRANSMISSION_ATTEMPTS = 3
BACKOFF_BASE = 5.0
BACKOFF_CAP = 60.0

CLASSIFICATION_ORDER = ("public_synthetic", "public", "restricted")

TERMINAL_DELIVERY = frozenset({"ACCEPTED", "FAILED_FINAL", "SUPPRESSED", "EXPIRED",
                               "REVOKED", "SUPERSEDED"})
DELIVERY_TRANSITIONS = {
    "PENDING": {"READY", "SUPPRESSED", "EXPIRED", "REVOKED", "SUPERSEDED"},
    "READY": {"SENDING", "SUPPRESSED", "EXPIRED", "REVOKED", "SUPERSEDED", "PENDING"},
    "SENDING": {"ACCEPTED", "RETRY_WAIT", "UNCERTAIN", "FAILED_FINAL"},
    "RETRY_WAIT": {"READY", "SUPPRESSED", "EXPIRED", "REVOKED", "SUPERSEDED", "FAILED_FINAL"},
    "UNCERTAIN": {"ACCEPTED", "FAILED_FINAL", "READY"},
    "ACCEPTED": set(),
    "FAILED_FINAL": set(),
    "SUPPRESSED": set(),
    "EXPIRED": set(),
    "REVOKED": set(),
    "SUPERSEDED": set(),
}


class DeliveryError(ValueError):
    """A delivery operation was refused."""


class DisclosureRefused(DeliveryError):
    """Content or its classification is not releasable to this channel."""


# --------------------------------------------------------------------------
# Authority-side records
# --------------------------------------------------------------------------

class Destination(StrictModel):
    """Where a channel sends. Identity is numeric; a display name is decorative."""

    transport: Literal["fake", "telegram"]
    identity: str
    description: str = ""


class Channel(StrictModel):
    channel_id: Identifier
    version: int
    destination: Destination


class Grant(StrictModel):
    """A service-scoped permission to notify one operator-owned channel.

    It never confers `execute`, `live_provider`, `network`, publication, budget
    expansion, or access to another project. It is deliberately independent of
    campaign execution approval so that "this campaign is awaiting approval" can
    be reported at all.
    """

    grant_id: Identifier
    version: int
    principal: str
    channel_id: Identifier
    allowed_conditions: list[str]
    disclosure_ceiling: Literal["public_synthetic", "public", "restricted"] = "public_synthetic"
    metadata_only_fallback: bool = False
    daily_automated_cap: int = 20
    reserved_critical: int = 5
    daily_reply_cap: int = 20
    reply_per_minute_cap: int = 5
    hard_daily_cap: int = 40
    expires_at: float
    quiet_hour_exceptions: list[str] = []


AUTHORITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS channels(
    channel_id TEXT NOT NULL, version INTEGER NOT NULL, record TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
    PRIMARY KEY(channel_id, version));
CREATE TABLE IF NOT EXISTS grants(
    grant_id TEXT NOT NULL, version INTEGER NOT NULL, record TEXT NOT NULL,
    revoked_at REAL, active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL,
    PRIMARY KEY(grant_id, version));
CREATE TABLE IF NOT EXISTS envelopes(
    id TEXT PRIMARY KEY, intent_id TEXT NOT NULL, grant_id TEXT NOT NULL,
    grant_version INTEGER NOT NULL, channel_id TEXT NOT NULL,
    channel_version INTEGER NOT NULL, purpose TEXT NOT NULL, classification TEXT NOT NULL,
    content_digest TEXT NOT NULL, path TEXT NOT NULL, not_before REAL NOT NULL,
    expires_at REAL NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS export_refusals(
    id INTEGER PRIMARY KEY AUTOINCREMENT, intent_id TEXT NOT NULL, reason TEXT NOT NULL,
    detail TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS envelope_immutable
BEFORE UPDATE ON envelopes
BEGIN SELECT RAISE(ABORT, 'a published envelope is immutable'); END;
CREATE TRIGGER IF NOT EXISTS grant_scope_immutable
BEFORE UPDATE OF grant_id,version,record,created_at ON grants
BEGIN SELECT RAISE(ABORT, 'a grant version is immutable; issue a new version'); END;
"""


# --------------------------------------------------------------------------
# Disclosure
# --------------------------------------------------------------------------

_ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:/[\w.\-]+){2,}")
_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://|\bwww\.", re.IGNORECASE)
_BOT_TOKEN = re.compile(r"\b\d{6,}:[A-Za-z0-9_\-]{20,}\b")
_LONG_SECRET = re.compile(r"\b[A-Fa-f0-9]{64,}\b|\b[A-Za-z0-9+/]{40,}={0,2}\b")
_CREDENTIAL_WORD = re.compile(
    r"\b(?:token|secret|password|passphrase|api[_-]?key|private[_-]?key|bearer)\b",
    re.IGNORECASE)

DISCLOSURE_RULES = (("absolute_path", _ABSOLUTE_PATH), ("url", _URL),
                    ("bot_token", _BOT_TOKEN), ("long_secret", _LONG_SECRET),
                    ("credential_reference", _CREDENTIAL_WORD))


def scan_release(text: str) -> list[str]:
    """Defence in depth over already-typed rendering, not a proof of safety.

    Fixed templates and allowlisted fields are the actual control. This scanner
    catches a template mistake or an unexpected source string; passing it does
    not make arbitrary free text releasable.
    """
    violations = [name for name, pattern in DISCLOSURE_RULES if pattern.search(text)]
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cs", "Co", "Cn"}
           for character in text if character != "\n"):
        violations.append("control_character")
    return sorted(set(violations))


def clip(text: str, *, keep_tail: str = "") -> str:
    """Bound a message without dropping the caveats at its end.

    Clipping happens on grapheme cluster boundaries so a combining mark is never
    orphaned, and the retained tail (outcome, assurance and identity lines) is
    always preserved.
    """
    tail = ("\n" + keep_tail) if keep_tail else ""
    budget_units = MAX_UTF16_UNITS - len(tail.encode("utf-16-le")) // 2
    budget_bytes = MAX_UTF8_BYTES - len(tail.encode())
    if budget_units <= 0 or budget_bytes <= 0:
        raise DisclosureRefused("Required message tail exceeds the message budget")
    clusters: list[str] = []
    for character in text:
        if clusters and unicodedata.combining(character):
            clusters[-1] += character
        else:
            clusters.append(character)
    kept: list[str] = []
    units = 0
    size = 0
    for cluster in clusters:
        unit_cost = len(cluster.encode("utf-16-le")) // 2
        byte_cost = len(cluster.encode())
        if units + unit_cost > budget_units or size + byte_cost > budget_bytes:
            kept.append("…")
            break
        kept.append(cluster)
        units += unit_cost
        size += byte_cost
    return "".join(kept) + tail


def _worst(classifications: list[str]) -> str:
    return max(classifications or ["public_synthetic"], key=CLASSIFICATION_ORDER.index)


CONDITION_TEMPLATES = {
    "approval_needed": "Campaign {campaign_id} has {unfinished_tasks} task(s) that cannot be "
                       "reserved: no approval is bound to its current contract digest.",
    "approval_unusable": "Campaign {campaign_id} has {unfinished_tasks} task(s) waiting: its "
                         "recorded approval no longer applies to the current contract digest.",
    "blocked_work": "Campaign {campaign_id} has {blocked_count} blocked task(s). "
                    "Independently eligible work can still proceed.",
    "budget_exhausted": "Campaign {campaign_id} has exhausted its declared {exhausted}. "
                        "Reservations are charged, not measured consumption.",
    "uncertain_capacity": "Campaign {campaign_id} has {attempt_count} attempt(s) still "
                          "holding reservations without a confirmed stop. Reconciliation is "
                          "required before any retry.",
    "expired_lease": "Campaign {campaign_id} has {attempt_count} attempt(s) whose lease "
                     "expired. Lease expiry does not confirm the job stopped.",
    "evidence_correction": "Evidence was marked {kind}; {affected} record(s) are affected. "
                           "A conclusion that depended on it is no longer current.",
    "verification_failed": "Campaign {campaign_id} has a recorded outcome whose evidence "
                           "cannot be verified. It is not presented as a result.",
    "milestone_reached": "Milestone {milestone_id} version {version} is reached.",
    "milestone_reopened": "Milestone {milestone_id} version {version} reopened; it is no "
                          "longer satisfied.",
    "watched_campaign_complete": "Campaign {campaign_id} completed. Execution {execution}; "
                                 "protocol {validity}; finding {finding}; evidence {assurance}.",
    "campaign_complete": "Campaign {campaign_id} completed. Execution {execution}; protocol "
                         "{validity}; finding {finding}; evidence {assurance}.",
    "watched_attempt_settled": "Watched attempt {attempt_id} finished in state {state}. "
                               "Evaluation recorded: {evaluation_recorded}.",
    "watched_task_settled": "Watched task {task_id} finished in state {state} after "
                            "{attempt_count} attempt(s).",
}


#: Fixed reply templates. Command answers are rendered from typed fields on the
#: authority side; no inbound text is ever echoed back as structure.
REPLY_TEMPLATES = {
    "help": "Enabled commands: {commands}. Scope: one paired private chat, this lab. "
            "No command can approve work, change a budget, edit policy, cancel a "
            "campaign, or rewrite a frozen assumption.",
    "status": "Campaign {campaign_id} is {state}. Execution {execution}; protocol "
              "{validity}; finding {finding}; evidence {assurance}.",
    "status_unknown": "No campaign with that reference is available to this channel.",
    "brief": "{summary}",
    "inbox": "{count} unresolved item(s): {items}. Reply /ack or /snooze with an exact "
             "reference such as M12-r1.",
    "inbox_empty": "Nothing is unresolved in the recorded state.",
    "ack": "{reference} acknowledged. Reminders stop for this revision. The underlying "
           "condition is unchanged and no evidence is certified.",
    "snooze": "{reference} deferred. The condition remains open and unresolved.",
    "stop": "Automated delivery is paused. This is a local preference; it cannot revoke a "
            "credential at the provider or recall an accepted message.",
    "resume": "Automated delivery resumed within the existing grant. This restores no "
              "revoked authority and broadens no scope.",
    "refused": "That request was not applied: {reason}. Ask for /inbox to refresh "
               "references, or /help for the enabled commands.",
}


def render_notification(intent: dict[str, Any], *, grant: Grant,
                        channel: Channel) -> dict[str, Any]:
    """Produce releasable bytes from allowlisted typed fields, or refuse.

    Every sentence comes from a fixed template. No source-authored string is
    ever used as structure, as a link, or as a command.
    """
    payload = intent["payload"]
    purpose = intent["purpose"]
    labels = payload.get("classifications")
    if not isinstance(labels, list) or not labels or any(
            label not in CLASSIFICATION_ORDER for label in labels):
        raise DisclosureRefused("Missing or invalid disclosure provenance")
    classification = _worst(labels)
    if CLASSIFICATION_ORDER.index(classification) > CLASSIFICATION_ORDER.index(
            grant.disclosure_ceiling):
        # Identifiers, digests and "there is an update" are still disclosure.
        raise DisclosureRefused(
            f"Content classified {classification} exceeds the channel ceiling "
            f"{grant.disclosure_ceiling}; a metadata-only view needs its own permission")
    if purpose in ("daily_brief", "catch_up"):
        headline = ("Kestrel daily brief" if purpose == "daily_brief"
                    else "Kestrel catch-up brief")
        body = str(payload.get("summary", ""))
        if payload.get("missed_days"):
            body += f"\nCovering {payload['missed_days']} missed scheduled day(s)."
        reference = f"Briefing {str(payload.get('content_digest', ''))[:12]}."
    elif purpose == "backfill_summary":
        headline = "Kestrel assistant connected"
        body = (f"{payload.get('items', 0)} existing condition(s) are now in the local inbox. "
                "Historical events are not replayed as messages.")
        reference = "Use the local inbox for detail."
    elif purpose.startswith("reply:"):
        kind = purpose.split(":", 1)[1]
        if kind not in REPLY_TEMPLATES:
            raise DisclosureRefused(f"No approved reply template for {kind!r}")
        headline = "Kestrel"
        body = _fill(REPLY_TEMPLATES[kind], payload.get("detail", {}))
        reference = f"Answering {payload.get('request', 'a request')}."
    elif purpose.startswith("item:"):
        condition = str(payload.get("condition", ""))
        if condition not in CONDITION_TEMPLATES:
            raise DisclosureRefused(f"No approved template for condition {condition!r}")
        if grant.allowed_conditions and condition not in grant.allowed_conditions:
            raise DisclosureRefused(f"Condition {condition!r} is outside this grant")
        headline = "Kestrel needs you" if condition in (
            "approval_needed", "approval_unusable", "blocked_work", "budget_exhausted"
        ) else "Kestrel update"
        body = _fill(CONDITION_TEMPLATES[condition], payload.get("detail", {}))
        reference = f"Item {payload.get('item', 'unknown')}."
    else:
        raise DisclosureRefused(f"No approved template for purpose {purpose!r}")
    tail = (f"{reference} Reservations are charged, not measured. Provider acceptance is not "
            "delivery, reading, review, or approval.")
    text = clip(f"{headline}\n{body}", keep_tail=tail)
    violations = scan_release(text)
    if violations:
        raise DisclosureRefused(f"Rendered content failed release scanning: {violations}")
    return {"text": text, "classification": classification,
            "transport": channel.destination.transport}


def _fill(template: str, detail: dict[str, Any]) -> str:
    """Fill a fixed template from allowlisted scalar fields only."""
    values: dict[str, Any] = {}
    for key, value in detail.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            values[key] = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            values[key] = ", ".join(item[:60] for item in value[:5])
        elif isinstance(value, dict):
            for inner, item in value.items():
                if isinstance(item, (str, int, float, bool)):
                    values.setdefault(inner, item)
    values.setdefault("blocked_count", len(detail.get("blocked_tasks", []) or []))
    try:
        return template.format(**values)
    except (KeyError, IndexError) as exc:
        raise DisclosureRefused(f"Template field unavailable: {exc}") from exc


# --------------------------------------------------------------------------
# Authority-side export
# --------------------------------------------------------------------------

def _atomic_write(directory: Path, name: str, data: bytes) -> Path:
    """Write, verify, then publish under a stable name. No symlink is followed."""
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise DeliveryError("Export directory is unavailable")
    target = directory / name
    if target.is_symlink():
        raise DeliveryError("Export target must not redirect through a symlink")
    handle, temporary = tempfile.mkstemp(prefix=".staging-", dir=directory)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if Path(temporary).read_bytes() != data:
            raise DeliveryError("Envelope verification failed before publication")
        os.chmod(temporary, 0o640)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


def spool_bytes(root: Path) -> int:
    total = 0
    for relative in ("notification-export", "telegram-runtime"):
        for path in (Path(root) / relative).rglob("*"):
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
    return total


class Exporter:
    """Turns committed intents into approved immutable envelopes."""

    def __init__(self, assistant: Any) -> None:
        self.assistant = assistant
        self.root = assistant.root
        self.export = self.root / "notification-export"
        self.envelopes = self.export / "envelopes"
        self.permits = self.export / "permits"
        for directory in (self.export, self.envelopes, self.permits):
            if directory.is_symlink() or not directory.is_dir():
                raise DeliveryError("Notification export layout is unavailable")

    # -- grants and channels -------------------------------------------

    def register_channel(self, channel: Channel, *, operator_token: str,
                         now: float | None = None) -> dict:
        now = time.time() if now is None else now
        self.assistant.authenticate_operator(operator_token)
        with self.assistant._transaction():
            self.assistant._db.execute("UPDATE channels SET active=0 WHERE channel_id=?",
                                       (channel.channel_id,))
            self.assistant._db.execute("INSERT INTO channels VALUES (?,?,?,1,?)",
                                       (channel.channel_id, channel.version,
                                        canonical(channel).decode(), now))
        self._publish_channel_policy(now=now)
        return {"channel_id": channel.channel_id, "version": channel.version,
                "transport": channel.destination.transport,
                "authority": "operator-issued channel record; no credential was stored"}

    def issue_grant(self, grant: Grant, *, operator_token: str,
                    now: float | None = None) -> dict:
        now = time.time() if now is None else now
        self.assistant.authenticate_operator(operator_token)
        if grant.expires_at <= now:
            raise DeliveryError("A notification grant must expire in the future")
        if self.channel(grant.channel_id) is None:
            raise DeliveryError("Register the operator-owned channel before granting to it")
        with self.assistant._transaction():
            self.assistant._db.execute("UPDATE grants SET active=0 WHERE grant_id=?",
                                       (grant.grant_id,))
            self.assistant._db.execute("INSERT INTO grants VALUES (?,?,?,NULL,1,?)",
                                       (grant.grant_id, grant.version,
                                        canonical(grant).decode(), now))
        self._publish_channel_policy(now=now)
        return {"grant_id": grant.grant_id, "version": grant.version,
                "channel_id": grant.channel_id, "expires_at": grant.expires_at,
                "authority": "notify_operator only; confers no execute, network, "
                             "live_provider, publication or budget authority"}

    def revoke_grant(self, grant_id: str, *, operator_token: str,
                     now: float | None = None) -> dict:
        now = time.time() if now is None else now
        self.assistant.authenticate_operator(operator_token)
        with self.assistant._transaction():
            self.assistant._db.execute(
                "UPDATE grants SET revoked_at=?, active=0 WHERE grant_id=? AND active=1",
                (now, grant_id))
        for permit in self.permits.glob("*.json"):
            permit.unlink()
        self._publish_channel_policy(now=now)
        return {"grant_id": grant_id, "revoked_at": now,
                "propagation_seconds": PERMIT_LIFETIME,
                "limitation": "An already accepted or in-flight message cannot be recalled."}

    def channel(self, channel_id: str) -> Channel | None:
        row = self.assistant._db.execute(
            "SELECT record FROM channels WHERE channel_id=? AND active=1",
            (channel_id,)).fetchone()
        return Channel.model_validate(parse_json(row[0])) if row else None

    def active_grant(self, *, now: float) -> tuple[Grant, Channel] | None:
        row = self.assistant._db.execute(
            "SELECT record FROM grants WHERE active=1 AND revoked_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1").fetchone()
        if row is None:
            return None
        grant = Grant.model_validate(parse_json(row[0]))
        if grant.expires_at <= now:
            return None
        channel = self.channel(grant.channel_id)
        return (grant, channel) if channel else None

    def _publish_channel_policy(self, *, now: float) -> None:
        """The minimum policy the gateway is allowed to see. No credential."""
        active = self.active_grant(now=now)
        if active is None:
            policy = {"policy_version": ENVELOPE_VERSION, "active": False,
                      "reason": "no unexpired unrevoked grant", "published_at": now}
        else:
            grant, channel = active
            policy = {"policy_version": ENVELOPE_VERSION, "active": True,
                      "published_at": now,
                      "channel": {"channel_id": channel.channel_id,
                                  "version": channel.version,
                                  "transport": channel.destination.transport,
                                  "identity": channel.destination.identity},
                      "grant": {"grant_id": grant.grant_id, "version": grant.version,
                                "expires_at": grant.expires_at},
                      "caps": {"daily_automated": grant.daily_automated_cap,
                               "reserved_critical": grant.reserved_critical,
                               "daily_reply": grant.daily_reply_cap,
                               "reply_per_minute": grant.reply_per_minute_cap,
                               "hard_daily": grant.hard_daily_cap}}
        _atomic_write(self.export, "channel.json", canonical(policy))

    def publish_routing(self) -> None:
        """Export only confirmed Telegram identity, never private config/state."""
        fields = {key: self.assistant._meta(f"telegram_{key}")
                  for key in ("bot_id", "chat_id", "user_id", "epoch")}
        if any(value is None for value in fields.values()):
            return
        routing = {"routing_version": ENVELOPE_VERSION,
                   **{key: int(value) for key, value in fields.items()}}
        _atomic_write(self.export, "routing.json", canonical(routing))

    # -- export --------------------------------------------------------

    def export_pending(self, *, now: float | None = None) -> dict[str, Any]:
        """Publish an envelope per releasable intent. Replayable and idempotent."""
        now = time.time() if now is None else now
        result: dict[str, Any] = {"exported": [], "refused": [], "deferred": []}
        if self.assistant.paused():
            result["refused"].append({"reason": "paused",
                                      "detail": "delivery is locally paused"})
            return result
        status = self.assistant.status(now=now)
        if status["projection_stale"]:
            # A gateway must not imply the lab is healthy just because it can
            # reach a transport.
            result["refused"].append({"reason": "projection_stale",
                                      "detail": "source projection is older than its bound"})
            return result
        active = self.active_grant(now=now)
        if active is None:
            result["refused"].append({"reason": "no_active_grant",
                                      "detail": "no unexpired, unrevoked grant"})
            return result
        grant, channel = active
        self._publish_channel_policy(now=now)
        for intent in self.assistant.intents(state="PENDING"):
            if intent["expires_at"] <= now:
                continue
            if self.assistant._db.execute("SELECT 1 FROM envelopes WHERE intent_id=?",
                                          (intent["id"],)).fetchone():
                continue
            if intent["not_before"] > now:
                result["deferred"].append({"intent": intent["id"],
                                           "not_before": intent["not_before"]})
                continue
            if spool_bytes(self.root) > MAX_SPOOL_BYTES:
                self._refuse(intent, "spool_saturated",
                             "messaging spool byte budget exhausted", now)
                result["refused"].append({"intent": intent["id"],
                                          "reason": "spool_saturated"})
                break
            try:
                rendered = render_notification(intent, grant=grant, channel=channel)
            except DisclosureRefused as exc:
                self._refuse(intent, "disclosure_refused", str(exc), now)
                result["refused"].append({"intent": intent["id"],
                                          "reason": "disclosure_refused",
                                          "detail": str(exc)})
                continue
            identity = f"env-{uuid.uuid4().hex}"
            body = {
                "envelope_version": ENVELOPE_VERSION, "id": identity,
                "purpose": intent["purpose"], "route": intent["route"],
                "classification": rendered["classification"],
                "channel": {"channel_id": channel.channel_id, "version": channel.version,
                            "transport": channel.destination.transport,
                            "identity": channel.destination.identity},
                "grant": {"grant_id": grant.grant_id, "version": grant.version},
                "text": rendered["text"], "not_before": intent["not_before"],
                "expires_at": intent["expires_at"], "created_at": now,
            }
            body["content_digest"] = digest({k: v for k, v in body.items()
                                             if k != "created_at"})
            encoded = canonical(body)
            if len(encoded) > MAX_ENVELOPE_BYTES:
                self._refuse(intent, "envelope_too_large", "envelope exceeds its byte cap",
                             now)
                result["refused"].append({"intent": intent["id"],
                                          "reason": "envelope_too_large"})
                continue
            path = _atomic_write(self.envelopes, f"{identity}.json", encoded)
            with self.assistant._transaction():
                self.assistant._db.execute(
                    "INSERT INTO envelopes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (identity, intent["id"], grant.grant_id, grant.version,
                     channel.channel_id, channel.version, intent["purpose"],
                     rendered["classification"], body["content_digest"], str(path),
                     intent["not_before"], intent["expires_at"], now))
                self.assistant._db.execute(
                    "UPDATE intents SET state='READY', updated_at=? WHERE id=?",
                    (now, intent["id"]))
            result["exported"].append(identity)
        self.refresh_permits(now=now)
        return result

    def _refuse(self, intent: dict, reason: str, detail: str, now: float) -> None:
        with self.assistant._transaction():
            self.assistant._db.execute(
                "INSERT INTO export_refusals(intent_id,reason,detail,created_at) "
                "VALUES (?,?,?,?)", (intent["id"], reason, detail[:2000], now))
            self.assistant._db.execute(
                "UPDATE intents SET state='SUPPRESSED', reason=?, updated_at=? WHERE id=?",
                (reason, now, intent["id"]))

    def _projection_deadline(self, *, now: float) -> float | None:
        status = self.assistant.status(now=now)
        if status["projection_stale"] or set(status["bindings"]) != {"controller", "evidence"}:
            return None
        try:
            with self.assistant.sources() as src:
                for feed in ("controller", "evidence"):
                    check = self.assistant._check_binding(feed, getattr(src, feed), now=now)
                    if check["status"] != "bound" or check["head"] != check.get("cursor"):
                        return None
        except (ValueError, OSError, sqlite3.Error):
            return None
        return min(row["updated_at"] for row in status["bindings"].values()) + \
            self.assistant.config.projection_freshness_seconds

    def refresh_permits(self, *, now: float | None = None) -> dict[str, Any]:
        """Re-issue short-lived release permits for still-valid envelopes.

        A permit never resets intent age, retry count or channel quota. Its
        lifetime is the declared upper bound on revocation propagation for an
        envelope that has not yet been transmitted.
        """
        now = time.time() if now is None else now
        active = self.active_grant(now=now)
        issued, withdrawn = [], []
        for permit in sorted(self.permits.glob("*.json")):
            permit.unlink()
        deadline = self._projection_deadline(now=now)
        if active is None or self.assistant.paused() or deadline is None:
            self._publish_channel_policy(now=now)
            return {"issued": [], "withdrawn": ["all"], "reason": "no releasable authority"}
        grant, channel = active
        self.publish_routing()
        self._publish_channel_policy(now=now)
        for row in self.assistant._db.execute(
                "SELECT * FROM envelopes ORDER BY created_at,id"):
            intent = self.assistant._db.execute(
                "SELECT * FROM intents WHERE id=?", (row["intent_id"],)).fetchone()
            if row["expires_at"] <= now or intent is None \
                    or intent["state"] not in ("READY", "PENDING") \
                    or row["grant_id"] != grant.grant_id or row["grant_version"] != grant.version \
                    or row["channel_version"] != channel.version:
                withdrawn.append(row["id"])
                continue
            try:
                rendered = render_notification(
                    {**dict(intent), "payload": parse_json(intent["payload"])},
                    grant=grant, channel=channel)
                if rendered["classification"] != row["classification"]:
                    withdrawn.append(row["id"])
                    continue
            except (DisclosureRefused, ValueError):
                withdrawn.append(row["id"])
                continue
            permit = {"permit_version": ENVELOPE_VERSION, "envelope": row["id"],
                      "content_digest": row["content_digest"],
                      "channel_id": row["channel_id"],
                      "channel_version": row["channel_version"],
                      "grant_id": row["grant_id"], "grant_version": row["grant_version"],
                      "issued_at": now, "expires_at": min(now + PERMIT_LIFETIME,
                                                          grant.expires_at, row["expires_at"],
                                                          deadline),
                      "not_before": row["not_before"]}
            _atomic_write(self.permits, f"{row['id']}.json", canonical(permit))
            issued.append(row["id"])
        return {"issued": issued, "withdrawn": withdrawn,
                "permit_lifetime_seconds": PERMIT_LIFETIME,
                "refresh_interval_seconds": PERMIT_REFRESH}

    def supersede(self, envelope_id: str, *, reason: str, now: float | None = None) -> dict:
        """Withdraw an unsent envelope's permit. An accepted message stays sent."""
        now = time.time() if now is None else now
        permit = self.permits / f"{envelope_id}.json"
        if permit.is_file():
            permit.unlink()
        return {"envelope": envelope_id, "permit_withdrawn": True, "reason": reason,
                "limitation": "This cannot recall a message the transport already accepted."}

    def refusals(self) -> list[dict]:
        return [dict(row) for row in self.assistant._db.execute(
            "SELECT * FROM export_refusals ORDER BY id")]


# --------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class TransportResult:
    outcome: Literal["accepted", "flood_control", "rejected_permanent",
                     "failed_before_send", "uncertain"]
    detail: str = ""
    provider_reference: str | None = None
    destination_confirmed: bool = False
    retry_after: float | None = None


class Transport(Protocol):
    name: str

    def send(self, envelope: dict[str, Any]) -> TransportResult: ...


@dataclass
class FakeTransport:
    """A scripted transport for offline tests. It opens no socket.

    Acceptance from this transport establishes nothing about a real provider,
    a device, or a person.
    """

    name: str = "fake"
    script: list[Any] = field(default_factory=list)
    sent: list[dict[str, Any]] = field(default_factory=list)
    default: TransportResult = field(
        default_factory=lambda: TransportResult(outcome="accepted",
                                                provider_reference="fake-1",
                                                destination_confirmed=True))

    def send(self, envelope: dict[str, Any]) -> TransportResult:
        self.sent.append(envelope)
        if not self.script:
            return self.default
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(envelope)
        return step


class NoEgressTransport:
    """Refuses every send. Used to prove nothing leaves without a transport."""

    name = "none"

    def send(self, envelope: dict[str, Any]) -> TransportResult:
        raise DeliveryError("No egress transport is configured or authorized")


# --------------------------------------------------------------------------
# Gateway
# --------------------------------------------------------------------------

GATEWAY_SCHEMA = """
CREATE TABLE IF NOT EXISTS deliveries(
    envelope_id TEXT PRIMARY KEY, content_digest TEXT NOT NULL, purpose TEXT NOT NULL,
    route TEXT NOT NULL, channel_id TEXT NOT NULL, channel_version INTEGER NOT NULL,
    grant_id TEXT NOT NULL, grant_version INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'PENDING', transmissions INTEGER NOT NULL DEFAULT 0,
    next_eligible REAL NOT NULL, not_before REAL NOT NULL, expires_at REAL NOT NULL,
    provider_reference TEXT, accepted_at REAL, reason TEXT, lease_owner TEXT,
    created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS delivery_attempts(
    id INTEGER PRIMARY KEY AUTOINCREMENT, envelope_id TEXT NOT NULL,
    transmission INTEGER NOT NULL, outcome TEXT NOT NULL, detail TEXT NOT NULL,
    provider_reference TEXT, lease_owner TEXT, started_at REAL NOT NULL,
    finished_at REAL);
CREATE TABLE IF NOT EXISTS quota_usage(
    day TEXT NOT NULL, kind TEXT NOT NULL, used INTEGER NOT NULL,
    PRIMARY KEY(day, kind));
CREATE TABLE IF NOT EXISTS sender_lease(
    id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS inbound_updates(
    update_id INTEGER NOT NULL, bot_identity TEXT NOT NULL, epoch INTEGER NOT NULL,
    accepted INTEGER NOT NULL, record TEXT NOT NULL, received_at REAL NOT NULL,
    processed_at REAL, PRIMARY KEY(bot_identity, epoch, update_id));
CREATE TABLE IF NOT EXISTS poll_cursor(
    id INTEGER PRIMARY KEY CHECK(id=1), bot_identity TEXT NOT NULL, epoch INTEGER NOT NULL,
    offset INTEGER NOT NULL, mode TEXT NOT NULL, last_success REAL,
    last_update_at REAL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS gateway_gaps(
    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, detail TEXT NOT NULL,
    opened_at REAL NOT NULL, closed_at REAL);
CREATE TRIGGER IF NOT EXISTS delivery_attempt_identity_immutable
BEFORE UPDATE OF envelope_id,transmission,started_at,lease_owner ON delivery_attempts
BEGIN SELECT RAISE(ABORT, 'attempt identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS delivery_attempt_outcome_once
BEFORE UPDATE OF outcome ON delivery_attempts WHEN OLD.outcome != 'sending'
BEGIN SELECT RAISE(ABORT, 'a recorded attempt outcome is immutable'); END;
CREATE TRIGGER IF NOT EXISTS delivery_attempts_no_delete
BEFORE DELETE ON delivery_attempts
BEGIN SELECT RAISE(ABORT, 'delivery attempts are permanent'); END;
"""


def utc_day(instant: float) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(instant, datetime.UTC).date().isoformat()


class Gateway:
    """The untrusted egress side. It sees envelopes, permits and policy only."""

    def __init__(self, root: Path, *, owner: str = "sender") -> None:
        self.root = Path(root).absolute()
        self.export = self.root / "notification-export"
        self.envelopes = self.export / "envelopes"
        self.permits = self.export / "permits"
        self.runtime = self.root / "telegram-runtime"
        for directory in (self.export, self.envelopes, self.permits, self.runtime):
            if directory.is_symlink() or not directory.is_dir():
                raise DeliveryError("Gateway layout is unavailable")
        self.owner = f"{owner}:{uuid.uuid4().hex}"
        path = self.runtime / "gateway.sqlite"
        if path.is_symlink():
            raise DeliveryError("Gateway journal must not redirect through a symlink")
        if not path.exists():
            try:
                path.touch(mode=0o660, exist_ok=False)
                path.chmod(0o660)
            except FileExistsError:
                pass
        self._db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, GATEWAY_SCHEMA_VERSION):
            self._db.close()
            raise DeliveryError(f"Unsupported gateway schema version {version}")
        self._db.executescript(GATEWAY_SCHEMA)
        self._db.execute(f"PRAGMA user_version={GATEWAY_SCHEMA_VERSION}")

    def close(self) -> None:
        with self._transaction():
            if not self._db.execute("SELECT 1 FROM deliveries WHERE state='SENDING' "
                                    "AND lease_owner=?", (self.owner,)).fetchone():
                self._db.execute("UPDATE sender_lease SET until=0 WHERE owner=?", (self.owner,))
        self._db.close()

    def __enter__(self) -> Gateway:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _transaction(self):
        from contextlib import contextmanager

        @contextmanager
        def scope():
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            else:
                self._db.execute("COMMIT")

        return scope()

    # -- policy and permits --------------------------------------------

    def policy(self) -> dict[str, Any]:
        path = self.export / "channel.json"
        if path.is_symlink() or not path.is_file():
            # Fail closed: unavailable policy is not permission.
            return {"active": False, "reason": "channel policy unavailable"}
        return parse_json(path.read_bytes(), max_bytes=64 * 1024)

    def routing(self) -> dict[str, Any]:
        path = self.export / "routing.json"
        if path.is_symlink() or not path.is_file():
            raise DeliveryError("No confirmed Telegram routing is published")
        record = parse_json(path.read_bytes(), max_bytes=4096)
        keys = {"bot_id", "chat_id", "user_id", "epoch"}
        if not isinstance(record, dict) or set(record) != keys | {"routing_version"} \
                or record["routing_version"] != ENVELOPE_VERSION \
                or any(type(record[key]) is not int or record[key] <= 0 for key in keys):
            raise DeliveryError("Invalid Telegram routing record")
        return record

    def read_envelope(self, identity: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"env-[0-9a-f]{32}", identity):
            return None
        path = self.envelopes / f"{identity}.json"
        if path.is_symlink() or not path.is_file():
            return None
        try:
            body = parse_json(path.read_bytes(), max_bytes=MAX_ENVELOPE_BYTES)
        except ValueError:
            return None
        if type(body) is not dict or body.get("envelope_version") != ENVELOPE_VERSION:
            return None
        expected = digest({k: v for k, v in body.items()
                           if k not in ("created_at", "content_digest")})
        if expected != body.get("content_digest"):
            return None
        return body

    def permit(self, identity: str, *, now: float) -> dict[str, Any] | None:
        path = self.permits / f"{identity}.json"
        if path.is_symlink() or not path.is_file():
            return None
        try:
            record = parse_json(path.read_bytes(), max_bytes=16 * 1024)
        except ValueError:
            return None
        if type(record) is not dict or record.get("permit_version") != ENVELOPE_VERSION:
            return None
        if not record.get("issued_at", 0) <= now < record.get("expires_at", 0):
            return None
        return record

    # -- intake ---------------------------------------------------------

    def intake(self, *, now: float | None = None) -> dict[str, Any]:
        """Register published envelopes as deliveries. Replay creates no duplicate."""
        now = time.time() if now is None else now
        registered, rejected = [], []
        for path in sorted(self.envelopes.glob("env-*.json")):
            identity = path.stem
            body = self.read_envelope(identity)
            if body is None:
                rejected.append({"envelope": identity, "reason": "malformed_or_tampered"})
                self._gap("malformed_envelope", {"envelope": identity}, now=now)
                continue
            existing = self.delivery(identity)
            if existing:
                if existing["content_digest"] != body["content_digest"]:
                    rejected.append({"envelope": identity, "reason": "digest_changed"})
                continue
            with self._transaction():
                self._db.execute(
                    "INSERT INTO deliveries(envelope_id,content_digest,purpose,route,"
                    "channel_id,channel_version,grant_id,grant_version,next_eligible,"
                    "not_before,expires_at,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (identity, body["content_digest"], body["purpose"], body["route"],
                     body["channel"]["channel_id"], body["channel"]["version"],
                     body["grant"]["grant_id"], body["grant"]["version"],
                     body["not_before"], body["not_before"], body["expires_at"], now, now))
            registered.append(identity)
        return {"registered": registered, "rejected": rejected}

    def delivery(self, identity: str) -> dict | None:
        row = self._db.execute("SELECT * FROM deliveries WHERE envelope_id=?",
                               (identity,)).fetchone()
        return dict(row) if row else None

    def deliveries(self, *, state: str | None = None) -> list[dict]:
        sql = "SELECT * FROM deliveries"
        parameters: tuple = ()
        if state:
            sql += " WHERE state=?"
            parameters = (state,)
        return [dict(row) for row in self._db.execute(sql + " ORDER BY created_at,envelope_id",
                                                      parameters)]

    def attempts(self, identity: str | None = None) -> list[dict]:
        sql = "SELECT * FROM delivery_attempts"
        parameters: tuple = ()
        if identity:
            sql += " WHERE envelope_id=?"
            parameters = (identity,)
        return [dict(row) for row in self._db.execute(sql + " ORDER BY id", parameters)]

    # -- lease -----------------------------------------------------------

    def acquire(self, *, now: float, seconds: float = 60.0) -> bool:
        with self._transaction():
            row = self._db.execute("SELECT * FROM sender_lease WHERE id=1").fetchone()
            if row and row["owner"] != self.owner and row["until"] > now:
                return False
            takeover = bool(row and row["owner"] != self.owner)
            self._db.execute(
                "INSERT INTO sender_lease VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET "
                "owner=excluded.owner, until=excluded.until", (self.owner, now + seconds))
            if takeover:
                # A local lease cannot fence an HTTP request already in flight
                # at the provider. Never treat expiry as permission to resend.
                self._db.execute(
                    "UPDATE deliveries SET state='UNCERTAIN', reason=?, updated_at=? "
                    "WHERE state='SENDING'",
                    ("sender takeover; the previous send may have been transmitted", now))
        return True

    def recover(self, *, now: float | None = None) -> dict[str, Any]:
        """A crash while SENDING leaves an unknown outcome, never a silent retry."""
        now = time.time() if now is None else now
        with self._transaction():
            changed = self._db.execute(
                "UPDATE deliveries SET state='UNCERTAIN', reason=?, updated_at=? "
                "WHERE state='SENDING'",
                ("process stopped after committing SENDING; transmission is unknown",
                 now)).rowcount
        return {"uncertain": changed,
                "policy": "No automatic resend. The condition stays in the local inbox and a "
                          "later scheduled occurrence may describe it as a distinct message."}

    # -- quotas ----------------------------------------------------------

    def _usage(self, day: str) -> dict[str, int]:
        return {row["kind"]: row["used"] for row in
                self._db.execute("SELECT * FROM quota_usage WHERE day=?", (day,))}

    def quota_state(self, *, now: float) -> dict[str, Any]:
        usage = self._usage(utc_day(now))
        return {"day": utc_day(now), "used": usage, "total": sum(usage.values())}

    def _charge(self, kind: str, caps: dict, *, now: float) -> str | None:
        """Charge one send attempt. Retries and summaries count; no overflow message."""
        day = utc_day(now)
        usage = self._usage(day)
        total = sum(usage.values())
        if total >= caps.get("hard_daily", 40):
            return "hard daily channel cap reached"
        if kind == "reply":
            if usage.get("reply", 0) >= caps.get("daily_reply", 20):
                return "daily reply cap reached"
            recent = self._db.execute(
                "SELECT COUNT(*) FROM delivery_attempts a JOIN deliveries d "
                "ON d.envelope_id=a.envelope_id WHERE d.purpose LIKE 'reply:%' "
                "AND a.started_at>?", (now - 60,)).fetchone()[0]
            if recent >= caps.get("reply_per_minute", 5):
                return "per-minute reply cap reached"
        else:
            automated = usage.get("automated", 0) + usage.get("critical", 0)
            cap = caps.get("daily_automated", 20)
            if automated >= cap:
                return "daily automated cap reached"
            if kind != "critical":
                ordinary_cap = max(0, cap - caps.get("reserved_critical", 5))
                if usage.get("automated", 0) >= ordinary_cap:
                    return "daily automated cap reached, reserving the critical allowance"
        self._db.execute(
            "INSERT INTO quota_usage VALUES (?,?,1) ON CONFLICT(day,kind) DO UPDATE SET "
            "used=used+1", (day, kind))
        return None

    # -- dispatch --------------------------------------------------------

    def dispatch_once(self, transport: Transport, *, now: float | None = None,
                      limit: int = 10, clock=None) -> dict[str, Any]:
        # An OS file lock excludes other local senders even if a slow request
        # outlives its database lease. It is not a provider-side request fence.
        import fcntl
        path = self.runtime / "sender.lock"
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o660)
        try:
            if os.fstat(descriptor).st_uid == os.geteuid():
                os.fchmod(descriptor, 0o660)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"sent": [], "terminal": [],
                        "deferred": [{"reason": "another sender holds the lock"}]}
            clock = clock or (time.time if now is None else lambda: now)
            return self._dispatch_locked(transport, clock=clock, limit=limit)
        finally:
            os.close(descriptor)

    def _dispatch_locked(self, transport: Transport, *, clock, limit: int) -> dict[str, Any]:
        now = clock()
        result: dict[str, Any] = {"sent": [], "deferred": [], "terminal": []}
        if not self.acquire(now=now):
            result["deferred"].append({"reason": "another sender holds the lease"})
            return result
        candidates = [row for row in self.deliveries()
                      if row["state"] in ("PENDING", "READY", "RETRY_WAIT")]
        for candidate in candidates[:limit]:
            now = clock()
            identity = candidate["envelope_id"]
            # Re-read all authority and eligibility immediately before a claim.
            delivery = self.delivery(identity)
            if delivery["state"] not in ("PENDING", "READY", "RETRY_WAIT"):
                continue
            if delivery["expires_at"] <= now:
                self._settle(identity, "EXPIRED", "intent expiry elapsed", now=now)
                result["terminal"].append({"envelope": identity, "state": "EXPIRED"})
                continue
            if delivery["next_eligible"] > now or delivery["not_before"] > now:
                continue
            policy = self.policy()
            if not policy.get("active") or policy.get("grant", {}).get("expires_at", 0) <= now:
                self._settle(identity, "REVOKED", "channel policy inactive or grant expired",
                             now=now)
                result["terminal"].append({"envelope": identity, "state": "REVOKED"})
                continue
            permit = self.permit(identity, now=now)
            if permit is None:
                result["deferred"].append({"envelope": identity,
                                           "reason": "no fresh release permit"})
                continue
            body = self.read_envelope(identity)
            if body is None or permit["content_digest"] != delivery["content_digest"] \
                    or body["content_digest"] != delivery["content_digest"]:
                self._settle(identity, "SUPPRESSED", "envelope content no longer matches",
                             now=now)
                result["terminal"].append({"envelope": identity, "state": "SUPPRESSED"})
                continue
            if any(permit[key] != expected for key, expected in (
                    ("grant_id", policy["grant"]["grant_id"]),
                    ("grant_version", policy["grant"]["version"]),
                    ("channel_id", policy["channel"]["channel_id"]),
                    ("channel_version", policy["channel"]["version"]))):
                self._settle(identity, "REVOKED", "grant or channel superseded", now=now)
                result["terminal"].append({"envelope": identity, "state": "REVOKED"})
                continue
            if body["channel"]["identity"] != policy["channel"]["identity"]:
                self._settle(identity, "SUPPRESSED",
                             "envelope destination differs from approved channel", now=now)
                result["terminal"].append({"envelope": identity, "state": "SUPPRESSED"})
                continue
            kind = ("critical" if delivery["route"] == "critical"
                    else "reply" if delivery["purpose"].startswith("reply") else "automated")
            with self._transaction():
                lease = self._db.execute("SELECT * FROM sender_lease WHERE id=1").fetchone()
                current = self.delivery(identity)
                if not lease or lease["owner"] != self.owner or lease["until"] <= now:
                    result["deferred"].append({"reason": "sender lease lost"})
                    break
                if current["state"] != delivery["state"] or \
                        current["transmissions"] != delivery["transmissions"]:
                    continue
                last = self._db.execute("SELECT MAX(started_at) FROM delivery_attempts").fetchone()[0]
                refusal = None
                eligible = self._next_day(now)
                if last is not None and now < last + 1:
                    refusal, eligible = "one-message-per-second pacing", last + 1
                if refusal is None:
                    refusal = self._charge(kind, policy.get("caps", {}), now=now)
                    if refusal == "per-minute reply cap reached":
                        eligible = now + 60
                if refusal:
                    self._db.execute(
                        "UPDATE deliveries SET state='PENDING', reason=?, next_eligible=?, "
                        "updated_at=? WHERE envelope_id=?", (refusal, eligible, now, identity))
                else:
                    claimed = self._db.execute(
                        "UPDATE deliveries SET state='SENDING', transmissions=transmissions+1,"
                        " reason=NULL, lease_owner=?, updated_at=? WHERE envelope_id=? "
                        "AND state=? AND transmissions=?",
                        (self.owner, now, identity, delivery["state"],
                         delivery["transmissions"])).rowcount
                    if claimed != 1:
                        raise DeliveryError("Delivery claim changed during transaction")
                    self._db.execute(
                        "INSERT INTO delivery_attempts(envelope_id,transmission,outcome,"
                        "detail,lease_owner,started_at) VALUES (?,?,?,?,?,?)",
                        (identity, delivery["transmissions"] + 1, "sending", "", self.owner, now))
            if refusal:
                result["deferred"].append({"envelope": identity, "reason": refusal})
                continue
            outcome = self._transmit(transport, identity, body, now=now)
            result["sent"].append({"envelope": identity, "outcome": outcome})
        return result

    def _transmit(self, transport: Transport, identity: str, body: dict,
                  *, now: float) -> str:
        delivery = self.delivery(identity)
        try:
            outcome = transport.send(body)
        except DeliveryError as exc:
            self._finish(identity, "failed_before_send", str(exc), now=now)
            self._settle(identity, "RETRY_WAIT" if delivery["transmissions"]
                         < MAX_TRANSMISSION_ATTEMPTS else "FAILED_FINAL",
                         f"transport refused before transmission: {exc}", now=now,
                         backoff=delivery["transmissions"])
            return "failed_before_send"
        except Exception as exc:  # noqa: BLE001 - unknown transport state
            self._finish(identity, "uncertain", f"{type(exc).__name__}: {exc}", now=now)
            self._settle(identity, "UNCERTAIN",
                         "transport raised after transmission may have started", now=now)
            return "uncertain"
        self._finish(identity, outcome.outcome, outcome.detail, now=now,
                     provider_reference=outcome.provider_reference)
        if outcome.outcome == "accepted":
            if not outcome.destination_confirmed:
                self._settle(identity, "UNCERTAIN",
                             "acceptance did not confirm the recipient identity", now=now)
                return "uncertain"
            with self._transaction():
                self._db.execute(
                    "UPDATE deliveries SET state='ACCEPTED', provider_reference=?, "
                    "accepted_at=?, reason=?, updated_at=? WHERE envelope_id=?",
                    (outcome.provider_reference, now,
                     "provider accepted the request; not delivery, reading or approval",
                     now, identity))
            return "accepted"
        if outcome.outcome == "rejected_permanent":
            self._settle(identity, "FAILED_FINAL",
                         f"definitive rejection: {outcome.detail}", now=now)
            return "rejected_permanent"
        if outcome.outcome == "uncertain":
            self._settle(identity, "UNCERTAIN",
                         f"ambiguous outcome; no automatic resend: {outcome.detail}", now=now)
            return "uncertain"
        exhausted = delivery["transmissions"] >= MAX_TRANSMISSION_ATTEMPTS
        state = "FAILED_FINAL" if exhausted else "RETRY_WAIT"
        self._settle(identity, state, f"{outcome.outcome}: {outcome.detail}", now=now,
                     backoff=delivery["transmissions"], retry_after=outcome.retry_after)
        return outcome.outcome

    def _finish(self, identity: str, outcome: str, detail: str, *, now: float,
                provider_reference: str | None = None) -> None:
        with self._transaction():
            row = self._db.execute(
                "SELECT id FROM delivery_attempts WHERE envelope_id=? ORDER BY id DESC "
                "LIMIT 1", (identity,)).fetchone()
            if row:
                self._db.execute(
                    "UPDATE delivery_attempts SET outcome=?, detail=?, provider_reference=?, "
                    "finished_at=? WHERE id=?",
                    (outcome, detail[:2000], provider_reference, now, row[0]))

    def _settle(self, identity: str, state: str, reason: str, *, now: float,
                backoff: int | None = None, retry_after: float | None = None) -> None:
        current = self.delivery(identity)
        if current is None:
            raise DeliveryError("Unknown delivery")
        if state not in DELIVERY_TRANSITIONS.get(current["state"], set()):
            raise DeliveryError(
                f"Illegal delivery transition {current['state']} -> {state}")
        eligible = current["next_eligible"]
        if state == "RETRY_WAIT":
            delay = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** max(0, (backoff or 1) - 1)))
            # Deterministic jitter keyed to the envelope, not a random source.
            jitter = (int(identity[-4:], 16) % 1000) / 1000.0
            delay = max(delay + jitter, retry_after or 0.0)
            eligible = now + delay
            if eligible > current["expires_at"]:
                state, reason = "EXPIRED", "next eligible retry falls after intent expiry"
        with self._transaction():
            self._db.execute(
                "UPDATE deliveries SET state=?, reason=?, next_eligible=?, updated_at=? "
                "WHERE envelope_id=?", (state, reason[:2000], eligible, now, identity))

    @staticmethod
    def _next_day(now: float) -> float:
        import datetime
        moment = datetime.datetime.fromtimestamp(now, datetime.UTC)
        tomorrow = (moment + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        return tomorrow.timestamp()

    def _gap(self, kind: str, detail: Any, *, now: float) -> None:
        with self._transaction():
            if not self._db.execute("SELECT 1 FROM gateway_gaps WHERE kind=? AND "
                                    "closed_at IS NULL", (kind,)).fetchone():
                self._db.execute("INSERT INTO gateway_gaps(kind,detail,opened_at) "
                                 "VALUES (?,?,?)", (kind, canonical(detail).decode(), now))

    def gaps(self, *, open_only: bool = True) -> list[dict]:
        sql = "SELECT * FROM gateway_gaps" + (" WHERE closed_at IS NULL" if open_only else "")
        return [{**dict(row), "detail": parse_json(row["detail"])}
                for row in self._db.execute(sql + " ORDER BY id")]

    def health(self, *, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        accepted = self._db.execute("SELECT MAX(accepted_at) FROM deliveries").fetchone()[0]
        backlog = self._db.execute(
            "SELECT COUNT(*) FROM deliveries WHERE state NOT IN "
            "('ACCEPTED','FAILED_FINAL','SUPPRESSED','EXPIRED','REVOKED','SUPERSEDED')"
        ).fetchone()[0]
        return {"observed_at": now, "last_acceptance": accepted, "send_backlog": backlog,
                "quota": self.quota_state(now=now), "open_gaps": self.gaps(),
                "policy_active": bool(self.policy().get("active")),
                "limitation": "Reaching a transport says nothing about lab health, and a "
                              "stopped gateway cannot report its own outage."}


def json_dump(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False)
