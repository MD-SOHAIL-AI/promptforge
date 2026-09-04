"""Unified PlatformIO integration layer for PromptForge AI.

All PlatformIO process execution is delegated to the injected
``SubprocessManager``. Firmware builds are delegated further to the existing
``FirmwareBuilder`` so artifact discovery, failure classification, and build
result construction retain a single canonical implementation.
"""

from __future__ import annotations

import asyncio
import configparser
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ..runtime.result import BuildResult
from ..runtime.subprocess_mgr import (
    ProcessConfig,
    ProcessResult,
    SubprocessManager,
)
from ..tools.build_firmware import BuildConfig, FirmwareBuilder

__all__ = [
    "PlatformIOCommandError",
    "PlatformIOEnvironment",
    "PlatformIOError",
    "PlatformIOProject",
    "PlatformIOProjectError",
    "PlatformIOResponseError",
    "PlatformIOService",
]

_PLATFORMIO_INI = "platformio.ini"
_UNRESOLVED_INTERPOLATION_RE = re.compile(r"\$\{[^}]+\}")


class PlatformIOError(RuntimeError):
    """Base error raised by the PlatformIO integration layer."""


class PlatformIOProjectError(PlatformIOError, ValueError):
    """A PlatformIO project or environment selection is invalid."""


class PlatformIOCommandError(PlatformIOError):
    """A PlatformIO command could not be started or completed successfully."""

    def __init__(
        self,
        message: str,
        *,
        result: ProcessResult | None = None,
        command: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.result = result
        self.command = tuple(command)


class PlatformIOResponseError(PlatformIOError):
    """PlatformIO returned successful but malformed machine-readable output."""


@dataclass(frozen=True, slots=True)
class PlatformIOEnvironment:
    """Resolved configuration of one ``[env:<name>]`` section."""

    name: str
    board: str
    framework: str
    platform: str

    def __post_init__(self) -> None:
        _validate_text(self.name, field_name="name")
        for field_name in ("board", "framework", "platform"):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise ValueError(f"{field_name} must be a string")
            if "\x00" in value:
                raise ValueError(f"{field_name} cannot contain NUL characters")

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "board": self.board,
            "framework": self.framework,
            "platform": self.platform,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PlatformIOEnvironment:
        values = _exact_schema(
            data,
            expected={"name", "board", "framework", "platform"},
            label="PlatformIO environment",
        )
        return cls(**values)


@dataclass(frozen=True, slots=True)
class PlatformIOProject:
    """Validated PlatformIO project manifest and resolved environments."""

    project_path: str
    platformio_ini: str
    environments: tuple[PlatformIOEnvironment, ...]

    def __post_init__(self) -> None:
        _validate_text(self.project_path, field_name="project_path")
        _validate_text(self.platformio_ini, field_name="platformio_ini")
        try:
            environments = tuple(self.environments)
        except TypeError as exc:
            raise ValueError("environments must be iterable") from exc
        if not environments:
            raise ValueError("environments must contain at least one environment")
        if any(
            not isinstance(environment, PlatformIOEnvironment)
            for environment in environments
        ):
            raise ValueError(
                "environments must contain only PlatformIOEnvironment values"
            )
        names = [environment.name for environment in environments]
        if len(set(names)) != len(names):
            raise ValueError("environment names must be unique")
        object.__setattr__(self, "environments", environments)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_path": self.project_path,
            "platformio_ini": self.platformio_ini,
            "environments": [item.to_dict() for item in self.environments],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PlatformIOProject:
        values = _exact_schema(
            data,
            expected={"project_path", "platformio_ini", "environments"},
            label="PlatformIO project",
        )
        raw_environments = values["environments"]
        if not isinstance(raw_environments, Sequence) or isinstance(
            raw_environments,
            (str, bytes, bytearray),
        ):
            raise ValueError("environments must be a sequence")
        return cls(
            project_path=values["project_path"],
            platformio_ini=values["platformio_ini"],
            environments=tuple(
                PlatformIOEnvironment.from_dict(item)
                for item in raw_environments
            ),
        )


class PlatformIOService:
    """Validate projects and execute PlatformIO through one process boundary."""

    def __init__(
        self,
        subprocess_mgr: SubprocessManager,
        *,
        executable: str = "pio",
        timeout_s: float = 180.0,
        env: Mapping[str, str] | None = None,
        session_id: str | None = None,
    ) -> None:
        if not callable(getattr(subprocess_mgr, "run", None)):
            raise ValueError("subprocess_mgr must provide an async run method")
        _validate_text(executable, field_name="executable")
        if (
            not isinstance(timeout_s, (int, float))
            or isinstance(timeout_s, bool)
            or not math.isfinite(float(timeout_s))
            or timeout_s <= 0
        ):
            raise ValueError("timeout_s must be a positive finite number")
        if session_id is not None:
            _validate_text(session_id, field_name="session_id")
        self._subprocess_mgr = subprocess_mgr
        self._executable = executable
        self._timeout_s = float(timeout_s)
        self._env = _validate_env(env or {})
        self._session_id = session_id

    def validate_project(
        self,
        project_path: str | Path,
    ) -> PlatformIOProject:
        """Validate and inspect a PlatformIO project without executing it."""

        root, ini_path, parser = _load_project(project_path)
        environments = _discover_from_parser(parser)
        _validate_source_directory(parser, root)
        return PlatformIOProject(
            project_path=str(root),
            platformio_ini=str(ini_path),
            environments=environments,
        )

    def discover_environments(
        self,
        project_path: str | Path,
    ) -> tuple[PlatformIOEnvironment, ...]:
        """Return resolved environments in declaration order."""

        return self.validate_project(project_path).environments

    async def build(
        self,
        project: str | Path | BuildConfig,
        *,
        environment: str | None = None,
        board: str | None = None,
        timeout_s: float | None = None,
        extra_args: Sequence[str] = (),
    ) -> BuildResult:
        """Build through the canonical ``FirmwareBuilder`` implementation."""

        if isinstance(project, BuildConfig):
            if any(
                value is not None
                for value in (environment, board, timeout_s)
            ) or tuple(extra_args):
                raise ValueError(
                    "build overrides cannot be used with an existing BuildConfig"
                )
            config = project
            self.validate_project(config.project_dir)
            _validate_environment_selection(
                self.discover_environments(config.project_dir),
                config.environment,
            )
        else:
            validated = self.validate_project(project)
            _validate_environment_selection(validated.environments, environment)
            config = BuildConfig(
                project_dir=validated.project_path,
                environment=environment,
                board=board,
                timeout_s=_validated_timeout(timeout_s, self._timeout_s),
                executable=self._executable,
                extra_args=tuple(extra_args),
                env=dict(self._env),
                session_id=self._session_id,
            )
        return await FirmwareBuilder(self._subprocess_mgr).build(config)

    async def clean(
        self,
        project_path: str | Path,
        *,
        environment: str | None = None,
        timeout_s: float | None = None,
    ) -> ProcessResult:
        """Run PlatformIO's built-in ``clean`` target for a project."""

        project = self.validate_project(project_path)
        _validate_environment_selection(project.environments, environment)
        args = [
            self._executable,
            "run",
            "--project-dir",
            project.project_path,
            "--target",
            "clean",
        ]
        if environment is not None:
            args.extend(["--environment", environment.strip()])
        return await self._run(
            args,
            cwd=project.project_path,
            timeout_s=timeout_s,
        )

    async def install_dependencies(
        self,
        project_path: str | Path,
        *,
        environment: str | None = None,
        timeout_s: float | None = None,
        force: bool = False,
    ) -> ProcessResult:
        """Install dependencies declared by a PlatformIO project."""

        if not isinstance(force, bool):
            raise ValueError("force must be a boolean")
        project = self.validate_project(project_path)
        _validate_environment_selection(project.environments, environment)
        args = [
            self._executable,
            "pkg",
            "install",
            "--project-dir",
            project.project_path,
        ]
        if environment is not None:
            args.extend(["--environment", environment.strip()])
        if force:
            args.append("--force")
        return await self._run(
            args,
            cwd=project.project_path,
            timeout_s=timeout_s,
        )

    async def list_boards(
        self,
        filter_text: str | None = None,
        *,
        installed_only: bool = False,
        timeout_s: float | None = None,
    ) -> tuple[Mapping[str, Any], ...]:
        """Return PlatformIO board records from ``pio boards --json-output``."""

        if filter_text is not None:
            _validate_text(filter_text, field_name="filter_text")
        if not isinstance(installed_only, bool):
            raise ValueError("installed_only must be a boolean")
        args = [self._executable, "boards", "--json-output"]
        if installed_only:
            args.append("--installed")
        if filter_text is not None:
            args.append(filter_text.strip())
        result = await self._run(args, timeout_s=timeout_s)
        if not result.success:
            raise PlatformIOCommandError(
                _failure_message("board discovery", result),
                result=result,
                command=args,
            )
        try:
            payload = json.loads(result.stdout_text)
        except json.JSONDecodeError as exc:
            raise PlatformIOResponseError(
                "PlatformIO board discovery returned invalid JSON"
            ) from exc
        if not isinstance(payload, list):
            raise PlatformIOResponseError(
                "PlatformIO board discovery must return a JSON list"
            )
        boards: list[Mapping[str, Any]] = []
        for index, item in enumerate(payload):
            if not isinstance(item, Mapping):
                raise PlatformIOResponseError(
                    f"PlatformIO board at index {index} is not an object"
                )
            boards.append(_freeze_json_mapping(item, path=f"boards[{index}]"))
        return tuple(boards)

    async def _run(
        self,
        args: Sequence[str],
        *,
        cwd: str | None = None,
        timeout_s: float | None = None,
    ) -> ProcessResult:
        config = ProcessConfig(
            args=list(args),
            cwd=cwd,
            env=dict(self._env),
            timeout_s=_validated_timeout(timeout_s, self._timeout_s),
            session_id=self._session_id,
        )
        try:
            return await self._subprocess_mgr.run(config)
        except asyncio.CancelledError:
            raise
        except OSError as exc:
            raise PlatformIOCommandError(
                f"PlatformIO could not be started: {exc}",
                command=args,
            ) from exc
        except Exception as exc:
            raise PlatformIOCommandError(
                f"Unexpected PlatformIO command failure: {exc}",
                command=args,
            ) from exc


def _load_project(
    project_path: str | Path,
) -> tuple[Path, Path, configparser.RawConfigParser]:
    if not isinstance(project_path, (str, Path)):
        raise PlatformIOProjectError("project_path must be a string or Path")
    try:
        root = Path(project_path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise PlatformIOProjectError(f"invalid project path: {exc}") from exc
    if not root.exists():
        raise PlatformIOProjectError(
            f"project directory does not exist: {root}"
        )
    if not root.is_dir():
        raise PlatformIOProjectError(
            f"project path is not a directory: {root}"
        )
    ini_path = root / _PLATFORMIO_INI
    if not ini_path.is_file():
        raise PlatformIOProjectError(f"missing {_PLATFORMIO_INI}: {ini_path}")

    parser = configparser.RawConfigParser(
        strict=True,
        inline_comment_prefixes=(";", "#"),
    )
    try:
        with ini_path.open("r", encoding="utf-8-sig") as handle:
            parser.read_file(handle)
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise PlatformIOProjectError(
            f"invalid {_PLATFORMIO_INI}: {exc}"
        ) from exc
    return root, ini_path, parser


def _discover_from_parser(
    parser: configparser.RawConfigParser,
) -> tuple[PlatformIOEnvironment, ...]:
    names = tuple(
        section[4:].strip()
        for section in parser.sections()
        if section.startswith("env:") and section[4:].strip()
    )
    if not names:
        raise PlatformIOProjectError(
            f"{_PLATFORMIO_INI} contains no [env:<name>] sections"
        )
    return tuple(
        PlatformIOEnvironment(
            name=name,
            board=_resolve_option(parser, name, "board"),
            framework=_resolve_option(parser, name, "framework"),
            platform=_resolve_option(parser, name, "platform"),
        )
        for name in names
    )


def _resolve_option(
    parser: configparser.RawConfigParser,
    environment: str,
    option: str,
    seen: set[str] | None = None,
) -> str:
    section = f"env:{environment}"
    visited = set(seen or ())
    if section in visited:
        raise PlatformIOProjectError(
            f"cyclic environment inheritance involving {section}"
        )
    visited.add(section)
    if parser.has_option(section, option):
        return parser.get(section, option, raw=True).strip()
    if parser.has_option(section, "extends"):
        parents = parser.get(section, "extends", raw=True)
        for parent in re.split(r"[\s,]+", parents):
            parent = parent.strip()
            if not parent:
                continue
            parent_name = parent[4:] if parent.startswith("env:") else parent
            parent_section = f"env:{parent_name}"
            if not parser.has_section(parent_section):
                raise PlatformIOProjectError(
                    f"environment {environment!r} extends undefined "
                    f"environment {parent_name!r}"
                )
            value = _resolve_option(
                parser,
                parent_name,
                option,
                visited,
            )
            if value:
                return value
    if parser.has_option("env", option):
        return parser.get("env", option, raw=True).strip()
    return ""


def _validate_source_directory(
    parser: configparser.RawConfigParser,
    project_root: Path,
) -> None:
    raw_source = "src"
    if parser.has_option("platformio", "src_dir"):
        raw_source = parser.get("platformio", "src_dir", raw=True).strip()
        if not raw_source:
            raise PlatformIOProjectError("platformio.src_dir cannot be empty")
    if _UNRESOLVED_INTERPOLATION_RE.search(raw_source):
        return
    source = Path(raw_source).expanduser()
    if not source.is_absolute():
        source = project_root / source
    if not source.resolve().is_dir():
        raise PlatformIOProjectError(
            f"configured source directory does not exist: {source.resolve()}"
        )


def _validate_environment_selection(
    environments: Sequence[PlatformIOEnvironment],
    environment: str | None,
) -> None:
    if environment is None:
        return
    _validate_text(environment, field_name="environment")
    normalized = environment.strip()
    names = {item.name for item in environments}
    if normalized not in names:
        raise PlatformIOProjectError(
            f"environment {normalized!r} is not defined in {_PLATFORMIO_INI}"
        )


def _validated_timeout(value: float | None, default: float) -> float:
    if value is None:
        return default
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or value <= 0
    ):
        raise ValueError("timeout_s must be a positive finite number")
    return float(value)


def _validate_env(env: Mapping[str, str]) -> Mapping[str, str]:
    if not isinstance(env, Mapping):
        raise ValueError("env must be a mapping")
    copied: dict[str, str] = {}
    for key, value in env.items():
        if (
            not isinstance(key, str)
            or not isinstance(value, str)
            or not key
            or "\x00" in key
            or "\x00" in value
        ):
            raise ValueError("env must contain NUL-free string keys and values")
        copied[key] = value
    return MappingProxyType(copied)


def _freeze_json_mapping(
    value: Mapping[object, Any],
    *,
    path: str,
) -> Mapping[str, Any]:
    frozen: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise PlatformIOResponseError(f"{path} contains a non-string key")
        frozen[key] = _freeze_json_value(item, path=f"{path}.{key}")
    return MappingProxyType(frozen)


def _freeze_json_value(value: Any, *, path: str) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return _freeze_json_mapping(value, path=path)
    if isinstance(value, list):
        return tuple(
            _freeze_json_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        )
    raise PlatformIOResponseError(f"{path} contains an unsupported JSON value")


def _failure_message(operation: str, result: ProcessResult) -> str:
    if result.timed_out:
        return f"PlatformIO {operation} timed out after {result.elapsed_s:.1f}s"
    detail = result.stderr_text.strip() or result.stdout_text.strip()
    suffix = f": {detail[:1000]}" if detail else ""
    return (
        f"PlatformIO {operation} failed with exit code {result.returncode}"
        f"{suffix}"
    )


def _validate_text(value: object, *, field_name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    if "\x00" in value:
        raise ValueError(f"{field_name} cannot contain NUL characters")


def _exact_schema(
    data: Mapping[str, Any],
    *,
    expected: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} must be a mapping")
    supplied = set(data)
    missing = expected - supplied
    unknown = supplied - expected
    if missing:
        raise ValueError(
            f"{label} is missing required fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise ValueError(
            f"{label} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    return dict(data)
