import pytest

from kestrel.agents import AgentTask, MockAgent, ReplayAgent, live_agent, validate_result
from kestrel.contracts import canonical, digest


def task():
    return AgentTask(task_id="mock-task", contract_digest="a" * 64, role="builder",
                     allowed_paths=["config.json"], max_output_bytes=65536, max_events=10,
                     max_calls=0, max_tokens=0, data_classification="public_synthetic")


@pytest.mark.acceptance("A04")
def test_mock_deterministic_without_calls():
    mock = MockAgent()
    assert mock.run(task()) == mock.run(task())
    assert mock.provider_calls == 0


@pytest.mark.acceptance("A29")
@pytest.mark.parametrize("status", ["quota", "malformed", "timeout", "refused", "cancelled", "error"])
def test_bounded_provider_failures(status):
    value = MockAgent().run(task()).model_dump()
    value.update(status=status, proposals=[])
    assert validate_result(task(), canonical(value)).status == status
    value["proposals"] = [{"path": "config.json", "content": "forged"}]
    with pytest.raises(ValueError):
        validate_result(task(), canonical(value))


@pytest.mark.acceptance("A29")
def test_duplicate_events_and_authority_edits_rejected():
    value = MockAgent().run(task()).model_dump()
    value["events"] *= 2
    with pytest.raises(ValueError, match="Duplicate"):
        validate_result(task(), canonical(value))
    value = MockAgent().run(task()).model_dump()
    value["proposals"] = [{"path": "policy.json", "content": "grant all"}]
    with pytest.raises(ValueError, match="forbidden"):
        validate_result(task(), canonical(value))


def test_synthetic_normalized_provider_recordings_are_not_live_captures():
    for provider in ("codex", "claude"):
        value = MockAgent().run(task()).model_dump()
        value.update(provider=provider, provider_version="unverified-synthetic-fixture",
                     source="synthetic_recording")
        replay = ReplayAgent(canonical(value), digest(value))
        assert replay.run(task()).source == "synthetic_recording"
        assert replay.provider_calls == 0
    with pytest.raises(PermissionError):
        live_agent()
