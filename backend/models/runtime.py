"""Canonical runtime execution models for PromptForge.

These models define the serialization boundary for live execution state,
execution identity, and terminal runtime results. They contain no orchestration
or I/O behavior.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..utils.serialization import _freeze_mapping

__all__ = ["ExecutionContext", "RuntimeResult", "RuntimeState"]


@dataclass(frozen=True, slots=True)
class RuntimeState:
    """Immutable snapshot of an execution's current lifecycle state."""

    current_step: str | None
    started_at: datetime
    updated_at: datetime
    status: str

    def __post_init__(self) -> None:
        _validate_optional_text(self.current_step, "current_step")
        started_at = _normalize_datetime(self.started_at, "started_at")
        updated_at = _normalize_datetime(self.updated_at, "updated_at")
        _validate_text(self.status, "status")
        if updated_at < started_at:
            raise ValueError("updated_at cannot be earlier than started_at")
        object.__setattr__(self, "started_at", started_at)
        object.__setattr__(self, "updated_at", updated_at)

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible state representation."""

        return {
            "current_step": self.current_step,
            "started_at": _format_datetime(self.started_at),
            "updated_at": _format_datetime(self.updated_at),
            "status": self.status,
        }

    def api_dict(self) -> dict[str, Any]:
        """Return the canonical API representation."""

        return self.to_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RuntimeState:
        """Reconstruct a state from its exact serialized schema."""

        values = _exact_schema(
            data,
            expected={"current_step", "started_at", "updated_at", "status"},
            label="runtime state",
        )
        return cls(
            current_step=values["current_step"],
            started_at=_parse_datetime(values["started_at"], "started_at"),
            updated_at=_parse_datetime(values["updated_at"], "updated_at"),
            status=values["status"],
        )

    def to_json(self) -> str:
        """Serialize the state as deterministic compact JSON."""

        return _json_dumps(self.to_dict())

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> RuntimeState:
        """Reconstruct a state from a UTF-8 JSON object."""

        return cls.from_dict(_load_json_object(payload))


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Immutable identity and target information for one execution."""

    task_id: str
    execution_id: str
    project_name: str
    target_board: str
    framework: str
    metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        for field_name in (
            "task_id",
            "execution_id",
            "project_name",
            "target_board",
            "framework",
        ):
            _validate_text(getattr(self, field_name), field_name)
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be a mapping")
        object.__setattr__(
            self,
            "metadata",
            _freeze_mapping(self.metadata, path="metadata", active=set()),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible context representation."""

        return {
            "task_id": self.task_id,
            "execution_id": self.execution_id,
            "project_name": self.project_name,
            "target_board": self.target_board,
            "framework": self.framework,
            "metadata": _thaw_json(self.metadata),
        }

    def api_dict(self) -> dict[str, Any]:
        """Return the canonical API representation."""

        return self.to_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutionContext:
        """Reconstruct a context from its exact serialized schema."""

        values = _exact_schema(
            data,
            expected={
                "task_id",
                "execution_id",
                "project_name",
                "target_board",
                "framework",
                "metadata",
            },
            label="execution context",
        )
        return cls(
            task_id=values["task_id"],
            execution_id=values["execution_id"],
            project_name=values["project_name"],
            target_board=values["target_board"],
            framework=values["framework"],
            metadata=values["metadata"],
        )

    def to_json(self) -> str:
        """Serialize the context as deterministic compact JSON."""

        return _json_dumps(self.to_dict())

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> ExecutionContext:
        """Reconstruct a context from a UTF-8 JSON object."""

        return cls.from_dict(_load_json_object(payload))


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    """Immutable terminal result of a runtime execution."""

    success: bool
    duration_ms: int
    logs: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.success, bool):
            raise ValueError("success must be a boolean")
        if (
            not isinstance(self.duration_ms, int)
            or isinstance(self.duration_ms, bool)
            or self.duration_ms < 0
        ):
            raise ValueError("duration_ms must be a non-negative integer")
        object.__setattr__(self, "logs", _coerce_messages(self.logs, "logs"))
        object.__setattr__(
            self,
            "errors",
            _coerce_messages(self.errors, "errors"),
        )

    def summary(self) -> str:
        """Return a deterministic one-line runtime result summary."""

        status = "SUCCESS" if self.success else "FAILED"
        return (
            f"Runtime {status} in {self.duration_ms}ms; "
            f"logs={len(self.logs)}; errors={len(self.errors)}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible result representation."""

        return {
            "success": self.success,
            "duration_ms": self.duration_ms,
            "logs": list(self.logs),
            "errors": list(self.errors),
        }

    def api_dict(self) -> dict[str, Any]:
        """Return the canonical API representation."""

        return self.to_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RuntimeResult:
        """Reconstruct a result from its exact serialized schema."""

        values = _exact_schema(
            data,
            expected={"success", "duration_ms", "logs", "errors"},
            label="runtime result",
        )
        return cls(
            success=values["success"],
            duration_ms=values["duration_ms"],
            logs=values["logs"],
            errors=values["errors"],
        )

    def to_json(self) -> str:
        """Serialize the result as deterministic compact JSON."""

        return _json_dumps(self.to_dict())

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> RuntimeResult:
        """Reconstruct a result from a UTF-8 JSON object."""

        return cls.from_dict(_load_json_object(payload))


def _coerce_messages(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, Iterable) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise ValueError(f"{field_name} must be an iterable of strings")
    messages = tuple(value)
    for message in messages:
        _validate_text(message, field_name)
    return messages


def _validate_text(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if "\x00" in value:
        raise ValueError(f"{field_name} cannot contain NUL characters")


def _validate_optional_text(value: object, field_name: str) -> None:
    if value is not None:
        _validate_text(value, field_name)


def _normalize_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _parse_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO 8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be valid ISO 8601") from exc
    return _normalize_datetime(parsed, field_name)


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


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _json_dumps(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _load_json_object(payload: object) -> Mapping[str, Any]:
    if not isinstance(payload, (str, bytes, bytearray)):
        raise TypeError("payload must be JSON text or bytes")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("payload must contain valid JSON") from exc
    if not isinstance(value, Mapping):
        raise ValueError("payload must contain a JSON object")
    return value
