"""Canonical execution planning contracts for PromptForge AI.

This module contains data definitions only. It intentionally performs no
planning, coordination, tool selection, or execution.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, unique
from types import MappingProxyType
from typing import Any

__all__ = ["ExecutionPlan", "ExecutionStep", "TaskType"]


@unique
class TaskType(str, Enum):
    """High-level workflow represented by an execution plan."""

    FIRMWARE_GENERATION = "FIRMWARE_GENERATION"
    FIRMWARE_MODIFICATION = "FIRMWARE_MODIFICATION"
    DEBUGGING = "DEBUGGING"
    SIMULATION = "SIMULATION"
    FLASH_ONLY = "FLASH_ONLY"
    MONITOR_ONLY = "MONITOR_ONLY"


@unique
class ExecutionStep(str, Enum):
    """Ordered operation requested by an execution plan."""

    GENERATE_CODE = "GENERATE_CODE"
    BUILD_FIRMWARE = "BUILD_FIRMWARE"
    DETECT_BOARD = "DETECT_BOARD"
    FLASH_FIRMWARE = "FLASH_FIRMWARE"
    START_MONITOR = "START_MONITOR"
    START_SIMULATION = "START_SIMULATION"
    DEBUG_FAILURE = "DEBUG_FAILURE"


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """Immutable, serializable description of planned task execution.

    Board and framework identifiers are strings so this shared contract does
    not depend on planner-owned discovery enums. String-valued enums are also
    accepted and retained, allowing producers to preserve richer local types.

    Collection inputs are defensively copied. Metadata must contain only
    JSON-compatible values and is recursively frozen after construction.
    """

    task_id: str
    task_type: TaskType
    target_board: str
    framework: str
    requirements: tuple[str, ...]
    execution_steps: tuple[ExecutionStep, ...]
    confidence: float
    metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        _validate_text(self.task_id, field_name="task_id")
        if not isinstance(self.task_type, TaskType):
            raise ValueError("task_type must be a TaskType")
        _validate_text(self.target_board, field_name="target_board")
        _validate_text(self.framework, field_name="framework")

        requirements = _coerce_sequence(
            self.requirements,
            field_name="requirements",
        )
        execution_steps = _coerce_sequence(
            self.execution_steps,
            field_name="execution_steps",
        )
        for requirement in requirements:
            _validate_text(requirement, field_name="requirements item")
        if any(not isinstance(step, ExecutionStep) for step in execution_steps):
            raise ValueError(
                "execution_steps must contain ExecutionStep values"
            )
        if len(set(requirements)) != len(requirements):
            raise ValueError("requirements cannot contain duplicates")
        if len(set(execution_steps)) != len(execution_steps):
            raise ValueError("execution_steps cannot contain duplicates")

        if (
            not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
            or not math.isfinite(self.confidence)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("confidence must be between 0.0 and 1.0")

        object.__setattr__(self, "requirements", requirements)
        object.__setattr__(self, "execution_steps", execution_steps)
        object.__setattr__(self, "confidence", float(self.confidence))
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a new JSON-compatible dictionary for this plan."""

        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "target_board": _string_value(self.target_board),
            "framework": _string_value(self.framework),
            "requirements": list(self.requirements),
            "execution_steps": [step.value for step in self.execution_steps],
            "confidence": self.confidence,
            "metadata": _thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutionPlan:
        """Build a plan from its exact canonical serialized schema."""

        if not isinstance(data, Mapping):
            raise ValueError("execution plan data must be a mapping")

        expected_fields = {
            "task_id",
            "task_type",
            "target_board",
            "framework",
            "requirements",
            "execution_steps",
            "confidence",
            "metadata",
        }
        supplied_fields = set(data)
        missing_fields = expected_fields - supplied_fields
        unknown_fields = supplied_fields - expected_fields
        if missing_fields:
            names = ", ".join(sorted(missing_fields))
            raise ValueError(
                f"execution plan data is missing required fields: {names}"
            )
        if unknown_fields:
            names = ", ".join(sorted(unknown_fields))
            raise ValueError(
                f"execution plan data contains unknown fields: {names}"
            )

        raw_steps = _coerce_sequence(
            data["execution_steps"],
            field_name="execution_steps",
        )
        return cls(
            task_id=data["task_id"],
            task_type=_parse_enum(TaskType, data["task_type"], "task_type"),
            target_board=data["target_board"],
            framework=data["framework"],
            requirements=data["requirements"],
            execution_steps=tuple(
                _parse_enum(ExecutionStep, step, "execution_steps item")
                for step in raw_steps
            ),
            confidence=data["confidence"],
            metadata=data["metadata"],
        )


def _validate_text(value: object, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if "\x00" in value:
        raise ValueError(f"{field_name} cannot contain NUL characters")


def _coerce_sequence(value: object, *, field_name: str) -> tuple[Any, ...]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise ValueError(f"{field_name} must be a sequence")
    return tuple(value)


def _parse_enum(
    enum_type: type[TaskType] | type[ExecutionStep],
    value: object,
    field_name: str,
) -> TaskType | ExecutionStep:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(member.value for member in enum_type)
        raise ValueError(f"{field_name} must be one of: {allowed}") from exc


def _string_value(value: str) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return value


def _freeze_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping")
    return _freeze_mapping(metadata, path="metadata", active=set())


def _freeze_mapping(
    value: Mapping[object, Any],
    *,
    path: str,
    active: set[int],
) -> Mapping[str, Any]:
    identity = id(value)
    if identity in active:
        raise ValueError(f"{path} cannot contain cyclic references")

    active.add(identity)
    try:
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError(f"{path} keys must be non-empty strings")
            frozen[key] = _freeze_json_value(
                item,
                path=f"{path}.{key}",
                active=active,
            )
        return MappingProxyType(frozen)
    finally:
        active.remove(identity)


def _freeze_json_value(
    value: Any,
    *,
    path: str,
    active: set[int],
) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be a finite number")
        return value
    if isinstance(value, Mapping):
        return _freeze_mapping(value, path=path, active=active)
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        identity = id(value)
        if identity in active:
            raise ValueError(f"{path} cannot contain cyclic references")
        active.add(identity)
        try:
            return tuple(
                _freeze_json_value(
                    item,
                    path=f"{path}[{index}]",
                    active=active,
                )
                for index, item in enumerate(value)
            )
        finally:
            active.remove(identity)
    raise ValueError(f"{path} contains a non-JSON-compatible value")


def _thaw_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value
