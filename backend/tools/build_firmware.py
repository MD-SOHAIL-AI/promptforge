"""PlatformIO firmware build adapter for the PromptForge runtime.

This module owns one build attempt. It validates a PlatformIO project, invokes
PlatformIO exclusively through ``SubprocessManager``, locates the generated
firmware artifact, and returns the frozen ``BuildResult`` runtime contract.

It intentionally does not flash, inspect hardware, monitor serial output,
retry, update sessions, or orchestrate other runtime stages.
"""

from __future__ import annotations

import asyncio
import configparser
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

from backend.observability.diagnostics import emit_diagnostic_event
from backend.security.build_policy import validate_build_configuration

from ..runtime.failure_classifier import FailureClassification, classify_failure
from ..runtime.result import (
    BuildResult,
    FailureResult,
    ResultStatus,
)
from ..runtime.subprocess_mgr import (
    ProcessConfig,
    ProcessResult,
    SubprocessManager,
)

__all__ = [
    "BuildArtifact",
    "BuildConfig",
    "FirmwareBuilder",
    "build_firmware",
]

_PLATFORM = "platformio"
_PLATFORMIO_INI = "platformio.ini"
_DEFAULT_SOURCE_DIR = "src"
_DEFAULT_BUILD_OUTPUT_DIR = Path(".pio") / "build"
_ARTIFACT_PRIORITY = {
    ".bin": 0,
    ".hex": 1,
    ".uf2": 2,
    ".elf": 3,
}
_WARNING_RE = re.compile(r"\bwarning\s*:", re.IGNORECASE)
_VERSION_RE = re.compile(
    r"(?:PlatformIO\s+Core,\s+version\s+|PlatformIO\s+Core\s+)([^\s]+)",
    re.IGNORECASE,
)
_UNRESOLVED_INTERPOLATION_RE = re.compile(r"\$\{[^}]+\}")
_FORBIDDEN_TARGETS = {
    "upload",
    "uploadfs",
    "erase",
    "monitor",
    "program",
}
_SCOPE_CHANGING_OPTIONS = {
    "-d",
    "--project-dir",
    "-e",
    "--environment",
    "--project-conf",
}


class ProjectValidationError(ValueError):
    """Raised internally when a project cannot be safely built."""


@dataclass(frozen=True, slots=True)
class BuildConfig:
    """Immutable configuration for one PlatformIO build attempt."""

    project_dir: str | Path
    environment: Optional[str] = None
    board: Optional[str] = None
    timeout_s: float = 600.0 
    executable: str = "pio"
    extra_args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    session_id: Optional[str] = None
    version_timeout_s: float = 10.0
    expected_project_root: str | Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "extra_args", tuple(self.extra_args))
        object.__setattr__(self, "env", dict(self.env))

        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be greater than zero")
        if self.version_timeout_s <= 0:
            raise ValueError("version_timeout_s must be greater than zero")
        if not self.executable or "\x00" in self.executable:
            raise ValueError("executable must be a non-empty command")
        if self.environment is not None and (
            not self.environment.strip() or "\x00" in self.environment
        ):
            raise ValueError("environment must be a non-empty name")
        if self.board is not None and not self.board.strip():
            raise ValueError("board must be a non-empty identifier")
        if any(not isinstance(arg, str) or "\x00" in arg for arg in self.extra_args):
            raise ValueError("extra_args must contain NUL-free strings")
        if any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or "\x00" in key
            or "\x00" in value
            for key, value in self.env.items()
        ):
            raise ValueError("env must contain NUL-free string keys and values")
        if self.expected_project_root is not None:
            expected = Path(self.expected_project_root).expanduser()
            if not expected.is_absolute() or ".." in expected.parts:
                raise ValueError("expected_project_root must be an absolute canonical path")

        _validate_extra_args(self.extra_args)


