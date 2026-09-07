"""Offline readers for recorded provider output, not provider execution backends.

Wire formats were checked against official documentation on 2026-09-06. Live
SDK/version validation and reusable-credential isolation remain separate gates.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from kestrel.agents import AgentResult, AgentTask, Proposal, validate_result
from kestrel.contracts import StrictModel, canonical, parse_json


class CandidateOutput(StrictModel):
    task_id: str
    contract_digest: str
    proposals: list[Proposal] = Field(max_length=20)


def _result(task: AgentTask, provider: str, version: str, source: str, output: dict | None,
            events: list[dict], status: str = "completed") -> AgentResult:
    candidate = CandidateOutput.model_validate(output) if output is not None else None
    if candidate and (candidate.task_id != task.task_id or candidate.contract_digest != task.contract_digest):
        raise ValueError("Recorded provider output does not match the requested task")
    result = AgentResult(task_id=task.task_id, contract_digest=task.contract_digest,
                         provider=provider, provider_version=version, source=source,
                         status=status, proposals=candidate.proposals if candidate else [],
                         events=events, calls=0, tokens=0)
    # Replaying artifacts incurs no new calls/tokens. Historical usage stays in events.
    return validate_result(task, canonical(result))


def read_codex(task: AgentTask, stream: bytes, *, provider_version: str,
               source: Literal["synthetic_recording", "captured_recording"]) -> AgentResult:
    if len(stream) > task.max_output_bytes:
        raise ValueError("Provider recording exceeds byte reservation")
    lines = stream.splitlines()
    if not lines or len(lines) > task.max_events:
        raise ValueError("Provider recording has no events or exceeds event reservation")
    seen, summaries, events = set(), [], []
    thread_started = turn_started = turn_completed = False
    for index, line in enumerate(lines):
        event = parse_json(line, max_bytes=task.max_output_bytes)
        if type(event) is not dict or type(event.get("type")) is not str:
            raise ValueError("Malformed Codex event")
        kind = event["type"]
        if turn_completed:
            raise ValueError("Extra or duplicate events after terminal provider event")
        if kind == "thread.started":
            if thread_started or turn_started or not event.get("thread_id"):
                raise ValueError("Duplicate/malformed thread start")
            thread_started = True
        elif kind == "turn.started":
            if not thread_started or turn_started:
                raise ValueError("Duplicate or out-of-order turn start")
            turn_started = True
        elif kind in {"item.started", "item.updated", "item.completed"}:
            if not turn_started or type(event.get("item")) is not dict:
                raise ValueError("Item outside a provider turn")
            item = event["item"]
            key = (kind, item.get("id"))
            if type(key[1]) is not str or key in seen:
                raise ValueError("Duplicate or unidentified provider item event")
            seen.add(key)
            if item.get("type") == "agent_message" and kind == "item.completed":
                summaries.append(parse_json(item["text"], max_bytes=task.max_output_bytes))
        elif kind == "turn.completed":
            if not turn_started or type(event.get("usage")) is not dict:
                raise ValueError("Terminal event lacks usage or active turn")
            usage = event["usage"]
            for name in ("input_tokens", "output_tokens"):
                if type(usage.get(name)) is not int or usage[name] < 0:
                    raise ValueError("Provider usage must be nonnegative integers")
            turn_completed = True
        elif kind in {"turn.failed", "error"}:
            return _result(task, "codex", provider_version, source, None,
                           [{"id": f"recorded-{index}", "type": kind}], status="error")
        else:
            raise ValueError("Unsupported provider event version/type")
        entry = {"id": f"recorded-{index}", "type": kind}
        if kind == "turn.completed":
            entry["historical_usage"] = event["usage"]
        if kind.startswith("item."):
            entry["item_type"] = event["item"].get("type")
        events.append(entry)
    if not turn_completed or len(summaries) != 1:
        raise ValueError("Incomplete turn or ambiguous structured final output")
    return _result(task, "codex", provider_version, source, summaries[0], events)


def read_claude(task: AgentTask, payload: bytes, *, provider_version: str,
                source: Literal["synthetic_recording", "captured_recording"]) -> AgentResult:
    event = parse_json(payload, max_bytes=task.max_output_bytes)
    if type(event) is not dict or event.get("type") != "result" or type(event.get("is_error")) is not bool:
        raise ValueError("Expected a serialized Claude result event")
    if type(event.get("session_id")) is not str or not event["session_id"]:
        raise ValueError("Result requires recorded session identity")
    usage = event.get("usage")
    if type(usage) is not dict:
        raise ValueError("Result lacks historical usage")
    for key in ("input_tokens", "output_tokens"):
        if type(usage.get(key)) is not int or usage[key] < 0:
            raise ValueError("Provider usage must be nonnegative integers")
    events = [{"id": "recorded-result", "type": "result", "historical_usage": usage}]
    if event["is_error"] or event.get("subtype") != "success":
        return _result(task, "claude", provider_version, source, None, events, status="error")
    if event.get("structured_output") is None:
        raise ValueError("Successful exit without structured output cannot publish a proposal")
    return _result(task, "claude", provider_version, source, event["structured_output"], events)
