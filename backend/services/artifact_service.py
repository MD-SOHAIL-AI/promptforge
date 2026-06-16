"""Deterministic artifact persistence for PromptForge AI.

This service stores and retrieves existing artifact content. It does not build
firmware, execute tools, flash hardware, or start simulations.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, unique
from io import BytesIO
from pathlib import Path
from typing import Any

from ..utils.serialization import _freeze_json_value, _freeze_mapping

__all__ = [
    "ArtifactConflictError",
    "ArtifactIntegrityError",
    "ArtifactMetadata",
    "ArtifactNotFoundError",
    "ArtifactService",
    "ArtifactServiceError",
    "ArtifactType",
]


@unique
class ArtifactType(str, Enum):
    """Artifact categories persisted by PromptForge."""

    FIRMWARE_BINARY = "FIRMWARE_BINARY"
    BUILD_LOG = "BUILD_LOG"
    SERIAL_LOG = "SERIAL_LOG"
    SIMULATION_OUTPUT = "SIMULATION_OUTPUT"
    GENERATED_SOURCE_ARCHIVE = "GENERATED_SOURCE_ARCHIVE"


class ArtifactServiceError(RuntimeError):
    """Base error for artifact persistence failures."""


class ArtifactNotFoundError(ArtifactServiceError, FileNotFoundError):
    """The requested artifact does not exist."""


class ArtifactConflictError(ArtifactServiceError, FileExistsError):
    """An artifact identifier already owns different content."""


class ArtifactIntegrityError(ArtifactServiceError, ValueError):
    """Persisted artifact data or metadata failed validation."""


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    """Immutable description of one persisted artifact."""

    artifact_id: str
    artifact_type: ArtifactType
    name: str
    artifact_path: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        _validate_artifact_id(self.artifact_id)
        if not isinstance(self.artifact_type, ArtifactType):
            raise ValueError("artifact_type must be an ArtifactType")
        _validate_artifact_name(self.name)
        _validate_text(self.artifact_path, field_name="artifact_path")
        _validate_text(self.content_type, field_name="content_type")
        if (
            not isinstance(self.size_bytes, int)
            or isinstance(self.size_bytes, bool)
            or self.size_bytes < 0
        ):
            raise ValueError("size_bytes must be a non-negative integer")
        if not isinstance(self.sha256, str) or not _SHA256_RE.fullmatch(
            self.sha256
        ):
            raise ValueError("sha256 must be a lowercase SHA-256 digest")

        object.__setattr__(
            self,
            "created_at",
            _normalize_datetime(self.created_at, "created_at"),
        )
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a fresh JSON-compatible metadata representation."""

        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type.value,
            "name": self.name,
            "artifact_path": self.artifact_path,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "created_at": _format_datetime(self.created_at),
            "metadata": _thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ArtifactMetadata:
        """Build metadata from its exact canonical representation."""

        values = _exact_schema(
            data,
            expected={
                "artifact_id",
                "artifact_type",
                "name",
                "artifact_path",
                "content_type",
                "size_bytes",
                "sha256",
                "created_at",
                "metadata",
            },
            label="artifact metadata",
        )
        try:
            artifact_type = ArtifactType(values["artifact_type"])
        except (TypeError, ValueError) as exc:
            raise ValueError("artifact_type is not supported") from exc
        return cls(
            artifact_id=values["artifact_id"],
            artifact_type=artifact_type,
            name=values["name"],
            artifact_path=values["artifact_path"],
            content_type=values["content_type"],
            size_bytes=values["size_bytes"],
            sha256=values["sha256"],
            created_at=_parse_datetime(values["created_at"]),
            metadata=values["metadata"],
        )


