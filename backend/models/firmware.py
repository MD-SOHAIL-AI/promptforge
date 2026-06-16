"""Canonical generated firmware project models for PromptForge.

The models in this module are immutable data contracts.  They validate and
serialize firmware state but perform no filesystem access, builds, or flashing.
"""

from __future__ import annotations

import json
import posixpath
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

__all__ = ["BuildArtifact", "FirmwareFile", "FirmwareProject"]


@dataclass(frozen=True, slots=True)
class FirmwareFile:
    """One immutable UTF-8 text file in a generated firmware project."""

    path: str
    content: str
    size_bytes: int

    def __post_init__(self) -> None:
        _validate_file_path(self.path)
        if not isinstance(self.content, str):
            raise ValueError("content must be a string")
        if "\x00" in self.content:
            raise ValueError("content cannot contain NUL characters")
        _validate_size(self.size_bytes, "size_bytes")
        actual_size = len(self.content.encode("utf-8"))
        if self.size_bytes != actual_size:
            raise ValueError(
                "size_bytes must equal the UTF-8 encoded content size"
            )

    def to_dict(self) -> dict[str, str | int]:
        """Return the canonical JSON-compatible file representation."""

        return {
            "path": self.path,
            "content": self.content,
            "size_bytes": self.size_bytes,
        }

    def api_dict(self) -> dict[str, str | int]:
        """Return the canonical representation for API helpers."""

        return self.to_dict()

    def to_json(self) -> str:
        """Serialize the file as deterministic compact JSON."""

        return _json_dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FirmwareFile:
        """Reconstruct a firmware file from its exact serialized schema."""

        values = _exact_schema(
            data,
            expected={"path", "content", "size_bytes"},
            label="firmware file",
        )
        return cls(
            path=values["path"],
            content=values["content"],
            size_bytes=values["size_bytes"],
        )

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> FirmwareFile:
        """Reconstruct a firmware file from a UTF-8 JSON object."""

        return cls.from_dict(_load_json_object(payload))