@dataclass(frozen=True, slots=True)
class BuildArtifact:
    """A firmware artifact produced by a PlatformIO environment."""

    path: Path
    environment: str
    artifact_type: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class _ValidatedProject:
    root: Path
    build_root: Path
    parser: configparser.RawConfigParser
    environments: tuple[str, ...]
    selected_environment: Optional[str]
    board: str


class FirmwareBuilder:
    """Compile PlatformIO projects into structured ``BuildResult`` objects."""

    def __init__(self, subprocess_mgr: SubprocessManager) -> None:
        self._subprocess_mgr = subprocess_mgr

    async def build(self, config: BuildConfig) -> BuildResult:
        """Validate and execute exactly one PlatformIO build attempt."""
        started_monotonic = time.monotonic()
        build_id = f"build-{uuid.uuid4().hex}"

        try:
            project = self._validate_project(config)
        except (OSError, configparser.Error, ProjectValidationError) as exc:
            return self._exception_result(
                config=config,
                exc=exc,
                started_monotonic=started_monotonic,
                build_id=build_id,
                message=f"Invalid PlatformIO project: {exc}",
                metadata={"validation_failed": True},
            )

        version = ""
        version_probe_failed = False
        try:
            version_result = await self._subprocess_mgr.run(
                ProcessConfig(
                    args=[config.executable, "--version"],
                    cwd=str(project.root),
                    env=dict(config.env),
                    timeout_s=config.version_timeout_s,
                    session_id=config.session_id,
                )
            )
            if version_result.success:
                version = _parse_toolchain_version(version_result)
            else:
                version_probe_failed = True
        except asyncio.CancelledError:
            raise
        except OSError as exc:
            return self._exception_result(
                config=config,
                exc=exc,
                started_monotonic=started_monotonic,
                build_id=build_id,
                message=_platformio_start_failure_message(exc),
                board=project.board,
                metadata={"validation_failed": False},
            )
        except Exception:
            # Version discovery is supplemental. A manager/instrumentation
            # failure here must not suppress an otherwise valid build attempt.
            version_probe_failed = True

        try:
            before_build = _snapshot_artifacts(
                project.build_root, project.selected_environment
            )
        except OSError as exc:
            return self._exception_result(
                config=config,
                exc=exc,
                started_monotonic=started_monotonic,
                build_id=build_id,
                message=f"Could not inspect PlatformIO build directory: {exc}",
                board=project.board,
                toolchain_version=version,
                metadata={"build_root": str(project.build_root)},
            )
        process_config = ProcessConfig(
            args=self._build_args(config, project.root),
            cwd=str(project.root),
            env=dict(config.env),
            timeout_s=config.timeout_s,
            session_id=config.session_id,
        )
        _emit_build_event(
            "build.started",
            build_id=build_id,
            config=config,
            project=project,
            command=list(process_config.args),
        )

        try:
            process_result = await self._subprocess_mgr.run(process_config)
        except asyncio.CancelledError:
            raise
        except OSError as exc:
            return self._exception_result(
                config=config,
                exc=exc,
                started_monotonic=started_monotonic,
                build_id=build_id,
                message=_platformio_start_failure_message(exc),
                board=project.board,
                toolchain_version=version,
                metadata={"command": process_config.args},
            )
        except Exception as exc:
            return self._exception_result(
                config=config,
                exc=exc,
                started_monotonic=started_monotonic,
                build_id=build_id,
                message=f"Unexpected PlatformIO build error: {exc}",
                board=project.board,
                toolchain_version=version,
                metadata={"command": process_config.args},
            )

        warnings_count = _count_warnings(process_result)
        common_metadata = {
            "command": list(process_config.args),
            "environment": project.selected_environment,
            "configured_environments": list(project.environments),
            "output_truncated": process_result.output_truncated,
            "build_root": str(project.build_root),
            "version_probe_failed": version_probe_failed,
        }
        _emit_build_output(build_id=build_id, config=config, process_result=process_result)

        if not process_result.success:
            classification = classify_failure(
                stderr=process_result.stderr_text,
                stdout=process_result.stdout_text,
                process_result=process_result,
                stage="build",
                tool=_PLATFORM,
            )
            status = (
                ResultStatus.TIMEOUT
                if process_result.timed_out
                else ResultStatus.FAILED
            )
            message = (
                f"PlatformIO build timed out after {process_result.elapsed_s:.1f}s"
                if process_result.timed_out
                else f"PlatformIO build failed with exit code {process_result.returncode}"
            )
            _emit_build_event(
                "build.failed",
                build_id=build_id,
                config=config,
                project=project,
                command=list(process_config.args),
                duration_ms=_elapsed_ms(started_monotonic),
                status=status.value,
                message=message,
                returncode=process_result.returncode,
                warnings_count=warnings_count,
            )
            _emit_build_event(
                "build.completed",
                build_id=build_id,
                config=config,
                project=project,
                command=list(process_config.args),
                duration_ms=_elapsed_ms(started_monotonic),
                status=status.value,
                message=message,
                returncode=process_result.returncode,
                warnings_count=warnings_count,
            )
            return BuildResult(
                success=False,
                status=status,
                duration_ms=_elapsed_ms(started_monotonic),
                message=message,
                metadata=common_metadata,
                platform=_PLATFORM,
                board=project.board,
                toolchain_version=version,
                warnings_count=warnings_count,
                process_result=process_result,
                failure=_failure_from_classification(
                    classification,
                    raw_output=_combined_output(process_result),
                    exception_type=(
                        "ProcessTimeoutError"
                        if process_result.timed_out
                        else "ExecutionError"
                    ),
                ),
            )

        try:
            artifacts = _find_artifacts(
                project.build_root,
                selected_environment=project.selected_environment,
                before_build=before_build,
            )
        except OSError as exc:
            return self._exception_result(
                config=config,
                exc=exc,
                started_monotonic=started_monotonic,
                build_id=build_id,
                message=f"Could not inspect generated firmware artifacts: {exc}",
                board=project.board,
                toolchain_version=version,
                metadata=common_metadata,
            )
        if not artifacts:
            message = (
                "PlatformIO completed successfully but produced no supported "
                "firmware artifact"
            )
            classification = classify_failure(
                stderr=f"ERROR: BuildError: {message}",
                stdout=process_result.stdout_text,
                process_result=process_result,
                stage="build",
                tool=_PLATFORM,
            )
            _emit_build_event(
                "build.failed",
                build_id=build_id,
                config=config,
                project=project,
                command=list(process_config.args),
                duration_ms=_elapsed_ms(started_monotonic),
                status=ResultStatus.FAILED.value,
                message=message,
                returncode=process_result.returncode,
                warnings_count=warnings_count,
            )
            _emit_build_event(
                "build.completed",
                build_id=build_id,
                config=config,
                project=project,
                command=list(process_config.args),
                duration_ms=_elapsed_ms(started_monotonic),
                status=ResultStatus.FAILED.value,
                message=message,
                returncode=process_result.returncode,
                warnings_count=warnings_count,
            )
            return BuildResult(
                success=False,
                status=ResultStatus.FAILED,
                duration_ms=_elapsed_ms(started_monotonic),
                message=message,
                metadata=common_metadata,
                platform=_PLATFORM,
                board=project.board,
                toolchain_version=version,
                warnings_count=warnings_count,
                process_result=process_result,
                failure=_failure_from_classification(
                    classification,
                    raw_output=_combined_output(process_result),
                    exception_type="ArtifactNotFoundError",
                ),
            )

        artifact = artifacts[0]
        metadata = {
            **common_metadata,
            "artifact_type": artifact.artifact_type,
            "artifact_environment": artifact.environment,
            "artifacts": [str(item.path) for item in artifacts],
        }
        _emit_build_event(
            "build.succeeded",
            build_id=build_id,
            config=config,
            project=project,
            command=list(process_config.args),
            duration_ms=_elapsed_ms(started_monotonic),
            status=ResultStatus.SUCCESS.value,
            message=f"PlatformIO build succeeded: {artifact.path.name}",
            firmware_path=str(artifact.path),
            artifact_type=artifact.artifact_type,
            warnings_count=warnings_count,
        )
        _emit_build_event(
            "build.completed",
            build_id=build_id,
            config=config,
            project=project,
            command=list(process_config.args),
            duration_ms=_elapsed_ms(started_monotonic),
            status=ResultStatus.SUCCESS.value,
            message=f"PlatformIO build succeeded: {artifact.path.name}",
            firmware_path=str(artifact.path),
            artifact_type=artifact.artifact_type,
            warnings_count=warnings_count,
        )
        return BuildResult(
            success=True,
            status=ResultStatus.SUCCESS,
            duration_ms=_elapsed_ms(started_monotonic),
            message=f"PlatformIO build succeeded: {artifact.path.name}",
            metadata=metadata,
            firmware_path=str(artifact.path),
            build_size_bytes=artifact.size_bytes,
            platform=_PLATFORM,
            board=project.board,
            toolchain_version=version,
            warnings_count=warnings_count,
            process_result=process_result,
        )

    @staticmethod
    def _validate_project(config: BuildConfig) -> _ValidatedProject:
        root = Path(config.project_dir).expanduser().resolve()
        if config.expected_project_root is not None and root != Path(config.expected_project_root).expanduser():
            raise ProjectValidationError("project directory changed after workspace authorization")
        if not root.exists():
            raise ProjectValidationError(f"project directory does not exist: {root}")
        if not root.is_dir():
            raise ProjectValidationError(f"project path is not a directory: {root}")

        project_file = root / _PLATFORMIO_INI
        if not project_file.is_file() or project_file.is_symlink():
            raise ProjectValidationError(
                f"missing {_PLATFORMIO_INI}: {project_file}"
            )

        parser = configparser.RawConfigParser(
            strict=True,
            inline_comment_prefixes=(";", "#"),
        )
        with project_file.open("r", encoding="utf-8-sig") as handle:
            parser.read_file(handle)
        try:
            validate_build_configuration(parser)
        except ValueError as exc:
            raise ProjectValidationError(str(exc)) from exc

        environments = tuple(
            section[4:]
            for section in parser.sections()
            if section.startswith("env:") and section[4:].strip()
        )
        if not environments:
            raise ProjectValidationError(
                f"{_PLATFORMIO_INI} contains no [env:<name>] sections"
            )

        requested_environment = (
            config.environment.strip() if config.environment is not None else None
        )
        if (
            requested_environment is not None
            and requested_environment not in environments
        ):
            raise ProjectValidationError(
                f"environment {requested_environment!r} is not defined in "
                f"{_PLATFORMIO_INI}"
            )

        selected_environment = requested_environment or _infer_environment(
            parser, environments
        )
        source_dir = _configured_source_dir(parser, root)
        if source_dir is not None and not source_dir.is_dir():
            raise ProjectValidationError(
                f"configured source directory does not exist: {source_dir}"
            )
        build_root = _configured_build_root(parser, root)

        board = (config.board or "").strip()
        if not board and selected_environment:
            board = _resolve_environment_option(
                parser, selected_environment, "board"
            )

        return _ValidatedProject(
            root=root,
            build_root=build_root,
            parser=parser,
            environments=environments,
            selected_environment=selected_environment,
            board=board,
        )

    @staticmethod
    def _build_args(config: BuildConfig, project_root: Path) -> list[str]:
        # PlatformIO's default ``run`` target already builds the program and
        # checks its size. Requesting both of its internal ``buildprog`` and
        # ``checkprogsize`` aliases explicitly creates a dependency cycle for
        # current Espressif32/SCons projects:
        # buildprog -> checkprogsize -> firmware.bin -> buildprog.
        # Keep the public CLI invocation here and let each platform define its
        # own correct build graph.
        args = [
            config.executable,
            "run",
            "--project-dir",
            str(project_root),
        ]
        if config.environment:
            args.extend(["--environment", config.environment.strip()])
        args.extend(config.extra_args)
        return args

    @staticmethod
    def _exception_result(
        *,
        config: BuildConfig,
        exc: BaseException,
        started_monotonic: float,
        build_id: str,
        message: str,
        board: str = "",
        toolchain_version: str = "",
        metadata: Optional[dict[str, object]] = None,
    ) -> BuildResult:
        classification = classify_failure(
            exception=exc,
            stage="build",
            tool=_PLATFORM,
        )
        _emit_build_event(
            "build.failed",
            build_id=build_id,
            config=config,
            project=None,
            command=list((metadata or {}).get("command", [])),
            duration_ms=_elapsed_ms(started_monotonic),
            status=ResultStatus.FAILED.value,
            message=message,
            error_code=type(exc).__name__,
        )
        return BuildResult(
            success=False,
            status=ResultStatus.FAILED,
            duration_ms=_elapsed_ms(started_monotonic),
            message=message,
            metadata=dict(metadata or {}),
            platform=_PLATFORM,
            board=board or (config.board or ""),
            toolchain_version=toolchain_version,
            failure=_failure_from_classification(
                classification,
                raw_output=str(exc),
                exception_type=type(exc).__name__,
            ),
        )


