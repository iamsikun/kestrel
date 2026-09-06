import pytest

from kestrel.agents import AgentTask, MockAgent
from kestrel.contracts import canonical
from kestrel.provider_records import read_claude, read_codex


def task():
    return AgentTask(task_id="capture", contract_digest="a" * 64, role="builder",
                     allowed_paths=["config.json"], max_output_bytes=65536, max_events=20,
                     max_calls=0, max_tokens=0, data_classification="public_synthetic")


def structured():
    result = MockAgent().run(task())
    return {"task_id": result.task_id, "contract_digest": result.contract_digest,
            "proposals": [p.model_dump() for p in result.proposals]}


def codex_events():
    return [{"type": "thread.started", "thread_id": "synthetic-thread"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"id": "i1", "type": "agent_message",
             "text": canonical(structured()).decode()}},
            {"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 10}}]


def claude_event():
    return {"type": "result", "subtype": "success", "is_error": False,
            "session_id": "synthetic-session", "usage": {"input_tokens": 20, "output_tokens": 10},
            "structured_output": structured()}


def read(events):
    return read_codex(task(), b"\n".join(canonical(e) for e in events),
                      provider_version="documentation-schema-2026-09-06",
                      source="synthetic_recording")


def test_mock_and_documented_provider_wires_share_boundary():
    # These are synthetic documented wire fixtures, not fabricated live captures.
    codex = read(codex_events())
    claude = read_claude(task(), canonical(claude_event()),
                         provider_version="documentation-schema-2026-09-06",
                         source="synthetic_recording")
    mock = MockAgent().run(task())
    assert mock.proposals == codex.proposals == claude.proposals
    assert codex.calls == claude.calls == 0
    assert codex.tokens == claude.tokens == 0
    assert codex.events[-1]["historical_usage"]["input_tokens"] == 20


@pytest.mark.acceptance("A29")
def test_duplicate_terminal_missing_terminal_and_oversized_stream():
    events = codex_events()
    for altered in (events + [events[-1]], events[:-1], [events[0], events[0], *events[1:]],
                    events * 50):
        with pytest.raises(ValueError):
            read(altered)


@pytest.mark.acceptance("A29")
def test_claude_false_success_and_provider_failure():
    event = claude_event()
    kwargs = {"provider_version": "documentation-schema-2026-09-06", "source": "synthetic_recording"}
    with pytest.raises(ValueError):
        read_claude(task(), canonical({**event, "structured_output": None}), **kwargs)
    failed = read_claude(task(), canonical({**event, "is_error": True}), **kwargs)
    assert failed.status == "error" and not failed.proposals


@pytest.mark.acceptance("A29")
def test_task_identity_and_usage_cannot_be_forged():
    event = claude_event()
    event["structured_output"]["contract_digest"] = "b" * 64
    kwargs = {"provider_version": "documentation-schema-2026-09-06", "source": "synthetic_recording"}
    with pytest.raises(ValueError):
        read_claude(task(), canonical(event), **kwargs)
    events = codex_events()
    events[-1]["usage"]["input_tokens"] = -1
    with pytest.raises(ValueError):
        read(events)
