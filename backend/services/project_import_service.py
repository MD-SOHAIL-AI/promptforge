"""External PlatformIO project registration for ForgeX."""

from __future__ import annotations

import asyncio
import configparser
import hashlib
import json
import os
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "ExternalProjectEnvironment",
    "ExternalProjectRecord",
    "ProjectImportError",
    "ProjectImportService",
]


class ProjectImportError(ValueError):
    """An external project cannot be imported or resolved."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "PROJECT_IMPORT_FAILED",
        supported: bool = False,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.supported = supported
        self.details = dict(details or {})


@dataclass(frozen=True, slots=True)
class ExternalProjectEnvironment:
    name: str
    platform: str = ""
    board: str = ""
    framework: str = ""
    monitor_speed: int | None = None
    upload_speed: int | None = None
    lib_deps: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "platform": self.platform,
            "board": self.board,
            "framework": self.framework,
            "monitor_speed": self.monitor_speed,
            "upload_speed": self.upload_speed,
            "lib_deps": list(self.lib_deps),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExternalProjectEnvironment":
        return cls(
            name=str(data.get("name", "")),
            platform=str(data.get("platform", "")),
            board=str(data.get("board", "")),
            framework=str(data.get("framework", "")),
            monitor_speed=_optional_int(data.get("monitor_speed")),
            upload_speed=_optional_int(data.get("upload_speed")),
            lib_deps=tuple(str(item) for item in data.get("lib_deps", ()) if str(item).strip()),
        )


@dataclass(frozen=True, slots=True)
class ExternalProjectRecord:
    project_id: str
    project_name: str
    target_board: str
    framework: str
    project_path: str
    created_at: datetime
    updated_at: datetime
    file_count: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "target_board": self.target_board,
            "framework": self.framework,
            "project_path": self.project_path,
            "created_at": _format_datetime(self.created_at),
            "updated_at": _format_datetime(self.updated_at),
            "file_count": self.file_count,
            "metadata": _json_copy(self.metadata),
            "external": True,
            "project_type": self.metadata.get("project_type", "platformio"),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExternalProjectRecord":
        metadata = _mapping(data.get("metadata", {}))
        return cls(
            project_id=str(data["project_id"]),
            project_name=str(data["project_name"]),
            target_board=str(data.get("target_board", "")),
            framework=str(data.get("framework", "PlatformIO")),
            project_path=str(data["project_path"]),
            created_at=_parse_datetime(str(data["created_at"])),
            updated_at=_parse_datetime(str(data["updated_at"])),
            file_count=int(data.get("file_count", 0)),
            metadata=metadata,
        )


class ProjectImportService:
    """Register external folders without copying them."""

    def __init__(
        self,
        registry_path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(registry_path, (str, Path)):
            raise TypeError("registry_path must be a path")
        self._registry_path = Path(registry_path).expanduser().resolve()
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = asyncio.Lock()

    @property
    def registry_path(self) -> Path:
        return self._registry_path

    async def import_project(
        self,
        project_path: str | Path,
        *,
        previous_board: str | None = None,
    ) -> ExternalProjectRecord:
        async with self._lock:
            return await asyncio.to_thread(
                self._import_project_sync,
                project_path,
                previous_board=previous_board,
            )

    async def list_projects(self) -> tuple[ExternalProjectRecord, ...]:
        async with self._lock:
            return await asyncio.to_thread(self._list_projects_sync)

    async def get_project(self, project_id: str) -> ExternalProjectRecord:
        async with self._lock:
            return await asyncio.to_thread(self._get_project_sync, project_id)

    async def refresh_project(self, project_id: str) -> ExternalProjectRecord:
        async with self._lock:
            return await asyncio.to_thread(self._refresh_project_sync, project_id)

    def _import_project_sync(
        self,
        project_path: str | Path,
        *,
        previous_board: str | None,
    ) -> ExternalProjectRecord:
        root = _validate_external_root(project_path)
        ini_path = root / "platformio.ini"
        has_platformio_ini = ini_path.is_file() and not ini_path.is_symlink()
        promptforge_manifest = _read_promptforge_manifest(root)
        environments = _parse_platformio_ini(ini_path) if has_platformio_ini else ()
        active = _select_environment(environments, previous_board)
        now = self._now()
        registry = self._read_registry()
        path_key = str(root).casefold()
        existing_id = next(
            (
                project_id
                for project_id, item in registry.items()
                if str(item.get("project_path", "")).casefold() == path_key
            ),
            None,
        )
        manifest_project_id = _safe_manifest_id(promptforge_manifest.get("project_id")) if promptforge_manifest else None
        project_id = existing_id or manifest_project_id or _project_id(root)
        if project_id in registry and existing_id is None:
            project_id = f"{project_id}-{uuid.uuid4().hex[:8]}"

        created_at = _parse_datetime(str(registry[project_id]["created_at"])) if project_id in registry else now
        metadata = _metadata(
            root,
            environments,
            active,
            has_platformio_ini=has_platformio_ini,
            promptforge_manifest=promptforge_manifest,
            created_at=created_at,
            updated_at=now,
        )
        manifest_name = _safe_manifest_text(promptforge_manifest.get("project_name")) if promptforge_manifest else None
        manifest_board = _safe_manifest_text(promptforge_manifest.get("target_board")) if promptforge_manifest else None
        manifest_framework = _safe_manifest_text(promptforge_manifest.get("framework")) if promptforge_manifest else None
        record = ExternalProjectRecord(
            project_id=project_id,
            project_name=manifest_name or root.name,
            target_board=active.board if active is not None and active.board else (manifest_board or "UNKNOWN"),
            framework=active.framework if active is not None and active.framework else (("PlatformIO" if has_platformio_ini else manifest_framework) or "UNKNOWN"),
            project_path=str(root),
            created_at=created_at,
            updated_at=now,
            file_count=_count_files(root),
            metadata=metadata,
        )
        registry[project_id] = record.to_dict()
        self._write_registry(registry)
        return record

    def _list_projects_sync(self) -> tuple[ExternalProjectRecord, ...]:
        projects: list[ExternalProjectRecord] = []
        changed = False
        registry = self._read_registry()
        for project_id, item in registry.items():
            try:
                record = ExternalProjectRecord.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            path = Path(record.project_path)
            metadata = dict(record.metadata)
            missing = not path.exists() or not path.is_dir()
            if metadata.get("missing") != missing:
                metadata["missing"] = missing
                registry[project_id] = {
                    **record.to_dict(),
                    "metadata": metadata,
                }
                changed = True
                record = ExternalProjectRecord(
                    project_id=record.project_id,
                    project_name=record.project_name,
                    target_board=record.target_board,
                    framework=record.framework,
                    project_path=record.project_path,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                    file_count=0 if missing else record.file_count,
                    metadata=metadata,
                )
            projects.append(record)
        if changed:
            self._write_registry(registry)
        return tuple(sorted(projects, key=lambda item: item.updated_at, reverse=True))

    def _get_project_sync(self, project_id: str) -> ExternalProjectRecord:
        registry = self._read_registry()
        data = registry.get(project_id)
        if data is None:
            raise ProjectImportError(
                "External project was not found",
                code="PROJECT_NOT_FOUND",
                supported=False,
                details={"project_id": project_id},
            )
        record = ExternalProjectRecord.from_dict(data)
        path = Path(record.project_path)
        if not path.exists() or not path.is_dir():
            metadata = {**dict(record.metadata), "missing": True}
            raise ProjectImportError(
                "Missing project path",
                code="PROJECT_PATH_MISSING",
                supported=False,
                details={"project_id": project_id, "path": record.project_path, "metadata": metadata},
            )
        return record

    def _refresh_project_sync(self, project_id: str) -> ExternalProjectRecord:
        record = self._get_project_sync(project_id)
        return self._import_project_sync(record.project_path, previous_board=record.target_board)

    def _read_registry(self) -> dict[str, dict[str, Any]]:
        if not self._registry_path.exists():
            return {}
        try:
            data = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectImportError("External project registry is invalid") from exc
        if not isinstance(data, dict):
            raise ProjectImportError("External project registry must be a JSON object")
        projects = data.get("projects", data)
        if not isinstance(projects, dict):
            raise ProjectImportError("External project registry projects must be an object")
        return {
            str(project_id): dict(item)
            for project_id, item in projects.items()
            if isinstance(item, Mapping)
        }

    def _write_registry(self, registry: Mapping[str, Mapping[str, Any]]) -> None:
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(
            {"schema_version": 1, "projects": registry},
            ensure_ascii=True,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        temporary = self._registry_path.with_name(f".{self._registry_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(content + "\n", encoding="utf-8")
            temporary.replace(self._registry_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)


def _validate_external_root(project_path: str | Path) -> Path:
    if not isinstance(project_path, (str, Path)):
        raise ProjectImportError("Project path must be a string")
    if isinstance(project_path, str) and ("\x00" in project_path or not project_path.strip()):
        raise ProjectImportError("Project path is invalid")
    try:
        root = Path(project_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProjectImportError("Project path is invalid") from exc
    if not root.exists():
        raise ProjectImportError("Selected folder does not exist", details={"path": str(root)})
    if not root.is_dir():
        raise ProjectImportError("Selected path is not a folder", details={"path": str(root)})
    if root.is_symlink():
        raise ProjectImportError("Selected folder cannot be a symlink", details={"path": str(root)})
    if root.parent == root:
        raise ProjectImportError("System root folders cannot be imported", details={"path": str(root)})
    home = Path.home().resolve()
    if root == home:
        raise ProjectImportError("User home root cannot be imported directly", details={"path": str(root)})
    drive = Path(root.anchor).resolve() if root.anchor else None
    if drive is not None and root == drive:
        raise ProjectImportError("Drive root folders cannot be imported", details={"path": str(root)})
    return root


def _parse_platformio_ini(ini_path: Path) -> tuple[ExternalProjectEnvironment, ...]:
    parser = configparser.RawConfigParser(
        strict=True,
        inline_comment_prefixes=(";", "#"),
    )
    try:
        with ini_path.open("r", encoding="utf-8-sig") as handle:
            parser.read_file(handle)
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise ProjectImportError(
            "platformio.ini could not be parsed",
            code="INVALID_PLATFORMIO_INI",
            supported=False,
            details={"path": str(ini_path)},
        ) from exc
    environments: list[ExternalProjectEnvironment] = []
    for section in parser.sections():
        if not section.startswith("env:"):
            continue
        name = section[4:].strip()
        if not name:
            continue
        environments.append(
            ExternalProjectEnvironment(
                name=name,
                platform=_resolve_option(parser, name, "platform"),
                board=_resolve_option(parser, name, "board"),
                framework=_resolve_option(parser, name, "framework"),
                monitor_speed=_optional_int(_resolve_option(parser, name, "monitor_speed")),
                upload_speed=_optional_int(_resolve_option(parser, name, "upload_speed")),
                lib_deps=_split_list(_resolve_option(parser, name, "lib_deps")),
            )
        )
    return tuple(environments)


def _resolve_option(
    parser: configparser.RawConfigParser,
    environment: str,
    option: str,
    seen: set[str] | None = None,
) -> str:
    section = f"env:{environment}"
    visited = set(seen or ())
    if section in visited:
        return ""
    visited.add(section)
    if parser.has_option(section, option):
        return parser.get(section, option, raw=True).strip()
    if parser.has_option(section, "extends"):
        for parent in re.split(r"[\s,]+", parser.get(section, "extends", raw=True)):
            parent = parent.strip()
            if not parent:
                continue
            parent_name = parent[4:] if parent.startswith("env:") else parent
            if parser.has_section(f"env:{parent_name}"):
                value = _resolve_option(parser, parent_name, option, visited)
                if value:
                    return value
    if parser.has_option("env", option):
        return parser.get("env", option, raw=True).strip()
    return ""


def _split_list(value: str) -> tuple[str, ...]:
    if not value:
        return ()
    items: list[str] = []
    for line in value.replace(",", "\n").splitlines():
        item = line.strip()
        if item:
            items.append(item)
    return tuple(items)


def _select_environment(
    environments: tuple[ExternalProjectEnvironment, ...],
    previous_board: str | None,
) -> ExternalProjectEnvironment | None:
    if not environments:
        return None
    if previous_board:
        match = next((item for item in environments if item.board == previous_board), None)
        if match is not None:
            return match
    return next((item for item in environments if item.board), environments[0])


def _metadata(
    root: Path,
    environments: tuple[ExternalProjectEnvironment, ...],
    active: ExternalProjectEnvironment | None,
    *,
    has_platformio_ini: bool,
    promptforge_manifest: Mapping[str, Any] | None,
    created_at: datetime,
    updated_at: datetime,
) -> dict[str, Any]:
    active_board = active.board if active is not None else ""
    active_framework = active.framework if active is not None else ""
    metadata = {
        "external": True,
        "project_type": "platformio" if has_platformio_ini else "generic",
        "path": str(root),
        "name": root.name,
        "created_at": _format_datetime(created_at),
        "last_opened_at": _format_datetime(updated_at),
        "missing": False,
        "has_platformio_ini": has_platformio_ini,
        "files_loaded": True,
        "active_environment": active.name if active is not None else None,
        "board": active_board,
        "framework": active_framework,
        "generation_target_board": _target_family(active_board),
        "generation_framework": "PlatformIO" if has_platformio_ini else "UNKNOWN",
        "monitor_speed": active.monitor_speed if active is not None else None,
        "upload_speed": active.upload_speed if active is not None else None,
        "platformio": {
            "environments": [item.to_dict() for item in environments],
        },
    }
    if promptforge_manifest:
        metadata["promptforge_manifest"] = _json_copy(promptforge_manifest)
    return metadata


def _read_promptforge_manifest(root: Path) -> dict[str, Any] | None:
    manifest = root / ".promptforge-project.json"
    if not manifest.is_file() or manifest.is_symlink():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return dict(data) if isinstance(data, Mapping) else None


def _safe_manifest_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or "\x00" in value or len(value) > 128:
        return None
    if not all(character.isalnum() or character in "._- " for character in value):
        return None
    return value


def _safe_manifest_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or "\x00" in value or len(value) > 128:
        return None
    if not all(character.isalnum() or character in "._-" for character in value):
        return None
    return value


def _project_id(root: Path) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", root.name.casefold()).strip("-_")
    if not slug:
        slug = "project"
    digest = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:10]
    return f"external-{slug[:80]}-{digest}"


def _target_family(board: str) -> str:
    normalized = board.casefold().replace("_", "-")
    if "esp32" in normalized and "s3" in normalized:
        return "ESP32-S3"
    if "esp32" in normalized and "c3" in normalized:
        return "ESP32-C3"
    if "esp32" in normalized or "wroom" in normalized or "wrover" in normalized:
        return "ESP32"
    if "uno" in normalized:
        return "Arduino Uno"
    if "stm32" in normalized:
        return "STM32"
    return "UNKNOWN"


def _count_files(root: Path) -> int:
    count = 0
    for path in _iter_external_files(root):
        if path.is_file():
            count += 1
    return count


def _iter_external_files(root: Path):
    for current, directories, files in os.walk(root):
        current_path = Path(current)
        directories[:] = sorted(
            directory
            for directory in directories
            if directory not in _IGNORED_DIRECTORIES
            and not (current_path / directory).is_symlink()
        )
        for filename in sorted(files):
            path = current_path / filename
            if path.name in _IGNORED_FILES or path.is_symlink():
                continue
            yield path


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            return None
    return None


def _mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return _json_copy(value)


def _json_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(value), ensure_ascii=True, allow_nan=False))


def _parse_datetime(value: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


_IGNORED_DIRECTORIES = {
    ".git",
    ".pio",
    ".vs",
    ".vscode/.browse.c_cpp.db",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
_IGNORED_FILES = {".DS_Store", "Thumbs.db"}
