"""Canonical workflow execution models for PromptForge.

These immutable models are the serialization boundary for workflow execution
state and outcomes.  They contain no orchestration or runtime behavior.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, unique
from types import MappingProxyType
from typing import Any, TypeAlias

__all__ = [
    "ExecutionFailure",
    "ExecutionOutcome",
    "ExecutionStatus",
    "ExecutionStepResult",
    "JSONValue",
]


JSONValue: TypeAlias = (
    None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
)


@unique
class ExecutionStatus(str, Enum):
    """Lifecycle state of a PromptForge workflow execution."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class ExecutionFailure:
    """Structured terminal failure associated with an execution."""

    category: str
    message: str
    details: Mapping[str, JSONValue] = field(
        default_factory=dict,
        hash=False,
    )

    def __post_init__(self) -> None:
        _validate_text(self.category, "category", maximum=128)
        _validate_text(self.message, "message", maximum=16_384)
        object.__setattr__(
            self,
            "details",
            _freeze_mapping(self.details, path="details"),
        )

    def to_dict(self) -> dict[str, JSONValue]:
        """Return a detached JSON-compatible failure representation."""

        return {
            "category": self.category,
            "message": self.message,
            "details": _thaw_json(self.details),
        }

    def api_dict(self) -> dict[str, JSONValue]:
        """Return the canonical representation for existing API helpers."""

        return self.to_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutionFailure:
        """Reconstruct a failure from its exact serialized schema."""

        values = _exact_schema(
            data,
            expected={"category", "message", "details"},
            label="execution failure",
        )
        return cls(
            category=values["category"],
            message=values["message"],
            details=values["details"],
        )


@dataclass(frozen=True, slots=True)
class ExecutionStepResult:
    """Immutable result of one attempted workflow step."""

    step_name: str
    success: bool
    execution_time_ms: int
    metadata: Mapping[str, JSONValue] = field(
        default_factory=dict,
        hash=False,
    )

    def __post_init__(self) -> None:
        _validate_text(self.step_name, "step_name", maximum=256)
        if not isinstance(self.success, bool):
            raise ValueError("success must be a boolean")
        _validate_duration(self.execution_time_ms, "execution_time_ms")
        object.__setattr__(
            self,
            "metadata",
            _freeze_mapping(self.metadata, path="metadata"),
        )

    def to_dict(self) -> dict[str, JSONValue]:
        """Return a detached JSON-compatible step representation."""

        return {
            "step_name": self.step_name,
            "success": self.success,
            "execution_time_ms": self.execution_time_ms,
            "metadata": _thaw_json(self.metadata),
        }

    def api_dict(self) -> dict[str, JSONValue]:
        """Return the canonical representation for existing API helpers."""

        return self.to_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutionStepResult:
        """Reconstruct a step result from its exact serialized schema."""

        values = _exact_schema(
            data,
            expected={
                "step_name",
                "success",
                "execution_time_ms",
                "metadata",
            },
            label="execution step result",
        )
        return cls(
            step_name=values["step_name"],
            success=values["success"],
            execution_time_ms=values["execution_time_ms"],
            metadata=values["metadata"],
        )


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """Canonical aggregate describing workflow execution state and results."""

    execution_id: str
    task_id: str
    status: ExecutionStatus
    execution_time_ms: int
    steps: tuple[ExecutionStepResult, ...] = ()
    failure: ExecutionFailure | None = None

    def __post_init__(self) -> None:
        _validate_identifier(self.execution_id, "execution_id")
        _validate_identifier(self.task_id, "task_id")
        if not isinstance(self.status, ExecutionStatus):
            raise ValueError("status must be an ExecutionStatus")
        _validate_duration(self.execution_time_ms, "execution_time_ms")

        steps = _coerce_steps(self.steps)
        object.__setattr__(self, "steps", steps)

        if self.failure is not None and not isinstance(
            self.failure,
            ExecutionFailure,
        ):
            raise ValueError("failure must be an ExecutionFailure or None")
        if self.status is ExecutionStatus.SUCCESS:
            if self.failure is not None:
                raise ValueError("successful execution cannot contain a failure")
            if any(not step.success for step in steps):
                raise ValueError("successful execution cannot contain failed steps")
        elif self.status is ExecutionStatus.FAILED:
            if self.failure is None:
                raise ValueError("failed execution must contain a failure")
        elif self.status in {ExecutionStatus.PENDING, ExecutionStatus.RUNNING}:
            if self.failure is not None:
                raise ValueError(
                    "non-terminal execution cannot contain a failure"
                )

    def is_success(self) -> bool:
        """Return whether the execution completed successfully."""

        return self.status is ExecutionStatus.SUCCESS

    def is_failed(self) -> bool:
        """Return whether the execution terminated with a failure."""

        return self.status is ExecutionStatus.FAILED

    def summary(self) -> str:
        """Return a deterministic one-line execution summary."""

        successful_steps = sum(step.success for step in self.steps)
        summary = (
            f"Execution {self.execution_id} for task {self.task_id}: "
            f"{self.status.value} in {self.execution_time_ms}ms; "
            f"steps={successful_steps}/{len(self.steps)} successful"
        )
        if self.failure is not None:
            summary += (
                f"; failure={self.failure.category}: "
                f"{self.failure.message}"
            )
        return summary

    def to_dict(self) -> dict[str, JSONValue]:
        """Return the canonical JSON-compatible outcome representation."""

        return {
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "status": self.status.value,
            "execution_time_ms": self.execution_time_ms,
            "steps": [step.to_dict() for step in self.steps],
            "failure": self.failure.to_dict() if self.failure else None,
        }

    def api_dict(self) -> dict[str, JSONValue]:
        """Return the canonical representation for existing API helpers."""

        return self.to_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutionOutcome:
        """Reconstruct an outcome from its exact serialized schema."""

        values = _exact_schema(
            data,
            expected={
                "execution_id",
                "task_id",
                "status",
                "execution_time_ms",
                "steps",
                "failure",
            },
            label="execution outcome",
        )
        raw_steps = values["steps"]
        if not isinstance(raw_steps, Sequence) or isinstance(
            raw_steps,
            (str, bytes, bytearray),
        ):
            raise ValueError("steps must be a sequence")
        raw_failure = values["failure"]
        if raw_failure is not None and not isinstance(raw_failure, Mapping):
            raise ValueError("failure must be a mapping or None")
        try:
            status = ExecutionStatus(values["status"])
        except (TypeError, ValueError) as exc:
            allowed = ", ".join(status.value for status in ExecutionStatus)
            raise ValueError(f"status must be one of: {allowed}") from exc

        return cls(
            execution_id=values["execution_id"],
            task_id=values["task_id"],
            status=status,
            execution_time_ms=values["execution_time_ms"],
            steps=tuple(ExecutionStepResult.from_dict(item) for item in raw_steps),
            failure=(
                ExecutionFailure.from_dict(raw_failure)
                if raw_failure is not None
                else None
            ),
        )

    def to_json(self) -> str:
        """Serialize the outcome as deterministic compact JSON."""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> ExecutionOutcome:
        """Reconstruct an outcome from a UTF-8 JSON object."""

        if not isinstance(payload, (str, bytes, bytearray)):
            raise TypeError("payload must be JSON text or bytes")
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("payload must contain valid JSON") from exc
        if not isinstance(value, Mapping):
            raise ValueError("payload must contain a JSON object")
        return cls.from_dict(value)


