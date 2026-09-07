"""A small typed Telegram Bot API adapter and the pairing workflow.

Only four methods are reachable: `getMe`, `getWebhookInfo`, `sendMessage` and
`getUpdates`. No method name, URL, token, recipient or request body can come
from a worker, an assistant item, or an inbound message.

The bot token appears inside the request URL, so it is constructed only here and
scrubbed from every log line, exception and repr. Routine tests never open a
socket: they inject a scripted HTTP transport.

Official documentation checked on 2026-09-07 (see `API_DOCUMENTATION`). Telegram
is a hosted service and cannot be pinned like a local wheel; these are the
documented behaviours this adapter is written against, not guarantees. No live
integration has been performed, so nothing here is a verified live integration.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from kestrel.contracts import canonical, parse_json
from kestrel.notifications import Channel, Destination, Grant, TransportResult

API_SCHEME = "https"
API_HOST = "api.telegram.org"
API_BASE = f"{API_SCHEME}://{API_HOST}"
ALLOWED_METHODS = ("getMe", "getWebhookInfo", "sendMessage", "getUpdates")

API_DOCUMENTATION = {
    "bot_api": "https://core.telegram.org/bots/api",
    "features_deep_linking": "https://core.telegram.org/bots/features#deep-linking",
    "faq": "https://core.telegram.org/bots/faq",
    "checked_on": "2026-09-07",
    "live_integration_verified": False,
}

#: Documented behaviours this adapter depends on.
API_ASSUMPTIONS = {
    "base_url": "https://api.telegram.org/bot<token>/METHOD_NAME over HTTPS only",
    "envelope": "responses carry ok, plus result or error_code/description/parameters",
    "retry_after": "flood control may supply parameters.retry_after in seconds",
    "text_limit": "sendMessage text is 1-4096 characters after entity parsing",
    "webhook_exclusive": "getUpdates does not work while an outgoing webhook is set",
    "offset_confirms": "an offset greater than a received update_id confirms it",
    "retention": "undelivered updates are kept for at most 24 hours",
    "idle_reset": "after a week without updates the next update_id is chosen randomly",
    "deep_link": "t.me/<bot>?start=<param>, A-Za-z0-9_- and at most 64 characters",
    "chat_rate": "roughly one message per second per chat for ordinary bot messaging",
}

SEND_RESPONSE_CAP = 256 * 1024
POLL_RESPONSE_CAP = 1024 * 1024
SEND_DEADLINE = 10.0
POLL_TIMEOUT = 25
POLL_DEADLINE = 35.0
MAX_TEXT_CHARACTERS = 4096
PAIRING_LIFETIME = 600.0
PAIRING_NONCE_BITS = 128

_TOKEN_SHAPE = re.compile(r"^\d{5,}:[A-Za-z0-9_\-]{20,}$")


class TelegramError(ValueError):
    """A Telegram adapter operation was refused or could not be interpreted."""


class TelegramTransportFailure(TelegramError):
    """The request could not be completed. `transmitted` records what is known."""

    def __init__(self, message: str, *, transmitted: bool) -> None:
        super().__init__(message)
        self.transmitted = transmitted


def wire(model, data: Any):
    """Validate an external payload into a strict internal record."""
    try:
        return model.model_validate(data)
    except Exception as exc:  # pydantic ValidationError and friends
        raise TelegramError(f"unexpected {model.__name__} shape: "
                            f"{type(exc).__name__}") from None


def redact(text: str, secrets_seen: tuple[str, ...]) -> str:
    """Remove credential material from anything that might be recorded."""
    cleaned = str(text)
    for secret in secrets_seen:
        if secret:
            cleaned = cleaned.replace(secret, "<redacted>")
            head = secret.split(":", 1)[0]
            if len(head) >= 5:
                cleaned = cleaned.replace(head, "<redacted>")
    return re.sub(r"\d{5,}:[A-Za-z0-9_\-]{20,}", "<redacted>", cleaned)


# --------------------------------------------------------------------------
# Wire models: ignore additive fields, refuse unknown critical shapes
# --------------------------------------------------------------------------

class WireModel(BaseModel):
    """Strict types, but tolerant of fields Telegram adds later."""

    model_config = ConfigDict(extra="ignore", strict=True, frozen=True, allow_inf_nan=False)


class BotIdentity(WireModel):
    id: int
    is_bot: bool
    first_name: str
    username: str | None = None


class WebhookInfo(WireModel):
    url: str = ""
    pending_update_count: int = 0
    has_custom_certificate: bool = False


class ChatRef(WireModel):
    id: int
    type: Literal["private", "group", "supergroup", "channel"]


class UserRef(WireModel):
    id: int
    is_bot: bool
    username: str | None = None


class MessageRef(WireModel):
    message_id: int
    date: int
    chat: ChatRef
    sender: UserRef | None = Field(default=None, alias="from")
    text: str | None = Field(default=None, max_length=8192)


class SentMessage(WireModel):
    message_id: int
    date: int
    chat: ChatRef


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)
    final_url: str = ""


class HttpTransport(Protocol):
    def request(self, *, method: str, url: str, body: bytes, headers: dict[str, str],
                timeout: float, max_bytes: int) -> HttpResponse: ...


@dataclass
class FakeHttpTransport:
    """Scripted HTTP for offline tests. It opens no socket and resolves no name."""

    script: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def request(self, *, method: str, url: str, body: bytes, headers: dict[str, str],
                timeout: float, max_bytes: int) -> HttpResponse:
        self.calls.append({"method": method, "url": url, "body": body,
                           "headers": dict(headers), "timeout": timeout,
                           "max_bytes": max_bytes})
        if not self.script:
            raise TelegramTransportFailure("no scripted response", transmitted=False)
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(url, body)
        return step


class StandardHttpTransport:
    """Standard-library HTTPS with verification, deadlines and no redirects.

    Constructing this does not contact anything; every call is a real network
    request and therefore belongs to the separately authorized activation gate.
    A Python-side host check does not contain a compromised gateway: the
    deployment profile must also restrict egress.
    """

    def __init__(self) -> None:
        import ssl
        import urllib.request

        class _NoRedirects(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                raise TelegramTransportFailure(
                    "the Telegram API must not redirect", transmitted=True)

        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        # An explicitly empty proxy handler ignores ambient proxy environment.
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=context),
            _NoRedirects())

    def request(self, *, method: str, url: str, body: bytes, headers: dict[str, str],
                timeout: float, max_bytes: int) -> HttpResponse:
        import urllib.error
        import urllib.parse
        import urllib.request

        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != API_SCHEME or parsed.hostname != API_HOST:
            raise TelegramTransportFailure("refusing a non-Telegram origin",
                                           transmitted=False)
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=timeout) as response:
                payload = response.read(max_bytes + 1)
                if len(payload) > max_bytes:
                    raise TelegramTransportFailure("response exceeded its byte cap",
                                                   transmitted=True)
                return HttpResponse(status=response.status, body=payload,
                                    headers={k.lower(): v for k, v in response.headers.items()},
                                    final_url=response.url)
        except urllib.error.HTTPError as exc:
            payload = exc.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise TelegramTransportFailure("error response exceeded its byte cap",
                                               transmitted=True) from None
            return HttpResponse(status=exc.code, body=payload,
                                headers={k.lower(): v for k, v in exc.headers.items()},
                                final_url=url)
        except TelegramTransportFailure:
            raise
        except OSError as exc:
            # A connection error can happen before or after transmission; treat
            # it as possibly transmitted rather than assuming a free retry.
            raise TelegramTransportFailure(f"{type(exc).__name__}", transmitted=True) from None


ACTIVATION_ENV = "KESTREL_TELEGRAM_ACTIVATED"


def require_activation(operation: str) -> None:
    """Fail closed unless an authorized operator has enabled live Telegram use.

    This is the activation gate. It is deliberately not a development switch:
    every offline path works without it, and no code sets it automatically.
    """
    if os.environ.get(ACTIVATION_ENV) != "1":
        raise TelegramError(
            f"{operation} would contact {API_HOST}. Live Telegram activity requires separate "
            f"operator authorization; see docs/MESSAGING_OPERATIONS.md. Only an authorized "
            f"operator sets {ACTIVATION_ENV}=1.")


def build_live_client(credential_file: Path, *, channel: str,
                      operation: str = "This operation") -> TelegramClient:
    """Construct a client that will make real requests. Gated on activation."""
    require_activation(operation)
    return TelegramClient(read_credential(credential_file), http=StandardHttpTransport(),
                          channel=channel)


def read_credential(path: Path) -> str:
    """Read an operator-provisioned bot token from a protected file.

    The token is never a command-line argument, never requested in chat, and
    never written to the assistant or gateway databases.
    """
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise TelegramError("Bot credential file is unavailable")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise TelegramError(f"Bot credential file must not be group or world readable "
                            f"(mode {mode:o})")
    token = path.read_text().strip()
    if not _TOKEN_SHAPE.match(token):
        raise TelegramError("Bot credential does not have the documented token shape")
    return token


@dataclass
class CallRecord:
    """What may be logged about a call. Never the URL, body, or credential."""

    method: str
    status_category: str
    duration_ms: int
    channel: str
    attempt: str | None = None


class TelegramClient:
    """A typed adapter over the four permitted methods."""

    def __init__(self, token: str, *, http: HttpTransport, channel: str = "unnamed",
                 clock=time.monotonic) -> None:
        if not _TOKEN_SHAPE.match(str(token)):
            raise TelegramError("Bot credential does not have the documented token shape")
        self._token = token
        self._http = http
        self.channel = channel
        self._clock = clock
        self.log: list[CallRecord] = []

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"<TelegramClient channel={self.channel!r} credential=<redacted>>"

    __str__ = __repr__

    @property
    def bot_prefix(self) -> str:
        """The token's numeric prefix, safe to record as a bot identity."""
        return self._token.split(":", 1)[0]

    def _url(self, method: str) -> str:
        if method not in ALLOWED_METHODS:
            raise TelegramError(f"Method {method!r} is outside the permitted adapter surface")
        return f"{API_BASE}/bot{self._token}/{method}"

    def scrub(self, text: Any) -> str:
        return redact(text, (self._token,))

    def call(self, method: str, parameters: dict[str, Any], *, timeout: float,
             max_bytes: int, attempt: str | None = None) -> dict[str, Any]:
        """Perform one bounded call and return its parsed envelope."""
        from kestrel.contracts import canonical

        body = canonical(parameters)
        started = self._clock()
        try:
            response = self._http.request(
                method="POST", url=self._url(method), body=body,
                headers={"Content-Type": "application/json",
                         "Content-Length": str(len(body)),
                         "Accept": "application/json"},
                timeout=timeout, max_bytes=max_bytes)
        except TelegramTransportFailure as exc:
            self._record(method, "transport_failure", started, attempt)
            raise TelegramTransportFailure(self.scrub(exc), transmitted=exc.transmitted) \
                from None
        except Exception as exc:  # noqa: BLE001 - unknown client state
            self._record(method, "transport_error", started, attempt)
            raise TelegramTransportFailure(
                self.scrub(f"{type(exc).__name__}"), transmitted=True) from None
        if len(response.body) > max_bytes:
            self._record(method, "oversized", started, attempt)
            raise TelegramTransportFailure("response exceeded its byte cap", transmitted=True)
        if response.final_url and not response.final_url.startswith(API_BASE):
            self._record(method, "redirected", started, attempt)
            raise TelegramTransportFailure("response came from another origin",
                                           transmitted=True)
        try:
            envelope = parse_json(response.body, max_bytes=max_bytes)
        except ValueError as exc:
            self._record(method, "malformed", started, attempt)
            raise TelegramError(f"malformed API response: {self.scrub(exc)}") from None
        if type(envelope) is not dict or type(envelope.get("ok")) is not bool:
            self._record(method, "malformed", started, attempt)
            raise TelegramError("API response did not carry a boolean ok field")
        category = "ok" if envelope["ok"] else f"error_{response.status}"
        self._record(method, category, started, attempt)
        envelope["_status"] = response.status
        return envelope

    def _record(self, method: str, category: str, started: float,
                attempt: str | None) -> None:
        self.log.append(CallRecord(method=method, status_category=category,
                                   duration_ms=int(max(0.0, self._clock() - started) * 1000),
                                   channel=self.channel, attempt=attempt))

    # -- permitted methods ---------------------------------------------

    def get_me(self) -> BotIdentity:
        envelope = self.call("getMe", {}, timeout=SEND_DEADLINE, max_bytes=SEND_RESPONSE_CAP)
        if not envelope["ok"]:
            raise TelegramError(f"getMe failed: {self._describe(envelope)}")
        identity = wire(BotIdentity, envelope["result"])
        if not identity.is_bot:
            raise TelegramError("The credential does not identify a bot account")
        return identity

    def get_webhook_info(self) -> WebhookInfo:
        envelope = self.call("getWebhookInfo", {}, timeout=SEND_DEADLINE,
                             max_bytes=SEND_RESPONSE_CAP)
        if not envelope["ok"]:
            raise TelegramError(f"getWebhookInfo failed: {self._describe(envelope)}")
        return wire(WebhookInfo, envelope["result"])

    def send_message(self, *, chat_id: int, text: str,
                     attempt: str | None = None) -> dict[str, Any]:
        """Send one plain-text message with previews and paid options disabled."""
        if not 1 <= len(text) <= MAX_TEXT_CHARACTERS:
            raise TelegramError("Message text is outside the documented 1-4096 range")
        parameters = {"chat_id": chat_id, "text": text,
                      "link_preview_options": {"is_disabled": True},
                      "disable_notification": False}
        envelope = self.call("sendMessage", parameters, timeout=SEND_DEADLINE,
                             max_bytes=SEND_RESPONSE_CAP, attempt=attempt)
        if envelope["ok"]:
            message = wire(SentMessage, envelope["result"])
            return {"ok": True, "message": message,
                    "destination_confirmed": message.chat.id == chat_id}
        return {"ok": False, "status": envelope["_status"],
                "error_code": envelope.get("error_code"),
                "description": self.scrub(envelope.get("description", "")),
                "retry_after": self._retry_after(envelope)}

    def get_updates(self, *, offset: int | None, timeout: int = POLL_TIMEOUT,
                    limit: int = 100) -> list[dict[str, Any]]:
        """Fetch raw updates. Types are re-validated by the caller."""
        parameters: dict[str, Any] = {"timeout": timeout, "limit": limit,
                                      "allowed_updates": ["message"]}
        if offset is not None:
            parameters["offset"] = offset
        envelope = self.call("getUpdates", parameters,
                             timeout=max(POLL_DEADLINE, timeout + 10),
                             max_bytes=POLL_RESPONSE_CAP)
        if not envelope["ok"]:
            raise TelegramError(f"getUpdates failed: {self._describe(envelope)}")
        result = envelope["result"]
        if type(result) is not list or len(result) > 100:
            raise TelegramError("getUpdates did not return a bounded update list")
        for update in result:
            if type(update) is not dict or type(update.get("update_id")) is not int:
                raise TelegramError("An update did not carry an integer update_id")
        return result

    @staticmethod
    def _retry_after(envelope: dict[str, Any]) -> float | None:
        parameters = envelope.get("parameters")
        if type(parameters) is dict and type(parameters.get("retry_after")) is int:
            return float(parameters["retry_after"])
        return None

    def _describe(self, envelope: dict[str, Any]) -> str:
        return self.scrub(f"error_code={envelope.get('error_code')} "
                          f"description={envelope.get('description')!r}")


