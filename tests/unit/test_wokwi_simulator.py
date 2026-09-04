from __future__ import annotations

import asyncio
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from backend.runtime.result import (
    ResultStatus,
    SimulationArtifact,
    SimulationResult,
)
from backend.runtime.subprocess_mgr import ProcessConfig, ProcessResult
from backend.tools.board_detector import BoardType
from backend.tools.build_firmware import BuildArtifact
from backend.tools.tool_registry import execute_tool, get_tool
from backend.tools.wokwi_simulator import (
    SimulationConfig,
    WokwiSimulator,
    simulate_firmware,
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
        args=["wokwi-cli"],
        elapsed_s=elapsed_s,
        timed_out=timed_out,
        output_truncated=output_truncated,
    )


def make_artifact(
    tmp_path: Path,
    *,
    filename: str = "firmware.bin",
    content: bytes = b"firmware",
    declared_size: int | None = None,
    environment: str = "esp32dev",
) -> tuple[Path, BuildArtifact]:
    project = tmp_path / "project"
    project.mkdir()
    (project / "platformio.ini").write_text(
        f"[env:{environment}]\nplatform = espressif32\n",
        encoding="utf-8",
    )
    firmware = project / ".pio" / "build" / environment / filename
    firmware.parent.mkdir(parents=True)
    firmware.write_bytes(content)
    return project, BuildArtifact(
        path=firmware,
        environment=environment,
        artifact_type=firmware.suffix.lstrip("."),
        size_bytes=len(content) if declared_size is None else declared_size,
    )


def successful_manager(output: str = "boot\nready\n") -> FakeSubprocessManager:
    return FakeSubprocessManager(
        process_result(stdout="Wokwi CLI help"),
        process_result(stdout="diagram is valid"),
        process_result(stdout=output, elapsed_s=1.5),
    )


def run_simulation(
    manager: FakeSubprocessManager,
    artifact: BuildArtifact,
    *,
    board_type: BoardType = BoardType.ESP32,
    config: SimulationConfig | None = None,
) -> SimulationResult:
    return asyncio.run(
        WokwiSimulator(manager).simulate(
            artifact,
            board_type,
            config or SimulationConfig(),
        )
    )


