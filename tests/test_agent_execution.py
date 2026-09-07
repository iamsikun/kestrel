import time

import pytest

from kestrel.agent_execution import AgentPlan
from kestrel.agents import AgentTask, MockAgent
from kestrel.application import Lab
from kestrel.contracts import TaskSpec, canonical, digest, parse_json
from kestrel.controller import AuthorityError, BudgetError, StateError


def historical_task():
    return AgentTask(task_id="historical-agent", contract_digest="a" * 64, role="builder",
                     allowed_paths=["config.json"], max_output_bytes=65536, max_events=10,
                     max_calls=0, max_tokens=0, data_classification="public_synthetic")


def prepare(lab, payload=None, *, backend="normalized_replay", campaign_budget=1, task_budget=1,
            malformed_plan=False, classification="public_synthetic"):
    parents = []
    if payload is None:
        plan = AgentPlan()
    else:
        raw = lab.store.put_bytes(payload, producer="test:synthetic-recording")
        parents.append(raw["digest"])
        plan = AgentPlan(backend=backend, recording=raw["digest"], recorded_task=historical_task(),
                         provider_version="kestrel-mock-v1" if backend == "normalized_replay" else "synthetic-fixture")
    artifact = lab.store.put_bytes(b"{" if malformed_plan else canonical(plan),
                                   producer="controller:agent-plan", lineage=parents, classification=classification)
    contract = {"budget": {"attempts": campaign_budget, "runtime_seconds": campaign_budget,
                            "provider_calls": 0, "tokens": 0},
                "controls": {"agent_plan": artifact["digest"]}, "profile": "development",
                "capabilities": ["offline_agent", "execute"], "data_classification": "public_synthetic"}
    campaign = lab.controller.propose(contract)
    lab.store.put_bytes(canonical(contract), producer=f"contract:{campaign}",
                        lineage=[artifact["digest"]], classification=classification)
    task = TaskSpec(id=f"agent-{campaign}", campaign_id=campaign, recipe=artifact["digest"],
                    operation="offline_agent", profile="development", resources={"cpu": 1},
                    budget={"runtime_seconds": task_budget, "provider_calls": 0, "tokens": 0})
    lab.controller.add_task(campaign, task.model_dump(mode="json"))
    approval = lab.approve(campaign, digest(contract), (lab.root / "operator.token").read_text())
    return campaign, task.id, approval


def reserve(lab, task, approval):
    return lab.controller.reserve(task, approval_id=approval, principal="developer",
                                  backend="offline-agent", available_capabilities=["development"])


@pytest.mark.acceptance("A29")
@pytest.mark.parametrize("status", ["quota", "malformed", "timeout", "refused", "cancelled", "error"])
def test_failed_recordings_are_durable_accounted_attempts(tmp_path, status):
    value = MockAgent().run(historical_task()).model_dump(mode="json")
    value.update(source="synthetic_recording", status=status, proposals=[])
    with Lab.initialize(tmp_path / "lab") as lab:
        campaign, task, approval = prepare(lab, canonical(value))
        lab.agents.execute_ready(campaign, approval)
        attempt = lab.controller.attempts(campaign)[0]
        assert attempt["state"] == ("CANCELLED" if status == "cancelled" else "FAILED")
        assert attempt["result"] is None and attempt["diagnostic"]["status"] == status
        assert attempt["stopped_confirmed"] and not attempt["resources_held"]
        assert attempt["diagnostic"]["usage"] == {"provider_calls": 0, "tokens": 0}
        receipt = parse_json(lab.store.read(attempt["diagnostic"]["execution"]))
        assert receipt["attempt_id"] == attempt["id"]
        assert lab.controller.budget_used(campaign) == {
            "attempts": 1, "runtime_seconds": 1, "provider_calls": 0, "tokens": 0}
        lab.agents.execute_ready(campaign, approval)
        assert len(lab.controller.attempts(campaign)) == 1
        with pytest.raises(StateError):
            reserve(lab, task, approval)
    with Lab(tmp_path / "lab") as lab:
        assert lab.controller.attempt(attempt["id"])["diagnostic"] == attempt["diagnostic"]


