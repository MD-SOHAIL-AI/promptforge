"""Pure flash-stage conversions for PromptForge workflows."""

from __future__ import annotations

from dataclasses import replace
from enum import Enum

from ...contracts.execution_context import ExecutionContext
from ...tools.board_detector import BoardInfo, BoardType
from ...tools.build_firmware import BuildArtifact
from ...tools.flash_firmware import FlashConfig
from .build_adapter import validate_build_artifact

__all__ = [
    "FlashAdapter",
    "FlashAdapterError",
    "board_and_artifact_to_flash_config",
    "update_context_for_flash",
    "validate_board_info",
]


class FlashAdapterError(ValueError):
    """Detected board state cannot be converted into flash configuration."""


class FlashAdapter:
    """Stateless facade for flash-stage adapter functions."""

    __slots__ = ()

    @staticmethod
    def to_flash_config(
        board: BoardInfo,
        artifact: BuildArtifact,
        *,
        baudrate: int = 115_200,
        verify: bool = True,
        timeout_s: float = 60.0,
    ) -> FlashConfig:
        return board_and_artifact_to_flash_config(
            board,
            artifact,
            baudrate=baudrate,
            verify=verify,
            timeout_s=timeout_s,
        )

    @staticmethod
    def update_context(
        context: ExecutionContext,
        board: BoardInfo,
        artifact: BuildArtifact,
        config: FlashConfig,
    ) -> ExecutionContext:
        return update_context_for_flash(context, board, artifact, config)

    @staticmethod
    def adapt(
        board: BoardInfo,
        artifact: BuildArtifact,
        context: ExecutionContext,
        *,
        baudrate: int = 115_200,
        verify: bool = True,
        timeout_s: float = 60.0,
    ) -> tuple[FlashConfig, ExecutionContext]:
        config = board_and_artifact_to_flash_config(
            board,
            artifact,
            baudrate=baudrate,
            verify=verify,
            timeout_s=timeout_s,
        )
        return config, update_context_for_flash(
            context,
            board,
            artifact,
            config,
        )


def board_and_artifact_to_flash_config(
    board: BoardInfo,
    artifact: BuildArtifact,
    *,
    baudrate: int = 115_200,
    verify: bool = True,
    timeout_s: float = 60.0,
) -> FlashConfig:
    """Convert detected board and build artifact data into ``FlashConfig``."""

    validate_board_info(board, allow_unknown=False)
    validate_build_artifact(artifact)
    return FlashConfig(
        port=board.port.strip(),
        baudrate=baudrate,
        verify=verify,
        timeout_s=timeout_s,
    )


def update_context_for_flash(
    context: ExecutionContext,
    board: BoardInfo,
    artifact: BuildArtifact,
    config: FlashConfig,
) -> ExecutionContext:
    """Return the immutable context snapshot prepared for flashing."""

    if not isinstance(context, ExecutionContext):
        raise TypeError("context must be an ExecutionContext")
    if not isinstance(config, FlashConfig):
        raise TypeError("config must be a FlashConfig")
    expected = board_and_artifact_to_flash_config(
        board,
        artifact,
        baudrate=config.baudrate,
        verify=config.verify,
        timeout_s=config.timeout_s,
    )
    if config != expected:
        raise FlashAdapterError("FlashConfig port must match BoardInfo.port")
    if context.firmware_path is not None and (
        context.firmware_path != str(artifact.path)
    ):
        raise FlashAdapterError(
            "artifact path must match context.firmware_path"
        )

    board_snapshot = _board_snapshot(board)
    payload = {
        "port": config.port,
        "baudrate": config.baudrate,
        "verify": config.verify,
        "timeout_s": config.timeout_s,
        "artifact_path": str(artifact.path),
    }
    return replace(
        context,
        firmware_path=str(artifact.path),
        build_artifact={
            "path": str(artifact.path),
            "environment": artifact.environment,
            "artifact_type": artifact.artifact_type,
            "size_bytes": artifact.size_bytes,
        },
        board_info=board_snapshot,
        metadata=_stage_metadata(context, "flash", payload),
    )


def validate_board_info(
    board: object,
    *,
    allow_unknown: bool = False,
) -> None:
    if not isinstance(board, BoardInfo):
        raise TypeError("board must be a BoardInfo")
    if not isinstance(board.board_type, BoardType):
        raise FlashAdapterError("board_type must be a BoardType")
    if not allow_unknown and board.board_type is BoardType.UNKNOWN:
        raise FlashAdapterError("board_type cannot be UNKNOWN")
    _required_text(board.port, "port")
    for field_name in ("vid", "pid"):
        value = getattr(board, field_name)
        if value is not None and (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= 0xFFFF
        ):
            raise FlashAdapterError(f"{field_name} must be a USB identifier")
    for field_name in ("manufacturer", "description", "serial_number"):
        value = getattr(board, field_name)
        if value is not None:
            _required_text(value, field_name)


def _board_snapshot(board: BoardInfo) -> dict[str, object]:
    return {
        "board_type": _identifier(board.board_type),
        "port": board.port,
        "vid": board.vid,
        "pid": board.pid,
        "manufacturer": board.manufacturer,
        "description": board.description,
        "serial_number": board.serial_number,
    }


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise FlashAdapterError(
            f"{field_name} must be a non-empty NUL-free string"
        )
    return value.strip()


def _identifier(value: str) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return value


def _stage_metadata(
    context: ExecutionContext,
    stage: str,
    payload: dict[str, object],
) -> dict[str, object]:
    metadata = context.to_dict()["metadata"]
    workflow = metadata.get("workflow", {})
    if not isinstance(workflow, dict):
        raise FlashAdapterError("context metadata.workflow must be a mapping")
    workflow[stage] = payload
    metadata["workflow"] = workflow
    return metadata
