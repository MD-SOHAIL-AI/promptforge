"""Pure monitor-stage conversions for PromptForge workflows."""

from __future__ import annotations

from dataclasses import replace

from ...contracts.execution_context import ExecutionContext
from ...tools.board_detector import BoardInfo
from ...tools.serial_monitor import SerialMonitorConfig
from .flash_adapter import FlashAdapterError, validate_board_info

__all__ = [
    "MonitorAdapter",
    "MonitorAdapterError",
    "board_to_serial_monitor_config",
    "update_context_for_monitor",
]


class MonitorAdapterError(ValueError):
    """Detected board state cannot be converted into monitor configuration."""


class MonitorAdapter:
    """Stateless facade for monitor-stage adapter functions."""

    __slots__ = ()

    @staticmethod
    def to_monitor_config(
        board: BoardInfo,
        **options: object,
    ) -> SerialMonitorConfig:
        return board_to_serial_monitor_config(board, **options)

    @staticmethod
    def update_context(
        context: ExecutionContext,
        board: BoardInfo,
        config: SerialMonitorConfig,
    ) -> ExecutionContext:
        return update_context_for_monitor(context, board, config)

    @staticmethod
    def adapt(
        board: BoardInfo,
        context: ExecutionContext,
        **options: object,
    ) -> tuple[SerialMonitorConfig, ExecutionContext]:
        config = board_to_serial_monitor_config(board, **options)
        return config, update_context_for_monitor(context, board, config)


def board_to_serial_monitor_config(
    board: BoardInfo,
    *,
    baudrate: int = 115_200,
    timeout_s: float = 30.0,
    connect_timeout_s: float = 10.0,
    success_pattern: str = "",
    failure_pattern: str = "",
    read_timeout: float = 0.1,
    max_line_length: int = 4_096,
    max_log_lines: int = 2_000,
    drain_on_connect: bool = True,
) -> SerialMonitorConfig:
    """Convert one detected board into ``SerialMonitorConfig``."""

    try:
        validate_board_info(board, allow_unknown=False)
    except FlashAdapterError as exc:
        raise MonitorAdapterError(str(exc)) from exc
    return SerialMonitorConfig(
        port=board.port.strip(),
        baudrate=baudrate,
        timeout_s=timeout_s,
        connect_timeout_s=connect_timeout_s,
        success_pattern=success_pattern,
        failure_pattern=failure_pattern,
        read_timeout=read_timeout,
        max_line_length=max_line_length,
        max_log_lines=max_log_lines,
        drain_on_connect=drain_on_connect,
    )


def update_context_for_monitor(
    context: ExecutionContext,
    board: BoardInfo,
    config: SerialMonitorConfig,
) -> ExecutionContext:
    """Return the immutable context snapshot prepared for serial monitoring."""

    if not isinstance(context, ExecutionContext):
        raise TypeError("context must be an ExecutionContext")
    if not isinstance(config, SerialMonitorConfig):
        raise TypeError("config must be a SerialMonitorConfig")
    expected = board_to_serial_monitor_config(
        board,
        baudrate=config.baudrate,
        timeout_s=config.timeout_s,
        connect_timeout_s=config.connect_timeout_s,
        success_pattern=config.success_pattern,
        failure_pattern=config.failure_pattern,
        read_timeout=config.read_timeout,
        max_line_length=config.max_line_length,
        max_log_lines=config.max_log_lines,
        drain_on_connect=config.drain_on_connect,
    )
    if config != expected:
        raise MonitorAdapterError(
            "SerialMonitorConfig port must match BoardInfo.port"
        )

    board_snapshot = {
        "board_type": board.board_type.value,
        "port": board.port,
        "vid": board.vid,
        "pid": board.pid,
        "manufacturer": board.manufacturer,
        "description": board.description,
        "serial_number": board.serial_number,
    }
    payload = {
        "port": config.port,
        "baudrate": config.baudrate,
        "timeout_s": config.timeout_s,
        "connect_timeout_s": config.connect_timeout_s,
        "success_pattern": config.success_pattern,
        "failure_pattern": config.failure_pattern,
    }
    return replace(
        context,
        board_info=board_snapshot,
        metadata=_stage_metadata(context, "monitor", payload),
    )


def _stage_metadata(
    context: ExecutionContext,
    stage: str,
    payload: dict[str, object],
) -> dict[str, object]:
    metadata = context.to_dict()["metadata"]
    workflow = metadata.get("workflow", {})
    if not isinstance(workflow, dict):
        raise MonitorAdapterError("context metadata.workflow must be a mapping")
    workflow[stage] = payload
    metadata["workflow"] = workflow
    return metadata
