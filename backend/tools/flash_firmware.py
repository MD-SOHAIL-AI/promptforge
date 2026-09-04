"""Firmware flashing adapter for the PromptForge runtime.

This module performs one validated flash attempt and returns the frozen
``FlashResult`` contract. All process execution is delegated to
``SubprocessManager``. It does not retry, monitor serial output, mutate session
state, or orchestrate other runtime stages.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from serial.tools import list_ports

from ..runtime.failure_classifier import FailureClassification, classify_failure
from ..runtime.result import (
    FailureResult,
    FlashResult,
    ResultStatus,
    VerificationStatus,
)
from ..runtime.subprocess_mgr import (
    ProcessConfig,
    ProcessResult,
    SubprocessManager,
)
from .board_detector import BoardInfo, BoardType
from .build_firmware import BuildArtifact
from ..services.serial_service import SerialService

__all__ = [
    "FirmwareFlasher",
    "FlashConfig",
    "flash_firmware",
]

_ESP32_TYPES = {
    BoardType.ESP32,
    BoardType.ESP32_S3,
    BoardType.ESP32_C3,
}
_ESPTOOL_CHIPS = {
    BoardType.ESP32: "esp32",
    BoardType.ESP32_S3: "esp32s3",
    BoardType.ESP32_C3: "esp32c3",
}
_SUPPORTED_SUFFIXES = {
    BoardType.ESP32: {".bin"},
    BoardType.ESP32_S3: {".bin"},
    BoardType.ESP32_C3: {".bin"},
    BoardType.STM32: {".elf", ".hex", ".bin"},
    BoardType.ARDUINO_UNO: {".hex"},
}
_VERIFICATION_FAILURE_RE = re.compile(
    r"verification\s+(?:failed|error)|verify\s+error|mismatch",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(
    r"(?:version|v)\s*([0-9]+(?:\.[0-9A-Za-z_-]+)+)",
    re.IGNORECASE,
)
_STM32_TARGET_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), target)
    for pattern, target in (
        (r"(?:stm32|nucleo[_ -]*)?f0\d", "target/stm32f0x.cfg"),
        (r"(?:stm32|nucleo[_ -]*)?f1\d", "target/stm32f1x.cfg"),
        (r"(?:stm32|nucleo[_ -]*)?f2\d", "target/stm32f2x.cfg"),
        (r"(?:stm32|nucleo[_ -]*)?f3\d", "target/stm32f3x.cfg"),
        (r"(?:stm32|nucleo[_ -]*)?f4\d", "target/stm32f4x.cfg"),
        (r"(?:stm32|nucleo[_ -]*)?f7\d", "target/stm32f7x.cfg"),
        (r"(?:stm32|nucleo[_ -]*)?g0\d", "target/stm32g0x.cfg"),
        (r"(?:stm32|nucleo[_ -]*)?g4\d", "target/stm32g4x.cfg"),
        (r"(?:stm32|nucleo[_ -]*)?h7\d", "target/stm32h7x.cfg"),
        (r"(?:stm32|nucleo[_ -]*)?l0\d", "target/stm32l0.cfg"),
        (r"(?:stm32|nucleo[_ -]*)?l1\d", "target/stm32l1.cfg"),
        (r"(?:stm32|nucleo[_ -]*)?l4\d", "target/stm32l4x.cfg"),
        (r"(?:stm32|nucleo[_ -]*)?l5\d", "target/stm32l5x.cfg"),
        (r"(?:stm32|nucleo[_ -]*)?u5\d", "target/stm32u5x.cfg"),
        (r"(?:stm32)?wb\d", "target/stm32wbx.cfg"),
        (r"(?:stm32)?wl\d", "target/stm32wlx.cfg"),
    )
)


@dataclass(frozen=True, slots=True)
class FlashConfig:
    """Immutable configuration for one firmware flash attempt."""

    port: str
    baudrate: int = 115_200
    verify: bool = True
    timeout_s: float = 60.0

    def __post_init__(self) -> None:
        if not isinstance(self.port, str) or not self.port.strip():
            raise ValueError("port must be a non-empty string")
        if "\x00" in self.port:
            raise ValueError("port cannot contain NUL characters")
        if not isinstance(self.baudrate, int) or isinstance(self.baudrate, bool):
            raise ValueError("baudrate must be an integer")
        if self.baudrate <= 0:
            raise ValueError("baudrate must be greater than zero")
        if not isinstance(self.verify, bool):
            raise ValueError("verify must be a boolean")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s must be greater than zero")


@dataclass(frozen=True, slots=True)
class _BackendCommand:
    tool: str
    executable: str
    probe_args: tuple[str, ...]
    flash_args: tuple[str, ...]
    cwd: Optional[str]
    verification_integrated: bool
    metadata: dict[str, object]


class FirmwareFlasher:
    """Validate and flash one firmware artifact to one detected board."""

    def __init__(self, subprocess_mgr: SubprocessManager) -> None:
        self._subprocess_mgr = subprocess_mgr

    async def flash(
        self,
        artifact: BuildArtifact,
        board: BoardInfo,
        config: FlashConfig,
    ) -> FlashResult:
        """Execute exactly one flash attempt and return a ``FlashResult``."""
        started = time.monotonic()
        port = config.port.strip()

        validation_failure = self._validate_inputs(artifact, board, config, started)
        if validation_failure is not None:
            return validation_failure

        try:
            if not _port_is_connected(port):
                return self._classified_failure(
                    started=started,
                    board=board,
                    port=port,
                    signal=f"device disconnected: serial port {port!r} was not found",
                    exception_type="DeviceDisconnectedError",
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._classified_failure(
                started=started,
                board=board,
                port=port,
                exception=exc,
                signal=f"serial permission error while checking port {port!r}: {exc}",
                exception_type=type(exc).__name__,
            )

        try:
            backend = _select_backend(artifact, board, config)
        except ValueError as exc:
            return self._classified_failure(
                started=started,
                board=board,
                port=port,
                signal=f"wrong target or invalid firmware: {exc}",
                exception_type=type(exc).__name__,
            )

        try:
            probe_result = await self._subprocess_mgr.run(
                ProcessConfig(
                    args=[backend.executable, *backend.probe_args],
                    cwd=backend.cwd,
                    timeout_s=min(config.timeout_s, 10.0),
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._classified_failure(
                started=started,
                board=board,
                port=port,
                tool=backend.tool,
                signal=f"{backend.executable} not found: {exc}",
                exception_type=type(exc).__name__,
                metadata=backend.metadata,
            )

        if not _probe_succeeded(backend.tool, probe_result):
            return self._process_failure(
                started=started,
                board=board,
                port=port,
                backend=backend,
                process_result=probe_result,
                tool_version="",
                signal_prefix=f"{backend.executable} not installed or unavailable",
                backend_probe=True,
            )

        tool_version = _parse_tool_version(probe_result)
        try:
            flash_result = await self._subprocess_mgr.run(
                ProcessConfig(
                    args=list(backend.flash_args),
                    cwd=backend.cwd,
                    timeout_s=config.timeout_s,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._classified_failure(
                started=started,
                board=board,
                port=port,
                tool=backend.tool,
                tool_version=tool_version,
                signal=f"{backend.tool} flash failed: {exc}",
                exception=exc,
                exception_type=type(exc).__name__,
                metadata=backend.metadata,
            )

        if not flash_result.success:
            return self._process_failure(
                started=started,
                board=board,
                port=port,
                backend=backend,
                process_result=flash_result,
                tool_version=tool_version,
            )

        verification_status = (
            VerificationStatus.PASSED
            if config.verify and backend.verification_integrated
            else VerificationStatus.SKIPPED
        )
        actual_size = Path(artifact.path).stat().st_size
        flash_duration_ms = max(0, int(flash_result.elapsed_s * 1000))
        return FlashResult(
            success=True,
            status=ResultStatus.SUCCESS,
            duration_ms=_elapsed_ms(started),
            message=f"Firmware flashed successfully with {backend.tool}",
            metadata={
                **backend.metadata,
                "artifact_path": str(Path(artifact.path).resolve()),
                "command": list(backend.flash_args),
                "output_truncated": flash_result.output_truncated,
            },
            port=port,
            board=board.board_type.value,
            flash_duration_ms=flash_duration_ms,
            verification_status=verification_status,
            bytes_written=actual_size,
            tool=backend.tool,
            tool_version=tool_version,
            process_result=flash_result,
        )

    @staticmethod
    def _validate_inputs(
        artifact: BuildArtifact,
        board: BoardInfo,
        config: FlashConfig,
        started: float,
    ) -> Optional[FlashResult]:
        port = config.port.strip()
        artifact_path = Path(artifact.path).expanduser()

        if board.board_type is BoardType.UNKNOWN:
            return FirmwareFlasher._classified_failure(
                started=started,
                board=board,
                port=port,
                signal="wrong target: unsupported or unknown board",
                exception_type="UnsupportedBoardError",
            )
        if board.board_type not in _SUPPORTED_SUFFIXES:
            return FirmwareFlasher._classified_failure(
                started=started,
                board=board,
                port=port,
                signal=f"wrong target: unsupported board {board.board_type.value}",
                exception_type="UnsupportedBoardError",
            )
        if not board.port.strip():
            return FirmwareFlasher._classified_failure(
                started=started,
                board=board,
                port=port,
                signal="device disconnected: detected board has no serial port",
                exception_type="DeviceDisconnectedError",
            )
        if not _same_port(board.port, port):
            return FirmwareFlasher._classified_failure(
                started=started,
                board=board,
                port=port,
                signal=(
                    "device disconnected: configured port does not match "
                    f"detected board port {board.port!r}"
                ),
                exception_type="DeviceDisconnectedError",
            )
        if not artifact_path.exists():
            return FirmwareFlasher._classified_failure(
                started=started,
                board=board,
                port=port,
                signal=f"invalid firmware: file does not exist: {artifact_path}",
                exception_type="FileNotFoundError",
            )
        if not artifact_path.is_file():
            return FirmwareFlasher._classified_failure(
                started=started,
                board=board,
                port=port,
                signal=f"invalid firmware: artifact is not a file: {artifact_path}",
                exception_type="InvalidFirmwareError",
            )

        try:
            actual_size = artifact_path.stat().st_size
        except OSError as exc:
            return FirmwareFlasher._classified_failure(
                started=started,
                board=board,
                port=port,
                signal=f"invalid firmware: cannot read artifact metadata: {exc}",
                exception=exc,
                exception_type=type(exc).__name__,
            )

        if actual_size <= 0:
            return FirmwareFlasher._classified_failure(
                started=started,
                board=board,
                port=port,
                signal="invalid firmware: artifact is empty",
                exception_type="InvalidFirmwareError",
            )
        if artifact.size_bytes > 0 and artifact.size_bytes != actual_size:
            return FirmwareFlasher._classified_failure(
                started=started,
                board=board,
                port=port,
                signal=(
                    "invalid firmware: artifact size changed after build "
                    f"({artifact.size_bytes} != {actual_size})"
                ),
                exception_type="InvalidFirmwareError",
            )

        suffix = artifact_path.suffix.lower()
        if suffix not in _SUPPORTED_SUFFIXES[board.board_type]:
            return FirmwareFlasher._classified_failure(
                started=started,
                board=board,
                port=port,
                signal=(
                    f"invalid firmware: {suffix or 'extensionless'} artifact is "
                    f"not supported for {board.board_type.value}"
                ),
                exception_type="InvalidFirmwareError",
            )
        return None

    @staticmethod
    def _process_failure(
        *,
        started: float,
        board: BoardInfo,
        port: str,
        backend: _BackendCommand,
        process_result: ProcessResult,
        tool_version: str,
        signal_prefix: str = "",
        backend_probe: bool = False,
    ) -> FlashResult:
        output = _combined_output(process_result)
        signal = "\n".join(part for part in (signal_prefix, output) if part)
        classification = classify_failure(
            stderr=(signal or process_result.stderr_text),
            stdout=process_result.stdout_text,
            process_result=process_result,
            stage="flash",
            tool=backend.tool,
        )
        timed_out = process_result.timed_out
        verification_failed = bool(_VERIFICATION_FAILURE_RE.search(signal))
        message = (
            f"{backend.tool} flash timed out after {process_result.elapsed_s:.1f}s"
            if timed_out
            else (
                f"{backend.tool} backend is unavailable"
                if backend_probe
                else f"{backend.tool} flash failed with exit code {process_result.returncode}"
            )
        )
        return FlashResult(
            success=False,
            status=ResultStatus.TIMEOUT if timed_out else ResultStatus.FAILED,
            duration_ms=_elapsed_ms(started),
            message=message,
            metadata={
                **backend.metadata,
                "command": list(backend.flash_args),
                "backend_probe": backend_probe,
                "output_truncated": process_result.output_truncated,
            },
            port=port,
            board=board.board_type.value,
            verification_status=(
                VerificationStatus.FAILED
                if verification_failed
                else VerificationStatus.SKIPPED
            ),
            tool=backend.tool,
            tool_version=tool_version,
            process_result=process_result,
            failure=_failure_from_classification(
                classification,
                raw_output=signal,
                exception_type=(
                    "ProcessTimeoutError" if timed_out else "ExecutionError"
                ),
            ),
        )

    @staticmethod
    def _classified_failure(
        *,
        started: float,
        board: BoardInfo,
        port: str,
        signal: str,
        exception_type: str,
        tool: str = "",
        tool_version: str = "",
        exception: Optional[BaseException] = None,
        metadata: Optional[dict[str, object]] = None,
    ) -> FlashResult:
        classification = classify_failure(
            exception=exception,
            stderr=signal,
            stage="flash",
            tool=tool or "firmware-flasher",
        )
        return FlashResult(
            success=False,
            status=ResultStatus.FAILED,
            duration_ms=_elapsed_ms(started),
            message=signal,
            metadata=dict(metadata or {}),
            port=port,
            board=board.board_type.value,
            verification_status=VerificationStatus.SKIPPED,
            tool=tool,
            tool_version=tool_version,
            failure=_failure_from_classification(
                classification,
                raw_output=signal,
                exception_type=exception_type,
            ),
        )


async def flash_firmware(
    artifact: BuildArtifact,
    board: BoardInfo,
    config: FlashConfig,
    subprocess_mgr: SubprocessManager,
) -> FlashResult:
    """Convenience entry point for callers that do not retain a flasher."""
    return await FirmwareFlasher(subprocess_mgr).flash(artifact, board, config)


def _select_backend(
    artifact: BuildArtifact,
    board: BoardInfo,
    config: FlashConfig,
) -> _BackendCommand:
    path = Path(artifact.path).expanduser().resolve()
    port = config.port.strip()

    if board.board_type in _ESP32_TYPES:
        project_root = _find_platformio_project(path)
        if project_root is not None and artifact.environment.strip():
            args = (
                "pio",
                "run",
                "--project-dir",
                str(project_root),
                "--environment",
                artifact.environment.strip(),
                "--target",
                "upload",
                "--upload-port",
                port,
            )
            return _BackendCommand(
                tool="platformio",
                executable="pio",
                probe_args=("--version",),
                flash_args=args,
                cwd=str(project_root),
                verification_integrated=True,
                metadata={"backend": "platformio", "project_root": str(project_root)},
            )

        offset = "0x0" if _is_merged_esp_image(path) else "0x10000"
        args = (
            "esptool",
            "--chip",
            _ESPTOOL_CHIPS[board.board_type],
            "--port",
            port,
            "--baud",
            str(config.baudrate),
            "write-flash",
            offset,
            str(path),
        )
        return _BackendCommand(
            tool="esptool",
            executable="esptool",
            probe_args=("version",),
            flash_args=args,
            cwd=str(path.parent),
            verification_integrated=True,
            metadata={"backend": "esptool", "flash_offset": offset},
        )

    if board.board_type is BoardType.STM32:
        target_config = _stm32_target_config(artifact, board)
        program_parts = ["program", str(path)]
        if config.verify:
            program_parts.append("verify")
        program_parts.extend(("reset", "exit"))
        if path.suffix.lower() == ".bin":
            program_parts.append("0x08000000")
        args = (
            "openocd",
            "-f",
            "interface/stlink.cfg",
            "-f",
            target_config,
            "-c",
            " ".join(program_parts),
        )
        return _BackendCommand(
            tool="openocd",
            executable="openocd",
            probe_args=("--version",),
            flash_args=args,
            cwd=str(path.parent),
            verification_integrated=config.verify,
            metadata={"backend": "openocd", "target_config": target_config},
        )

    if board.board_type is BoardType.ARDUINO_UNO:
        args = [
            "avrdude",
            "-p",
            "atmega328p",
            "-c",
            "arduino",
            "-P",
            port,
            "-b",
            str(config.baudrate),
            "-D",
        ]
        if not config.verify:
            args.append("-V")
        args.extend(("-U", f"flash:w:{path}:i"))
        return _BackendCommand(
            tool="avrdude",
            executable="avrdude",
            probe_args=("-?",),
            flash_args=tuple(args),
            cwd=str(path.parent),
            verification_integrated=config.verify,
            metadata={"backend": "avrdude", "part": "atmega328p"},
        )

    raise ValueError(f"no flashing backend for {board.board_type.value}")


def _find_platformio_project(artifact_path: Path) -> Optional[Path]:
    for parent in artifact_path.parents:
        if (parent / "platformio.ini").is_file():
            return parent
    return None


def _is_merged_esp_image(path: Path) -> bool:
    name = path.stem.casefold()
    return any(token in name for token in ("merged", "factory", "full-flash", "full_flash"))


def _stm32_target_config(artifact: BuildArtifact, board: BoardInfo) -> str:
    text = " ".join(
        part
        for part in (
            artifact.environment,
            str(artifact.path),
            board.manufacturer or "",
            board.description or "",
        )
        if part
    )
    for pattern, target in _STM32_TARGET_RULES:
        if pattern.search(text):
            return target
    return "target/stm32f4x.cfg"


def _port_is_connected(port: str) -> bool:
    return SerialService.is_port_available(port, list(list_ports.comports()))


def _same_port(left: str, right: str) -> bool:
    return str(left).strip().casefold() == str(right).strip().casefold()


def _probe_succeeded(tool: str, result: ProcessResult) -> bool:
    # avrdude historically returns a non-zero status for its help command even
    # though the executable and configuration were loaded successfully.
    if tool == "avrdude":
        return not result.timed_out
    return result.success


def _parse_tool_version(result: ProcessResult) -> str:
    output = _combined_output(result).strip()
    match = _VERSION_RE.search(output)
    if match:
        return match.group(1)
    return output.splitlines()[0].strip()[:128] if output else ""


def _combined_output(result: ProcessResult) -> str:
    return "\n".join(
        part for part in (result.stderr_text, result.stdout_text) if part
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
        stage="flash",
        exception_type=exception_type,
        raw_output=raw_output,
        metadata={
            "severity": classification.severity.value,
            "confidence": classification.confidence,
        },
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