async def build_firmware(
    config: BuildConfig,
    subprocess_mgr: SubprocessManager,
) -> BuildResult:
    """Build through the canonical firmware builder implementation."""
    return await FirmwareBuilder(subprocess_mgr).build(config)


def _validate_extra_args(args: Sequence[str]) -> None:
    for arg in args:
        normalized = arg.strip().lower()
        if normalized in _SCOPE_CHANGING_OPTIONS or any(
            normalized.startswith(f"{option}=")
            for option in _SCOPE_CHANGING_OPTIONS
            if option.startswith("--")
        ):
            raise ValueError(
                f"extra_args cannot override project or environment scope: {arg!r}"
            )

        if normalized in {"-t", "--target"} or normalized.startswith("--target="):
            raise ValueError(
                "extra_args cannot select PlatformIO targets; the firmware "
                "builder only performs the default compile operation"
            )

        if normalized in _FORBIDDEN_TARGETS:
            raise ValueError(
                f"build target {arg!r} is not allowed by the firmware builder"
            )


def _platformio_start_failure_message(exc: OSError) -> str:
    return (
        "PlatformIO is not installed or not on PATH. "
        "Run `npm run setup:platformio`, then restart ForgeX. "
        f"Original error: {exc}"
    )


def _infer_environment(
    parser: configparser.RawConfigParser,
    environments: tuple[str, ...],
) -> Optional[str]:
    if parser.has_option("platformio", "default_envs"):
        raw = parser.get("platformio", "default_envs", raw=True)
        defaults = tuple(
            item.strip()
            for item in re.split(r"[\s,]+", raw)
            if item.strip()
        )
        if len(defaults) == 1 and defaults[0] in environments:
            return defaults[0]
    if len(environments) == 1:
        return environments[0]
    return None