class ArtifactService:
    """Persist execution and build artifacts below one managed root."""

    def __init__(
        self,
        artifacts_root: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(artifacts_root, (str, Path)):
            raise ValueError("artifacts_root must be a path")
        if isinstance(artifacts_root, str) and not artifacts_root.strip():
            raise ValueError("artifacts_root must be non-empty")
        if clock is not None and not callable(clock):
            raise ValueError("clock must be callable")

        self._root = Path(artifacts_root).expanduser().resolve()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = asyncio.Lock()

    @property
    def artifacts_root(self) -> Path:
        """Return the absolute managed artifact root."""

        return self._root

    async def save_artifact(
        self,
        artifact_type: ArtifactType,
        name: str,
        content: bytes | bytearray | memoryview | str | Path,
        *,
        artifact_id: str | None = None,
        content_type: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactMetadata:
        """Persist artifact content and return immutable metadata.

        ``str`` values are encoded as UTF-8 content. Use ``Path`` to import an
        existing file. Identical saves are idempotent; identifier collisions
        with different content or metadata are rejected.
        """

        if not isinstance(artifact_type, ArtifactType):
            raise ValueError("artifact_type must be an ArtifactType")
        _validate_artifact_name(name)
        if artifact_id is not None:
            _validate_artifact_id(artifact_id)
        if content_type is not None:
            _validate_text(content_type, field_name="content_type")
        frozen_metadata = _freeze_metadata(metadata or {})

        async with self._lock:
            payload = await asyncio.to_thread(_read_content, content)
            return await asyncio.to_thread(
                self._save_artifact_sync,
                artifact_type,
                name,
                payload,
                artifact_id,
                content_type or _default_content_type(artifact_type, name),
                frozen_metadata,
            )

    async def load_artifact(self, artifact_id: str) -> bytes:
        """Load and integrity-check artifact bytes."""

        _validate_artifact_id(artifact_id)
        async with self._lock:
            return await asyncio.to_thread(
                self._load_artifact_sync,
                artifact_id,
            )

    async def delete_artifact(self, artifact_id: str) -> bool:
        """Delete a managed artifact and return whether it existed."""

        _validate_artifact_id(artifact_id)
        async with self._lock:
            return await asyncio.to_thread(
                self._delete_artifact_sync,
                artifact_id,
            )

    async def list_artifacts(
        self,
        artifact_type: ArtifactType | None = None,
    ) -> tuple[ArtifactMetadata, ...]:
        """List valid artifacts in deterministic order."""

        if artifact_type is not None and not isinstance(
            artifact_type,
            ArtifactType,
        ):
            raise ValueError("artifact_type must be an ArtifactType")
        async with self._lock:
            return await asyncio.to_thread(
                self._list_artifacts_sync,
                artifact_type,
            )

    async def archive_artifacts(
        self,
        artifact_ids: Sequence[str],
        archive_name: str,
        *,
        artifact_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> ArtifactMetadata:
        """Create and persist a reproducible ZIP containing selected artifacts."""

        ids = _validate_artifact_ids(artifact_ids)
        _validate_artifact_name(archive_name)
        if not archive_name.casefold().endswith(".zip"):
            raise ValueError("archive_name must end with .zip")
        if artifact_id is not None:
            _validate_artifact_id(artifact_id)
        frozen_metadata = _freeze_metadata(metadata or {})

        async with self._lock:
            return await asyncio.to_thread(
                self._archive_artifacts_sync,
                ids,
                archive_name,
                artifact_id,
                frozen_metadata,
            )

    def _save_artifact_sync(
        self,
        artifact_type: ArtifactType,
        name: str,
        payload: bytes,
        artifact_id: str | None,
        content_type: str,
        metadata: Mapping[str, Any],
    ) -> ArtifactMetadata:
        self._ensure_root()
        digest = hashlib.sha256(payload).hexdigest()
        resolved_id = artifact_id or _derived_artifact_id(
            artifact_type,
            name,
            digest,
        )
        target = self._artifact_directory(resolved_id)
        created_at = _normalize_datetime(self._clock(), "clock result")
        candidate = ArtifactMetadata(
            artifact_id=resolved_id,
            artifact_type=artifact_type,
            name=name,
            artifact_path=str(target / _PAYLOAD_NAME),
            content_type=content_type,
            size_bytes=len(payload),
            sha256=digest,
            created_at=created_at,
            metadata=metadata,
        )

        if target.exists() or target.is_symlink():
            existing = self._read_metadata(target)
            if _same_artifact(existing, candidate):
                self._load_artifact_sync(existing.artifact_id)
                return existing
            raise ArtifactConflictError(
                f"artifact_id already exists: {resolved_id}"
            )

        stage = Path(tempfile.mkdtemp(prefix=".artifact-", dir=self._root))
        try:
            (stage / _PAYLOAD_NAME).write_bytes(payload)
            _write_json(
                stage / _MANIFEST_NAME,
                _manifest_for(candidate),
            )
            os.replace(stage, target)
        except BaseException:
            _remove_directory(stage)
            raise
        return self._read_metadata(target)

    def _load_artifact_sync(self, artifact_id: str) -> bytes:
        directory = self._require_artifact_directory(artifact_id)
        metadata = self._read_metadata(directory)
        payload_path = directory / _PAYLOAD_NAME
        if payload_path.is_symlink() or not payload_path.is_file():
            raise ArtifactIntegrityError("artifact payload is missing")
        payload = payload_path.read_bytes()
        if len(payload) != metadata.size_bytes:
            raise ArtifactIntegrityError("artifact size does not match metadata")
        if hashlib.sha256(payload).hexdigest() != metadata.sha256:
            raise ArtifactIntegrityError("artifact checksum does not match metadata")
        return payload

    def _delete_artifact_sync(self, artifact_id: str) -> bool:
        self._ensure_root()
        directory = self._artifact_directory(artifact_id)
        if not directory.exists() and not directory.is_symlink():
            return False
        self._assert_artifact_directory(directory)
        _remove_directory(directory)
        return True

    def _list_artifacts_sync(
        self,
        artifact_type: ArtifactType | None,
    ) -> tuple[ArtifactMetadata, ...]:
        self._ensure_root()
        artifacts: list[ArtifactMetadata] = []
        for path in self._root.iterdir():
            if path.name.startswith(".") or not path.is_dir() or path.is_symlink():
                continue
            manifest = path / _MANIFEST_NAME
            if not manifest.is_file() or manifest.is_symlink():
                continue
            item = self._read_metadata(path)
            if artifact_type is None or item.artifact_type is artifact_type:
                artifacts.append(item)
        return tuple(
            sorted(
                artifacts,
                key=lambda item: (
                    item.created_at,
                    item.artifact_type.value,
                    item.name.casefold(),
                    item.artifact_id,
                ),
            )
        )

    def _archive_artifacts_sync(
        self,
        artifact_ids: tuple[str, ...],
        archive_name: str,
        artifact_id: str | None,
        metadata: Mapping[str, Any],
    ) -> ArtifactMetadata:
        artifacts: list[tuple[ArtifactMetadata, bytes]] = []
        for item_id in sorted(artifact_ids):
            directory = self._require_artifact_directory(item_id)
            item_metadata = self._read_metadata(directory)
            payload = self._load_artifact_sync(item_id)
            artifacts.append((item_metadata, payload))

        archive = _build_archive(artifacts)
        archive_metadata = {
            **_thaw_json_value(metadata),
            "artifact_ids": [item.artifact_id for item, _ in artifacts],
            "artifact_count": len(artifacts),
        }
        return self._save_artifact_sync(
            ArtifactType.GENERATED_SOURCE_ARCHIVE,
            archive_name,
            archive,
            artifact_id,
            "application/zip",
            _freeze_metadata(archive_metadata),
        )

    def _read_metadata(self, directory: Path) -> ArtifactMetadata:
        self._assert_artifact_directory(directory)
        manifest_path = directory / _MANIFEST_NAME
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ArtifactIntegrityError("artifact manifest is missing")
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactIntegrityError("artifact manifest is invalid") from exc
        values = _validate_manifest(raw, expected_id=directory.name)
        values.pop("schema_version")
        values["artifact_path"] = str(directory / _PAYLOAD_NAME)
        try:
            return ArtifactMetadata.from_dict(values)
        except ValueError as exc:
            raise ArtifactIntegrityError(str(exc)) from exc

    def _require_artifact_directory(self, artifact_id: str) -> Path:
        self._ensure_root()
        directory = self._artifact_directory(artifact_id)
        if not directory.exists() and not directory.is_symlink():
            raise ArtifactNotFoundError(f"artifact not found: {artifact_id}")
        self._assert_artifact_directory(directory)
        return directory

    def _artifact_directory(self, artifact_id: str) -> Path:
        directory = self._root / artifact_id
        if directory.parent != self._root:
            raise ArtifactIntegrityError("artifact path escapes artifacts_root")
        return directory

    def _assert_artifact_directory(self, directory: Path) -> None:
        if directory.is_symlink():
            raise ArtifactIntegrityError("managed artifact cannot be a symlink")
        if not directory.is_dir():
            raise ArtifactIntegrityError("managed artifact must be a directory")
        if directory.resolve().parent != self._root:
            raise ArtifactIntegrityError("artifact path escapes artifacts_root")

    def _ensure_root(self) -> None:
        if self._root.exists() and not self._root.is_dir():
            raise ArtifactIntegrityError("artifacts_root must be a directory")
        self._root.mkdir(parents=True, exist_ok=True)


def _read_content(
    content: bytes | bytearray | memoryview | str | Path,
) -> bytes:
    if isinstance(content, Path):
        if content.is_symlink():
            raise ValueError("artifact source cannot be a symlink")
        if not content.is_file():
            raise ValueError("artifact source must be an existing file")
        return content.read_bytes()
    if isinstance(content, str):
        return content.encode("utf-8")
    if isinstance(content, (bytes, bytearray, memoryview)):
        return bytes(content)
    raise ValueError("content must be bytes, text, or an existing Path")


def _build_archive(
    artifacts: Sequence[tuple[ArtifactMetadata, bytes]],
) -> bytes:
    manifest = {
        "schema_version": 1,
        "artifacts": [
            {
                "artifact_id": metadata.artifact_id,
                "artifact_type": metadata.artifact_type.value,
                "name": metadata.name,
                "content_type": metadata.content_type,
                "size_bytes": metadata.size_bytes,
                "sha256": metadata.sha256,
                "created_at": _format_datetime(metadata.created_at),
                "metadata": _thaw_json_value(metadata.metadata),
            }
            for metadata, _ in artifacts
        ],
    }
    output = BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_STORED) as archive:
        _write_zip_entry(
            archive,
            "manifest.json",
            _json_bytes(manifest),
        )
        for metadata, payload in artifacts:
            _write_zip_entry(
                archive,
                f"artifacts/{metadata.artifact_id}/{metadata.name}",
                payload,
            )
    return output.getvalue()


def _write_zip_entry(
    archive: zipfile.ZipFile,
    name: str,
    payload: bytes,
) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, payload)


def _manifest_for(metadata: ArtifactMetadata) -> dict[str, Any]:
    data = metadata.to_dict()
    data.pop("artifact_path")
    return {"schema_version": _SCHEMA_VERSION, **data}


def _validate_manifest(data: object, *, expected_id: str) -> dict[str, Any]:
    values = _exact_schema(
        data,
        expected={
            "schema_version",
            "artifact_id",
            "artifact_type",
            "name",
            "content_type",
            "size_bytes",
            "sha256",
            "created_at",
            "metadata",
        },
        label="artifact manifest",
        error_type=ArtifactIntegrityError,
    )
    if values["schema_version"] != _SCHEMA_VERSION:
        raise ArtifactIntegrityError("unsupported artifact manifest version")
    if values["artifact_id"] != expected_id:
        raise ArtifactIntegrityError(
            "artifact manifest identifier does not match its directory"
        )
    return values


def _same_artifact(
    existing: ArtifactMetadata,
    candidate: ArtifactMetadata,
) -> bool:
    return (
        existing.artifact_type is candidate.artifact_type
        and existing.name == candidate.name
        and existing.content_type == candidate.content_type
        and existing.size_bytes == candidate.size_bytes
        and existing.sha256 == candidate.sha256
        and existing.metadata == candidate.metadata
    )


def _derived_artifact_id(
    artifact_type: ArtifactType,
    name: str,
    digest: str,
) -> str:
    identity = hashlib.sha256(
        f"{artifact_type.value}\0{name}\0{digest}".encode("utf-8")
    ).hexdigest()
    return f"artifact-{identity[:32]}"


def _default_content_type(
    artifact_type: ArtifactType,
    name: str,
) -> str:
    if artifact_type is ArtifactType.GENERATED_SOURCE_ARCHIVE:
        return "application/zip"
    if artifact_type is ArtifactType.FIRMWARE_BINARY:
        return "application/octet-stream"
    if artifact_type in {ArtifactType.BUILD_LOG, ArtifactType.SERIAL_LOG}:
        return "text/plain; charset=utf-8"
    if name.casefold().endswith(".json"):
        return "application/json"
    return "application/octet-stream"


def _validate_artifact_ids(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise ValueError("artifact_ids must be a sequence")
    ids = tuple(value)
    if not ids:
        raise ValueError("artifact_ids cannot be empty")
    for artifact_id in ids:
        _validate_artifact_id(artifact_id)
    if len(set(ids)) != len(ids):
        raise ValueError("artifact_ids cannot contain duplicates")
    return ids


def _validate_artifact_id(value: object) -> None:
    if not isinstance(value, str) or not _ARTIFACT_ID_RE.fullmatch(value):
        raise ValueError("artifact_id must be a filesystem-safe identifier")


def _validate_artifact_name(value: object) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("name must be a non-empty filename")
    if len(value) > 128 or "\x00" in value or "/" in value or "\\" in value:
        raise ValueError("name must be a safe filename")
    if value in {".", ".."} or value.endswith((" ", ".")):
        raise ValueError("name must be a safe filename")
    if any(character in '<>:"|?*' or ord(character) < 32 for character in value):
        raise ValueError("name contains a cross-platform unsafe character")
    stem = value.split(".", 1)[0].casefold()
    if stem in _WINDOWS_RESERVED_NAMES:
        raise ValueError("name contains a reserved filename")


def _validate_text(value: object, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if "\x00" in value:
        raise ValueError(f"{field_name} cannot contain NUL characters")


def _normalize_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("created_at must be an ISO 8601 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("created_at must be valid ISO 8601") from exc
    return _normalize_datetime(parsed, "created_at")


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _exact_schema(
    data: object,
    *,
    expected: set[str],
    label: str,
    error_type: type[Exception] = ValueError,
) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise error_type(f"{label} must be a mapping")
    supplied = set(data)
    missing = expected - supplied
    unknown = supplied - expected
    if missing:
        raise error_type(
            f"{label} is missing required fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise error_type(
            f"{label} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    return dict(data)


def _freeze_metadata(metadata: object) -> Mapping[str, Any]:
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping")
    return _freeze_mapping(metadata, path="metadata", active=set())


def _thaw_json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json_value(item) for item in value]
    return value


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.write_bytes(_json_bytes(data) + b"\n")


def _json_bytes(data: Mapping[str, Any]) -> bytes:
    return json.dumps(
        data,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
        separators=(",", ": "),
    ).encode("utf-8")


def _remove_directory(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_dir():
        raise ArtifactIntegrityError(
            "refusing to remove a non-directory artifact path"
        )
    shutil.rmtree(path)


_MANIFEST_NAME = "metadata.json"
_PAYLOAD_NAME = "payload"
_SCHEMA_VERSION = 1
_ARTIFACT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
