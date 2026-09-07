"""Durable, approved execution of finite built-in mock and recording readers.

This is an offline data transformation, not a process sandbox or a live provider
driver. Only the fixed adapters below run in this process; project callbacks,
imports, tools, settings, and provider subprocesses are never accepted.
"""

from __future__ import annotations

import fcntl
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from kestrel.agents import AgentResult, AgentTask, MockAgent, Proposal, ReplayAgent, validate_result
from kestrel.artifacts import Artifacts, _safe_read
from kestrel.contracts import Digest, StrictModel, canonical, digest, parse_json
from kestrel.controller import TERMINAL, AuthorityError, Controller, StateError
from kestrel.provider_records import read_claude, read_codex


class AgentPlan(StrictModel):
    version: Literal["offline-agent-v1"] = "offline-agent-v1"
    backend: Literal["mock", "normalized_replay", "codex_recording", "claude_recording"] = "mock"
    role: Literal["planner", "builder", "critic"] = "builder"
    allowed_paths: list[str] = Field(default_factory=lambda: ["config.json"], max_length=20)
    max_output_bytes: int = Field(default=65536, ge=1, le=65536)
    max_events: int = Field(default=10, ge=1, le=100)
    runtime_seconds: int = Field(default=1, ge=1, le=5)
    expected_proposals: list[Proposal] | None = None
    recording: Digest | None = None
    recorded_task: AgentTask | None = None
    provider_version: str | None = Field(default=None, max_length=256)
    source: Literal["synthetic_recording", "captured_recording"] = "synthetic_recording"

    @model_validator(mode="after")
    def recording_identity(self):
        if self.backend == "mock":
            if self.recording is not None or self.recorded_task is not None:
                raise ValueError("Mock plans cannot substitute recordings")
        elif self.recording is None or self.recorded_task is None or not self.provider_version:
            raise ValueError("A replay plan needs exact recording identity and historical context")
        if self.recorded_task and (
            self.recorded_task.max_output_bytes > self.max_output_bytes
            or self.recorded_task.max_events > self.max_events
            or self.recorded_task.allowed_paths != self.allowed_paths
            or self.recorded_task.max_calls or self.recorded_task.max_tokens
        ):
            raise ValueError("Historical replay context cannot expand this offline envelope")
        return self


class AgentReceipt(StrictModel):
    attempt_id: str
    plan: Digest
    input_recording: Digest | None = None
    output: Digest | None
    usage: dict[str, int]
    elapsed_seconds: float = Field(ge=0)
    status: Literal["completed", "quota", "malformed", "timeout", "refused", "cancelled", "error", "interrupted"]
    error: str | None = Field(max_length=2048)


