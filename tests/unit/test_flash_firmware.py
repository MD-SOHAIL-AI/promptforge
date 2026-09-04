from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.runtime.result import FlashResult, ResultStatus, VerificationStatus
from backend.runtime.subprocess_mgr import ProcessConfig, ProcessResult
from backend.tools.board_detector import BoardInfo, BoardType
from backend.tools.build_firmware import BuildArtifact
from backend.tools.flash_firmware import (
    FirmwareFlasher,
    FlashConfig,
    flash_firmware,
)


class FakeSubprocessManager:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[ProcessConfig] = []

    async def run(self, config: ProcessConfig) -> ProcessResult:
        self.calls.append(config)
        if not self.responses:
            raise AssertionError("unexpected subprocess invocation")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            response = response(config)
        return response


def process_result(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    elapsed_s: float = 0.25,
    timed_out: bool = False,
    output_truncated: bool = False,
) -> ProcessResult:
    return ProcessResult(
        returncode=returncode,
        stdout=stdout.encode(),
        stderr=stderr.encode(),
        args=["tool"],
        elapsed_s=elapsed_s,
        timed_out=timed_out,
        output_truncated=output_truncated,
    )


def board(
    board_type: BoardType,
    *,
    port: str = "COM7",
    manufacturer: str | None = None,
    description: str | None = None,
) -> BoardInfo:
    return BoardInfo(
        board_type=board_type,
        port=port,
        vid=0x1234,
        pid=0x5678,
        manufacturer=manufacturer,
        description=description,
        serial_number="SERIAL-1",
    )


def artifact(
    path: Path,
    *,
    environment: str = "release",
    content: bytes = b"firmware-data",
    declared_size: int | None = None,
) -> BuildArtifact:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return BuildArtifact(
        path=path,
        environment=environment,
        artifact_type=path.suffix.lstrip("."),
        size_bytes=len(content) if declared_size is None else declared_size,
    )


def install_ports(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    monkeypatch.setattr(
        "backend.tools.flash_firmware.list_ports.comports",
        lambda: [SimpleNamespace(device=name) for name in names],
    )


def run_flash(
    manager: FakeSubprocessManager,
    build_artifact: BuildArtifact,
    board_info: BoardInfo,
    config: FlashConfig | None = None,
) -> FlashResult:
    return asyncio.run(
        FirmwareFlasher(manager).flash(
            build_artifact,
            board_info,
            config or FlashConfig(port=board_info.port),
        )
    )


def test_platformio_esp32_flash_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "platformio.ini").write_text(
        "[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\n",
        encoding="utf-8",
    )
    build_artifact = artifact(
        project / ".pio" / "build" / "esp32dev" / "firmware.bin",
        environment="esp32dev",
    )
    install_ports(monkeypatch, "COM7")
    manager = FakeSubprocessManager(
        process_result(stdout="PlatformIO Core, version 6.1.18"),
        process_result(stdout="Uploading... SUCCESS", elapsed_s=1.75),
    )

    result = run_flash(
        manager,
        build_artifact,
        board(BoardType.ESP32),
        FlashConfig(port="COM7", baudrate=921600, verify=True, timeout_s=30),
    )

    assert result.success is True
    assert result.status is ResultStatus.SUCCESS
    assert result.tool == "platformio"
    assert result.tool_version == "6.1.18"
    assert result.port == "COM7"
    assert result.board == "ESP32"
    assert result.flash_duration_ms == 1750
    assert result.verification_status is VerificationStatus.PASSED
    assert result.bytes_written == build_artifact.size_bytes
    assert manager.calls[0].args == ["pio", "--version"]
    assert manager.calls[1].args == [
        "pio",
        "run",
        "--project-dir",
        str(project.resolve()),
        "--environment",
        "esp32dev",
        "--target",
        "upload",
        "--upload-port",
        "COM7",
    ]
    assert manager.calls[1].cwd == str(project.resolve())
    assert manager.calls[1].timeout_s == 30


