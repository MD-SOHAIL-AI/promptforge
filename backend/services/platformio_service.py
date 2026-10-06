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
import os
import re
import shutil
import sys
import time
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
    "PlatformIOStatus",
    "resolve_platformio_executable",
]

_PLATFORMIO_INI = "platformio.ini"
_UNRESOLVED_INTERPOLATION_RE = re.compile(r"\$\{[^}]+\}")
_DEFAULT_PLATFORMIO_EXECUTABLE = "pio"
_PLATFORMIO_EXECUTABLE_ENV = ("FORGEX_PLATFORMIO_EXECUTABLE", "PLATFORMIO_EXECUTABLE")


@dataclass(frozen=True, slots=True)
class PlatformIOStatus:
    """Host PlatformIO readiness for health checks and setup guidance."""

    available: bool
    executable: str | None
    version: str | None
    reason: str
    message: str

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "available": self.available,
            "executable": self.executable,
            "version": self.version,
            "reason": self.reason,
            "message": self.message,
        }


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
        executable: str = _DEFAULT_PLATFORMIO_EXECUTABLE,
        timeout_s: float = 180.0,
        env: Mapping[str, str] | None = None,
        session_id: str | None = None,
        artifact_store: str | Path | None = None,
        native_test_runner: Any | None = None,
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
        resolved = resolve_platformio_executable(executable=executable)
        self._executable = resolved.executable or executable
        self._base_status = resolved
        self._timeout_s = float(timeout_s)
        self._env = _validate_env(env or {})
        self._session_id = session_id
        self._artifact_store = Path(artifact_store) if artifact_store else None
        # Host code injection only. The runner must isolate workspace writes,
        # network and physical devices; a process group/job is insufficient.
        self._native_test_runner = native_test_runner
        self._dependency_validation_cache: dict[str, tuple[str, ...]] = {}
        self._status_task: asyncio.Task | None = None
        self._cached_status: PlatformIOStatus | None = None
        self._status_checked_at = 0.0

    @property
    def executable(self) -> str:
        """PlatformIO command used for subprocess calls."""

        return self._executable

    async def status(self, *, timeout_s: float = 8.0) -> PlatformIOStatus:
        """Return host PlatformIO readiness without requiring a project."""

        if self._status_fresh():
            return self._cached_status
        return await asyncio.shield(self._start_status_probe(timeout_s))

    def _status_fresh(self) -> bool:
        return self._cached_status is not None and time.monotonic() - self._status_checked_at < (
            30.0 if self._cached_status.available else 5.0)

    def _start_status_probe(self, timeout_s: float = 8.0) -> asyncio.Task:
        if self._status_task is None or self._status_task.done():
            async def refresh():
                result = await self._probe_status(timeout_s=timeout_s)
                self._cached_status = result
                self._status_checked_at = time.monotonic()
                return result
            self._status_task = asyncio.create_task(refresh())
            # Health probes may have no waiter. Retrieve failures without
            # altering the exception delivered to an explicit status caller.
            self._status_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        return self._status_task

    async def health_status(self) -> PlatformIOStatus:
        """Liveness never waits for a compiler process or starts duplicate probes."""
        if not self._base_status.available:
            return self._base_status
        if not self._status_fresh():
            self._start_status_probe()
        if self._cached_status is not None:
            return self._cached_status
        return PlatformIOStatus(available=self._base_status.available,
            executable=self._base_status.executable, version=None, reason="checking",
            message="PlatformIO executable detected; readiness verification is pending.")

    async def close_status_probe(self) -> None:
        task = self._status_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _probe_status(self, *, timeout_s: float = 8.0) -> PlatformIOStatus:

        if not self._base_status.available or not self._base_status.executable:
            return self._base_status
        try:
            result = await self._run(
                [self._base_status.executable, "--version"],
                timeout_s=timeout_s,
            )
        except asyncio.CancelledError:
            raise
        except PlatformIOCommandError as exc:
            return PlatformIOStatus(
                available=False,
                executable=self._base_status.executable,
                version=None,
                reason="failed",
                message=f"PlatformIO executable was found but could not be started: {exc}",
            )
        if not result.success:
            detail = result.stderr_text.strip() or result.stdout_text.strip()
            suffix = f": {detail[:300]}" if detail else ""
            return PlatformIOStatus(
                available=False,
                executable=self._base_status.executable,
                version=None,
                reason="failed",
                message=f"PlatformIO readiness check failed{suffix}",
            )
        version = _parse_platformio_version_output(result.stdout_text, result.stderr_text)
        return PlatformIOStatus(
            available=True,
            executable=self._base_status.executable,
            version=version,
            reason="available",
            message=(
                f"PlatformIO is available at {self._base_status.executable}"
                + (f" ({version})" if version else "")
            ),
        )

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
                expected_project_root=validated.project_path,
                environment=environment,
                board=board,
                timeout_s=_validated_timeout(timeout_s, self._timeout_s),
                executable=self._executable,
                extra_args=tuple(extra_args),
                env=dict(self._env),
                session_id=self._session_id,
            )
        from .partition_preflight import PartitionPreflightError, partition_input
        from ..runtime.result import ResultStatus, FailureResult
        import tempfile
        started = time.monotonic()
        preflight = None
        preflight_executed = False
        validated_partitions = []
        try:
            _, _, parser = _load_project(config.project_dir)
            names = [item.name for item in self.discover_environments(config.project_dir)]
            defaults = re.split(r"[,\s]+", parser.get("platformio", "default_envs", fallback="", raw=True).strip())
            selected = [config.environment] if config.environment else ([name for name in defaults if name] or names)
            for name in selected:
                preflight = partition_input(config.project_dir, parser, name,
                    config.env.get("PLATFORMIO_CORE_DIR") or os.getenv("PLATFORMIO_CORE_DIR") or Path.home() / ".platformio")
                if preflight:
                    preflight_executed = True
                    with tempfile.TemporaryDirectory(prefix="forgex-partitions-") as temporary:
                        process = await self._subprocess_mgr.run(ProcessConfig(
                            args=[sys.executable, preflight["tool"], "--flash-size", preflight["flash_size"],
                                  "--offset", preflight["offset"], preflight["source"], str(Path(temporary) / "partitions.bin")],
                            cwd=str(config.project_dir), env=dict(config.env), session_id=config.session_id,
                            timeout_s=max(0.001, min(30.0, config.timeout_s - (time.monotonic() - started)))))
                    if not process.success:
                        raise PartitionPreflightError("PARTITION_TABLE_INVALID", (process.stderr_text or "Partition validation failed")[:2000])
                    validated_partitions.append(name)
        except PartitionPreflightError as exc:
            return BuildResult(success=False, status=ResultStatus.FAILED, message=str(exc),
                metadata={"error_code": exc.code, "preflight": {"success": False, "environment": config.environment}},
                failure=FailureResult(category="configuration_error", message=str(exc), retryable=False, stage="build",
                    metadata={"error_code": exc.code}))
        remaining = config.timeout_s - (time.monotonic() - started)
        if remaining <= 0:
            return BuildResult(success=False, status=ResultStatus.TIMEOUT, message="Build deadline expired during preflight",
                metadata={"error_code": "BUILD_TIMEOUT"})
        from dataclasses import replace
        result = await FirmwareBuilder(self._subprocess_mgr).build(replace(config, timeout_s=remaining) if preflight_executed else config)
        result.metadata["partition_preflight"] = {"success": bool(result.success or len(validated_partitions) == len(selected)),
            "environment": config.environment, "validated_environments": validated_partitions,
            "validation": "espressif_tool" if preflight_executed else "canonical_platformio_build"}
        metadata = dict(result.metadata)
        metadata["toolchain"] = {"executable": config.executable, "version": getattr(result, "toolchain_version", None),
                                  "environment": config.environment, "board": result.board}
        result.metadata.update(metadata)
        if result.success and self._artifact_store is not None:
            from .firmware_manifest import seal_platformio_build
            try:
                result.metadata["artifact_manifest"] = seal_platformio_build(
                    result, config.project_dir, config.environment, self._artifact_store,
                    core_dir=self._env.get("PLATFORMIO_CORE_DIR"))
                result.metadata["flash_ready"] = True
            except (OSError, ValueError) as exc:
                result.metadata.update(flash_ready=False, flash_readiness_error=str(exc))
        return result

    async def validate_dependencies(self, project: Path) -> dict[str, object]:
        """Resolve declared registry IDs before compiling, without guessing owners."""
        _, _, parser = _load_project(project)
        from backend.security.build_policy import validate_build_configuration
        validate_build_configuration(parser)
        declared = sorted({line.strip() for section in parser.sections()
            for line in parser.get(section, "lib_deps", fallback="", raw=True).splitlines() if line.strip()})
        if len(declared) > 32:
            raise ValueError("BUILD_DEPENDENCY_LIMIT")
        checked, missing = [], []
        for dependency in declared:
            identity = dependency.split("@", 1)[0].strip()
            if identity not in self._dependency_validation_cache:
                candidates = await self.search_libraries(identity, limit=5)
                # Network failures must not poison subsequent requests, and a
                # long-running backend must keep this cache bounded.
                if candidates:
                    if len(self._dependency_validation_cache) >= 128:
                        self._dependency_validation_cache.clear()
                    self._dependency_validation_cache[identity] = candidates
            else:
                candidates = self._dependency_validation_cache[identity]
            matches = [candidate for candidate in candidates if
                       (candidate if "/" in identity else candidate.split("/", 1)[-1]).casefold() == identity.casefold()]
            if len(matches) != 1:
                missing.append({"requested": dependency, "candidates": list(candidates)})
            else:
                checked.append(dependency)
        return {"valid": not missing, "dependencies": checked, "missing": missing,
                "source": "platformio_registry", "versions_verified": False}

    async def search_libraries(self, query: str, *, limit: int = 5) -> tuple[str, ...]:
        """Return bounded registry package IDs without trusting registry prose."""
        if not isinstance(query, str) or not 1 <= len(query) <= 100 or any(
            ord(char) < 32 or ord(char) == 127 for char in query
        ):
            raise ValueError("invalid PlatformIO library search query")
        result = await self._run(
            [self._executable, "pkg", "search", query], timeout_s=10.0
        )
        if not result.success:
            raise RuntimeError("DEPENDENCY_REGISTRY_UNAVAILABLE")
        packages: list[str] = []
        for line in result.stdout_text.splitlines():
            candidate = line.strip()
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}/[A-Za-z0-9][A-Za-z0-9_. +()-]{0,99}", candidate):
                continue
            if candidate.casefold() not in {item.casefold() for item in packages}:
                packages.append(candidate)
            if len(packages) >= max(1, min(limit, 5)):
                break
        return tuple(packages)

    async def test_native(self, project_path, *, environment, trusted=False):
        """Run project-controlled native code only through an isolated runner."""
        import shutil
        from backend.security.build_policy import validate_build_configuration
        if trusted is not True:
            raise ValueError("AGENT_PROJECT_TRUST_REQUIRED")
        root, _, parser = _load_project(project_path)
        validate_build_configuration(parser)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", environment):
            raise ValueError("TEST_ENVIRONMENT_INVALID")
        section = "env:" + environment
        if not parser.has_section(section) or parser.get(section, "platform", fallback="").strip() != "native":
            raise ValueError("DEVICE_TEST_REQUIRES_SEPARATE_HARDWARE_APPROVAL")
        if self._native_test_runner is None:
            return {"success": False, "available": False,
                    "error": "NATIVE_TEST_SANDBOX_UNAVAILABLE",
                    "message": "Native tests require an isolated runner that denies host device and network access."}
        if not shutil.which("gcc"):
            return {"success":False,"available":False,"error":"NATIVE_TOOLCHAIN_UNAVAILABLE"}
        if not root.joinpath("test").is_dir() or not any(root.joinpath("test").rglob("*.cpp")):
            return {"success":False,"available":True,"error":"NO_NATIVE_TESTS"}
        result = await self._native_test_runner.run(
            [self.executable, "test", "-e", environment], cwd=str(root), timeout_s=180)
        output = result.stdout_text + result.stderr_text
        # Exit zero also occurs when no tests run. Require the runner's result
        # summary before recording evidence of successful execution.
        plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
        summaries = re.findall(r"(\d+)\s+test cases?:\s*(?:(\d+)\s+failed,?\s*)?(?:(\d+)\s+skipped,?\s*)?(\d+)\s+succeeded", plain, flags=re.I)
        executed,failed,skipped,succeeded = (map(lambda item:int(item or 0),summaries[-1]) if summaries else (0,0,0,0))
        return {"success":result.returncode == 0 and not result.timed_out and executed > 0 and succeeded == executed and not failed and not skipped,"available":True,
                "test_cases_executed":executed,"test_cases_succeeded":succeeded,
                "environment":environment,"exit_code":result.returncode,"timed_out":result.timed_out,
                "output":output[-6000:],"hardware_used":False}

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