# --------------------------------------------------------------------------
# Transport adapter for the delivery gateway
# --------------------------------------------------------------------------

@dataclass
class TelegramTransport:
    """Bridges the delivery state machine to the adapter.

    Definitive rejections fail the envelope, flood control retries after the
    server's own delay, and anything that may have been transmitted stays
    uncertain rather than being resent.
    """

    client: TelegramClient
    expected_chat_id: int
    name: str = "telegram"

    def send(self, envelope: dict[str, Any]) -> TransportResult:
        if str(envelope["channel"]["identity"]) != str(self.expected_chat_id):
            # Nothing is transmitted, and retrying a misaddressed envelope is
            # pointless, so this is definitive rather than uncertain.
            return TransportResult(
                outcome="rejected_permanent",
                detail="envelope destination differs from the paired chat")
        try:
            outcome = self.client.send_message(chat_id=self.expected_chat_id,
                                               text=envelope["text"],
                                               attempt=envelope["id"])
        except TelegramTransportFailure as exc:
            if exc.transmitted:
                return TransportResult(outcome="uncertain", detail=str(exc))
            return TransportResult(outcome="failed_before_send", detail=str(exc))
        except TelegramError as exc:
            return TransportResult(outcome="uncertain",
                                   detail=f"unreadable response: {exc}")
        if outcome["ok"]:
            message = outcome["message"]
            return TransportResult(
                outcome="accepted",
                provider_reference=f"{message.chat.id}:{message.message_id}",
                destination_confirmed=outcome["destination_confirmed"],
                detail="request accepted by the API; not delivery, reading or approval")
        status = outcome["status"]
        if status == 429 or outcome["retry_after"] is not None:
            return TransportResult(outcome="flood_control", detail=outcome["description"],
                                   retry_after=outcome["retry_after"])
        if status in (400, 401, 403, 404):
            return TransportResult(outcome="rejected_permanent",
                                   detail=outcome["description"])
        return TransportResult(outcome="uncertain",
                               detail=f"server failure {status}: {outcome['description']}")