@pytest.mark.parametrize(
    ("board_type", "chip"),
    [
        (BoardType.ESP32, "esp32"),
        (BoardType.ESP32_S3, "esp32s3"),
        (BoardType.ESP32_C3, "esp32c3"),
    ],
)
def test_standalone_esp_binary_uses_esptool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    board_type: BoardType,
    chip: str,
) -> None:
    build_artifact = artifact(tmp_path / "firmware.bin")
    install_ports(monkeypatch, "/dev/ttyUSB0")
    manager = FakeSubprocessManager(
        process_result(stdout="esptool v5.0.1"),
        process_result(stdout="Hash of data verified.", elapsed_s=2),
    )

    result = run_flash(
        manager,
        build_artifact,
        board(board_type, port="/dev/ttyUSB0"),
        FlashConfig(port="/dev/ttyUSB0", baudrate=460800),
    )

    assert result.success is True
    assert result.tool == "esptool"
    assert result.verification_status is VerificationStatus.PASSED
    assert manager.calls[1].args == [
        "esptool",
        "--chip",
        chip,
        "--port",
        "/dev/ttyUSB0",
        "--baud",
        "460800",
        "write-flash",
        "0x10000",
        str(build_artifact.path.resolve()),
    ]


def test_merged_esp_binary_uses_zero_offset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_artifact = artifact(tmp_path / "factory-merged.bin")
    install_ports(monkeypatch, "COM7")
    manager = FakeSubprocessManager(
        process_result(stdout="esptool v5.0"),
        process_result(),
    )

    result = run_flash(manager, build_artifact, board(BoardType.ESP32_S3))

    assert result.success is True
    assert manager.calls[1].args[-2:] == ["0x0", str(build_artifact.path.resolve())]
    assert result.metadata["flash_offset"] == "0x0"


def test_stm32_elf_uses_openocd_and_infers_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_artifact = artifact(
        tmp_path / "output" / "nucleo_f446re" / "firmware.elf",
        environment="nucleo_f446re",
    )
    install_ports(monkeypatch, "COM7")
    manager = FakeSubprocessManager(
        process_result(stdout="Open On-Chip Debugger 0.12.0"),
        process_result(elapsed_s=3),
    )

    result = run_flash(manager, build_artifact, board(BoardType.STM32))

    assert result.success is True
    assert result.tool == "openocd"
    assert result.metadata["target_config"] == "target/stm32f4x.cfg"
    assert manager.calls[1].args[:5] == [
        "openocd",
        "-f",
        "interface/stlink.cfg",
        "-f",
        "target/stm32f4x.cfg",
    ]
    assert manager.calls[1].args[-2:] == [
        "-c",
        f"program {build_artifact.path.resolve()} verify reset exit",
    ]


def test_stm32_binary_adds_flash_base_and_can_skip_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_artifact = artifact(
        tmp_path / "stm32h743" / "firmware.bin",
        environment="stm32h743",
    )
    install_ports(monkeypatch, "COM7")
    manager = FakeSubprocessManager(
        process_result(stdout="Open On-Chip Debugger 0.12.0"),
        process_result(),
    )

    result = run_flash(
        manager,
        build_artifact,
        board(BoardType.STM32),
        FlashConfig(port="COM7", verify=False),
    )

    assert result.success is True
    assert result.verification_status is VerificationStatus.SKIPPED
    assert result.metadata["target_config"] == "target/stm32h7x.cfg"
    assert manager.calls[1].args[-1].endswith("reset exit 0x08000000")
    assert " verify " not in manager.calls[1].args[-1]


def test_arduino_uno_uses_avrdude_with_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_artifact = artifact(tmp_path / "firmware.hex")
    install_ports(monkeypatch, "COM7")
    manager = FakeSubprocessManager(
        process_result(
            returncode=1,
            stderr="avrdude version 8.0 usage",
        ),
        process_result(stdout="123 bytes of flash verified"),
    )

    result = run_flash(
        manager,
        build_artifact,
        board(BoardType.ARDUINO_UNO),
        FlashConfig(port="COM7", baudrate=115200, verify=True),
    )

    assert result.success is True
    assert result.tool == "avrdude"
    assert result.tool_version == "8.0"
    assert "-V" not in manager.calls[1].args
    assert manager.calls[1].args == [
        "avrdude",
        "-p",
        "atmega328p",
        "-c",
        "arduino",
        "-P",
        "COM7",
        "-b",
        "115200",
        "-D",
        "-U",
        f"flash:w:{build_artifact.path.resolve()}:i",
    ]