def _configured_source_dir(
    parser: configparser.RawConfigParser,
    project_root: Path,
) -> Optional[Path]:
    raw_source_dir = _DEFAULT_SOURCE_DIR
    if parser.has_option("platformio", "src_dir"):
        raw_source_dir = parser.get("platformio", "src_dir", raw=True).strip()
        if not raw_source_dir:
            raise ProjectValidationError("platformio.src_dir cannot be empty")

    # PlatformIO can resolve ${sysenv.*} and ${section.option} expressions.
    # Leave those to PlatformIO rather than rejecting a valid dynamic path.
    if _UNRESOLVED_INTERPOLATION_RE.search(raw_source_dir):
        return None

    source_dir = Path(raw_source_dir).expanduser()
    if not source_dir.is_absolute():
        source_dir = project_root / source_dir
    return source_dir.resolve()


def _configured_build_root(
    parser: configparser.RawConfigParser,
    project_root: Path,
) -> Path:
    raw_build_dir = str(_DEFAULT_BUILD_OUTPUT_DIR)
    if parser.has_option("platformio", "build_dir"):
        raw_build_dir = parser.get("platformio", "build_dir", raw=True).strip()
        if not raw_build_dir:
            raise ProjectValidationError("platformio.build_dir cannot be empty")
        if _UNRESOLVED_INTERPOLATION_RE.search(raw_build_dir):
            raise ProjectValidationError(
                "platformio.build_dir must resolve to a static path for "
                "artifact discovery"
            )

    build_root = Path(raw_build_dir).expanduser()
    if not build_root.is_absolute():
        build_root = project_root / build_root
    return build_root.resolve()