class OfflineAgents:
    def __init__(self, controller: Controller, store: Artifacts, root: Path):
        self.controller, self.store, self.root = controller, store, Path(root)
        if self.root.is_symlink() or self.root.resolve() != self.root:
            raise ValueError("Agent receipt root cannot redirect through symlinks")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    @contextmanager
    def _lock(self, attempt_id: str):
        # Identity comes from the controller; no provider path is interpreted.
        self.controller.attempt(attempt_id)
        fd = os.open(self.root / f"{attempt_id}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _write(self, attempt_id: str, record: dict) -> None:
        payload = canonical(record)
        descriptor, name = tempfile.mkstemp(prefix=".agent-receipt-", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.root / f"{attempt_id}.json")
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)

    def _plan(self, attempt: dict) -> tuple[AgentPlan, AgentTask, str]:
        task = self.controller.task(attempt["task_id"])
        spec = task["spec"]
        campaign = self.controller.campaign(attempt["campaign_id"])
        if attempt["backend"] != "offline-agent" or spec["operation"] != "offline_agent":
            raise AuthorityError("This backend handles only explicitly approved offline-agent tasks")
        identity = spec["recipe"]
        if campaign["contract"].get("data_classification") != "public_synthetic":
            raise AuthorityError("Offline agent tasks accept public synthetic inputs only")
        if identity != campaign["contract"]["controls"].get("agent_plan"):
            raise AuthorityError("Agent plan is not frozen in the approved contract")
        record = self.store.get(identity)
        if record["status"] != "valid" or record["size"] > 65536 or record["classification"] == "restricted":
            raise ValueError("Agent plan is invalid or exceeds the data bound")
        plan = AgentPlan.model_validate(parse_json(self.store.read(identity), max_bytes=65536))
        if (spec["budget"].get("runtime_seconds", 0) < plan.runtime_seconds
                or spec["budget"].get("provider_calls", 0) != 0
                or spec["budget"].get("tokens", 0) != 0):
            raise AuthorityError("Offline task must reserve its finite runtime and zero new provider usage")
        context = AgentTask(task_id=task["id"], contract_digest=campaign["digest"], role=plan.role,
                            allowed_paths=plan.allowed_paths, max_output_bytes=plan.max_output_bytes,
                            max_events=plan.max_events, max_calls=0, max_tokens=0,
                            data_classification="public_synthetic")
        return plan, context, identity

    def _invoke(self, plan: AgentPlan, task: AgentTask) -> tuple[AgentResult, bytes | None]:
        if plan.backend == "mock":
            return MockAgent().run(task), None
        record = self.store.get(plan.recording)
        if record["status"] != "valid" or record["size"] > plan.max_output_bytes or record["classification"] == "restricted":
            raise ValueError("Recording is invalid or exceeds the byte reservation")
        payload = self.store.read(plan.recording)
        if plan.backend == "normalized_replay":
            result = ReplayAgent(payload, digest(parse_json(payload, max_bytes=plan.max_output_bytes))).run(plan.recorded_task)
            if result.source != plan.source or result.provider_version != plan.provider_version:
                raise ValueError("Normalized recording differs from frozen source/provider version")
        else:
            reader = read_codex if plan.backend == "codex_recording" else read_claude
            result = reader(plan.recorded_task, payload, provider_version=plan.provider_version,
                            source=plan.source)
        # Historical output identity is retained in the frozen plan. This new
        # offline attempt is a replay occurrence, not a new call or replicate.
        result = result.model_copy(update={"task_id": task.task_id, "contract_digest": task.contract_digest})
        return validate_result(task, canonical(result)), payload

    def _finalize(self, attempt: dict, receipt: dict) -> dict:
        receipt = AgentReceipt.model_validate(receipt).model_dump(mode="json")
        if receipt["attempt_id"] != attempt["id"]:
            raise ValueError("Agent receipt belongs to another attempt")
        plan, task, identity = self._plan(attempt)
        if (receipt["plan"] != identity or receipt["input_recording"] != plan.recording
                or receipt["usage"] != {"provider_calls": 0, "tokens": 0}):
            raise ValueError("Agent receipt differs from the frozen plan or offline usage")
        if receipt["status"] == "completed":
            if not receipt["output"] or receipt["elapsed_seconds"] > plan.runtime_seconds:
                raise ValueError("Successful agent receipt requires bounded output and elapsed work")
            output_record = self.store.get(receipt["output"])
            if output_record["status"] != "valid" or output_record["size"] > plan.max_output_bytes:
                raise ValueError("Agent output is invalid or oversized")
            result = validate_result(task, self.store.read(receipt["output"]))
            if result.status != "completed" or (plan.expected_proposals is not None and result.proposals != plan.expected_proposals):
                raise ValueError("Receipt does not match a validated completed agent result")
        attempt = self.controller.reconcile(attempt["id"], lambda label: {
            "backend_label": label, "status": "stopped", "proof": "exclusive-offline-reader-lock"
        })
        if attempt["state"] in TERMINAL:
            return attempt
        if attempt["state"] != "VERIFYING":
            attempt = self.controller.transition(attempt["id"], "VERIFYING", fence=attempt["fence"])
        lineage = [self.controller.campaign(attempt["campaign_id"])["digest"], receipt["plan"]]
        if receipt["input_recording"]:
            lineage.append(receipt["input_recording"])
        if receipt.get("output"):
            lineage.append(receipt["output"])
        artifact = self.store.put_bytes(canonical(receipt), producer=f"agent-execution:{attempt['id']}",
                                        media_type="application/json", lineage=lineage)
        record = {"kind": "offline_agent", "execution": artifact["digest"],
                  "agent_output": receipt.get("output"), "status": receipt["status"],
                  "usage": receipt["usage"], "elapsed_seconds": receipt["elapsed_seconds"]}
        if receipt["status"] == "completed":
            self.controller.record_result(attempt["id"], record)
            state = "SUCCEEDED"
        else:
            self.controller.record_diagnostic(attempt["id"], record, fence=attempt["fence"])
            state = "CANCELLED" if receipt["status"] == "cancelled" else "FAILED"
        return self.controller.transition(attempt["id"], state, fence=attempt["fence"])

    def run(self, attempt_id: str) -> dict:
        with self._lock(attempt_id):
            attempt = self.controller.attempt(attempt_id)
            if attempt["state"] in TERMINAL:
                return attempt
            path = self.root / f"{attempt_id}.json"
            if path.exists():
                return self._finalize(attempt, parse_json(_safe_read(self.root, path.name, 65536), max_bytes=65536))
            plan, task, identity = self._plan(attempt)
            receipt = {"attempt_id": attempt_id, "plan": identity, "output": None,
                       "input_recording": plan.recording,
                       "usage": {"provider_calls": 0, "tokens": 0}, "elapsed_seconds": 0.0,
                       "status": "interrupted", "error": "Consumed launch without a durable receipt; no replay performed"}
            if attempt["launch_permitted"]:
                if attempt["lease_until"] <= time.time():
                    attempt = self.controller.reconcile(attempt_id, lambda label: {
                        "backend_label": label, "status": "absent", "never_launched": True
                    })
                self.controller.authorize_launch(attempt_id)
                attempt = self.controller.transition(attempt_id, "RUNNING", fence=attempt["fence"])
                started = time.monotonic()
                try:
                    result, _ = self._invoke(plan, task)
                    output = self.store.put_bytes(canonical(result), producer="controller:agent-content",
                                                   media_type="application/json")
                    receipt.update(output=output["digest"], status=result.status, error=None)
                    if result.status == "completed" and plan.expected_proposals is not None and result.proposals != plan.expected_proposals:
                        raise ValueError("Agent proposal differs from the frozen candidate plan")
                except (ValueError, OSError, TypeError, KeyError, RecursionError) as exc:
                    receipt.update(status="malformed", error=f"{type(exc).__name__}: {str(exc)[:1024]}")
                receipt["elapsed_seconds"] = time.monotonic() - started
                if receipt["elapsed_seconds"] > plan.runtime_seconds:
                    receipt.update(status="timeout", error="Finite offline transformation exceeded elapsed budget")
            self._write(attempt_id, receipt)
            return self._finalize(attempt, receipt)

    def cancel(self, attempt_id: str) -> dict:
        with self._lock(attempt_id):
            attempt = self.controller.attempt(attempt_id)
            if attempt["state"] in TERMINAL:
                return attempt
            plan, _, identity = self._plan(attempt)
            receipt = {"attempt_id": attempt_id, "plan": identity, "output": None,
                       "input_recording": plan.recording,
                       "usage": {"provider_calls": 0, "tokens": 0}, "elapsed_seconds": 0.0,
                       "status": "cancelled", "error": "Cancelled before offline output publication"}
            self._write(attempt_id, receipt)
            return self._finalize(attempt, receipt)

    def execute_ready(self, campaign_id: str, approval_id: str) -> None:
        for old in self.controller.attempts(campaign_id):
            if old["backend"] == "offline-agent" and old["state"] not in TERMINAL:
                self.run(old["id"])
        for task in self.controller.ready_tasks(campaign_id):
            if task["spec"]["operation"] != "offline_agent":
                continue
            attempt = self.controller.reserve(task["id"], approval_id=approval_id, principal="developer",
                                               backend="offline-agent", available_capabilities=["development"])
            self.run(attempt["id"])
        # A terminal agent failure prevents dependent executions; it never
        # authorizes a replacement plan or indefinite provider retry.
        failed = {a["task_id"] for a in self.controller.attempts(campaign_id)
                  if a["backend"] == "offline-agent" and a["state"] in TERMINAL and a["state"] != "SUCCEEDED"}
        for task in self.controller.tasks(campaign_id):
            if failed.intersection(task["spec"]["dependencies"]) and task["state"] not in TERMINAL:
                if task["state"] == "ACTIVE":
                    raise StateError("Dependent execution started before agent completion")
                self.controller.cancel_task(task["id"], "Approved prerequisite agent attempt failed")