def test_arduino_can_disable_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_artifact = artifact(tmp_path / "firmware.hex")
    install_ports(monkeypatch, "COM7")
    manager = FakeSubprocessManager(
        process_result(returncode=1, stderr="avrdude version 8.0"),
        process_result(),
    )

    result = run_flash(
        manager,
        build_artifact,
        board(BoardType.ARDUINO_UNO),
        FlashConfig(port="COM7", verify=False),
    )

    assert result.success is True
    assert result.verification_status is VerificationStatus.SKIPPED
    assert "-V" in manager.calls[1].args


@pytest.mark.parametrize(
    "kwargs",
    [
        {"port": ""},
        {"port": "COM\x007"},
        {"port": "COM7", "baudrate": 0},
        {"port": "COM7", "baudrate": True},
        {"port": "COM7", "verify": 1},
        {"port": "COM7", "timeout_s": 0},
    ],
)
def test_flash_config_validation(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        FlashConfig(**kwargs)  # type: ignore[arg-type]


def test_missing_firmware_is_invalid_firmware(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "missing.bin"
    build_artifact = BuildArtifact(path, "release", "bin", 100)
    install_ports(monkeypatch, "COM7")

    result = run_flash(
        FakeSubprocessManager(),
        build_artifact,
        board(BoardType.ESP32),
    )

    assert result.success is False
    assert result.failure is not None
    assert result.failure.category == "INVALID_FIRMWARE"
    assert result.process_result is None


def test_empty_or_changed_firmware_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_ports(monkeypatch, "COM7")
    empty = artifact(tmp_path / "empty.bin", content=b"")
    changed = artifact(tmp_path / "changed.bin", declared_size=999)

    empty_result = run_flash(
        FakeSubprocessManager(), empty, board(BoardType.ESP32)
    )
    changed_result = run_flash(
        FakeSubprocessManager(), changed, board(BoardType.ESP32)
    )

    assert empty_result.failure is not None
    assert empty_result.failure.category == "INVALID_FIRMWARE"
    assert changed_result.failure is not None
    assert changed_result.failure.category == "INVALID_FIRMWARE"


@pytest.mark.parametrize(
    ("board_type", "filename"),
    [
        (BoardType.ESP32, "firmware.hex"),
        (BoardType.STM32, "firmware.uf2"),
        (BoardType.ARDUINO_UNO, "firmware.bin"),
    ],
)
def test_invalid_board_artifact_combination_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    board_type: BoardType,
    filename: str,
) -> None:
    install_ports(monkeypatch, "COM7")
    build_artifact = artifact(tmp_path / filename)

    result = run_flash(
        FakeSubprocessManager(), build_artifact, board(board_type)
    )

    assert result.failure is not None
    assert result.failure.category == "INVALID_FIRMWARE"


def test_unknown_board_is_rejected(tmp_path: Path) -> None:
    build_artifact = artifact(tmp_path / "firmware.bin")

    result = run_flash(
        FakeSubprocessManager(),
        build_artifact,
        board(BoardType.UNKNOWN),
    )

    assert result.failure is not None
    assert result.failure.category == "INVALID_FIRMWARE"


def test_configured_port_must_match_detected_board(tmp_path: Path) -> None:
    build_artifact = artifact(tmp_path / "firmware.bin")

    result = run_flash(
        FakeSubprocessManager(),
        build_artifact,
        board(BoardType.ESP32, port="COM7"),
        FlashConfig(port="COM8"),
    )

    assert result.failure is not None
    assert result.failure.category == "DEVICE_DISCONNECTED"


def test_disconnected_port_fails_before_backend_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_artifact = artifact(tmp_path / "firmware.bin")
    install_ports(monkeypatch, "COM8")
    manager = FakeSubprocessManager()

    result = run_flash(manager, build_artifact, board(BoardType.ESP32))

    assert result.failure is not None
    assert result.failure.category == "DEVICE_DISCONNECTED"
    assert manager.calls == []


def test_port_permission_error_is_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_artifact = artifact(tmp_path / "firmware.bin")

    def denied() -> list[object]:
        raise PermissionError("access denied to serial COM7")

    monkeypatch.setattr(
        "backend.tools.flash_firmware.list_ports.comports", denied
    )

    result = run_flash(
        FakeSubprocessManager(), build_artifact, board(BoardType.ESP32)
    )

    assert result.failure is not None
    assert result.failure.category == "SERIAL_PERMISSION_ERROR"


def test_missing_backend_is_toolchain_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_artifact = artifact(tmp_path / "firmware.bin")
    install_ports(monkeypatch, "COM7")

    result = run_flash(
        FakeSubprocessManager(FileNotFoundError("esptool missing")),
        build_artifact,
        board(BoardType.ESP32),
    )

    assert result.failure is not None
    assert result.failure.category == "TOOLCHAIN_MISSING"
    assert result.tool == "esptool"
    assert result.process_result is None


def test_failed_backend_probe_is_toolchain_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_artifact = artifact(tmp_path / "firmware.bin")
    install_ports(monkeypatch, "COM7")
    probe = process_result(returncode=1, stderr="esptool is not installed")

    result = run_flash(
        FakeSubprocessManager(probe),
        build_artifact,
        board(BoardType.ESP32),
    )

    assert result.failure is not None
    assert result.failure.category == "TOOLCHAIN_MISSING"
    assert result.process_result is probe
    assert result.metadata["backend_probe"] is True


def test_flash_timeout_returns_timeout_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_artifact = artifact(tmp_path / "firmware.bin")
    install_ports(monkeypatch, "COM7")
    timed_out = process_result(
        returncode=-1,
        stderr="Timed out waiting for packet header",
        elapsed_s=15,
        timed_out=True,
    )

    result = run_flash(
        FakeSubprocessManager(
            process_result(stdout="esptool v5.0"), timed_out
        ),
        build_artifact,
        board(BoardType.ESP32),
    )

    assert result.status is ResultStatus.TIMEOUT
    assert result.failure is not None
    assert result.failure.category == "FLASH_TIMEOUT"
    assert result.failure.exception_type == "ProcessTimeoutError"
    assert result.process_result is timed_out


@pytest.mark.parametrize(
    ("stderr", "category"),
    [
        ("serial port COM7 is busy", "PORT_BUSY"),
        ("could not open port COM7: no such device", "DEVICE_DISCONNECTED"),
        ("invalid firmware: bad magic byte", "INVALID_FIRMWARE"),
    ],
)
def test_flash_process_failures_are_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stderr: str,
    category: str,
) -> None:
    build_artifact = artifact(tmp_path / "firmware.bin")
    install_ports(monkeypatch, "COM7")

    result = run_flash(
        FakeSubprocessManager(
            process_result(stdout="esptool v5.0"),
            process_result(returncode=2, stderr=stderr),
        ),
        build_artifact,
        board(BoardType.ESP32),
    )

    assert result.failure is not None
    assert result.failure.category == category