def _coerce_steps(value: object) -> tuple[ExecutionStepResult, ...]:
    if not isinstance(value, Iterable) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise ValueError("steps must be an iterable")
    try:
        steps = tuple(value)
    except TypeError as exc:
        raise ValueError("steps must be an iterable") from exc
    if any(not isinstance(step, ExecutionStepResult) for step in steps):
        raise ValueError("steps must contain only ExecutionStepResult values")
    return steps


def _validate_identifier(value: object, field_name: str) -> None:
    _validate_text(value, field_name, maximum=256)


def _validate_text(value: object, field_name: str, *, maximum: int) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if len(value) > maximum or "\x00" in value:
        raise ValueError(f"{field_name} is invalid")


def _validate_duration(value: object, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _exact_schema(
    data: object,
    *,
    expected: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} data must be a mapping")
    supplied = set(data)
    missing = expected - supplied
    unknown = supplied - expected
    if missing:
        raise ValueError(
            f"{label} data is missing required fields: "
            + ", ".join(sorted(missing))
        )
    if unknown:
        raise ValueError(
            f"{label} data contains unknown fields: "
            + ", ".join(sorted(unknown))
        )
    return dict(data)


def _freeze_mapping(
    value: object,
    *,
    path: str,
) -> Mapping[str, JSONValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} must be a mapping")
    frozen = _freeze_json(value, path=path, active=set())
    if not isinstance(frozen, Mapping):
        raise ValueError(f"{path} must be a mapping")
    return frozen


def _freeze_json(value: object, *, path: str, active: set[int]) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite numbers")
        return value

    identity = id(value)
    if identity in active:
        raise ValueError(f"{path} cannot contain cyclic references")
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            result: dict[str, JSONValue] = {}
            for key, item in value.items():
                if not isinstance(key, str) or not key:
                    raise ValueError(
                        f"{path} keys must be non-empty strings"
                    )
                result[key] = _freeze_json(
                    item,
                    path=f"{path}.{key}",
                    active=active,
                )
            return MappingProxyType(result)
        if isinstance(value, Sequence) and not isinstance(
            value,
            (str, bytes, bytearray),
        ):
            return tuple(
                _freeze_json(
                    item,
                    path=f"{path}[{index}]",
                    active=active,
                )
                for index, item in enumerate(value)
            )
    finally:
        active.remove(identity)
    raise ValueError(f"{path} contains a non-JSON-compatible value")


def _thaw_json(value: object) -> JSONValue:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value  # type: ignore[return-value]
