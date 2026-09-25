import pytest

from backend.agent_runtime.canonical_runtime import AttemptAccumulator
from backend.agent_runtime.tool_contracts import ProductToolCall


def test_settlement_is_single_use_and_rejects_late_frames():
    attempt = AttemptAccumulator(turn=1, step=1)
    attempt.content_delta("summary")
    attempt.settle([])
    with pytest.raises(RuntimeError, match="ALREADY_SETTLED"):
        attempt.settle([])
    with pytest.raises(RuntimeError, match="ALREADY_SETTLED"):
        attempt.content_delta("late")


def test_duplicate_call_ids_rejected_before_execution():
    attempt = AttemptAccumulator(turn=1, step=1)
    call = ProductToolCall("duplicate", "read_file", {"path": "one.cpp"})
    with pytest.raises(ValueError, match="DUPLICATE_CALL_ID"):
        attempt.settle([call, call])
    assert len(attempt.settle([call]).invocations) == 1


def test_settlement_detaches_nested_model_arguments():
    arguments = {"steps": [{"text": "Inspect", "status": "pending"}]}
    attempt = AttemptAccumulator(turn=1, step=1)
    settled = attempt.settle([ProductToolCall("plan", "update_plan", arguments)])
    arguments["steps"][0]["text"] = "Mutated"
    assert settled.invocations[0].arguments["steps"][0]["text"] == "Inspect"
    with pytest.raises(TypeError):
        settled.invocations[0].arguments["steps"] = []