def test_verification_failure_has_failed_verification_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_artifact = artifact(tmp_path / "firmware.hex")
    install_ports(monkeypatch, "COM7")

    result = run_flash(
        FakeSubprocessManager(
            process_result(returncode=1, stderr="avrdude version 8.0"),
            process_result(
                returncode=1,
                stderr="flash verification failed: mismatch at byte 10",
            ),
        ),
        build_artifact,
        board(BoardType.ARDUINO_UNO),
    )

    assert result.success is False
    assert result.verification_status is VerificationStatus.FAILED
    assert result.failure is not None
    assert result.failure.category == "INVALID_FIRMWARE"


def test_cancellation_is_propagated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_artifact = artifact(tmp_path / "firmware.bin")
    install_ports(monkeypatch, "COM7")

    with pytest.raises(asyncio.CancelledError):
        run_flash(
            FakeSubprocessManager(asyncio.CancelledError()),
            build_artifact,
            board(BoardType.ESP32),
        )


def test_convenience_function_uses_supplied_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_artifact = artifact(tmp_path / "firmware.hex")
    board_info = board(BoardType.ARDUINO_UNO)
    install_ports(monkeypatch, "COM7")
    manager = FakeSubprocessManager(
        process_result(returncode=1, stderr="avrdude version 8.0"),
        process_result(),
    )

    result = asyncio.run(
        flash_firmware(
            build_artifact,
            board_info,
            FlashConfig(port="COM7"),
            manager,
        )
    )

    assert result.success is True
    assert len(manager.calls) == 2
