"""Shared immutable execution context for PromptForge AI.

The context carries validated state between planning, coordination, tools, and
debugging. It is a data contract only and performs no execution, filesystem
access, tool invocation, or runtime interaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..utils.serialization import _freeze_json_value, _freeze_mapping

__all__ = ["ExecutionContext"]


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    """Immutable state shared across one PromptForge task execution.

    Artifact, board, and metadata values are JSON-compatible snapshots rather
    than imports from planner or tool modules. Each mapping is defensively
    copied and recursively frozen during construction.

    Paths are descriptive values only. Construction does not resolve paths or
    require them to exist, which keeps this contract free of filesystem side
    effects and suitable for replay or remote execution.
    """

    task_id: str
    project_path: str | None = None
    target_board: str = "UNKNOWN"
    framework: str = "UNKNOWN"
    firmware_path: str | None = None
    build_artifact: Mapping[str, Any] | None = field(
        default=None,
        hash=False,
    )
    board_info: Mapping[str, Any] | None = field(
        default=None,
        hash=False,
    )
    simulation_enabled: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        _validate_required_text(self.task_id, field_name="task_id")
        _validate_optional_text(self.project_path, field_name="project_path")
        _validate_required_text(self.target_board, field_name="target_board")
        _validate_required_text(self.framework, field_name="framework")
        _validate_optional_text(
            self.firmware_path,
            field_name="firmware_path",
        )
        if not isinstance(self.simulation_enabled, bool):
            raise ValueError("simulation_enabled must be a boolean")

        object.__setattr__(
            self,
            "build_artifact",
            _freeze_optional_mapping(
                self.build_artifact,
                field_name="build_artifact",
            ),
        )
        object.__setattr__(
            self,
            "board_info",
            _freeze_optional_mapping(
                self.board_info,
                field_name="board_info",
            ),
        )
        object.__setattr__(
            self,
            "metadata",
            _freeze_mapping_field(self.metadata, field_name="metadata"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a new JSON-compatible dictionary for this context."""

        return {
            "task_id": self.task_id,
            "project_path": self.project_path,
            "target_board": self.target_board,
            "framework": self.framework,
            "firmware_path": self.firmware_path,
            "build_artifact": _thaw_json_value(self.build_artifact),
            "board_info": _thaw_json_value(self.board_info),
            "simulation_enabled": self.simulation_enabled,
            "metadata": _thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExecutionContext:
        """Build a context from its canonical serialized representation.

        All canonical fields are required and unknown fields are rejected so
        malformed handoffs fail at the contract boundary instead of being
        silently accepted.
        """

        if not isinstance(data, Mapping):
            raise ValueError("execution context data must be a mapping")

        expected_fields = {
            "task_id",
            "project_path",
            "target_board",
            "framework",
            "firmware_path",
            "build_artifact",
            "board_info",
            "simulation_enabled",
            "metadata",
        }
        supplied_fields = set(data)
        missing_fields = expected_fields - supplied_fields
        unknown_fields = supplied_fields - expected_fields

        if missing_fields:
            names = ", ".join(sorted(missing_fields))
            raise ValueError(
                f"execution context data is missing required fields: {names}"
            )
        if unknown_fields:
            names = ", ".join(sorted(unknown_fields))
            raise ValueError(
                f"execution context data contains unknown fields: {names}"
            )

        return cls(
            task_id=data["task_id"],
            project_path=data["project_path"],
            target_board=data["target_board"],
            framework=data["framework"],
            firmware_path=data["firmware_path"],
            build_artifact=data["build_artifact"],
            board_info=data["board_info"],
            simulation_enabled=data["simulation_enabled"],
            metadata=data["metadata"],
        )


def _validate_required_text(value: object, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if "\x00" in value:
        raise ValueError(f"{field_name} cannot contain NUL characters")


def _validate_optional_text(value: object, *, field_name: str) -> None:
    if value is None:
        return
    _validate_required_text(value, field_name=field_name)


def _freeze_optional_mapping(
    value: Mapping[str, Any] | None,
    *,
    field_name: str,
) -> Mapping[str, Any] | None:
    if value is None:
        return None
    return _freeze_mapping_field(value, field_name=field_name)


def _freeze_mapping_field(
    value: Mapping[str, Any],
    *,
    field_name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    return _freeze_mapping(value, path=field_name, active=set())


def _thaw_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value