def _resolve_environment_option(
    parser: configparser.RawConfigParser,
    environment: str,
    option: str,
    seen: Optional[set[str]] = None,
) -> str:
    section = f"env:{environment}"
    seen = set(seen or ())
    if section in seen or not parser.has_section(section):
        return ""
    seen.add(section)

    if parser.has_option(section, option):
        return parser.get(section, option, raw=True).strip()

    if parser.has_option(section, "extends"):
        parents = parser.get(section, "extends", raw=True)
        for parent in re.split(r"[\s,]+", parents):
            parent = parent.strip()
            if not parent:
                continue
            parent_environment = (
                parent[4:] if parent.startswith("env:") else parent
            )
            value = _resolve_environment_option(
                parser, parent_environment, option, seen
            )
            if value:
                return value
    return ""


def _snapshot_artifacts(
    build_root: Path,
    selected_environment: Optional[str],
) -> dict[Path, tuple[int, int]]:
    return {
        artifact.path: (
            artifact.path.stat().st_mtime_ns,
            artifact.size_bytes,
        )
        for artifact in _scan_artifacts(build_root, selected_environment)
    }


def _find_artifacts(
    build_root: Path,
    *,
    selected_environment: Optional[str],
    before_build: Mapping[Path, tuple[int, int]],
) -> list[BuildArtifact]:
    artifacts = _scan_artifacts(build_root, selected_environment)

    def sort_key(artifact: BuildArtifact) -> tuple[int, int, int, str]:
        stat = artifact.path.stat()
        previous = before_build.get(artifact.path)
        # The pre-build snapshot is the authoritative boundary. Comparing a
        # timestamp to a wall-clock build start is unreliable on filesystems with
        # coarse or rounded mtimes and can promote a stale artifact created
        # immediately before the build. A missing or changed snapshot entry
        # deterministically identifies output from this build attempt.
        current = previous is None or previous != (
            stat.st_mtime_ns,
            stat.st_size,
        )
        return (
            0 if current else 1,
            _ARTIFACT_PRIORITY[artifact.path.suffix.lower()],
            -stat.st_mtime_ns,
            str(artifact.path).lower(),
        )

    return sorted(artifacts, key=sort_key)


