from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, fields

import pytest

from backend.agent.planner import Framework, TargetBoard
from backend.contracts.execution_plan import (
    ExecutionPlan,
    ExecutionStep,
    TaskType,
)


def plan_data() -> dict[str, object]:
    return {
        "task_id": "task-123",
        "task_type": TaskType.FIRMWARE_GENERATION,
        "target_board": "ESP32",
        "framework": "Arduino",
        "requirements": ["LED output", "serial output"],
        "execution_steps": [
            ExecutionStep.GENERATE_CODE,
            ExecutionStep.BUILD_FIRMWARE,
        ],
        "confidence": 0.9,
        "metadata": {"source": "api", "nested": {"pins": [2, 4]}},
    }


def make_plan(**overrides: object) -> ExecutionPlan:
    values = plan_data()
    values.update(overrides)
    return ExecutionPlan(**values)  # type: ignore[arg-type]


def test_enums_expose_the_canonical_values() -> None:
    assert [item.value for item in TaskType] == [
        "FIRMWARE_GENERATION",
        "FIRMWARE_MODIFICATION",
        "DEBUGGING",
        "SIMULATION",
        "FLASH_ONLY",
        "MONITOR_ONLY",
    ]
    assert [item.value for item in ExecutionStep] == [
        "GENERATE_CODE",
        "BUILD_FIRMWARE",
        "DETECT_BOARD",
        "FLASH_FIRMWARE",
        "START_MONITOR",
        "START_SIMULATION",
        "DEBUG_FAILURE",
    ]


def test_execution_plan_has_only_the_canonical_fields() -> None:
    assert [item.name for item in fields(ExecutionPlan)] == [
        "task_id",
        "task_type",
        "target_board",
        "framework",
        "requirements",
        "execution_steps",
        "confidence",
        "metadata",
    ]


def test_execution_plan_is_frozen_slotted_and_defensively_copied() -> None:
    requirements = ["LED output"]
    steps = [ExecutionStep.GENERATE_CODE]
    plan = make_plan(requirements=requirements, execution_steps=steps)
    requirements.append("serial output")
    steps.append(ExecutionStep.BUILD_FIRMWARE)

    assert plan.requirements == ("LED output",)
    assert plan.execution_steps == (ExecutionStep.GENERATE_CODE,)
    assert plan.confidence == 0.9
    assert not hasattr(plan, "__dict__")
    with pytest.raises(FrozenInstanceError):
        plan.confidence = 0.1  # type: ignore[misc]


def test_metadata_is_json_compatible_deeply_frozen_and_independent() -> None:
    metadata = {"nested": {"items": ["one"]}}
    plan = make_plan(metadata=metadata)
    metadata["nested"]["items"].append("two")  # type: ignore[index,union-attr]

    assert plan.metadata["nested"]["items"] == ("one",)  # type: ignore[index]
    with pytest.raises(TypeError):
        plan.metadata["new"] = True  # type: ignore[index]
    with pytest.raises(TypeError):
        plan.metadata["nested"]["new"] = True  # type: ignore[index]


def test_to_dict_returns_fresh_json_compatible_data() -> None:
    plan = make_plan(
        target_board=TargetBoard.ESP32,
        framework=Framework.ARDUINO,
    )

    first = plan.to_dict()
    second = plan.to_dict()
    first["requirements"].append("mutated")  # type: ignore[union-attr]
    first["metadata"]["nested"]["pins"].append(99)  # type: ignore[index]

    assert second["requirements"] == ["LED output", "serial output"]
    assert second["metadata"]["nested"]["pins"] == [2, 4]  # type: ignore[index]
    assert second["target_board"] == "ESP32"
    assert second["framework"] == "Arduino"
    json.dumps(second)


def test_from_dict_round_trips_the_canonical_representation() -> None:
    original = make_plan()

    restored = ExecutionPlan.from_dict(original.to_dict())

    assert restored == original
    assert restored.task_type is TaskType.FIRMWARE_GENERATION
    assert restored.execution_steps == (
        ExecutionStep.GENERATE_CODE,
        ExecutionStep.BUILD_FIRMWARE,
    )


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("task_id", "", "task_id"),
        ("task_id", "bad\x00id", "NUL"),
        ("task_type", "DEBUGGING", "TaskType"),
        ("target_board", " ", "target_board"),
        ("framework", None, "framework"),
    ],
)
def test_rejects_invalid_scalar_fields(
    field_name: str,
    value: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        make_plan(**{field_name: value})


@pytest.mark.parametrize("field_name", ["requirements", "execution_steps"])
@pytest.mark.parametrize("value", ["not-a-sequence", {"unordered"}, 123])
def test_rejects_invalid_plan_sequences(
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match="sequence"):
        make_plan(**{field_name: value})


def test_rejects_invalid_requirement_and_step_members() -> None:
    with pytest.raises(ValueError, match="requirements item"):
        make_plan(requirements=[""])
    with pytest.raises(ValueError, match="ExecutionStep"):
        make_plan(execution_steps=["BUILD_FIRMWARE"])
    with pytest.raises(ValueError, match="requirements.*duplicates"):
        make_plan(requirements=["same", "same"])
    with pytest.raises(ValueError, match="execution_steps.*duplicates"):
        make_plan(
            execution_steps=[
                ExecutionStep.BUILD_FIRMWARE,
                ExecutionStep.BUILD_FIRMWARE,
            ]
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01, True, float("nan"), float("inf")])
def test_rejects_invalid_confidence(confidence: object) -> None:
    with pytest.raises(ValueError, match="confidence"):
        make_plan(confidence=confidence)


@pytest.mark.parametrize(
    "metadata",
    [
        [],
        {"": "value"},
        {"unsupported": object()},
        {"not_finite": float("nan")},
        {"set": {"not", "json"}},
    ],
)
def test_rejects_invalid_metadata(metadata: object) -> None:
    with pytest.raises(ValueError, match="metadata"):
        make_plan(metadata=metadata)


def test_rejects_cyclic_metadata() -> None:
    metadata: dict[str, object] = {}
    metadata["self"] = metadata

    with pytest.raises(ValueError, match="cyclic"):
        make_plan(metadata=metadata)


def test_from_dict_requires_an_exact_schema() -> None:
    serialized = make_plan().to_dict()
    missing = dict(serialized)
    missing.pop("confidence")
    unknown = {**serialized, "estimated_tools": []}

    with pytest.raises(ValueError, match="missing required fields: confidence"):
        ExecutionPlan.from_dict(missing)
    with pytest.raises(ValueError, match="unknown fields: estimated_tools"):
        ExecutionPlan.from_dict(unknown)
    with pytest.raises(ValueError, match="must be a mapping"):
        ExecutionPlan.from_dict([])  # type: ignore[arg-type]


def test_from_dict_parses_enums_and_rejects_unknown_values() -> None:
    serialized = make_plan().to_dict()
    serialized["task_type"] = "DEBUGGING"
    serialized["execution_steps"] = ["DEBUG_FAILURE"]

    restored = ExecutionPlan.from_dict(serialized)

    assert restored.task_type is TaskType.DEBUGGING
    assert restored.execution_steps == (ExecutionStep.DEBUG_FAILURE,)

    serialized["execution_steps"] = ["UNKNOWN"]
    with pytest.raises(ValueError, match="execution_steps item"):
        ExecutionPlan.from_dict(serialized)
