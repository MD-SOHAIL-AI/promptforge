"""Filesystem-safe firmware project persistence for PromptForge AI.

``ProjectService`` materializes and reconstructs ``GeneratedProject`` values.
It performs no planning, code generation, builds, or tool execution.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ..contracts.generated_project import GeneratedFile, GeneratedProject
from .code_generation_service import CodeGenerationService
from .platformio_service import PlatformIOService

__all__ = [
    "ProjectConflictError",
    "ProjectMetadata",
    "ProjectNotFoundError",
    "ProjectService",
    "ProjectServiceError",
    "ProjectStructureError",
]


class ProjectServiceError(RuntimeError):
    """Base error for project persistence failures."""


class ProjectNotFoundError(ProjectServiceError, FileNotFoundError):
    """The requested PromptForge project does not exist."""


class ProjectConflictError(ProjectServiceError, FileExistsError):
    """A project cannot be created or replaced without losing ownership."""


class ProjectStructureError(ProjectServiceError, ValueError):
    """A project or its persisted structure is invalid."""


@dataclass(frozen=True, slots=True)
class ProjectMetadata:
    """Immutable summary of one persisted PromptForge project."""

    project_id: str
    project_name: str
    target_board: str
    framework: str
    project_path: str
    created_at: datetime
    updated_at: datetime
    file_count: int
    metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        for field_name in (
            "project_id",
            "target_board",
            "framework",
            "project_path",
        ):
            _validate_text(getattr(self, field_name), field_name=field_name)
        _validate_project_name(self.project_name)
        created_at = _normalize_datetime(self.created_at, "created_at")
        updated_at = _normalize_datetime(self.updated_at, "updated_at")
        if updated_at < created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        if (
            not isinstance(self.file_count, int)
            or isinstance(self.file_count, bool)
            or self.file_count < 0
        ):
            raise ValueError("file_count must be a non-negative integer")

        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a fresh JSON-compatible metadata representation."""

        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "target_board": self.target_board,
            "framework": self.framework,
            "project_path": self.project_path,
            "created_at": _format_datetime(self.created_at),
            "updated_at": _format_datetime(self.updated_at),
            "file_count": self.file_count,
            "metadata": _thaw_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProjectMetadata:
        """Build metadata from its exact serialized representation."""

        values = _exact_schema(
            data,
            expected={
                "project_id",
                "project_name",
                "target_board",
                "framework",
                "project_path",
                "created_at",
                "updated_at",
                "file_count",
                "metadata",
            },
            label="project metadata",
        )
        return cls(
            project_id=values["project_id"],
            project_name=values["project_name"],
            target_board=values["target_board"],
            framework=values["framework"],
            project_path=values["project_path"],
            created_at=_parse_datetime(values["created_at"], "created_at"),
            updated_at=_parse_datetime(values["updated_at"], "updated_at"),
            file_count=values["file_count"],
            metadata=values["metadata"],
        )


