from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from backend.models.runtime import ExecutionContext, RuntimeResult, RuntimeState


def test_runtime_state_round_trips_and_normalizes_to_utc() -> None:
    offset = timezone(timedelta(hours=5, minutes=30))
    started = datetime(2026, 1, 1, 10, tzinfo=offset)
    state = RuntimeState(
        current_step="build",
        started_at=started,
        updated_at=started + timedelta(seconds=2),
        status="RUNNING",
    )

    assert state.started_at.tzinfo is timezone.utc
    assert RuntimeState.from_json(state.to_json()) == state


def test_runtime_state_rejects_reversed_timestamps() -> None:
    now = datetime.now(timezone.utc)

    with pytest.raises(ValueError, match="earlier"):
        RuntimeState("build", now, now - timedelta(seconds=1), "RUNNING")


def test_execution_context_freezes_and_detaches_metadata() -> None:
    metadata = {"features": ["wifi"]}
    context = ExecutionContext(
        task_id="task-1",
        execution_id="execution-1",
        project_name="weather-station",
        target_board="ESP32",
        framework="PlatformIO",
        metadata=metadata,
    )
    metadata["features"].append("ble")

    assert context.to_dict()["metadata"] == {"features": ["wifi"]}
    assert ExecutionContext.from_dict(context.to_dict()) == context
    with pytest.raises(TypeError):
        context.metadata["new"] = True  # type: ignore[index]


def test_runtime_result_is_immutable_and_serializes_collections_as_lists() -> None:
    result = RuntimeResult(
        success=False,
        duration_ms=125,
        logs=["build started"],
        errors=["compiler failed"],
    )

    assert result.logs == ("build started",)
    assert result.to_dict()["errors"] == ["compiler failed"]
    assert RuntimeResult.from_json(result.to_json()) == result
    with pytest.raises(FrozenInstanceError):
        result.duration_ms = 0  # type: ignore[misc]


def test_runtime_result_summary_is_deterministic() -> None:
    result = RuntimeResult(True, 42, logs=("ready",), errors=())

    assert result.summary() == "Runtime SUCCESS in 42ms; logs=1; errors=0"
