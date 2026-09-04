from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.contracts.execution_context import ExecutionContext
from backend.contracts.generated_project import GeneratedFile, GeneratedProject
from backend.runtime.result import BuildResult, ResultStatus
from backend.tools.board_detector import BoardInfo, BoardType
from backend.tools.build_firmware import BuildArtifact, BuildConfig
from backend.tools.flash_firmware import FlashConfig
from backend.tools.serial_monitor import SerialMonitorConfig
from backend.workflow.adapters.build_adapter import (
    BuildAdapter,
    BuildAdapterError,
    build_result_to_artifact,
    update_context_after_build,
)
from backend.workflow.adapters.flash_adapter import (
    FlashAdapter,
    FlashAdapterError,
    board_and_artifact_to_flash_config,
    update_context_for_flash,
)
from backend.workflow.adapters.generation_adapter import (
    GenerationAdapter,
    GenerationAdapterError,
    generated_project_to_build_config,
    project_root_to_build_config,
    resolve_build_config,
    update_context_after_generation,
)
from backend.workflow.adapters.monitor_adapter import (
    MonitorAdapter,
    MonitorAdapterError,
    board_to_serial_monitor_config,
    update_context_for_monitor,
)


def project(
    *,
    framework: str = "PlatformIO",
    target_board: str = "ESP32",
    ini: str = (
        "[env:esp32dev]\n"
        "platform = espressif32\n"
        "board = esp32dev\n"
    ),
    metadata: dict[str, object] | None = None,
) -> GeneratedProject:
    return GeneratedProject(
        project_id="project-123",
        project_name="blink",
        target_board=target_board,
        framework=framework,
        files=(
            GeneratedFile("platformio.ini", ini),
            GeneratedFile("src/main.cpp", "int main() { return 0; }"),
        ),
        created_at=datetime(2026, 6, 12, tzinfo=timezone.utc),
        metadata={"task_id": "task-123"} if metadata is None else metadata,
    )


def context(**overrides: object) -> ExecutionContext:
    values: dict[str, object] = {
        "task_id": "task-123",
        "target_board": "ESP32",
        "framework": "PlatformIO",
        "metadata": {"request_id": "request-7"},
    }
    values.update(overrides)
    return ExecutionContext(**values)  # type: ignore[arg-type]


def build_result(**overrides: object) -> BuildResult:
    values: dict[str, object] = {
        "success": True,
        "status": ResultStatus.SUCCESS,
        "message": "built",
        "firmware_path": "workspace/blink/.pio/build/esp32dev/firmware.bin",
        "build_size_bytes": 8192,
        "platform": "platformio",
        "board": "esp32dev",
        "toolchain_version": "6.1.18",
        "warnings_count": 1,
        "metadata": {
            "artifact_environment": "esp32dev",
            "artifact_type": "bin",
        },
    }
    values.update(overrides)
    return BuildResult(**values)  # type: ignore[arg-type]


def artifact(**overrides: object) -> BuildArtifact:
    values: dict[str, object] = {
        "path": Path("workspace/blink/.pio/build/esp32dev/firmware.bin"),
        "environment": "esp32dev",
        "artifact_type": "bin",
        "size_bytes": 8192,
    }
    values.update(overrides)
    return BuildArtifact(**values)  # type: ignore[arg-type]


def board(**overrides: object) -> BoardInfo:
    values: dict[str, object] = {
        "board_type": BoardType.ESP32,
        "port": "COM7",
        "vid": 0x10C4,
        "pid": 0xEA60,
        "manufacturer": "Silicon Labs",
        "description": "CP210x USB UART",
        "serial_number": "ABC123",
    }
    values.update(overrides)
    return BoardInfo(**values)  # type: ignore[arg-type]


def test_generated_project_maps_to_build_config_deterministically() -> None:
    first = generated_project_to_build_config(
        project(),
        project_path="workspace/blink",
    )
    second = GenerationAdapter.to_build_config(
        project(),
        project_path="workspace/blink",
    )

    assert first == second
    assert isinstance(first, BuildConfig)
    assert first.project_dir == "workspace/blink"
    assert first.environment == "esp32dev"
    assert first.board == "esp32dev"


