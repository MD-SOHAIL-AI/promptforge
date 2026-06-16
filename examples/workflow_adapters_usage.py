"""Deterministic workflow adapter integration example."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.contracts.execution_context import ExecutionContext
from backend.contracts.generated_project import GeneratedFile, GeneratedProject
from backend.runtime.result import BuildResult, ResultStatus
from backend.tools.board_detector import BoardInfo, BoardType
from backend.workflow.adapters.build_adapter import BuildAdapter
from backend.workflow.adapters.flash_adapter import FlashAdapter
from backend.workflow.adapters.generation_adapter import GenerationAdapter
from backend.workflow.adapters.monitor_adapter import MonitorAdapter


project = GeneratedProject(
    project_id="project-123",
    project_name="blink",
    target_board="ESP32",
    framework="PlatformIO",
    files=(
        GeneratedFile(
            "platformio.ini",
            "[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\n",
        ),
        GeneratedFile("src/main.cpp", "int main() { return 0; }"),
    ),
    created_at=datetime.now(timezone.utc),
    metadata={"task_id": "task-123"},
)
context = ExecutionContext(
    task_id="task-123",
    target_board="ESP32",
    framework="PlatformIO",
)

# ProjectService owns materialization and returns this path.
build_config, context = GenerationAdapter.adapt(
    project,
    context,
    project_path="workspace/projects/blink",
)

# PlatformIOService/FirmwareBuilder returns BuildResult; no tool is called here.
build_result = BuildResult(
    success=True,
    status=ResultStatus.SUCCESS,
    message="built",
    firmware_path=(
        "workspace/projects/blink/.pio/build/esp32dev/firmware.bin"
    ),
    build_size_bytes=8192,
    platform="platformio",
    board="esp32dev",
    metadata={
        "artifact_environment": "esp32dev",
        "artifact_type": "bin",
    },
)
artifact, context = BuildAdapter.adapt(build_result, context)

detected_board = BoardInfo(
    board_type=BoardType.ESP32,
    port="COM7",
    vid=0x10C4,
    pid=0xEA60,
    manufacturer="Silicon Labs",
    description="CP210x USB UART",
    serial_number="ABC123",
)
flash_config, context = FlashAdapter.adapt(
    detected_board,
    artifact,
    context,
)
monitor_config, context = MonitorAdapter.adapt(detected_board, context)

print(build_config)
print(flash_config)
print(monitor_config)
print(context.to_dict())