def _scan_artifacts(
    build_root: Path,
    selected_environment: Optional[str],
) -> list[BuildArtifact]:
    if not build_root.is_dir():
        return []

    environment_dirs: list[Path]
    if selected_environment:
        environment_dirs = [build_root / selected_environment]
    else:
        environment_dirs = sorted(
            (path for path in build_root.iterdir() if path.is_dir()),
            key=lambda path: path.name.lower(),
        )

    artifacts: list[BuildArtifact] = []
    for environment_dir in environment_dirs:
        if not environment_dir.is_dir():
            continue
        for path in environment_dir.iterdir():
            if (
                path.is_file()
                and path.stem.lower() == "firmware"
                and path.suffix.lower() in _ARTIFACT_PRIORITY
            ):
                resolved = path.resolve()
                try:
                    resolved.relative_to(environment_dir.resolve())
                except ValueError:
                    continue
                artifacts.append(
                    BuildArtifact(
                        path=resolved,
                        environment=environment_dir.name,
                        artifact_type=path.suffix.lower().lstrip("."),
                        size_bytes=path.stat().st_size,
                    )
                )
    return artifacts


def _parse_toolchain_version(result: ProcessResult) -> str:
    output = "\n".join(
        part.strip()
        for part in (result.stdout_text, result.stderr_text)
        if part.strip()
    )
    match = _VERSION_RE.search(output)
    if match:
        return match.group(1)
    return output.splitlines()[0].strip()[:128] if output else ""