def test_generation_mapping_supports_context_and_metadata_paths() -> None:
    from_context = generated_project_to_build_config(
        project(),
        context=context(project_path="workspace/from-context"),
    )
    from_metadata = generated_project_to_build_config(
        project(metadata={"project_path": "workspace/from-metadata"}),
    )

    assert from_context.project_dir == "workspace/from-context"
    assert from_metadata.project_dir == "workspace/from-metadata"


def test_generation_mapping_uses_single_default_environment() -> None:
    generated = project(
        ini=(
            "[platformio]\n"
            "default_envs = release\n"
            "[env:debug]\nboard = esp32dev\n"
            "[env:release]\nboard = esp32-s3-devkitc-1\n"
        )
    )

    config = generated_project_to_build_config(
        generated,
        project_path="workspace/blink",
    )

    assert config.environment == "release"
    assert config.board == "esp32-s3-devkitc-1"


def test_external_platformio_root_maps_to_build_config(tmp_path: Path) -> None:
    root = tmp_path / "external-platformio"
    root.mkdir()
    (root / "platformio.ini").write_text(
        "[platformio]\ndefault_envs = esp32dev\n"
        "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\n",
        encoding="utf-8",
    )

    config = project_root_to_build_config(root)

    assert isinstance(config, BuildConfig)
    assert config.project_dir == str(root.resolve())
    assert config.environment == "esp32dev"
    assert config.board == "esp32dev"


def test_build_config_resolver_uses_active_workspace_root(tmp_path: Path) -> None:
    root = tmp_path / "open-folder"
    root.mkdir()
    (root / "platformio.ini").write_text(
        "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\n",
        encoding="utf-8",
    )
    active_context = context(
        project_path=None,
        metadata={
            "active_workspace": {
                "rootPath": str(root),
                "generation_mode": "generate_into_open_folder",
            }
        },
    )

    config = resolve_build_config(None, context=active_context)

    assert Path(config.project_dir) == root.resolve()
    assert config.environment == "esp32dev"


def test_build_config_resolver_missing_platformio_ini_is_user_facing(tmp_path: Path) -> None:
    root = tmp_path / "generic-folder"
    root.mkdir()

    with pytest.raises(GenerationAdapterError, match="Build requires platformio.ini"):
        project_root_to_build_config(root)


def test_generation_context_update_preserves_existing_metadata() -> None:
    original = context()
    updated = update_context_after_generation(
        original,
        project(),
        project_path="workspace/blink",
    )

    assert updated is not original
    assert original.project_path is None
    assert updated.project_path == "workspace/blink"
    assert updated.metadata["request_id"] == "request-7"
    assert updated.metadata["workflow"]["generation"] == {
        "project_id": "project-123",
        "project_name": "blink",
        "file_count": 2,
    }


@pytest.mark.parametrize(
    ("generated", "message"),
    [
        (project(framework="Arduino"), "PlatformIO"),
        (project(ini="[platformio]\n"), "environment"),
        (project(ini="not valid"), "invalid"),
    ],
)
def test_generation_rejects_unsupported_or_invalid_projects(
    generated: GeneratedProject,
    message: str,
) -> None:
    with pytest.raises(GenerationAdapterError, match=message):
        generated_project_to_build_config(
            generated,
            project_path="workspace/blink",
        )


def test_generation_requires_materialized_project_path() -> None:
    with pytest.raises(GenerationAdapterError, match="ProjectService"):
        generated_project_to_build_config(project())


def test_generation_rejects_context_identity_mismatch() -> None:
    with pytest.raises(GenerationAdapterError, match="task_id"):
        generated_project_to_build_config(
            project(),
            context=context(task_id="task-other", project_path="workspace/x"),
        )


def test_build_result_maps_to_artifact_deterministically() -> None:
    first = build_result_to_artifact(build_result())
    second = BuildAdapter.to_artifact(build_result())

    assert first == second == artifact()


def test_build_mapping_can_infer_metadata_from_path() -> None:
    result = build_result(metadata={})

    converted = build_result_to_artifact(result)

    assert converted.environment == "esp32dev"
    assert converted.artifact_type == "bin"


@pytest.mark.parametrize(
    "result",
    [
        build_result(success=False, status=ResultStatus.FAILED),
        build_result(firmware_path=None),
        build_result(build_size_bytes=0),
        build_result(metadata={"artifact_type": "hex"}),
    ],
)
def test_build_mapping_rejects_invalid_results(result: BuildResult) -> None:
    with pytest.raises(BuildAdapterError):
        build_result_to_artifact(result)


