"""Canonical result contract shared by PromptForge tools."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any

from ..utils.serialization import _freeze_mapping

__all__ = ["ToolResult", "ToolStatus"]


@unique
class ToolStatus(str, Enum):
    """Terminal status reported by a PromptForge tool."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Immutable, JSON-serializable result returned by any tool."""

    tool_name: str
    status: ToolStatus
    message: str
    execution_time_ms: int
    metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        _validate_text(self.tool_name, "tool_name")
        if not isinstance(self.status, ToolStatus):
            raise ValueError("status must be a ToolStatus")
        _validate_text(self.message, "message")
        if (
            not isinstance(self.execution_time_ms, int)
            or isinstance(self.execution_time_ms, bool)
            or self.execution_time_ms < 0
        ):
            raise ValueError(
                "execution_time_ms must be a non-negative integer"
            )
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be a mapping")
        object.__setattr__(
            self,
            "metadata",
            _freeze_mapping(self.metadata, path="metadata", active=set()),
        )

    def is_success(self) -> bool:
        """Return whether the tool completed successfully."""

        return self.status is ToolStatus.SUCCESS

    def is_failure(self) -> bool:
        """Return whether the tool terminated with a failure."""

        return self.status is ToolStatus.FAILED

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible result representation."""

        return {
            "tool_name": self.tool_name,
            "status": self.status.value,
            "message": self.message,
            "execution_time_ms": self.execution_time_ms,
            "metadata": _thaw_json(self.metadata),
        }

    def api_dict(self) -> dict[str, Any]:
        """Return the canonical API representation."""

        return self.to_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ToolResult:
        """Reconstruct a result from its exact serialized schema."""

        values = _exact_schema(data)
        try:
            status = ToolStatus(values["status"])
        except (TypeError, ValueError) as exc:
            allowed = ", ".join(item.value for item in ToolStatus)
            raise ValueError(f"status must be one of: {allowed}") from exc
        return cls(
            tool_name=values["tool_name"],
            status=status,
            message=values["message"],
            execution_time_ms=values["execution_time_ms"],
            metadata=values["metadata"],
        )

    def to_json(self) -> str:
        """Serialize the result as deterministic compact JSON."""

        return json.dumps(
            self.to_dict(),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> ToolResult:
        """Reconstruct a result from a UTF-8 JSON object."""

        if not isinstance(payload, (str, bytes, bytearray)):
            raise TypeError("payload must be JSON text or bytes")
        try:
            value = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("payload must contain valid JSON") from exc
        if not isinstance(value, Mapping):
            raise ValueError("payload must contain a JSON object")
        return cls.from_dict(value)


def _validate_text(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if "\x00" in value:
        raise ValueError(f"{field_name} cannot contain NUL characters")


def _exact_schema(data: object) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError("tool result data must be a mapping")
    expected = {
        "tool_name",
        "status",
        "message",
        "execution_time_ms",
        "metadata",
    }
    supplied = set(data)
    missing = expected - supplied
    unknown = supplied - expected
    if missing:
        raise ValueError(
            "tool result data is missing required fields: "
            + ", ".join(sorted(missing))
        )
    if unknown:
        raise ValueError(
            "tool result data contains unknown fields: "
            + ", ".join(sorted(unknown))
        )
    return dict(data)


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value