class ProjectService:
    """Manage generated firmware projects below one configured root.

    Blocking filesystem work runs in worker threads. A service-local lock
    serializes mutations and reads so callers cannot observe a half-replaced
    project directory.
    """

    def __init__(
        self,
        projects_root: str | Path,
        *,
        code_generation_service: CodeGenerationService | None = None,
        platformio_service: PlatformIOService | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(projects_root, (str, Path)):
            raise ValueError("projects_root must be a path")
        if isinstance(projects_root, str) and not projects_root.strip():
            raise ValueError("projects_root must be non-empty")
        if code_generation_service is not None and not callable(
            getattr(code_generation_service, "validate_project", None)
        ):
            raise ValueError(
                "code_generation_service must provide validate_project"
            )
        if platformio_service is not None and not callable(
            getattr(platformio_service, "validate_project", None)
        ):
            raise ValueError("platformio_service must provide validate_project")
        if clock is not None and not callable(clock):
            raise ValueError("clock must be callable")

        self._root = Path(projects_root).expanduser().resolve()
        self._code_generation_service = code_generation_service
        self._platformio_service = platformio_service
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = asyncio.Lock()

    @property
    def projects_root(self) -> Path:
        """Return the absolute managed project root."""

        return self._root

    async def create_project(
        self,
        project: GeneratedProject,
    ) -> ProjectMetadata:
        """Create and persist a new project without overwriting an existing one."""

        self._validate_generated_project(project)
        async with self._lock:
            return await asyncio.to_thread(self._create_project_sync, project)

    async def load_project(self, project_name: str) -> GeneratedProject:
        """Load a project from disk, using persisted files as source of truth."""

        _validate_project_name(project_name)
        async with self._lock:
            return await asyncio.to_thread(self._load_project_sync, project_name)

    async def save_project(
        self,
        project: GeneratedProject,
    ) -> ProjectMetadata:
        """Atomically replace an existing project owned by ``project_id``."""

        self._validate_generated_project(project)
        async with self._lock:
            return await asyncio.to_thread(self._save_project_sync, project)

    async def delete_project(self, project_name: str) -> bool:
        """Delete a managed project and return whether it existed."""

        _validate_project_name(project_name)
        async with self._lock:
            return await asyncio.to_thread(self._delete_project_sync, project_name)

    async def list_projects(self) -> tuple[ProjectMetadata, ...]:
        """Return valid managed projects in deterministic name order."""

        async with self._lock:
            return await asyncio.to_thread(self._list_projects_sync)

    async def validate_project(
        self,
        project: GeneratedProject | str,
    ) -> None:
        """Validate an in-memory project or a persisted managed project.

        Injected service validators are inspection-only. This method never
        generates code or invokes PlatformIO commands.
        """

        if isinstance(project, GeneratedProject):
            self._validate_generated_project(project)
            return
        _validate_project_name(project)
        async with self._lock:
            await asyncio.to_thread(self._validate_persisted_sync, project)

    def _create_project_sync(self, project: GeneratedProject) -> ProjectMetadata:
        self._ensure_root()
        target = self._project_path(project.project_name)
        if target.exists() or target.is_symlink():
            raise ProjectConflictError(
                f"project already exists: {project.project_name}"
            )
        if self._find_by_id(project.project_id) is not None:
            raise ProjectConflictError(
                f"project_id already exists: {project.project_id}"
            )

        updated_at = project.created_at
        stage = self._stage_project(project, updated_at=updated_at)
        try:
            self._validate_platformio_path(project, stage)
            os.replace(stage, target)
        except BaseException:
            _remove_tree(stage)
            raise
        return self._read_metadata(target)

    def _load_project_sync(self, project_name: str) -> GeneratedProject:
        project_path = self._require_project_path(project_name)
        manifest = self._read_manifest(project_path)
        return self._project_from_manifest(project_path, manifest)

    def _save_project_sync(self, project: GeneratedProject) -> ProjectMetadata:
        self._ensure_root()
        target = self._require_project_path(project.project_name)
        current = self._read_metadata(target)
        if current.project_id != project.project_id:
            raise ProjectConflictError(
                "project_id does not match the persisted project owner"
            )
        if current.created_at != project.created_at:
            raise ProjectConflictError(
                "created_at does not match the persisted project"
            )
        duplicate = self._find_by_id(project.project_id)
        if duplicate is not None and duplicate != target:
            raise ProjectConflictError(
                f"project_id already exists: {project.project_id}"
            )

        updated_at = _normalize_datetime(self._clock(), "clock result")
        updated_at = max(updated_at, current.updated_at)
        stage = self._stage_project(project, updated_at=updated_at)
        backup = target.with_name(f".{target.name}.backup")
        if backup.exists() or backup.is_symlink():
            raise ProjectServiceError("stale project backup prevents save")
        try:
            self._validate_platformio_path(project, stage)
            os.replace(target, backup)
            try:
                os.replace(stage, target)
            except BaseException:
                os.replace(backup, target)
                raise
            _remove_tree(backup)
        except BaseException:
            _remove_tree(stage)
            raise
        return self._read_metadata(target)

    def _delete_project_sync(self, project_name: str) -> bool:
        self._ensure_root()
        target = self._project_path(project_name)
        if not target.exists() and not target.is_symlink():
            return False
        self._assert_managed_directory(target)
        _remove_tree(target)
        return True

    def _list_projects_sync(self) -> tuple[ProjectMetadata, ...]:
        self._ensure_root()
        projects: list[ProjectMetadata] = []
        for path in self._root.iterdir():
            if path.name.startswith(".") or not path.is_dir() or path.is_symlink():
                continue
            manifest_path = path / _MANIFEST_NAME
            if not manifest_path.is_file() or manifest_path.is_symlink():
                continue
            projects.append(self._read_metadata(path))
        return tuple(
            sorted(
                projects,
                key=lambda item: (item.project_name.casefold(), item.project_id),
            )
        )

    def _validate_persisted_sync(self, project_name: str) -> None:
        path = self._require_project_path(project_name)
        project = self._project_from_manifest(path, self._read_manifest(path))
        self._validate_generated_project(project)
        self._validate_platformio_path(project, path)

    def _validate_generated_project(self, project: GeneratedProject) -> None:
        if not isinstance(project, GeneratedProject):
            raise TypeError("project must be a GeneratedProject")
        _validate_project_name(project.project_name)
        if any(file.path == _MANIFEST_NAME for file in project.files):
            raise ProjectStructureError(
                f"{_MANIFEST_NAME} is reserved for project metadata"
            )
        if self._code_generation_service is not None:
            try:
                self._code_generation_service.validate_project(project)
            except Exception as exc:
                raise ProjectStructureError(str(exc)) from exc

    def _validate_platformio_path(
        self,
        project: GeneratedProject,
        path: Path,
    ) -> None:
        if (
            self._platformio_service is None
            or _string_value(project.framework).casefold() != "platformio"
        ):
            return
        try:
            self._platformio_service.validate_project(path)
        except Exception as exc:
            raise ProjectStructureError(str(exc)) from exc

    def _stage_project(
        self,
        project: GeneratedProject,
        *,
        updated_at: datetime,
    ) -> Path:
        stage = Path(tempfile.mkdtemp(prefix=".promptforge-", dir=self._root))
        try:
            for generated_file in project.files:
                destination = _contained_file(stage, generated_file.path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(generated_file.content, encoding="utf-8")
            manifest = _manifest_for(project, updated_at=updated_at)
            _write_json(stage / _MANIFEST_NAME, manifest)
            return stage
        except BaseException:
            _remove_tree(stage)
            raise

    def _project_from_manifest(
        self,
        project_path: Path,
        manifest: Mapping[str, Any],
    ) -> GeneratedProject:
        files: list[GeneratedFile] = []
        for item in manifest["files"]:
            path = item["path"]
            file_path = _contained_file(project_path, path)
            _reject_symlink_components(project_path, file_path)
            if not file_path.is_file():
                raise ProjectStructureError(f"project file is missing: {path}")
            try:
                content = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ProjectStructureError(
                    f"project file is not valid UTF-8: {path}"
                ) from exc
            files.append(
                GeneratedFile(
                    path=path,
                    content=content,
                    file_type=item["file_type"],
                )
            )

        return GeneratedProject(
            project_id=manifest["project_id"],
            project_name=manifest["project_name"],
            target_board=manifest["target_board"],
            framework=manifest["framework"],
            files=tuple(files),
            created_at=_parse_datetime(manifest["created_at"], "created_at"),
            metadata=manifest["metadata"],
        )

    def _read_metadata(self, project_path: Path) -> ProjectMetadata:
        manifest = self._read_manifest(project_path)
        return ProjectMetadata(
            project_id=manifest["project_id"],
            project_name=manifest["project_name"],
            target_board=manifest["target_board"],
            framework=manifest["framework"],
            project_path=str(project_path),
            created_at=_parse_datetime(manifest["created_at"], "created_at"),
            updated_at=_parse_datetime(manifest["updated_at"], "updated_at"),
            file_count=len(manifest["files"]),
            metadata=manifest["metadata"],
        )

    def _read_manifest(self, project_path: Path) -> dict[str, Any]:
        self._assert_managed_directory(project_path)
        manifest_path = project_path / _MANIFEST_NAME
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise ProjectStructureError("project manifest is missing")
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectStructureError("project manifest is invalid") from exc
        return _validate_manifest(raw, expected_name=project_path.name)

    def _find_by_id(self, project_id: str) -> Path | None:
        for metadata in self._list_projects_sync():
            if metadata.project_id == project_id:
                return Path(metadata.project_path)
        return None

    def _require_project_path(self, project_name: str) -> Path:
        self._ensure_root()
        path = self._project_path(project_name)
        if not path.exists() and not path.is_symlink():
            raise ProjectNotFoundError(f"project not found: {project_name}")
        self._assert_managed_directory(path)
        return path

    def _project_path(self, project_name: str) -> Path:
        path = self._root / project_name
        if path.parent != self._root:
            raise ProjectStructureError("project path escapes projects_root")
        return path

    def _assert_managed_directory(self, path: Path) -> None:
        if path.is_symlink():
            raise ProjectStructureError("managed project cannot be a symlink")
        if not path.is_dir():
            raise ProjectStructureError("managed project must be a directory")
        resolved = path.resolve()
        if resolved.parent != self._root:
            raise ProjectStructureError("project path escapes projects_root")

    def _ensure_root(self) -> None:
        if self._root.exists() and not self._root.is_dir():
            raise ProjectStructureError("projects_root must be a directory")
        self._root.mkdir(parents=True, exist_ok=True)


def _manifest_for(
    project: GeneratedProject,
    *,
    updated_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "project_id": project.project_id,
        "project_name": project.project_name,
        "target_board": _string_value(project.target_board),
        "framework": _string_value(project.framework),
        "created_at": _format_datetime(project.created_at),
        "updated_at": _format_datetime(updated_at),
        "metadata": _thaw_json_value(project.metadata),
        "files": [
            {"path": item.path, "file_type": item.file_type}
            for item in project.files
        ],
    }


def _validate_manifest(
    data: object,
    *,
    expected_name: str,
) -> dict[str, Any]:
    values = _exact_schema(
        data,
        expected={
            "schema_version",
            "project_id",
            "project_name",
            "target_board",
            "framework",
            "created_at",
            "updated_at",
            "metadata",
            "files",
        },
        label="project manifest",
    )
    if values["schema_version"] != _SCHEMA_VERSION:
        raise ProjectStructureError("unsupported project manifest version")
    if values["project_name"] != expected_name:
        raise ProjectStructureError(
            "project manifest name does not match its directory"
        )
    for field_name in (
        "project_id",
        "project_name",
        "target_board",
        "framework",
    ):
        try:
            _validate_text(values[field_name], field_name=field_name)
        except ValueError as exc:
            raise ProjectStructureError(str(exc)) from exc
    try:
        _validate_project_name(values["project_name"])
        created_at = _parse_datetime(values["created_at"], "created_at")
        updated_at = _parse_datetime(values["updated_at"], "updated_at")
        if updated_at < created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        _freeze_metadata(values["metadata"])
    except ValueError as exc:
        raise ProjectStructureError(str(exc)) from exc

    raw_files = values["files"]
    if not isinstance(raw_files, Sequence) or isinstance(
        raw_files,
        (str, bytes, bytearray),
    ):
        raise ProjectStructureError("project manifest files must be a sequence")
    if not raw_files:
        raise ProjectStructureError("project manifest files cannot be empty")
    files: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_file in raw_files:
        file_data = _exact_schema(
            raw_file,
            expected={"path", "file_type"},
            label="project manifest file",
        )
        try:
            probe = GeneratedFile(
                path=file_data["path"],
                content="validation",
                file_type=file_data["file_type"],
            )
        except ValueError as exc:
            raise ProjectStructureError(str(exc)) from exc
        if probe.path == _MANIFEST_NAME:
            raise ProjectStructureError("manifest cannot list itself")
        normalized = probe.path.casefold()
        if normalized in seen:
            raise ProjectStructureError("manifest contains duplicate file paths")
        seen.add(normalized)
        files.append({"path": probe.path, "file_type": probe.file_type})
    values["files"] = files
    return values


def _contained_file(root: Path, relative_path: str) -> Path:
    destination = root / Path(*relative_path.split("/"))
    try:
        destination.resolve(strict=False).relative_to(root.resolve())
    except ValueError as exc:
        raise ProjectStructureError("project file escapes project directory") from exc
    return destination


def _reject_symlink_components(root: Path, path: Path) -> None:
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ProjectStructureError(
                f"project path component cannot be a symlink: {part}"
            )


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    serialized = json.dumps(
        data,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
        separators=(",", ": "),
    )
    path.write_text(serialized + "\n", encoding="utf-8")


def _remove_tree(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_dir():
        raise ProjectStructureError("refusing to remove a non-directory path")
    shutil.rmtree(path)


def _validate_project_name(value: object) -> None:
    if not isinstance(value, str) or not _PROJECT_NAME_RE.fullmatch(value):
        raise ValueError(
            "project_name must be a lowercase filesystem-safe name"
        )


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


def _string_value(value: object) -> str:
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        raise ValueError("identifier must resolve to a string")
    return raw


def _exact_schema(
    data: object,
    *,
    expected: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ProjectStructureError(f"{label} must be a mapping")
    supplied = set(data)
    missing = expected - supplied
    unknown = supplied - expected
    if missing:
        raise ProjectStructureError(
            f"{label} is missing required fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise ProjectStructureError(
            f"{label} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    return dict(data)


def _freeze_metadata(metadata: object) -> Mapping[str, Any]:
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
        if value != value or value in (float("inf"), float("-inf")):
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


_MANIFEST_NAME = ".promptforge-project.json"
_SCHEMA_VERSION = 1
_PROJECT_NAME_RE = re.compile(
    r"^(?=.{1,64}$)[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$"
)