def resolve_platformio_executable(
    *,
    executable: str = _DEFAULT_PLATFORMIO_EXECUTABLE,
    repo_root: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    path: str | None = None,
) -> PlatformIOStatus:
    """Resolve PlatformIO local-first without mutating process PATH."""

    _validate_text(executable, field_name="executable")
    environment = env or os.environ
    if executable != _DEFAULT_PLATFORMIO_EXECUTABLE:
        return PlatformIOStatus(
            available=True,
            executable=executable,
            version=None,
            reason="available",
            message=f"Using configured PlatformIO executable: {executable}",
        )

    for key in _PLATFORMIO_EXECUTABLE_ENV:
        value = str(environment.get(key, "")).strip()
        if not value:
            continue
        return PlatformIOStatus(
            available=True,
            executable=value,
            version=None,
            reason="available",
            message=f"Using PlatformIO executable from {key}: {value}",
        )

    root = Path(repo_root).resolve() if repo_root is not None else Path(__file__).resolve().parents[2]
    candidates = (
        root / ".venv" / "Scripts" / "pio.exe",
        root / ".venv" / "Scripts" / "pio",
        root / ".venv" / "bin" / "pio",
        root / ".venv" / "bin" / "pio.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return PlatformIOStatus(
                available=True,
                executable=str(candidate),
                version=None,
                reason="available",
                message=f"Using repo-local PlatformIO executable: {candidate}",
            )

    path_value = path if path is not None else str(environment.get("PATH", ""))
    found = shutil.which("pio", path=path_value) or shutil.which("platformio", path=path_value)
    if found:
        return PlatformIOStatus(
            available=True,
            executable=found,
            version=None,
            reason="available",
            message=f"Using PlatformIO executable from PATH: {found}",
        )

    suffix = ".venv\\Scripts\\pio.exe" if sys.platform == "win32" else ".venv/bin/pio"
    return PlatformIOStatus(
        available=False,
        executable=None,
        version=None,
        reason="missing",
        message=f"PlatformIO is not installed or not on PATH. Run `npm run setup:platformio` to install repo-local PlatformIO at {suffix}.",
    )


def _parse_platformio_version_output(*parts: str) -> str | None:
    output = "\n".join(part.strip() for part in parts if part.strip())
    if not output:
        return None
    match = re.search(
        r"(?:PlatformIO\s+Core,\s+version\s+|PlatformIO\s+Core\s+)([^\s]+)",
        output,
        re.IGNORECASE,
    )
    if match:
        return match.group(1)
    return output.splitlines()[0].strip()[:128]


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
    from .partition_preflight import PartitionPreflightError, resolve_option
    try:
        return resolve_option(parser, environment, option)
    except PartitionPreflightError as exc:
        raise PlatformIOProjectError(str(exc).lower()) from exc


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