@pytest.mark.acceptance("A29")
@pytest.mark.parametrize("corruption", ["malformed-json", "deep-json", "duplicate-events", "forbidden-path", "oversized", "usage", "live"])
def test_untrusted_agent_data_fails_without_expanding_authority(tmp_path, corruption):
    value = MockAgent().run(historical_task()).model_dump(mode="json")
    value["source"] = "synthetic_recording"
    if corruption == "duplicate-events":
        value["events"] *= 2
    elif corruption == "forbidden-path":
        value["proposals"][0]["path"] = "policy.json"
    elif corruption == "usage":
        value["calls"] = 1
    elif corruption == "live":
        value["source"] = "live"
    payload = b"{" if corruption == "malformed-json" else b" " * 65537 if corruption == "oversized" else canonical(value)
    if corruption == "deep-json":
        payload = b"[" * 2000 + b"0" + b"]" * 2000
    with Lab.initialize(tmp_path / "lab") as lab:
        campaign, _, approval = prepare(lab, payload)
        authority = lab.controller._db.execute("SELECT * FROM approvals").fetchall()
        lab.agents.execute_ready(campaign, approval)
        attempt = lab.controller.attempts(campaign)[0]
        assert attempt["state"] == "FAILED" and attempt["result"] is None
        assert attempt["diagnostic"]["status"] == "malformed"
        assert lab.controller._db.execute("SELECT * FROM approvals").fetchall() == authority
        assert lab.controller.resources_used()["cpu"] == 0
        assert lab.controller.budget_used(campaign)["attempts"] == 1
        with pytest.raises(AuthorityError):
            lab.controller.approve(campaign, token=(lab.root / "operator.token").read_text(),
                                   contract_digest=lab.controller.campaign(campaign)["digest"],
                                   expires_at=time.time() + 60, capabilities=["live_provider"])


@pytest.mark.acceptance("A29")
@pytest.mark.parametrize("backend", ["codex_recording", "claude_recording"])
def test_documented_wire_readers_use_real_approved_attempts(tmp_path, backend):
    historical = MockAgent().run(historical_task())
    structured = {"task_id": historical.task_id, "contract_digest": historical.contract_digest,
                   "proposals": [p.model_dump() for p in historical.proposals]}
    usage = {"input_tokens": 20, "output_tokens": 10}
    if backend == "codex_recording":
        payload = b"\n".join(canonical(e) for e in [
            {"type": "thread.started", "thread_id": "synthetic"}, {"type": "turn.started"},
            {"type": "item.completed", "item": {"id": "p1", "type": "agent_message", "text": canonical(structured).decode()}},
            {"type": "turn.completed", "usage": usage}])
    else:
        payload = canonical({"type": "result", "subtype": "success", "is_error": False,
                             "session_id": "synthetic", "usage": usage, "structured_output": structured})
    with Lab.initialize(tmp_path / "lab") as lab:
        campaign, task, approval = prepare(lab, payload, backend=backend)
        lab.agents.execute_ready(campaign, approval)
        attempt = lab.controller.attempts(campaign)[0]
        assert attempt["state"] == "SUCCEEDED" and not attempt["independent_replicate"]
        result = parse_json(lab.store.read(attempt["result"]["agent_output"]))
        assert result["task_id"] == task and result["contract_digest"] == lab.controller.campaign(campaign)["digest"]
        assert result["events"][-1]["historical_usage"] == usage
        assert result["calls"] == result["tokens"] == 0
        assert result["source"] == "synthetic_recording"


@pytest.mark.acceptance("A29")
def test_authority_and_budget_are_checked_before_mock_invocation(tmp_path, monkeypatch):
    with Lab.initialize(tmp_path / "lab") as lab:
        campaign, task, approval = prepare(lab, campaign_budget=1, task_budget=2)
        monkeypatch.setattr(MockAgent, "run", lambda *_: pytest.fail("Unauthorized mock invocation"))
        with pytest.raises(AuthorityError):
            reserve(lab, task, "absent-approval")
        with pytest.raises(BudgetError):
            reserve(lab, task, approval)
        assert lab.controller.attempts(campaign) == []
        assert lab.controller.budget_used(campaign)["attempts"] == 0


@pytest.mark.acceptance("A29")
@pytest.mark.parametrize("crash", ["before-launch", "after-invoke", "after-receipt", "after-result"])
def test_restart_retains_attempt_and_never_blindly_reinvokes(tmp_path, monkeypatch, crash):
    calls = []
    original_run = MockAgent.run
    def counted(agent, task):
        calls.append(task.task_id)
        return original_run(agent, task)
    monkeypatch.setattr(MockAgent, "run", counted)
    with Lab.initialize(tmp_path / "lab") as lab:
        campaign, task, approval = prepare(lab)
        attempt = reserve(lab, task, approval)
        with monkeypatch.context() as interrupted:
            def stop(*_args, **_kwargs):
                raise SystemExit("synthetic controller interruption")
            if crash == "before-launch":
                interrupted.setattr(lab.controller, "authorize_launch", stop)
            elif crash == "after-invoke":
                interrupted.setattr(lab.agents, "_write", stop)
            elif crash == "after-receipt":
                interrupted.setattr(lab.agents, "_finalize", stop)
            else:
                original = lab.controller.record_result
                def committed(*args, **kwargs):
                    original(*args, **kwargs)
                    stop()
                interrupted.setattr(lab.controller, "record_result", committed)
            with pytest.raises(SystemExit):
                lab.agents.run(attempt["id"])
    with Lab(tmp_path / "lab") as restored:
        restored.agents.execute_ready(campaign, approval)
        current = restored.controller.attempt(attempt["id"])
        assert current["state"] == ("FAILED" if crash == "after-invoke" else "SUCCEEDED")
        assert len(calls) == 1
        assert len(restored.controller.attempts(campaign)) == 1
        assert restored.controller.budget_used(campaign)["attempts"] == 1
        assert not current["resources_held"] and current["stopped_confirmed"]
        if crash == "after-invoke":
            assert current["diagnostic"]["status"] == "interrupted"
        else:
            with pytest.raises(StateError, match="Stale"):
                restored.controller.transition(current["id"], "FAILED", fence=attempt["fence"])


