"""Wokwi virtual hardware execution backend for PromptForge.

The simulator combines the virtual equivalents of firmware flashing and
serial observation in one bounded operation. It creates an isolated Wokwi
project, invokes ``wokwi-cli`` only through ``SubprocessManager``, and returns
the shared immutable ``SimulationResult`` contract.

Example::

    result = await simulate_firmware(
        artifact,
        BoardType.ESP32,
        SimulationConfig(timeout_s=10),
        subprocess_mgr,
    )
    for line in result.serial_output:
        print(line)

This module does not retry, update sessions, orchestrate workflows, access
physical hardware, or implement a serial transport.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..runtime.failure_classifier import (
    FailureCategory,
    FailureClassification,
    classify_failure,
)
from ..runtime.result import (
    FailureResult,
    ResultStatus,
    SimulationArtifact,
    SimulationResult,
)
from ..runtime.subprocess_mgr import (
    ProcessConfig,
    ProcessResult,
    SubprocessManager,
)
from .board_detector import BoardType
from .build_firmware import BuildArtifact

__all__ = [
    "SimulationArtifact",
    "SimulationConfig",
    "SimulationResult",
    "WokwiSimulator",
    "simulate_firmware",
]

_TOOL = "wokwi-cli"
_STAGE = "simulate"
_TOKEN_ENV = "WOKWI_CLI_TOKEN"
_SUPPORTED_BOARDS = {
    BoardType.ESP32: "board-esp32-devkit-c-v4",
    BoardType.ESP32_S3: "board-esp32-s3-devkitc-1",
    BoardType.ESP32_C3: "board-esp32-c3-devkitm-1",
}
_SUPPORTED_SUFFIXES = {".bin", ".uf2", ".elf"}
_KNOWN_BOARD_PARTS = frozenset(_SUPPORTED_BOARDS.values())
_HOST_TIMEOUT_GRACE_S = 15.0
_PROBE_TIMEOUT_S = 10.0
_LINT_TIMEOUT_S = 30.0


@dataclass(frozen=True, slots=True)
class SimulationConfig:
    """Immutable policy for one Wokwi simulation attempt."""

    timeout_s: float = 30.0
    collect_serial_output: bool = True
    max_output_lines: int = 2_000
    stop_on_error: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.timeout_s, (int, float)) or isinstance(
            self.timeout_s, bool
        ):
            raise ValueError("timeout_s must be a number")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be greater than zero")
        if not math.isfinite(self.timeout_s):
            raise ValueError("timeout_s must be finite")
        if not isinstance(self.collect_serial_output, bool):
            raise ValueError("collect_serial_output must be a boolean")
        if not isinstance(self.max_output_lines, int) or isinstance(
            self.max_output_lines, bool
        ):
            raise ValueError("max_output_lines must be an integer")
        if self.max_output_lines <= 0:
            raise ValueError("max_output_lines must be greater than zero")
        if not isinstance(self.stop_on_error, bool):
            raise ValueError("stop_on_error must be a boolean")


class WokwiSimulator:
    """Generate and execute isolated Wokwi projects for ESP32 firmware."""

    def __init__(self, subprocess_mgr: SubprocessManager) -> None:
        self._subprocess_mgr = subprocess_mgr

    async def simulate(
        self,
        artifact: BuildArtifact,
        board_type: BoardType,
        config: SimulationConfig,
    ) -> SimulationResult:
        """Validate and execute exactly one virtual hardware run."""
        started = time.monotonic()
        simulation_artifact: Optional[SimulationArtifact] = None

        try:
            source_project, firmware_path = _validate_inputs(
                artifact, board_type
            )
            token = _wokwi_token()
            simulation_artifact = _prepare_project(
                source_project=source_project,
                firmware_path=firmware_path,
                board_type=board_type,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _exception_result(
                started=started,
                board_type=board_type,
                exc=exc,
                signal=str(exc),
                classifier_stage=_classifier_stage_for_exception(exc),
                simulation_artifact=simulation_artifact,
                include_exception=False,
            )

        env = {_TOKEN_ENV: token}
        project_path = simulation_artifact.project_path

        try:
            probe_result = await self._subprocess_mgr.run(
                ProcessConfig(
                    args=[_TOOL, "--help"],
                    cwd=project_path,
                    env=env,
                    timeout_s=min(config.timeout_s, _PROBE_TIMEOUT_S),
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _exception_result(
                started=started,
                board_type=board_type,
                exc=exc,
                signal=f"{_TOOL} not installed or unavailable: {exc}",
                classifier_stage="build",
                simulation_artifact=simulation_artifact,
                include_exception=True,
            )

        if not probe_result.success:
            return _process_failure(
                started=started,
                board_type=board_type,
                process_result=probe_result,
                simulation_artifact=simulation_artifact,
                message="Wokwi CLI is unavailable",
                classifier_stage="build",
                signal_prefix=f"{_TOOL} not installed or unavailable",
                exception_type="ToolchainUnavailableError",
            )

        try:
            lint_result = await self._subprocess_mgr.run(
                ProcessConfig(
                    args=[_TOOL, "lint"],
                    cwd=project_path,
                    env=env,
                    timeout_s=min(
                        max(config.timeout_s, _PROBE_TIMEOUT_S),
                        _LINT_TIMEOUT_S,
                    ),
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _exception_result(
                started=started,
                board_type=board_type,
                exc=exc,
                signal=f"Wokwi project validation failed: {exc}",
                classifier_stage="build",
                simulation_artifact=simulation_artifact,
                include_exception=True,
            )

        if not lint_result.success:
            return _process_failure(
                started=started,
                board_type=board_type,
                process_result=lint_result,
                simulation_artifact=simulation_artifact,
                message="Wokwi project validation failed",
                classifier_stage="build",
                signal_prefix="ERROR: BuildError: invalid Wokwi project",
                exception_type="InvalidWokwiProjectError",
            )

        timeout_ms = max(1, int(config.timeout_s * 1_000))
        process_config = ProcessConfig(
            args=[
                _TOOL,
                project_path,
                "--quiet",
                "--timeout",
                str(timeout_ms),
                "--timeout-exit-code",
                "0",
            ],
            cwd=project_path,
            env=env,
            timeout_s=config.timeout_s + _HOST_TIMEOUT_GRACE_S,
        )

        try:
            process_result = await self._subprocess_mgr.run(process_config)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return _exception_result(
                started=started,
                board_type=board_type,
                exc=exc,
                signal=f"Wokwi simulation failed: {exc}",
                classifier_stage="observe",
                simulation_artifact=simulation_artifact,
                include_exception=True,
            )

        output = _capture_output(process_result, config)
        common = {
            "simulation_artifact": simulation_artifact,
            "board": board_type.value,
            "serial_output": output.retained if config.collect_serial_output else [],
            "lines_captured": output.total_lines,
            "output_truncated": output.truncated,
            "simulation_duration_ms": max(
                0, int(process_result.elapsed_s * 1_000)
            ),
            "process_result": process_result,
            "metadata": {
                "command": list(process_config.args),
                "source_project": str(source_project),
                "serial_lines_retained": len(output.retained),
                "serial_lines_dropped": output.dropped_lines,
                "subprocess_output_truncated": process_result.output_truncated,
                "lint_command": [_TOOL, "lint"],
            },
        }

        if process_result.timed_out:
            classification = classify_failure(
                stderr=(
                    f"observation timeout: Wokwi simulation exceeded host "
                    f"limit after {process_result.elapsed_s:.1f}s"
                ),
                stdout=process_result.stdout_text,
                process_result=process_result,
                stage="observe",
                tool=_TOOL,
            )
            return SimulationResult(
                success=False,
                status=ResultStatus.TIMEOUT,
                duration_ms=_elapsed_ms(started),
                message="Wokwi simulation exceeded its host timeout",
                failure=_failure_from_classification(
                    classification,
                    raw_output=_combined_output(process_result),
                    exception_type="ProcessTimeoutError",
                ),
                **common,
            )

        if not process_result.success:
            return _process_failure(
                started=started,
                board_type=board_type,
                process_result=process_result,
                simulation_artifact=simulation_artifact,
                message=(
                    "Wokwi simulation failed with exit code "
                    f"{process_result.returncode}"
                ),
                classifier_stage="observe",
                exception_type="ExecutionError",
                output=output,
                source_project=source_project,
                collect_serial_output=config.collect_serial_output,
            )

        runtime_classification = classify_failure(
            stderr=process_result.stderr_text,
            stdout=process_result.stdout_text,
            process_result=process_result,
            stage="observe",
            tool=_TOOL,
        )
        if (
            config.stop_on_error
            and runtime_classification.category is not FailureCategory.UNKNOWN
        ):
            return SimulationResult(
                success=False,
                status=ResultStatus.FAILED,
                duration_ms=_elapsed_ms(started),
                message="Wokwi simulation detected a runtime failure",
                failure=_failure_from_classification(
                    runtime_classification,
                    raw_output=_combined_output(process_result),
                    exception_type="SimulatedRuntimeError",
                ),
                **common,
            )

        return SimulationResult(
            success=True,
            status=ResultStatus.SUCCESS,
            duration_ms=_elapsed_ms(started),
            message="Wokwi simulation completed successfully",
            **common,
        )


@dataclass(frozen=True, slots=True)
class _CapturedOutput:
    retained: list[str]
    total_lines: int
    dropped_lines: int
    truncated: bool


async def simulate_firmware(
    artifact: BuildArtifact,
    board_type: BoardType,
    config: SimulationConfig,
    subprocess_mgr: SubprocessManager,
) -> SimulationResult:
    """Convenience entry point for callers that do not retain a simulator."""
    return await WokwiSimulator(subprocess_mgr).simulate(
        artifact, board_type, config
    )


def _validate_inputs(
    artifact: BuildArtifact,
    board_type: BoardType,
) -> tuple[Path, Path]:
    if (
        not isinstance(board_type, BoardType)
        or board_type not in _SUPPORTED_BOARDS
    ):
        value = getattr(board_type, "value", board_type)
        raise ValueError(f"wrong target: unsupported Wokwi board {value!r}")

    firmware_path = Path(artifact.path).expanduser().resolve()
    if not firmware_path.exists():
        raise FileNotFoundError(
            f"invalid firmware: file does not exist: {firmware_path}"
        )
    if not firmware_path.is_file():
        raise ValueError(
            f"invalid firmware: artifact is not a file: {firmware_path}"
        )
    if firmware_path.suffix.lower() not in _SUPPORTED_SUFFIXES:
        raise ValueError(
            f"invalid firmware: {firmware_path.suffix or 'extensionless'} "
            "artifacts are not supported by Wokwi ESP32 simulation"
        )

    actual_size = firmware_path.stat().st_size
    if actual_size <= 0:
        raise ValueError("invalid firmware: artifact is empty")
    if artifact.size_bytes > 0 and artifact.size_bytes != actual_size:
        raise ValueError(
            "invalid firmware: artifact size changed after build "
            f"({artifact.size_bytes} != {actual_size})"
        )

    source_project = _find_project_root(firmware_path)
    if not source_project.exists() or not source_project.is_dir():
        raise ValueError(f"project directory does not exist: {source_project}")
    return source_project, firmware_path


def _find_project_root(firmware_path: Path) -> Path:
    for parent in firmware_path.parents:
        if (parent / "platformio.ini").is_file():
            return parent.resolve()
    return firmware_path.parent.resolve()


def _wokwi_token() -> str:
    token = os.environ.get(_TOKEN_ENV, "").strip()
    if not token:
        raise RuntimeError(
            f"{_TOKEN_ENV} is required to run Wokwi simulations"
        )
    return token


def _prepare_project(
    *,
    source_project: Path,
    firmware_path: Path,
    board_type: BoardType,
) -> SimulationArtifact:
    simulation_id = uuid.uuid4().hex
    project_path = (
        source_project / ".promptforge" / "wokwi" / simulation_id
    ).resolve()
    project_path.mkdir(parents=True, exist_ok=False)

    copied_firmware = project_path / firmware_path.name
    shutil.copy2(firmware_path, copied_firmware)

    elf_path = _matching_elf(firmware_path)
    copied_elf: Optional[Path] = None
    if elf_path is not None and elf_path != firmware_path:
        copied_elf = project_path / elf_path.name
        shutil.copy2(elf_path, copied_elf)
    elif firmware_path.suffix.lower() == ".elf":
        copied_elf = copied_firmware

    source_diagram = source_project / "diagram.json"
    target_diagram = project_path / "diagram.json"
    if source_diagram.is_file():
        _validate_diagram(source_diagram, board_type)
        shutil.copy2(source_diagram, target_diagram)
    else:
        target_diagram.write_text(
            json.dumps(_generated_diagram(board_type), indent=2) + "\n",
            encoding="utf-8",
        )

    toml_lines = [
        "[wokwi]",
        "version = 1",
        f"firmware = '{copied_firmware.name}'",
    ]
    if copied_elf is not None:
        toml_lines.append(f"elf = '{copied_elf.name}'")
    (project_path / "wokwi.toml").write_text(
        "\n".join(toml_lines) + "\n",
        encoding="utf-8",
    )

    return SimulationArtifact(
        project_path=str(project_path),
        firmware_path=str(copied_firmware),
        simulation_id=simulation_id,
    )


def _matching_elf(firmware_path: Path) -> Optional[Path]:
    if firmware_path.suffix.lower() == ".elf":
        return firmware_path
    candidate = firmware_path.with_suffix(".elf")
    return candidate if candidate.is_file() else None


def _validate_diagram(path: Path, board_type: BoardType) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Wokwi project diagram: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("parts"), list):
        raise ValueError("invalid Wokwi project diagram: parts must be a list")
    part_types = {
        item.get("type")
        for item in data["parts"]
        if isinstance(item, dict) and isinstance(item.get("type"), str)
    }
    expected = _SUPPORTED_BOARDS[board_type]
    configured_boards = part_types & _KNOWN_BOARD_PARTS
    if expected not in configured_boards:
        raise ValueError(
            "wrong target: diagram.json does not contain the requested "
            f"{board_type.value} board"
        )


def _generated_diagram(board_type: BoardType) -> dict[str, object]:
    return {
        "version": 1,
        "author": "PromptForge AI",
        "editor": "wokwi",
        "parts": [
            {
                "type": _SUPPORTED_BOARDS[board_type],
                "id": "esp",
                "top": 0,
                "left": 0,
                "attrs": {},
            }
        ],
        "connections": [],
        "dependencies": {},
    }


def _capture_output(
    process_result: ProcessResult,
    config: SimulationConfig,
) -> _CapturedOutput:
    lines = process_result.stdout_text.splitlines()
    total_lines = len(lines)
    retained = lines[-config.max_output_lines :]
    dropped = max(0, total_lines - len(retained))
    return _CapturedOutput(
        retained=retained,
        total_lines=total_lines,
        dropped_lines=dropped,
        truncated=process_result.output_truncated or dropped > 0,
    )


def _process_failure(
    *,
    started: float,
    board_type: BoardType,
    process_result: ProcessResult,
    simulation_artifact: SimulationArtifact,
    message: str,
    classifier_stage: str,
    signal_prefix: str = "",
    exception_type: str,
    output: Optional[_CapturedOutput] = None,
    source_project: Optional[Path] = None,
    collect_serial_output: bool = True,
) -> SimulationResult:
    captured = output or _CapturedOutput([], 0, 0, process_result.output_truncated)
    signal = "\n".join(
        part
        for part in (signal_prefix, _combined_output(process_result))
        if part
    )
    classification = classify_failure(
        stderr=signal,
        stdout=process_result.stdout_text,
        process_result=process_result,
        stage=classifier_stage,
        tool=_TOOL,
    )
    return SimulationResult(
        success=False,
        status=(
            ResultStatus.TIMEOUT
            if process_result.timed_out else ResultStatus.FAILED
        ),
        duration_ms=_elapsed_ms(started),
        message=message,
        metadata={
            "source_project": str(source_project) if source_project else "",
            "command": list(process_result.args),
            "serial_lines_retained": len(captured.retained),
            "serial_lines_dropped": captured.dropped_lines,
            "subprocess_output_truncated": process_result.output_truncated,
        },
        simulation_artifact=simulation_artifact,
        board=board_type.value,
        serial_output=(
            list(captured.retained) if collect_serial_output else []
        ),
        lines_captured=captured.total_lines,
        output_truncated=captured.truncated,
        simulation_duration_ms=max(0, int(process_result.elapsed_s * 1_000)),
        process_result=process_result,
        failure=_failure_from_classification(
            classification,
            raw_output=signal,
            exception_type=exception_type,
        ),
    )


def _exception_result(
    *,
    started: float,
    board_type: object,
    exc: BaseException,
    signal: str,
    classifier_stage: str,
    simulation_artifact: Optional[SimulationArtifact],
    include_exception: bool,
) -> SimulationResult:
    classification_signal = signal
    if (
        classifier_stage == "build"
        and "wokwi project" in signal.casefold()
    ):
        classification_signal = f"ERROR: BuildError: {signal}"
    classification = classify_failure(
        exception=exc if include_exception else None,
        stderr=classification_signal,
        stage=classifier_stage,
        tool=_TOOL,
    )
    board_value = getattr(board_type, "value", str(board_type))
    return SimulationResult(
        success=False,
        status=ResultStatus.FAILED,
        duration_ms=_elapsed_ms(started),
        message=signal,
        simulation_artifact=simulation_artifact,
        board=str(board_value),
        failure=_failure_from_classification(
            classification,
            raw_output=signal,
            exception_type=type(exc).__name__,
        ),
    )


def _classifier_stage_for_exception(exc: BaseException) -> str:
    text = str(exc).casefold()
    if "firmware" in text or "target" in text:
        return "flash"
    return "build"


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
        stage=_STAGE,
        exception_type=exception_type,
        raw_output=raw_output,
        metadata={
            "severity": classification.severity.value,
            "confidence": classification.confidence,
        },
    )


def _combined_output(result: ProcessResult) -> str:
    return "\n".join(
        part for part in (result.stderr_text, result.stdout_text) if part
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1_000))