# --------------------------------------------------------------------------
# Pairing
# --------------------------------------------------------------------------

PAIRING_SCHEMA = """
CREATE TABLE IF NOT EXISTS pairing_sessions(
    id TEXT PRIMARY KEY, bot_id INTEGER, bot_username TEXT, nonce_hash TEXT NOT NULL,
    state TEXT NOT NULL, candidate TEXT, created_at REAL NOT NULL,
    expires_at REAL NOT NULL, consumed_at REAL);
CREATE TABLE IF NOT EXISTS pairing_rejections(
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, reason TEXT NOT NULL,
    update_id INTEGER, created_at REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS pairing_nonce_immutable
BEFORE UPDATE OF nonce_hash,created_at,expires_at ON pairing_sessions
BEGIN SELECT RAISE(ABORT, 'a pairing nonce is immutable'); END;
"""


def nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


def start_link(username: str, nonce: str) -> str:
    """The documented private-chat deep link. A-Za-z0-9_- and at most 64 chars."""
    if not re.fullmatch(r"[A-Za-z0-9_]{4,32}", username):
        raise TelegramError("Bot username is not in the documented format")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", nonce):
        raise TelegramError("Pairing nonce is not a valid deep-link parameter")
    return f"https://t.me/{username}?start={nonce}"