@dataclass(frozen=True, slots=True)
class FirmwareProject:
    """Immutable collection of files generated for one firmware target."""

    project_name: str
    target_board: str
    framework: str
    generated_files: tuple[FirmwareFile, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        _validate_text(self.project_name, "project_name", maximum=128)
        _validate_text(self.target_board, "target_board", maximum=128)
        _validate_text(self.framework, "framework", maximum=128)

        generated_files = _coerce_files(self.generated_files)
        if not generated_files:
            raise ValueError("generated_files must contain at least one file")
        seen_paths: set[str] = set()
        for generated_file in generated_files:
            normalized_path = generated_file.path.casefold()
            if normalized_path in seen_paths:
                raise ValueError("generated_files cannot contain duplicate paths")
            seen_paths.add(normalized_path)

        object.__setattr__(self, "generated_files", generated_files)
        object.__setattr__(
            self,
            "created_at",
            _normalize_datetime(self.created_at, "created_at"),
        )

    def get_file(self, path: str) -> FirmwareFile | None:
        """Return the file at ``path``, or ``None`` if it is not present."""

        _validate_file_path(path)
        return next(
            (
                generated_file
                for generated_file in self.generated_files
                if generated_file.path == path
            ),
            None,
        )

    def list_files(self) -> tuple[str, ...]:
        """Return generated file paths in stable project order."""

        return tuple(item.path for item in self.generated_files)

    def total_size(self) -> int:
        """Return the total UTF-8 byte size of all generated files."""

        return sum(item.size_bytes for item in self.generated_files)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible project representation."""

        return {
            "project_name": self.project_name,
            "target_board": self.target_board,
            "framework": self.framework,
            "generated_files": [
                generated_file.to_dict()
                for generated_file in self.generated_files
            ],
            "created_at": _format_datetime(self.created_at),
        }

    def api_dict(self) -> dict[str, Any]:
        """Return the canonical representation for API helpers."""

        return self.to_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FirmwareProject:
        """Reconstruct a project from its exact serialized schema."""

        values = _exact_schema(
            data,
            expected={
                "project_name",
                "target_board",
                "framework",
                "generated_files",
                "created_at",
            },
            label="firmware project",
        )
        raw_files = values["generated_files"]
        if not isinstance(raw_files, Sequence) or isinstance(
            raw_files,
            (str, bytes, bytearray),
        ):
            raise ValueError("generated_files must be a sequence")
        return cls(
            project_name=values["project_name"],
            target_board=values["target_board"],
            framework=values["framework"],
            generated_files=tuple(
                FirmwareFile.from_dict(item) for item in raw_files
            ),
            created_at=_parse_datetime(values["created_at"], "created_at"),
        )

    def to_json(self) -> str:
        """Serialize the project as deterministic compact JSON."""

        return _json_dumps(self.to_dict())

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> FirmwareProject:
        """Reconstruct a project from a UTF-8 JSON object."""

        return cls.from_dict(_load_json_object(payload))


@dataclass(frozen=True, slots=True)
class BuildArtifact:
    """Immutable metadata for a compiled firmware artifact."""

    firmware_path: str
    firmware_size: int
    checksum: str
    created_at: datetime

    def __post_init__(self) -> None:
        path = self.firmware_path
        if isinstance(path, Path):
            path = str(path)
            object.__setattr__(self, "firmware_path", path)
        _validate_text(path, "firmware_path", maximum=4_096)
        _validate_size(self.firmware_size, "firmware_size")
        _validate_text(self.checksum, "checksum", maximum=256)
        object.__setattr__(
            self,
            "created_at",
            _normalize_datetime(self.created_at, "created_at"),
        )

    def to_dict(self) -> dict[str, str | int]:
        """Return the canonical JSON-compatible artifact representation."""

        return {
            "firmware_path": self.firmware_path,
            "firmware_size": self.firmware_size,
            "checksum": self.checksum,
            "created_at": _format_datetime(self.created_at),
        }

    def api_dict(self) -> dict[str, str | int]:
        """Return the canonical representation for API helpers."""

        return self.to_dict()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BuildArtifact:
        """Reconstruct artifact metadata from its exact serialized schema."""

        values = _exact_schema(
            data,
            expected={
                "firmware_path",
                "firmware_size",
                "checksum",
                "created_at",
            },
            label="build artifact",
        )
        return cls(
            firmware_path=values["firmware_path"],
            firmware_size=values["firmware_size"],
            checksum=values["checksum"],
            created_at=_parse_datetime(values["created_at"], "created_at"),
        )

    def to_json(self) -> str:
        """Serialize artifact metadata as deterministic compact JSON."""

        return _json_dumps(self.to_dict())

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> BuildArtifact:
        """Reconstruct artifact metadata from a UTF-8 JSON object."""

        return cls.from_dict(_load_json_object(payload))


def _coerce_files(value: object) -> tuple[FirmwareFile, ...]:
    if not isinstance(value, Iterable) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise ValueError("generated_files must be an iterable")
    try:
        files = tuple(value)
    except TypeError as exc:
        raise ValueError("generated_files must be an iterable") from exc
    if any(not isinstance(item, FirmwareFile) for item in files):
        raise ValueError("generated_files must contain only FirmwareFile values")
    return files


def _validate_size(value: object, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")


def _validate_text(value: object, field_name: str, *, maximum: int) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if len(value) > maximum or "\x00" in value:
        raise ValueError(f"{field_name} is invalid")


def _validate_file_path(value: object) -> None:
    if not isinstance(value, str):
        raise ValueError("path must be a string")
    if not value or value != value.strip():
        raise ValueError("path must be non-empty without outer whitespace")
    if len(value) > 240 or "\x00" in value or "\\" in value:
        raise ValueError("path must be a safe POSIX path")

    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("/"):
        raise ValueError("path must be relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("path cannot contain empty, current, or parent segments")
    for part in path.parts:
        if part.endswith((" ", ".")):
            raise ValueError("path segments cannot end with spaces or dots")
        if any(character in '<>:"|?*' for character in part):
            raise ValueError("path contains an unsafe character")
        if any(ord(character) < 32 for character in part):
            raise ValueError("path cannot contain control characters")
        if part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES:
            raise ValueError("path contains a reserved filename")
    if posixpath.normpath(value) != value:
        raise ValueError("path must be normalized")


def _normalize_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO 8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be valid ISO 8601") from exc
    return _normalize_datetime(parsed, field_name)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