def test_build_context_update_records_artifact_snapshot() -> None:
    converted, updated = BuildAdapter.adapt(build_result(), context())

    assert updated.firmware_path == str(converted.path)
    assert updated.build_artifact == {
        "path": str(converted.path),
        "environment": "esp32dev",
        "artifact_type": "bin",
        "size_bytes": 8192,
    }
    assert updated.metadata["workflow"]["build"]["warnings_count"] == 1


def test_build_context_rejects_artifact_result_mismatch() -> None:
    with pytest.raises(BuildAdapterError, match="match"):
        update_context_after_build(
            context(),
            artifact(size_bytes=1),
            result=build_result(),
        )


def test_board_and_artifact_map_to_flash_config() -> None:
    config = board_and_artifact_to_flash_config(
        board(),
        artifact(),
        baudrate=921600,
        verify=False,
        timeout_s=45,
    )

    assert config == FlashConfig(
        port="COM7",
        baudrate=921600,
        verify=False,
        timeout_s=45,
    )


def test_flash_context_update_records_board_and_policy() -> None:
    original = context(firmware_path=str(artifact().path))
    config, updated = FlashAdapter.adapt(board(), artifact(), original)

    assert config.port == "COM7"
    assert updated.board_info["board_type"] == "ESP32"
    assert updated.board_info["serial_number"] == "ABC123"
    assert updated.metadata["workflow"]["flash"]["verify"] is True


@pytest.mark.parametrize(
    "detected",
    [
        board(board_type=BoardType.UNKNOWN),
        board(port=" "),
        board(vid=-1),
        board(pid=0x10000),
    ],
)
def test_flash_mapping_rejects_invalid_board_data(
    detected: BoardInfo,
) -> None:
    with pytest.raises(FlashAdapterError):
        board_and_artifact_to_flash_config(detected, artifact())


def test_flash_context_rejects_port_and_artifact_mismatches() -> None:
    with pytest.raises(FlashAdapterError, match="port"):
        update_context_for_flash(
            context(),
            board(),
            artifact(),
            FlashConfig(port="COM8"),
        )
    with pytest.raises(FlashAdapterError, match="firmware_path"):
        update_context_for_flash(
            context(firmware_path="other.bin"),
            board(),
            artifact(),
            FlashConfig(port="COM7"),
        )


def test_board_maps_to_serial_monitor_config() -> None:
    config = board_to_serial_monitor_config(
        board(),
        baudrate=230400,
        timeout_s=12,
        success_pattern="READY",
        failure_pattern="FATAL",
    )

    assert config == SerialMonitorConfig(
        port="COM7",
        baudrate=230400,
        timeout_s=12,
        success_pattern="READY",
        failure_pattern="FATAL",
    )


def test_monitor_context_update_records_board_and_policy() -> None:
    config, updated = MonitorAdapter.adapt(
        board(),
        context(),
        success_pattern="READY",
    )

    assert updated.board_info["port"] == "COM7"
    assert updated.metadata["workflow"]["monitor"] == {
        "port": "COM7",
        "baudrate": 115200,
        "timeout_s": 30.0,
        "connect_timeout_s": 10.0,
        "success_pattern": "READY",
        "failure_pattern": "",
    }
    with pytest.raises(FrozenInstanceError):
        updated.project_path = "changed"  # type: ignore[misc]
    assert config.port == "COM7"


def test_monitor_mapping_rejects_unknown_board_and_port_mismatch() -> None:
    with pytest.raises(MonitorAdapterError):
        board_to_serial_monitor_config(board(board_type=BoardType.UNKNOWN))
    with pytest.raises(MonitorAdapterError, match="port"):
        update_context_for_monitor(
            context(),
            board(),
            SerialMonitorConfig(port="COM8"),
        )


def test_adapters_are_stateless_and_repeatable() -> None:
    assert not hasattr(GenerationAdapter(), "__dict__")
    assert not hasattr(BuildAdapter(), "__dict__")
    assert not hasattr(FlashAdapter(), "__dict__")
    assert not hasattr(MonitorAdapter(), "__dict__")

    assert board_to_serial_monitor_config(board()) == (
        board_to_serial_monitor_config(board())
    )