@pytest.fixture(autouse=True)
def wokwi_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WOKWI_CLI_TOKEN", "test-token")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_s": 0},
        {"timeout_s": True},
        {"timeout_s": float("inf")},
        {"timeout_s": float("nan")},
        {"collect_serial_output": 1},
        {"max_output_lines": 0},
        {"max_output_lines": True},
        {"stop_on_error": 1},
    ],
)
def test_simulation_config_validation(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SimulationConfig(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("board_type", "part_type"),
    [
        (BoardType.ESP32, "board-esp32-devkit-c-v4"),
        (BoardType.ESP32_S3, "board-esp32-s3-devkitc-1"),
        (BoardType.ESP32_C3, "board-esp32-c3-devkitm-1"),
    ],
)
def test_success_generates_project_and_returns_structured_result(
    tmp_path: Path,
    board_type: BoardType,
    part_type: str,
) -> None:
    source_project, artifact = make_artifact(tmp_path)
    (Path(artifact.path).with_suffix(".elf")).write_bytes(b"elf")
    manager = successful_manager()

    result = run_simulation(
        manager,
        artifact,
        board_type=board_type,
        config=SimulationConfig(timeout_s=12),
    )

    assert result.success is True
    assert result.status is ResultStatus.SUCCESS
    assert result.board == board_type.value
    assert result.serial_output == ["boot", "ready"]
    assert result.lines_captured == 2
    assert result.simulation_duration_ms == 1500
    assert result.failure is None
    assert isinstance(result.simulation_artifact, SimulationArtifact)
    simulation_project = Path(result.simulation_artifact.project_path)
    assert simulation_project.parent.parent == source_project / ".promptforge"
    assert Path(result.simulation_artifact.firmware_path).read_bytes() == b"firmware"
    assert json.loads((simulation_project / "diagram.json").read_text())["parts"][0][
        "type"
    ] == part_type
    toml = (simulation_project / "wokwi.toml").read_text()
    assert "firmware = 'firmware.bin'" in toml
    assert "elf = 'firmware.elf'" in toml
    assert manager.calls[0].args == ["wokwi-cli", "--help"]
    assert manager.calls[1].args == ["wokwi-cli", "lint"]
    assert manager.calls[2].args == [
        "wokwi-cli",
        str(simulation_project),
        "--quiet",
        "--timeout",
        "12000",
        "--timeout-exit-code",
        "0",
    ]
    assert manager.calls[2].env == {"WOKWI_CLI_TOKEN": "test-token"}
    assert manager.calls[2].timeout_s == 27

    api = result.api_dict()
    assert api["simulation_artifact"]["simulation_id"]
    assert api["serial_output"] == ["boot", "ready"]
    assert api["process_exit_code"] == 0


def test_simulation_artifact_is_immutable() -> None:
    artifact = SimulationArtifact("project", "firmware", "id")

    with pytest.raises(FrozenInstanceError):
        artifact.project_path = "changed"  # type: ignore[misc]


def test_existing_matching_diagram_is_preserved(tmp_path: Path) -> None:
    project, artifact = make_artifact(tmp_path)
    diagram = {
        "version": 1,
        "parts": [
            {"type": "board-esp32-devkit-c-v4", "id": "esp"},
            {"type": "wokwi-led", "id": "led1"},
        ],
        "connections": [],
    }
    (project / "diagram.json").write_text(json.dumps(diagram), encoding="utf-8")

    result = run_simulation(successful_manager(), artifact)

    assert result.simulation_artifact is not None
    copied = Path(result.simulation_artifact.project_path) / "diagram.json"
    assert json.loads(copied.read_text()) == diagram


@pytest.mark.parametrize(
    "diagram",
    [
        "not json",
        json.dumps({"version": 1, "parts": "invalid"}),
        json.dumps(
            {
                "version": 1,
                "parts": [{"type": "board-esp32-s3-devkitc-1"}],
            }
        ),
    ],
)
def test_invalid_or_mismatched_diagram_is_rejected(
    tmp_path: Path,
    diagram: str,
) -> None:
    project, artifact = make_artifact(tmp_path)
    (project / "diagram.json").write_text(diagram, encoding="utf-8")
    manager = FakeSubprocessManager()

    result = run_simulation(manager, artifact)

    assert result.success is False
    assert result.failure is not None
    assert result.failure.stage == "simulate"
    assert result.failure.category in {"BUILD_ERROR", "INVALID_FIRMWARE"}
    assert manager.calls == []


@pytest.mark.parametrize(
    ("filename", "content", "declared_size"),
    [
        ("firmware.txt", b"firmware", None),
        ("firmware.bin", b"", None),
        ("firmware.bin", b"firmware", 999),
    ],
)
def test_invalid_firmware_is_rejected(
    tmp_path: Path,
    filename: str,
    content: bytes,
    declared_size: int | None,
) -> None:
    _, artifact = make_artifact(
        tmp_path,
        filename=filename,
        content=content,
        declared_size=declared_size,
    )

    result = run_simulation(FakeSubprocessManager(), artifact)

    assert result.success is False
    assert result.failure is not None
    assert result.failure.category == "INVALID_FIRMWARE"


def test_missing_firmware_is_invalid_firmware(tmp_path: Path) -> None:
    path = tmp_path / "missing.bin"
    artifact = BuildArtifact(path, "esp32dev", "bin", 10)

    result = run_simulation(FakeSubprocessManager(), artifact)

    assert result.failure is not None
    assert result.failure.category == "INVALID_FIRMWARE"


def test_unsupported_board_is_rejected(tmp_path: Path) -> None:
    _, artifact = make_artifact(tmp_path)

    result = run_simulation(
        FakeSubprocessManager(), artifact, board_type=BoardType.STM32
    )

    assert result.failure is not None
    assert result.failure.category == "INVALID_FIRMWARE"


def test_missing_token_fails_before_creating_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, artifact = make_artifact(tmp_path)
    monkeypatch.delenv("WOKWI_CLI_TOKEN")

    result = run_simulation(FakeSubprocessManager(), artifact)

    assert result.success is False
    assert "WOKWI_CLI_TOKEN" in result.message
    assert not (project / ".promptforge").exists()


def test_missing_cli_is_classified_as_toolchain_missing(tmp_path: Path) -> None:
    _, artifact = make_artifact(tmp_path)
    manager = FakeSubprocessManager(FileNotFoundError("wokwi-cli missing"))

    result = run_simulation(manager, artifact)

    assert result.failure is not None
    assert result.failure.category == "TOOLCHAIN_MISSING"
    assert result.process_result is None


def test_failed_cli_probe_is_classified_as_toolchain_missing(
    tmp_path: Path,
) -> None:
    _, artifact = make_artifact(tmp_path)
    probe = process_result(returncode=1, stderr="wokwi-cli is not installed")

    result = run_simulation(FakeSubprocessManager(probe), artifact)

    assert result.failure is not None
    assert result.failure.category == "TOOLCHAIN_MISSING"
    assert result.process_result is probe


def test_lint_failure_returns_build_error(tmp_path: Path) -> None:
    _, artifact = make_artifact(tmp_path)
    lint = process_result(returncode=1, stderr="unknown part type")
    manager = FakeSubprocessManager(process_result(), lint)

    result = run_simulation(manager, artifact)

    assert result.success is False
    assert result.failure is not None
    assert result.failure.category == "BUILD_ERROR"
    assert result.failure.exception_type == "InvalidWokwiProjectError"


def test_serial_output_is_bounded_and_reports_overflow(tmp_path: Path) -> None:
    _, artifact = make_artifact(tmp_path)
    manager = successful_manager("one\ntwo\nthree\nfour\n")

    result = run_simulation(
        manager,
        artifact,
        config=SimulationConfig(max_output_lines=2),
    )

    assert result.success is True
    assert result.serial_output == ["three", "four"]
    assert result.lines_captured == 4
    assert result.output_truncated is True
    assert result.metadata["serial_lines_dropped"] == 2


def test_serial_collection_can_be_disabled(tmp_path: Path) -> None:
    _, artifact = make_artifact(tmp_path)

    result = run_simulation(
        successful_manager("one\ntwo\n"),
        artifact,
        config=SimulationConfig(collect_serial_output=False),
    )

    assert result.success is True
    assert result.serial_output == []
    assert result.lines_captured == 2


def test_host_timeout_returns_observe_timeout(tmp_path: Path) -> None:
    _, artifact = make_artifact(tmp_path)
    timed_out = process_result(
        returncode=-1,
        stdout="booting\n",
        elapsed_s=45,
        timed_out=True,
    )
    manager = FakeSubprocessManager(process_result(), process_result(), timed_out)

    result = run_simulation(manager, artifact)

    assert result.status is ResultStatus.TIMEOUT
    assert result.failure is not None
    assert result.failure.category == "OBSERVE_TIMEOUT"
    assert result.failure.exception_type == "ProcessTimeoutError"


def test_cli_runtime_failure_returns_failed_result(tmp_path: Path) -> None:
    _, artifact = make_artifact(tmp_path)
    failed = process_result(returncode=3, stderr="simulation server error")
    manager = FakeSubprocessManager(process_result(), process_result(), failed)

    result = run_simulation(manager, artifact)

    assert result.success is False
    assert result.status is ResultStatus.FAILED
    assert result.failure is not None
    assert result.failure.stage == "simulate"
    assert result.process_result is failed


def test_runtime_watchdog_output_is_detected(tmp_path: Path) -> None:
    _, artifact = make_artifact(tmp_path)

    result = run_simulation(
        successful_manager("boot\nGuru Meditation Error: task watchdog\n"),
        artifact,
    )

    assert result.success is False
    assert result.failure is not None
    assert result.failure.category == "WATCHDOG_RESET"
    assert result.failure.exception_type == "SimulatedRuntimeError"


def test_runtime_error_detection_can_be_disabled(tmp_path: Path) -> None:
    _, artifact = make_artifact(tmp_path)

    result = run_simulation(
        successful_manager("Guru Meditation Error: task watchdog\n"),
        artifact,
        config=SimulationConfig(stop_on_error=False),
    )

    assert result.success is True
    assert result.failure is None


def test_subprocess_output_truncation_is_propagated(tmp_path: Path) -> None:
    _, artifact = make_artifact(tmp_path)
    manager = FakeSubprocessManager(
        process_result(),
        process_result(),
        process_result(stdout="line\n", output_truncated=True),
    )

    result = run_simulation(manager, artifact)

    assert result.output_truncated is True
    assert result.metadata["subprocess_output_truncated"] is True


def test_cancellation_is_propagated(tmp_path: Path) -> None:
    _, artifact = make_artifact(tmp_path)

    with pytest.raises(asyncio.CancelledError):
        run_simulation(
            FakeSubprocessManager(asyncio.CancelledError()),
            artifact,
        )


def test_convenience_function_and_registry_use_supplied_manager(
    tmp_path: Path,
) -> None:
    _, artifact = make_artifact(tmp_path)
    manager = successful_manager()

    direct = asyncio.run(
        simulate_firmware(
            artifact,
            BoardType.ESP32,
            SimulationConfig(),
            manager,
        )
    )

    assert direct.success is True
    assert callable(get_tool("wokwi_simulator"))

    registry_manager = successful_manager()
    registered = asyncio.run(
        execute_tool(
            "wokwi_simulator",
            artifact,
            BoardType.ESP32,
            SimulationConfig(),
            registry_manager,
        )
    )
    assert registered.success is True
    assert len(registry_manager.calls) == 3