class Pairing:
    """Operator-driven enrollment for exactly one private chat."""

    def __init__(self, assistant: Any, client: TelegramClient) -> None:
        self.assistant = assistant
        self.client = client

    def begin(self, *, now: float | None = None) -> dict[str, Any]:
        """Validate bot identity and webhook state, then open one session."""
        now = time.time() if now is None else now
        identity = self.client.get_me()
        webhook = self.client.get_webhook_info()
        if webhook.url:
            # Another integration owns this bot. Never delete its webhook or
            # discard its pending updates.
            raise TelegramError(
                "This bot already has an outgoing webhook, so getUpdates cannot be used. "
                "Resolve the conflict with its owner; nothing was changed.")
        if identity.username is None:
            raise TelegramError("The bot has no username, so no deep link can be built")
        nonce = secrets.token_urlsafe(PAIRING_NONCE_BITS // 8)[:43]
        session = f"pair-{secrets.token_hex(8)}"
        with self.assistant._transaction():
            self.assistant._db.execute("UPDATE pairing_sessions SET state='cancelled' "
                                       "WHERE state='open'")
            self.assistant._db.execute(
                "INSERT INTO pairing_sessions(id,bot_id,bot_username,nonce_hash,state,"
                "created_at,expires_at) VALUES (?,?,?,?, 'open', ?,?)",
                (session, identity.id, identity.username, nonce_hash(nonce), now,
                 now + PAIRING_LIFETIME))
        return {"session": session, "bot_id": identity.id, "bot_username": identity.username,
                "expires_at": now + PAIRING_LIFETIME,
                "start_link": start_link(identity.username, nonce),
                "pending_webhook_updates": webhook.pending_update_count,
                "instruction": "Open this link in your own Telegram client and press start. "
                               "Only the nonce is sent; it does not approve any research.",
                "authority": "pairing identifies a chat; the operator still confirms locally"}

    def poll(self, *, now: float | None = None, updates: list[dict] | None = None,
             ) -> dict[str, Any]:
        """Look only for the pairing event. Other inbound content is discarded."""
        now = time.time() if now is None else now
        row = self.assistant._db.execute(
            "SELECT * FROM pairing_sessions WHERE state='open' ORDER BY created_at DESC "
            "LIMIT 1").fetchone()
        if row is None:
            raise TelegramError("No pairing session is open")
        if row["expires_at"] <= now:
            with self.assistant._transaction():
                self.assistant._db.execute("UPDATE pairing_sessions SET state='expired' "
                                           "WHERE id=?", (row["id"],))
            raise TelegramError("The pairing session expired; start a new one")
        batch = self.client.get_updates(offset=None, timeout=0) if updates is None else updates
        for raw in batch:
            reason = self._reject_reason(raw, row)
            if reason:
                # Minimal retention of unrelated third-party content.
                with self.assistant._transaction():
                    self.assistant._db.execute(
                        "INSERT INTO pairing_rejections(session_id,reason,update_id,"
                        "created_at) VALUES (?,?,?,?)",
                        (row["id"], reason, raw.get("update_id"), now))
                continue
            message = wire(MessageRef, raw["message"])
            candidate = {"bot_id": row["bot_id"], "bot_username": row["bot_username"],
                         "chat_id": message.chat.id, "user_id": message.sender.id,
                         "username": message.sender.username, "observed_at": now,
                         "update_id": raw["update_id"]}
            with self.assistant._transaction():
                self.assistant._db.execute(
                    "UPDATE pairing_sessions SET state='candidate', candidate=?, "
                    "consumed_at=? WHERE id=?",
                    (__import__("json").dumps(candidate, sort_keys=True), now, row["id"]))
            return {"session": row["id"], "candidate": candidate,
                    "confirmation_required": True,
                    "authority": "an identified chat is not an approved recipient; confirm "
                                 "locally with the messaging operator credential"}
        return {"session": row["id"], "candidate": None,
                "rejected": [dict(entry) for entry in self.assistant._db.execute(
                    "SELECT reason,update_id FROM pairing_rejections WHERE session_id=? "
                    "ORDER BY id", (row["id"],))]}

    def _reject_reason(self, raw: dict, session) -> str | None:
        if type(raw) is not dict or type(raw.get("update_id")) is not int:
            return "not_an_update"
        kinds = sorted(set(raw) - {"update_id"})
        if kinds != ["message"]:
            return f"unsupported_update_kind:{','.join(kinds) or 'empty'}"
        try:
            message = wire(MessageRef, raw["message"])
        except ValueError:
            return "unparseable_message"
        if message.chat.type != "private":
            return "not_a_private_chat"
        if message.sender is None or message.sender.is_bot:
            return "bot_or_absent_sender"
        text = (message.text or "").strip()
        if not text.startswith("/start "):
            return "not_a_pairing_command"
        supplied = text.split(" ", 1)[1].strip()
        if not hmac.compare_digest(nonce_hash(supplied), session["nonce_hash"]):
            return "nonce_mismatch"
        return None

    def confirm(self, *, operator_token: str, channel_id: str = "personal",
                grant_id: str = "notify_operator", grant_lifetime: float = 30 * 86_400.0,
                allowed_conditions: list[str] | None = None,
                now: float | None = None) -> dict[str, Any]:
        """Bind the candidate identity into a channel and a service grant."""
        from kestrel.notifications import Exporter

        now = time.time() if now is None else now
        row = self.assistant._db.execute(
            "SELECT * FROM pairing_sessions WHERE state='candidate' ORDER BY created_at "
            "DESC LIMIT 1").fetchone()
        if row is None:
            raise TelegramError("No pairing candidate is awaiting confirmation")
        if row["expires_at"] <= now:
            raise TelegramError("The pairing session expired before confirmation")
        candidate = parse_json(row["candidate"], max_bytes=16 * 1024)
        exporter = Exporter(self.assistant)
        existing = exporter.channel(channel_id)
        channel = Channel(channel_id=channel_id,
                          version=(existing.version + 1) if existing else 1,
                          destination=Destination(transport="telegram",
                                                  identity=str(candidate["chat_id"]),
                                                  description="paired private chat"))
        exporter.register_channel(channel, operator_token=operator_token, now=now)
        previous = self.assistant._db.execute(
            "SELECT MAX(version) FROM grants WHERE grant_id=?", (grant_id,)).fetchone()[0]
        grant = Grant(grant_id=grant_id, version=(previous or 0) + 1, principal="operator",
                      channel_id=channel_id,
                      allowed_conditions=allowed_conditions or [],
                      expires_at=now + grant_lifetime)
        issued = exporter.issue_grant(grant, operator_token=operator_token, now=now)
        with self.assistant._transaction():
            self.assistant._db.execute(
                "UPDATE pairing_sessions SET state='confirmed' WHERE id=?", (row["id"],))
            self.assistant._set_meta("telegram_bot_id", str(candidate["bot_id"]))
            self.assistant._set_meta("telegram_chat_id", str(candidate["chat_id"]))
            self.assistant._set_meta("telegram_user_id", str(candidate["user_id"]))
            self.assistant._set_meta("telegram_epoch",
                                     str(int(self.assistant._meta("telegram_epoch", "0")) + 1))
        exporter.publish_routing()
        return {"session": row["id"], "channel": channel.channel_id,
                "channel_version": channel.version, "grant": issued,
                "bot_id": candidate["bot_id"], "chat_id": candidate["chat_id"],
                "user_id": candidate["user_id"],
                "authority": "binds one private chat as the approved recipient; it grants no "
                             "execution, budget or policy authority",
                "next_step": "A single authorized synthetic test message is a separate, "
                             "explicitly authorized activation step."}

    def sessions(self) -> list[dict]:
        return [dict(row) for row in self.assistant._db.execute(
            "SELECT id,bot_id,bot_username,state,created_at,expires_at,consumed_at "
            "FROM pairing_sessions ORDER BY created_at")]

    def rejections(self) -> list[dict]:
        return [dict(row) for row in self.assistant._db.execute(
            "SELECT * FROM pairing_rejections ORDER BY id")]


def rotate_credential(assistant: Any, client: TelegramClient, *,
                      now: float | None = None) -> dict[str, Any]:
    """Confirm a replacement credential still identifies the enrolled bot.

    Sending and polling must be paused before this runs, and any ambiguous
    attempt reconciled, because a rotated credential cannot resolve an earlier
    uncertain send.
    """
    now = time.time() if now is None else now
    identity = client.get_me()
    recorded = assistant._meta("telegram_bot_id")
    if recorded is None:
        raise TelegramError("No bot is enrolled; run pairing instead of rotation")
    if str(identity.id) != recorded:
        return {"same_bot": False, "recorded_bot_id": recorded,
                "observed_bot_id": identity.id,
                "required": "re-enrollment; this credential identifies a different bot"}
    return {"same_bot": True, "bot_id": identity.id, "checked_at": now,
            "limitation": "Rotation cannot resolve an already uncertain send, and a copied "
                          "token on another host remains a residual risk."}


# --------------------------------------------------------------------------
# Inbound polling
# --------------------------------------------------------------------------

#: Telegram documents that update identifiers may be chosen randomly after a
#: week without updates, so a numeric gap alone never implies a lost message.
IDLE_RESET_SECONDS = 7 * 86_400.0
#: Undelivered updates are documented as retained for at most 24 hours.
RETENTION_SECONDS = 24 * 3_600.0
MAX_JOURNAL_ROWS = 20_000
MAX_INBOUND_TEXT = 4096

ACCEPTED_UPDATE_KINDS = ("message",)


def normalize_update(raw: Any, *, chat_id: int, user_id: int) -> tuple[dict | None, str]:
    """Validate one raw update against the registered private chat.

    Returns a bounded normalized record or a rejection reason. Every rejection
    is still journaled as a tombstone so an offset advance never acknowledges
    bytes that were not accounted for.
    """
    if type(raw) is not dict or type(raw.get("update_id")) is not int:
        return None, "not_an_update"
    kinds = sorted(set(raw) - {"update_id"})
    if kinds != list(ACCEPTED_UPDATE_KINDS):
        return None, f"unsupported_update_kind:{','.join(kinds) or 'empty'}"
    raw_message = raw.get("message")
    if isinstance(raw_message, dict) and any(key in raw_message for key in (
            "forward_origin", "forward_from", "forward_from_chat", "forward_sender_name",
            "forward_date", "forward_from_message_id", "forward_signature",
            "is_automatic_forward")):
        return None, "forwarded_message"
    try:
        message = wire(MessageRef, raw["message"])
    except TelegramError:
        return None, "unparseable_message"
    if message.chat.type != "private":
        return None, "not_a_private_chat"
    if message.chat.id != chat_id:
        return None, "foreign_chat"
    if message.sender is None or message.sender.is_bot:
        return None, "bot_or_absent_sender"
    if message.sender.id != user_id:
        return None, "foreign_sender"
    text = (message.text or "").strip()
    if not text:
        return None, "empty_text"
    if len(text) > MAX_INBOUND_TEXT:
        return None, "oversized_text"
    return {"update_id": raw["update_id"], "message_id": message.message_id,
            "date": message.date, "chat_id": message.chat.id,
            "user_id": message.sender.id, "text": text}, ""


class InboundPoller:
    """One long poller writing an untrusted journal in the gateway database.

    An update is durably journaled before the offset advances, because advancing
    the offset is what confirms receipt upstream.
    """

    def __init__(self, gateway: Any, client: TelegramClient, *, bot_identity: str,
                 epoch: int, chat_id: int, user_id: int) -> None:
        self.gateway = gateway
        self.client = client
        self.bot_identity = str(bot_identity)
        self.epoch = int(epoch)
        self.chat_id = int(chat_id)
        self.user_id = int(user_id)

    # -- cursor ---------------------------------------------------------

    def cursor(self) -> dict[str, Any] | None:
        row = self.gateway._db.execute("SELECT * FROM poll_cursor WHERE id=1").fetchone()
        return dict(row) if row else None

    def _write_cursor(self, *, offset: int, mode: str, now: float,
                      last_update_at: float | None = None,
                      success: bool = True) -> None:
        existing = self.cursor()
        self.gateway._db.execute(
            "INSERT INTO poll_cursor VALUES (1,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "bot_identity=excluded.bot_identity, epoch=excluded.epoch, "
            "offset=excluded.offset, mode=excluded.mode, "
            "last_success=excluded.last_success, last_update_at=excluded.last_update_at, "
            "updated_at=excluded.updated_at",
            (self.bot_identity, self.epoch, offset, mode,
             now if success else (existing or {}).get("last_success"),
             last_update_at if last_update_at is not None
             else (existing or {}).get("last_update_at"), now))

    def journal_size(self) -> int:
        return self.gateway._db.execute("SELECT COUNT(*) FROM inbound_updates").fetchone()[0]

    def pending(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.gateway._db.execute(
            "SELECT * FROM inbound_updates WHERE accepted=1 AND processed_at IS NULL "
            "AND bot_identity=? AND epoch=? ORDER BY update_id LIMIT ?",
            (self.bot_identity, self.epoch, limit)).fetchall()
        return [{**dict(row), "record": parse_json(row["record"], max_bytes=32 * 1024)}
                for row in rows]

    def mark_processed(self, update_id: int, *, now: float) -> None:
        with self.gateway._transaction():
            self.gateway._db.execute(
                "UPDATE inbound_updates SET processed_at=? WHERE bot_identity=? AND epoch=? "
                "AND update_id=?", (now, self.bot_identity, self.epoch, update_id))

    # -- polling --------------------------------------------------------

    def poll_once(self, *, now: float, updates: list[dict] | None = None,
                  timeout: int = POLL_TIMEOUT) -> dict[str, Any]:
        cursor = self.cursor()
        if cursor and (cursor["bot_identity"] != self.bot_identity
                       or cursor["epoch"] != self.epoch):
            # Re-enrollment starts a new epoch; the old cursor is not reused.
            self.gateway._gap("inbound_epoch_change",
                              {"recorded": cursor["bot_identity"], "epoch": cursor["epoch"]},
                              now=now)
            with self.gateway._transaction():
                self._write_cursor(offset=0, mode="rebase", now=now, success=False)
            cursor = self.cursor()
        mode = cursor["mode"] if cursor else "rebase"
        last_update_at = cursor["last_update_at"] if cursor else None
        last_success = cursor["last_success"] if cursor else None
        if last_update_at is not None and now - last_update_at > IDLE_RESET_SECONDS:
            # Documented idle reset: identifiers may restart, so rebase rather
            # than trusting the retained high offset.
            mode = "rebase"
        if last_success is not None and now - last_success > RETENTION_SECONDS:
            self.gateway._gap(
                "possible_inbound_loss",
                {"offline_seconds": now - last_success,
                 "note": "Longer than Telegram's documented 24 hour retention; replies may "
                         "have been lost. Never assume the operator did not answer."},
                now=now)
        if self.journal_size() >= MAX_JOURNAL_ROWS:
            self.gateway._gap("inbound_journal_full", {"rows": self.journal_size()}, now=now)
            return {"polled": 0, "accepted": 0, "rejected": 0, "mode": mode,
                    "offset_advanced": False,
                    "reason": "inbound journal is full; the offset is deliberately held"}
        offset = None if mode == "rebase" else (cursor["offset"] if cursor else None)
        batch = (self.client.get_updates(offset=offset, timeout=timeout)
                 if updates is None else list(updates))
        accepted, rejected, duplicates = [], [], []
        highest = offset - 1 if offset else None
        for raw in batch:
            update_id = raw.get("update_id") if type(raw) is dict else None
            record, reason = normalize_update(raw, chat_id=self.chat_id, user_id=self.user_id)
            if type(update_id) is int:
                highest = update_id if highest is None else max(highest, update_id)
            try:
                with self.gateway._transaction():
                    self.gateway._db.execute(
                        "INSERT INTO inbound_updates(update_id,bot_identity,epoch,accepted,"
                        "record,received_at) VALUES (?,?,?,?,?,?)",
                        (update_id if type(update_id) is int else -1, self.bot_identity,
                         self.epoch, 1 if record else 0,
                         canonical(record or {"rejected": reason,
                                              "update_id": update_id}).decode(), now))
            except sqlite3.IntegrityError:
                # Only an identity that is actually durable is a replay. Disk,
                # constraint and other persistence failures must hold the cursor.
                existing = self.gateway._db.execute(
                    "SELECT 1 FROM inbound_updates WHERE bot_identity=? AND epoch=? "
                    "AND update_id=?", (self.bot_identity, self.epoch,
                                        update_id if type(update_id) is int else -1)).fetchone()
                if existing is None:
                    raise
                duplicates.append(update_id)
                continue
            (accepted if record else rejected).append(update_id)
        advanced = False
        if highest is not None:
            with self.gateway._transaction():
                # The offset is advanced only after every update in the batch is
                # durably journaled, because advancing it confirms receipt.
                self._write_cursor(offset=highest + 1, mode="normal", now=now,
                                   last_update_at=now if batch else last_update_at)
            advanced = True
        elif mode == "rebase":
            with self.gateway._transaction():
                self._write_cursor(offset=cursor["offset"] if cursor else 0, mode="normal",
                                   now=now)
        return {"polled": len(batch), "accepted": len(accepted), "rejected": len(rejected),
                "duplicates": duplicates, "mode": mode, "offset_advanced": advanced,
                "offset": (self.cursor() or {}).get("offset")}

    def rejections(self) -> list[dict[str, Any]]:
        return [{**dict(row), "record": parse_json(row["record"], max_bytes=32 * 1024)}
                for row in self.gateway._db.execute(
                    "SELECT * FROM inbound_updates WHERE accepted=0 ORDER BY received_at,"
                    "update_id")]