@pytest.mark.acceptance("A29")
def test_cancelled_agent_attempt_cannot_run_from_a_delayed_dispatch(tmp_path, monkeypatch):
    with Lab.initialize(tmp_path / "lab") as lab:
        campaign, task, approval = prepare(lab)
        attempt = reserve(lab, task, approval)
        lab.agents.cancel(attempt["id"])
        monkeypatch.setattr(MockAgent, "run", lambda *_: pytest.fail("Cancelled mock invocation"))
        assert lab.agents.run(attempt["id"])["state"] == "CANCELLED"
        assert lab.controller.resources_used()["cpu"] == 0
        assert lab.controller.budget_used(campaign)["attempts"] == 1


@pytest.mark.acceptance("A29")
def test_revocation_prevents_reserved_agent_dispatch(tmp_path, monkeypatch):
    with Lab.initialize(tmp_path / "lab") as lab:
        campaign, task, approval = prepare(lab)
        attempt = reserve(lab, task, approval)
        lab.controller.revoke(approval, token=(lab.root / "operator.token").read_text())
        monkeypatch.setattr(MockAgent, "run", lambda *_: pytest.fail("Revoked mock invocation"))
        with pytest.raises(AuthorityError):
            lab.agents.run(attempt["id"])
        assert lab.controller.attempt(attempt["id"])["state"] == "STARTING"
        lab.agents.cancel(attempt["id"])
        assert lab.controller.budget_used(campaign)["attempts"] == 1


@pytest.mark.acceptance("A29")
def test_replayed_capture_remains_in_export_and_invalidation_lineage(tmp_path):
    value = MockAgent().run(historical_task()).model_dump(mode="json")
    value["source"] = "synthetic_recording"
    with Lab.initialize(tmp_path / "lab") as lab:
        campaign, _, approval = prepare(lab, canonical(value))
        lab.agents.execute_ready(campaign, approval)
        attempt = lab.controller.attempts(campaign)[0]
        receipt_id = attempt["result"]["execution"]
        receipt = parse_json(lab.store.read(receipt_id))
        raw_id = receipt["input_recording"]
        assert raw_id in lab.store.get(receipt_id)["lineage"]
        bundle = lab.store.export_bundle(tmp_path / "agent.zip", [receipt_id])
        from kestrel.artifacts import Artifacts
        with_artifacts = Artifacts(tmp_path / "imported")
        try:
            imported = with_artifacts.import_bundle(bundle)
            assert raw_id in {record["digest"] for record in imported}
        finally:
            with_artifacts.close()
        lab.store.invalidate(raw_id, "Synthetic capture provenance revoked")
        assert lab.store.get(receipt_id)["status"] != "valid"


@pytest.mark.acceptance("A29")
@pytest.mark.parametrize("condition", ["malformed", "invalidated", "deleted", "tampered", "restricted"])
def test_cancellation_does_not_require_readable_or_valid_plan(tmp_path, monkeypatch, condition):
    with Lab.initialize(tmp_path / "lab") as lab:
        campaign, task, approval = prepare(lab, malformed_plan=condition == "malformed",
                                           classification="restricted" if condition == "restricted" else "public_synthetic")
        attempt = reserve(lab, task, approval)
        identity = lab.controller.task(task)["spec"]["recipe"]
        if condition == "invalidated":
            lab.store.invalidate(identity, "Synthetic plan invalidation")
        elif condition == "deleted":
            lab.store.db.execute("UPDATE artifacts SET status='deleted' WHERE digest=?", (identity,))
            lab.store.db.commit()
        elif condition == "tampered":
            path = lab.store.objects / identity
            path.chmod(0o600)
            path.write_bytes(b"tampered synthetic input")
        monkeypatch.setattr(MockAgent, "run", lambda *_: pytest.fail("Invalid plan cannot invoke agent"))
        with pytest.raises(ValueError):
            lab.agents.run(attempt["id"])
        assert lab.controller.attempt(attempt["id"])["resources_held"]
        # Cancellation must not read even a well-formed plan or contract. Its
        # classification decision uses persisted metadata and defaults closed.
        with monkeypatch.context() as cancelling:
            cancelling.setattr(lab.store, "read", lambda *_: pytest.fail("Cancellation read unavailable plan bytes"))
            current = lab.agents.cancel(attempt["id"])
        assert current["state"] == "CANCELLED" and current["stopped_confirmed"]
        assert not current["resources_held"]
        assert lab.controller.budget_used(campaign)["attempts"] == 1
        record = lab.store.get(current["diagnostic"]["execution"])
        assert record["assurance"] == "traceable"
        assert record["classification"] == ("restricted" if condition == "restricted" else "public_synthetic")
        receipt = parse_json(lab.store.read(record["digest"]))
        assert receipt["input_content_verified"] is False
        assert receipt["plan"] == identity


