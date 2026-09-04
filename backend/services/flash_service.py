"""Shared build-and-flash orchestration for ForgeX routes and agent runs."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..runtime.result import BuildResult, FlashResult
from ..runtime.subprocess_mgr import SubprocessManager
from ..tools.board_detector import BoardDetectionError, BoardDetector, BoardInfo, BoardType
from ..tools.flash_firmware import FlashConfig
from ..validation.board_validator import BoardValidator
from ..workflow.adapters.build_adapter import BuildAdapter, BuildAdapterError
from .platformio_service import PlatformIOService


@dataclass(frozen=True, slots=True)
class FlashProjectOutcome:
    project_id: str
    build: BuildResult
    flash: FlashResult | None
    board: BoardInfo | None = None


class FlashServiceError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        build: BuildResult | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.build = build


class ProjectFlashService:
    def __init__(
        self,
        *,
        platformio: PlatformIOService,
        detector: BoardDetector,
        manager: SubprocessManager,
        tool_executor: Callable[..., Any],
        workspace: object | None = None,
    ) -> None:
        self._platformio = platformio
        self._detector = detector
        self._manager = manager
        self._tool_executor = tool_executor
        self._workspace = workspace

    async def build_and_flash(
        self,
        *,
        project_id: str,
        project_path: str | Path,
        board_type: BoardType | None,
        port: str | None,
        environment: str | None = None,
        baudrate: int = 115_200,
        verify: bool = True,
        timeout_s: float = 60.0,
        expected_artifact_hash: str | None = None,
    ) -> FlashProjectOutcome:
        root = Path(project_path)
        try:
            build = await self._platformio.build(root, environment=environment)
        except (OSError, ValueError) as exc:
            raise FlashServiceError(
                422,
                "BUILD_REQUEST_INVALID",
                _flash_build_error_message(root, exc),
                {"project_id": project_id, "rootPath": str(root)},
            ) from exc

        if self._workspace is not None:
            _save_flash_build_workspace(self._workspace, project_id, build)
        if not build.success:
            return FlashProjectOutcome(project_id=project_id, build=build, flash=None)

        try:
            artifact = BuildAdapter.to_artifact(build, environment=environment)
        except BuildAdapterError as exc:
            raise FlashServiceError(500, "INVALID_BUILD_ARTIFACT", "Build output was invalid", build=build) from exc

        if expected_artifact_hash:
            digest = hashlib.sha256()
            try:
                with artifact.path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError as exc:
                raise FlashServiceError(409, "ARTIFACT_HASH_UNAVAILABLE", "The approved firmware artifact could not be verified.", build=build) from exc
            if digest.hexdigest().casefold() != expected_artifact_hash.strip().casefold():
                raise FlashServiceError(409, "ARTIFACT_HASH_MISMATCH", "The firmware changed after approval; build and approve it again.", build=build)

        if port is None or not port.strip():
            raise FlashServiceError(
                422,
                "BOARD_PORT_REQUIRED",
                "Select a detected board port before flashing.",
                {"board_type": board_type.value if board_type is not None else None},
                build=build,
            )

        try:
            boards = await asyncio.to_thread(self._detector.detect_boards)
        except BoardDetectionError as exc:
            raise FlashServiceError(503, "BOARD_DETECTION_FAILED", "Connected boards could not be detected", build=build) from exc

        board = next((item for item in boards if item.port.casefold() == port.casefold()), None)
        requested_type = board_type
        if board is None:
            raise FlashServiceError(
                404,
                "BOARD_NOT_FOUND",
                f"No matching {(requested_type or BoardType.UNKNOWN).value} device detected. Connect a board and try again.",
                {"port": port, "board_type": requested_type.value if requested_type is not None else None},
                build=build,
            )
        if requested_type is None:
            requested_type = board.board_type
        if board.board_type is not requested_type:
            raise FlashServiceError(
                409,
                "BOARD_TYPE_MISMATCH",
                "Detected board does not match the requested board type",
                {"requested": requested_type.value, "detected": board.board_type.value},
                build=build,
            )

        board_validation = BoardValidator().validate(
            {
                "target_board": requested_type.value,
                "framework": "Arduino",
                "firmware_size_bytes": artifact.size_bytes,
            },
            {"target_board": board.board_type.value},
        )
        if not board_validation.compatible:
            raise FlashServiceError(
                409,
                "BOARD_VALIDATION_FAILED",
                "Board validation failed before flashing",
                {
                    "issues": [
                        {"rule_id": issue.rule_id, "message": issue.message}
                        for issue in board_validation.errors
                    ]
                },
                build=build,
            )

        config = FlashConfig(port=board.port, baudrate=baudrate, verify=verify, timeout_s=timeout_s)
        produced = self._tool_executor("flash_firmware", artifact, board, config, self._manager)
        result = await produced if inspect.isawaitable(produced) else produced
        if not isinstance(result, FlashResult):
            raise FlashServiceError(500, "INVALID_FLASH_RESULT", "Flash tooling returned an invalid result", build=build)
        if self._workspace is not None:
            _save_flash_workspace(self._workspace, project_id, result)
        return FlashProjectOutcome(project_id=project_id, build=build, flash=result, board=board)


def parse_board_type_hint(value: str | None) -> BoardType | None:
    if not value:
        return None
    normalized = value.casefold().replace("-", "_").replace(" ", "_")
    for item in BoardType:
        if normalized == item.value.casefold() or normalized == item.name.casefold():
            return item
    if "esp32_s3" in normalized or normalized.endswith("_s3") or normalized == "s3":
        return BoardType.ESP32_S3
    if "esp32_c3" in normalized or normalized.endswith("_c3") or normalized == "c3":
        return BoardType.ESP32_C3
    if "esp32" in normalized or "wroom" in normalized or "wrover" in normalized:
        return BoardType.ESP32
    if "stm32" in normalized:
        return BoardType.STM32
    if "arduino_uno" in normalized or normalized == "uno" or normalized.endswith("_uno"):
        return BoardType.ARDUINO_UNO
    return None


def _save_flash_build_workspace(workspace: object, project_id: str, build: object) -> None:
    try:
        execution_id = f"flash-{project_id}"
        api_dict = build.api_dict()
        workspace.save_artifact(execution_id, "build_report.json", api_dict)
        workspace.save_log(execution_id, "build", getattr(build, "message", "Build completed"))
        firmware_path = getattr(build, "firmware_path", None)
        if getattr(build, "success", False) and firmware_path:
            path = Path(firmware_path)
            if path.name in {"firmware.bin", "firmware.elf"}:
                workspace.save_build(
                    execution_id,
                    path.name,
                    path,
                    metadata={
                        "project_id": project_id,
                        "status": "SUCCESS",
                        "target_board": getattr(build, "board", "UNKNOWN") or "UNKNOWN",
                        "framework": "PlatformIO",
                        "duration_ms": api_dict.get("duration_ms"),
                    },
                )
    except Exception:
        pass


def _save_flash_workspace(workspace: object, project_id: str, result: FlashResult) -> None:
    try:
        workspace.save_log(f"flash-{project_id}", "flash", result.message)
    except Exception:
        pass


def _flash_build_error_message(root: Path, exc: Exception) -> str:
    if not (root / "platformio.ini").is_file():
        return f"Flash requires platformio.ini. Generate/build a PlatformIO project first. Workspace root: {root}"
    return str(exc).strip() or "Project could not be built before flashing"
