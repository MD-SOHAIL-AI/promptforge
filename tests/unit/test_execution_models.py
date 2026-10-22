from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from backend.models.execution import (
    ExecutionFailure,
    ExecutionOutcome,
    ExecutionStatus,
    ExecutionStepResult,
)


def test_successful_outcome_round_trips_through_json() -> None:
    outcome = ExecutionOutcome(
        execution_id="execution-1",
        task_id="task-1",
        status=ExecutionStatus.SUCCESS,
        execution_time_ms=25,
        steps=(
            ExecutionStepResult(
                step_name="GENERATE_CODE",
                success=True,
                execution_time_ms=10,
                metadata={"files": ["src/main.cpp"]},
            ),
            ExecutionStepResult(
                step_name="BUILD_FIRMWARE",
                success=True,
                execution_time_ms=15,
            ),
        ),
    )

    assert outcome.is_success() is True
    assert outcome.is_failed() is False
    assert ExecutionOutcome.from_json(outcome.to_json()) == outcome
    assert outcome.to_dict()["status"] == "SUCCESS"


def test_failed_outcome_has_structured_summary() -> None:
    failure = ExecutionFailure(
        category="BUILD_ERROR",
        message="compiler failed",
        details={"exit_code": 1},
    )
    outcome = ExecutionOutcome(
        execution_id="execution-1",
        task_id="task-1",
        status=ExecutionStatus.FAILED,
        execution_time_ms=25,
        steps=(
            ExecutionStepResult("BUILD_FIRMWARE", False, 25),
        ),
        failure=failure,
    )

    assert outcome.is_success() is False
    assert outcome.is_failed() is True
    assert outcome.summary() == (
        "Execution execution-1 for task task-1: FAILED in 25ms; "
        "steps=0/1 successful; failure=BUILD_ERROR: compiler failed"
    )


def test_models_are_frozen_and_defensively_copy_json_data() -> None:
    metadata = {"nested": {"values": [1, 2]}}
    step = ExecutionStepResult("step", True, 1, metadata)
    metadata["nested"] = {"values": [99]}

    assert step.to_dict()["metadata"] == {"nested": {"values": [1, 2]}}
    with pytest.raises(FrozenInstanceError):
        step.success = False  # type: ignore[misc]
    with pytest.raises(TypeError):
        step.metadata["new"] = True  # type: ignore[index]


def test_outcome_coerces_step_sequences_to_tuple() -> None:
    step = ExecutionStepResult("step", True, 1)
    outcome = ExecutionOutcome(
        "execution-1",
        "task-1",
        ExecutionStatus.RUNNING,
        1,
        [step],  # type: ignore[arg-type]
    )

    assert outcome.steps == (step,)


def test_outcome_accepts_step_generators_and_api_dict_alias() -> None:
    step = ExecutionStepResult("step", True, 1)
    outcome = ExecutionOutcome(
        "execution-1",
        "task-1",
        ExecutionStatus.SUCCESS,
        1,
        (item for item in (step,)),  # type: ignore[arg-type]
    )

    assert outcome.steps == (step,)
    assert outcome.api_dict() == outcome.to_dict()
    assert step.api_dict() == step.to_dict()


@pytest.mark.parametrize(
    "status,failure",
    [
        (ExecutionStatus.FAILED, None),
        (
            ExecutionStatus.SUCCESS,
            ExecutionFailure("ERROR", "failed"),
        ),
        (
            ExecutionStatus.RUNNING,
            ExecutionFailure("ERROR", "failed"),
        ),
    ],
)
def test_inconsistent_outcome_states_are_rejected(
    status: ExecutionStatus,
    failure: ExecutionFailure | None,
) -> None:
    with pytest.raises(ValueError):
        ExecutionOutcome(
            "execution-1",
            "task-1",
            status,
            1,
            failure=failure,
        )


def test_success_rejects_failed_steps() -> None:
    with pytest.raises(ValueError, match="failed steps"):
        ExecutionOutcome(
            "execution-1",
            "task-1",
            ExecutionStatus.SUCCESS,
            1,
            steps=(ExecutionStepResult("step", False, 1),),
        )


def test_exact_serialized_schema_is_enforced() -> None:
    data = {
        "execution_id": "execution-1",
        "task_id": "task-1",
        "status": "CANCELLED",
        "execution_time_ms": 1,
        "steps": [],
        "failure": None,
    }

    assert ExecutionOutcome.from_dict(data).status is ExecutionStatus.CANCELLED
    data["unknown"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        ExecutionOutcome.from_dict(data)