@pytest.mark.acceptance("A29")
def test_invalid_plan_cancellation_receipt_recovers_without_reinvocation(tmp_path, monkeypatch):
    with Lab.initialize(tmp_path / "lab") as lab:
        campaign, task, approval = prepare(lab, malformed_plan=True)
        attempt = reserve(lab, task, approval)
        with monkeypatch.context() as interrupted:
            def stop(*_args, **_kwargs):
                raise SystemExit("Interrupted after durable cancellation receipt")
            interrupted.setattr(lab.agents, "_finalize", stop)
            with pytest.raises(SystemExit):
                lab.agents.cancel(attempt["id"])
    with Lab(tmp_path / "lab") as lab:
        monkeypatch.setattr(MockAgent, "run", lambda *_: pytest.fail("Cancellation recovery invoked mock"))
        lab.agents.execute_ready(campaign, approval)
        assert lab.controller.attempt(attempt["id"])["state"] == "CANCELLED"
        assert lab.controller.resources_used()["cpu"] == 0


@pytest.mark.acceptance("A29")
def test_offline_cancellation_cannot_certify_another_backend_stopped(tmp_path):
    with Lab.initialize(tmp_path / "lab") as lab:
        _, task, approval = prepare(lab)
        attempt = lab.controller.reserve(task, approval_id=approval, principal="developer",
                                         backend="development", available_capabilities=["development"])
        with pytest.raises(AuthorityError):
            lab.agents.cancel(attempt["id"])
        current = lab.controller.attempt(attempt["id"])
        assert current["state"] == "STARTING" and current["resources_held"]
        assert not current["stopped_confirmed"]


@pytest.mark.acceptance("A29")
@pytest.mark.parametrize("forgery", ["live", "captured", "provider-version"])
def test_receipt_output_provenance_must_match_the_frozen_plan(tmp_path, forgery):
    """Independent audit regression: the receipt-driven recovery path replays a
    stored output without reinvoking the reader, so that output's provenance must
    still match the frozen plan. A live label has no authorized integration."""
    with Lab.initialize(tmp_path / "lab") as lab:
        if forgery == "provider-version":
            # A pinned provider version only exists for a replay plan; the mock
            # backend declares none, so that case must exercise the replay path.
            recording = MockAgent().run(historical_task()).model_dump(mode="json")
            recording["source"] = "synthetic_recording"
            campaign, task, approval = prepare(lab, canonical(recording))
        else:
            campaign, task, approval = prepare(lab)
        attempt = reserve(lab, task, approval)
        plan_digest = lab.controller.task(task)["spec"]["recipe"]
        honest = MockAgent().run(historical_task()).model_dump(mode="json")
        honest.update(task_id=task, contract_digest=lab.controller.campaign(campaign)["digest"])
        if forgery == "live":
            honest.update(source="live", provider="claude",
                          provider_version="claude-opus-unverified")
        elif forgery == "captured":
            honest.update(source="captured_recording", provider="codex",
                          provider_version="codex-cli-unverified")
        else:
            honest.update(source="synthetic_recording",
                          provider_version="kestrel-mock-v99-unpinned")
        forged = lab.store.put_bytes(canonical(honest), producer="test:forged-agent-output")
        plan = AgentPlan.model_validate(parse_json(lab.store.read(plan_digest)))
        lab.agents._write(attempt["id"], {
            "attempt_id": attempt["id"], "plan": plan_digest,
            "input_recording": plan.recording,
            "output": forged["digest"], "usage": {"provider_calls": 0, "tokens": 0},
            "elapsed_seconds": 0.0, "status": "completed", "error": None,
        })
        expected = ("No authorized live provider integration" if forgery == "live"
                    else "provenance differs from the frozen plan")
        with pytest.raises(ValueError, match=expected):
            lab.agents.run(attempt["id"])
        current = lab.controller.attempt(attempt["id"])
        assert current["state"] != "SUCCEEDED"
        assert current["result"] is None
        assert lab.controller.results(campaign) == []
        assert lab.controller.budget_used(campaign)["provider_calls"] == 0