def _count_warnings(result: ProcessResult) -> int:
    return len(_WARNING_RE.findall(_combined_output(result)))


def _combined_output(result: ProcessResult) -> str:
    return "\n".join(
        part for part in (result.stderr_text, result.stdout_text) if part
    )


def _emit_build_event(
    event: str,
    *,
    build_id: str,
    config: BuildConfig,
    project: _ValidatedProject | None,
    command: Sequence[str],
    duration_ms: int | None = None,
    status: str | None = None,
    message: str | None = None,
    returncode: int | None = None,
    warnings_count: int | None = None,
    firmware_path: str | None = None,
    artifact_type: str | None = None,
    error_code: str | None = None,
) -> None:
    fields: dict[str, object] = {
        "build_id": build_id,
        "session_id": config.session_id,
        "project_id": str(Path(config.project_dir)),
        "command": list(command),
        "environment": config.environment or (project.selected_environment if project else None),
        "board": config.board or (project.board if project else None),
    }
    if duration_ms is not None:
        fields["duration_ms"] = duration_ms
    if status is not None:
        fields["status"] = status
    if returncode is not None:
        fields["returncode"] = returncode
    if warnings_count is not None:
        fields["warnings_count"] = warnings_count
    if firmware_path:
        fields["firmware_path"] = firmware_path
    if artifact_type:
        fields["artifact_type"] = artifact_type
    if error_code:
        fields["error_code"] = error_code
    emit_diagnostic_event(
        component="build",
        event=event,
        level="ERROR" if event == "build.failed" else "INFO",
        message=message,
        **fields,
    )


def _emit_build_output(*, build_id: str, config: BuildConfig, process_result: ProcessResult) -> None:
    if process_result.stdout_text:
        emit_diagnostic_event(
            component="build",
            event="build.stdout",
            level="DEBUG",
            build_id=build_id,
            session_id=config.session_id,
            project_id=str(Path(config.project_dir)),
            status="captured",
            output=process_result.stdout_text[:16_000],
            output_truncated=process_result.output_truncated or len(process_result.stdout_text) > 16_000,
        )
    if process_result.stderr_text:
        emit_diagnostic_event(
            component="build",
            event="build.stderr",
            level="DEBUG" if process_result.success else "ERROR",
            build_id=build_id,
            session_id=config.session_id,
            project_id=str(Path(config.project_dir)),
            status="captured",
            output=process_result.stderr_text[:16_000],
            output_truncated=process_result.output_truncated or len(process_result.stderr_text) > 16_000,
        )


def _failure_from_classification(
    classification: FailureClassification,
    *,
    raw_output: str,
    exception_type: str,
) -> FailureResult:
    return FailureResult(
        category=classification.category.value,
        message=classification.message,
        retryable=classification.retryable,
        stage="build",
        exception_type=exception_type,
        raw_output=raw_output,
        metadata={
            "severity": classification.severity.value,
            "confidence": classification.confidence,
        },
    )


def _elapsed_ms(started_monotonic: float) -> int:
    return max(0, int((time.monotonic() - started_monotonic) * 1000))
