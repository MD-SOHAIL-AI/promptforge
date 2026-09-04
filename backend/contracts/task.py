"""Canonical user-intent task contract for PromptForge AI.

``Task`` describes a request before it is interpreted by a planner.  This
module intentionally contains no planning, coordination, or execution logic.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, unique
from typing import Any

from ..utils.serialization import _freeze_json_value, _freeze_mapping

__all__ = ["Task", "TaskPriority", "TaskSource"]


@unique
class TaskPriority(str, Enum):
    """Scheduling importance assigned to a task."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@unique
class TaskSource(str, Enum):
    """Origin from which a task entered PromptForge."""

    USER = "USER"
    API = "API"
    SYSTEM = "SYSTEM"


@dataclass(frozen=True, slots=True)
class Task:
    """Immutable representation of user intent before planning.

    ``metadata`` accepts JSON-compatible values. It is defensively copied and
    recursively frozen so mutation of caller-owned containers cannot alter a
    task after construction.
    """

    task_id: str
    prompt: str
    source: TaskSource = TaskSource.USER
    priority: TaskPriority = TaskPriority.NORMAL
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        _validate_text(self.task_id, field_name="task_id")
        _validate_text(self.prompt, field_name="prompt")

        if not isinstance(self.source, TaskSource):
            raise ValueError("source must be a TaskSource")
        if not isinstance(self.priority, TaskPriority):
            raise ValueError("priority must be a TaskPriority")
        if not isinstance(self.created_at, datetime):
            raise ValueError("created_at must be a datetime")
        if (
            self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise ValueError("created_at must be timezone-aware")

        object.__setattr__(
            self,
            "created_at",
            self.created_at.astimezone(timezone.utc),
        )
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a new JSON-compatible dictionary for this task."""

        return {
            "task_id": self.task_id,
            "prompt": self.prompt,
            "source": self.source.value,
            "priority": self.priority.value,
            "created_at": _format_datetime(self.created_at),
            "metadata": _thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Task:
        """Build a task from its canonical serialized representation.

        The input schema is exact: all canonical fields are required and
        unknown fields are rejected. This prevents misspelled or unsupported
        fields from being silently discarded at the contract boundary.
        """

        if not isinstance(data, Mapping):
            raise ValueError("task data must be a mapping")

        expected_fields = {
            "task_id",
            "prompt",
            "source",
            "priority",
            "created_at",
            "metadata",
        }
        supplied_fields = set(data)
        missing_fields = expected_fields - supplied_fields
        unknown_fields = supplied_fields - expected_fields

        if missing_fields:
            names = ", ".join(sorted(missing_fields))
            raise ValueError(f"task data is missing required fields: {names}")
        if unknown_fields:
            names = ", ".join(sorted(unknown_fields))
            raise ValueError(f"task data contains unknown fields: {names}")

        return cls(
            task_id=data["task_id"],
            prompt=data["prompt"],
            source=_parse_enum(TaskSource, data["source"], "source"),
            priority=_parse_enum(
                TaskPriority, data["priority"], "priority"
            ),
            created_at=_parse_datetime(data["created_at"]),
            metadata=data["metadata"],
        )


def _validate_text(value: object, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if "\x00" in value:
        raise ValueError(f"{field_name} cannot contain NUL characters")


def _parse_enum(
    enum_type: type[TaskSource] | type[TaskPriority],
    value: object,
    field_name: str,
) -> TaskSource | TaskPriority:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(member.value for member in enum_type)
        raise ValueError(
            f"{field_name} must be one of: {allowed}"
        ) from exc


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("created_at must be an ISO 8601 string")

    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("created_at must be a valid ISO 8601 string") from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("created_at must include a timezone offset")
    return parsed


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _freeze_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping")
    return _freeze_mapping(metadata, path="metadata", active=set())


def _thaw_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value
