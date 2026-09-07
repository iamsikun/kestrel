"""Bounded proposals, never permission grants or executable controller callbacks."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from kestrel.contracts import Digest, Identifier, StrictModel, canonical, digest, parse_json


class AgentTask(StrictModel):
    task_id: Identifier
    contract_digest: Digest
    role: Literal["planner", "builder", "critic"]
    allowed_paths: list[str]
    max_output_bytes: int = Field(ge=1, le=64 * 1024)
    max_events: int = Field(ge=1, le=100)
    max_calls: int = Field(ge=0, le=20)
    max_tokens: int = Field(ge=0, le=100_000)
    data_classification: Literal["public_synthetic"]


class Proposal(StrictModel):
    path: str
    content: str = Field(max_length=8192)


class AgentResult(StrictModel):
    version: Literal["0.1"] = "0.1"
    task_id: Identifier
    contract_digest: Digest
    provider: Literal["mock", "codex", "claude"]
    provider_version: str
    source: Literal["deterministic_mock", "synthetic_recording", "captured_recording", "live"]
    status: Literal["completed", "quota", "malformed", "timeout", "refused", "cancelled", "error"]
    proposals: list[Proposal] = Field(max_length=20)
    events: list[dict] = Field(max_length=100)
    calls: int = Field(ge=0)
    tokens: int = Field(ge=0)


def validate_result(task: AgentTask, payload: bytes) -> AgentResult:
    result = AgentResult.model_validate(parse_json(payload, max_bytes=task.max_output_bytes))
    if result.task_id != task.task_id or result.contract_digest != task.contract_digest:
        raise ValueError("Agent returned another task or contract")
    if result.calls > task.max_calls or result.tokens > task.max_tokens:
        raise ValueError("Provider usage exceeds the predeclared reservation")
    if len(result.events) > task.max_events:
        raise ValueError("Too many provider events")
    ids = [event.get("id") for event in result.events]
    if any(type(identity) is not str for identity in ids) or len(ids) != len(set(ids)):
        raise ValueError("Duplicate or missing provider event identity")
    if result.source == "live":
        raise ValueError(
            "No authorized live provider integration exists; a live label cannot be published"
        )
    if result.status != "completed" and result.proposals:
        raise ValueError("Failed provider results cannot publish candidate proposals")
    if any(p.path not in task.allowed_paths for p in result.proposals):
        raise ValueError("Agent attempted to change a forbidden path")
    if len({p.path for p in result.proposals}) != len(result.proposals):
        raise ValueError("Duplicate candidate file proposal")
    return result


class MockAgent:
    """Deterministic replay tests the harness, not coding intelligence."""

    provider_calls = 0

    def run(self, task: AgentTask) -> AgentResult:
        result = AgentResult(task_id=task.task_id, contract_digest=task.contract_digest,
                             provider="mock", provider_version="kestrel-mock-v1",
                             source="deterministic_mock", status="completed",
                             proposals=[Proposal(path="config.json", content='{"method":"inferior"}')],
                             events=[{"id": "mock-proposal-1", "type": "proposal"}], calls=0, tokens=0)
        return validate_result(task, canonical(result))


class ReplayAgent:
    """Historical normalized recordings share a boundary; they are not a live SDK."""

    provider_calls = 0

    def __init__(self, recording: bytes, expected_digest: str):
        if digest(parse_json(recording)) != expected_digest:
            raise ValueError("Recording identity mismatch")
        self.recording = recording

    def run(self, task: AgentTask) -> AgentResult:
        result = validate_result(task, self.recording)
        if result.source not in {"captured_recording", "synthetic_recording"}:
            raise ValueError("Replay requires an explicitly labeled recording")
        return result


def live_agent(*args, **kwargs):
    raise PermissionError(
        "Live agents are unavailable: no authorized provider integration or credential boundary"
    )
