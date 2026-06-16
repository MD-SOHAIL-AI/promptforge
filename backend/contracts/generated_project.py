"""Canonical generated firmware project contracts for PromptForge AI.

The contracts in this module represent in-memory generation output only. They
perform no filesystem writes, builds, tool execution, or project generation.
"""

from __future__ import annotations

import math
import posixpath
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Any

__all__ = ["GeneratedFile", "GeneratedProject"]


@dataclass(frozen=True, slots=True)
class GeneratedFile:
    """One immutable UTF-8 text file in a generated firmware project."""

    path: str
    content: str
    file_type: str = "text"

    def __post_init__(self) -> None:
        _validate_file_path(self.path)
        _validate_text(self.content, field_name="content", allow_empty=True)
        _validate_text(self.file_type, field_name="file_type")

    def to_dict(self) -> dict[str, str]:
        """Return the canonical JSON-compatible file representation."""

        return {
            "path": self.path,
            "content": self.content,
            "file_type": self.file_type,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GeneratedFile:
        """Build a generated file from its exact serialized schema."""

        values = _exact_schema(
            data,
            expected={"path", "content", "file_type"},
            label="generated file",
        )
        return cls(
            path=values["path"],
            content=values["content"],
            file_type=values["file_type"],
        )


@dataclass(frozen=True, slots=True)
class GeneratedProject:
    """Immutable generated firmware project shared across PromptForge.

    Board and framework identifiers are strings to keep this contract
    independent from planner-specific enums. String-valued enums are accepted
    and preserved by construction, then serialized using their string values.
    """

    project_id: str
    project_name: str
    target_board: str
    framework: str
    files: tuple[GeneratedFile, ...]
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        _validate_text(self.project_id, field_name="project_id")
        _validate_text(self.project_name, field_name="project_name")
        _validate_text(self.target_board, field_name="target_board")
        _validate_text(self.framework, field_name="framework")

        files = _coerce_files(self.files)
        if not files:
            raise ValueError("files must contain at least one GeneratedFile")
        normalized_paths: set[str] = set()
        for generated_file in files:
            normalized_path = generated_file.path.casefold()
            if normalized_path in normalized_paths:
                raise ValueError("files cannot contain duplicate paths")
            normalized_paths.add(normalized_path)

        if not isinstance(self.created_at, datetime):
            raise ValueError("created_at must be a datetime")
        if (
            self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
        ):
            raise ValueError("created_at must be timezone-aware")

        object.__setattr__(self, "files", files)
        object.__setattr__(
            self,
            "created_at",
            self.created_at.astimezone(timezone.utc),
        )
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a new JSON-compatible dictionary for this project."""

        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "target_board": _string_value(self.target_board),
            "framework": _string_value(self.framework),
            "files": [generated_file.to_dict() for generated_file in self.files],
            "created_at": _format_datetime(self.created_at),
            "metadata": _thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GeneratedProject:
        """Build a project from its exact canonical serialized schema."""

        values = _exact_schema(
            data,
            expected={
                "project_id",
                "project_name",
                "target_board",
                "framework",
                "files",
                "created_at",
                "metadata",
            },
            label="generated project",
        )
        raw_files = values["files"]
        if not isinstance(raw_files, Sequence) or isinstance(
            raw_files,
            (str, bytes, bytearray),
        ):
            raise ValueError("files must be a sequence")

        return cls(
            project_id=values["project_id"],
            project_name=values["project_name"],
            target_board=values["target_board"],
            framework=values["framework"],
            files=tuple(GeneratedFile.from_dict(item) for item in raw_files),
            created_at=_parse_datetime(values["created_at"]),
            metadata=values["metadata"],
        )

    def get_file(self, path: str) -> GeneratedFile | None:
        """Return the file at ``path``, or ``None`` when it is absent."""

        _validate_file_path(path)
        return next(
            (
                generated_file
                for generated_file in self.files
                if generated_file.path == path
            ),
            None,
        )

    def list_files(self) -> tuple[str, ...]:
        """Return file paths in stable project order."""

        return tuple(generated_file.path for generated_file in self.files)


def _validate_text(
    value: object,
    *,
    field_name: str,
    allow_empty: bool = False,
) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not allow_empty and not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if "\x00" in value:
        raise ValueError(f"{field_name} cannot contain NUL characters")


def _validate_file_path(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("path must be a string")
    if not value or value != value.strip():
        raise ValueError("path must be non-empty and cannot have outer whitespace")
    if "\x00" in value or "\\" in value:
        raise ValueError("path must be a NUL-free POSIX path")
    if len(value) > 240:
        raise ValueError("path exceeds the 240-character limit")

    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("/"):
        raise ValueError("path must be relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path cannot contain empty, current, or parent segments")
    for part in path.parts:
        if part.endswith((" ", ".")):
            raise ValueError("path segments cannot end with spaces or dots")
        if any(character in '<>:"|?*' for character in part):
            raise ValueError("path contains a cross-platform unsafe character")
        stem = part.split(".", 1)[0].casefold()
        if stem in _WINDOWS_RESERVED_NAMES:
            raise ValueError("path contains a reserved filename")
    if posixpath.normpath(value) != value:
        raise ValueError("path must be normalized")
    if any(ord(character) < 32 for character in value):
        raise ValueError("path cannot contain control characters")


def _coerce_files(value: object) -> tuple[GeneratedFile, ...]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise ValueError("files must be a sequence")
    files = tuple(value)
    if any(not isinstance(item, GeneratedFile) for item in files):
        raise ValueError("files must contain only GeneratedFile values")
    return files


def _exact_schema(
    data: Mapping[str, Any],
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


_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
