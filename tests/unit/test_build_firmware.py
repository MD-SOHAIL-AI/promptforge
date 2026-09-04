from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from backend.runtime.result import BuildResult, ResultStatus
from backend.runtime.subprocess_mgr import ProcessConfig, ProcessResult
from backend.tools.build_firmware import (
    BuildConfig,
    FirmwareBuilder,
    build_firmware,
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
    timed_out: bool = False,
    elapsed_s: float = 0.1,
    output_truncated: bool = False,
) -> ProcessResult:
    return ProcessResult(
        returncode=returncode,
        stdout=stdout.encode(),
        stderr=stderr.encode(),
        args=["pio", "run"],
        elapsed_s=elapsed_s,
        timed_out=timed_out,
        output_truncated=output_truncated,
    )


def make_project(
    root: Path,
    ini: str = "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\n",
    *,
    source_dir: str = "src",
) -> Path:
    root.mkdir()
    (root / "platformio.ini").write_text(ini, encoding="utf-8")
    (root / source_dir).mkdir()
    (root / source_dir / "main.cpp").write_text(
        "void setup() {}\nvoid loop() {}\n",
        encoding="utf-8",
    )
    return root


def create_artifact(
    project: Path,
    environment: str,
    filename: str = "firmware.bin",
    content: bytes = b"firmware",
) -> Path:
    output_dir = project / ".pio" / "build" / environment
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = output_dir / filename
    artifact.write_bytes(content)
    return artifact


def run_build(
    manager: FakeSubprocessManager,
    config: BuildConfig,
) -> BuildResult:
    return asyncio.run(FirmwareBuilder(manager).build(config))


def version_result(version: str = "6.1.18") -> ProcessResult:
    return process_result(stdout=f"PlatformIO Core, version {version}\n")


def test_success_returns_structured_result_and_build_command(tmp_path: Path) -> None:
    project = make_project(tmp_path / "firmware")

    def successful_build(config: ProcessConfig) -> ProcessResult:
        create_artifact(project, "esp32dev", content=b"123456")
        return process_result(
            stdout="src/main.cpp:3: warning: unused variable\n",
            stderr="lib/foo.cpp:8: warning: deprecated API\n",
        )

    manager = FakeSubprocessManager(version_result(), successful_build)

    result = run_build(
        manager,
        BuildConfig(
            project_dir=project,
            environment="esp32dev",
            timeout_s=45,
            env={"PLATFORMIO_HOME_DIR": str(tmp_path / "pio-home")},
            session_id="session-123",
        ),
    )

    assert isinstance(result, BuildResult)
    assert result.success is True
    assert result.status is ResultStatus.SUCCESS
    assert result.firmware_path == str(
        (project / ".pio" / "build" / "esp32dev" / "firmware.bin").resolve()
    )
    assert result.build_size_bytes == 6
    assert result.platform == "platformio"
    assert result.board == "esp32dev"
    assert result.toolchain_version == "6.1.18"
    assert result.warnings_count == 2
    assert result.failure is None
    assert len(manager.calls) == 2
    assert manager.calls[1].args == [
        "pio",
        "run",
        "--project-dir",
        str(project.resolve()),
        "--environment",
        "esp32dev",
    ]
    assert manager.calls[1].cwd == str(project.resolve())
    assert manager.calls[1].timeout_s == 45
    assert manager.calls[1].session_id == "session-123"
    assert manager.calls[1].env == {
        "PLATFORMIO_HOME_DIR": str(tmp_path / "pio-home")
    }


@pytest.mark.parametrize(
    ("setup", "message_fragment"),
    [
        (lambda root: root, "does not exist"),
        (
            lambda root: root.mkdir() or root,
            "missing platformio.ini",
        ),
        (
            lambda root: (
                root.mkdir(),
                (root / "platformio.ini").write_text(
                    "[platformio]\ndefault_envs = demo\n", encoding="utf-8"
                ),
                (root / "src").mkdir(),
                root,
            )[-1],
            "contains no [env:<name>] sections",
        ),
    ],
)
def test_invalid_project_fails_before_process_execution(
    tmp_path: Path,
    setup: Callable[[Path], Path],
    message_fragment: str,
) -> None:
    project = setup(tmp_path / "invalid")
    manager = FakeSubprocessManager()

    result = run_build(manager, BuildConfig(project_dir=project))

    assert result.success is False
    assert result.status is ResultStatus.FAILED
    assert message_fragment in result.message
    assert result.process_result is None
    assert result.metadata["validation_failed"] is True
    assert manager.calls == []


def test_missing_source_directory_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "firmware"
    project.mkdir()
    (project / "platformio.ini").write_text(
        "[env:native]\nplatform = native\n", encoding="utf-8"
    )

    result = run_build(
        FakeSubprocessManager(),
        BuildConfig(project_dir=project),
    )

    assert result.success is False
    assert "source directory does not exist" in result.message


def test_malformed_platformio_ini_is_rejected(tmp_path: Path) -> None:
    project = tmp_path / "firmware"
    project.mkdir()
    (project / "src").mkdir()
    (project / "platformio.ini").write_text(
        "[env:one\nboard = uno\n", encoding="utf-8"
    )

    result = run_build(
        FakeSubprocessManager(),
        BuildConfig(project_dir=project),
    )

    assert result.success is False
    assert "Invalid PlatformIO project" in result.message


def test_unknown_requested_environment_is_rejected(tmp_path: Path) -> None:
    project = make_project(tmp_path / "firmware")

    result = run_build(
        FakeSubprocessManager(),
        BuildConfig(project_dir=project, environment="not-defined"),
    )

    assert result.success is False
    assert "not-defined" in result.message
    assert "not defined" in result.message


def test_custom_source_directory_and_inherited_board_are_supported(
    tmp_path: Path,
) -> None:
    project = make_project(
        tmp_path / "firmware",
        ini=(
            "[platformio]\n"
            "src_dir = source\n"
            "default_envs = release\n\n"
            "[env:base]\n"
            "platform = ststm32\n"
            "board = nucleo_f446re\n\n"
            "[env:release]\n"
            "extends = env:base\n"
        ),
        source_dir="source",
    )

    def successful_build(config: ProcessConfig) -> ProcessResult:
        create_artifact(project, "release", "firmware.elf")
        return process_result()

    result = run_build(
        FakeSubprocessManager(version_result(), successful_build),
        BuildConfig(project_dir=project),
    )

    assert result.success is True
    assert result.board == "nucleo_f446re"
    assert result.metadata["artifact_environment"] == "release"
    assert result.metadata["artifact_type"] == "elf"


def test_custom_build_directory_is_used_for_artifact_discovery(
    tmp_path: Path,
) -> None:
    project = make_project(
        tmp_path / "firmware",
        ini=(
            "[platformio]\n"
            "build_dir = output\n\n"
            "[env:esp32dev]\n"
            "platform = espressif32\n"
            "board = esp32dev\n"
        ),
    )

    def successful_build(config: ProcessConfig) -> ProcessResult:
        output_dir = project / "output" / "esp32dev"
        output_dir.mkdir(parents=True)
        (output_dir / "firmware.bin").write_bytes(b"custom")
        return process_result()

    result = run_build(
        FakeSubprocessManager(version_result(), successful_build),
        BuildConfig(project_dir=project),
    )

    assert result.success is True
    assert result.firmware_path == str(
        (project / "output" / "esp32dev" / "firmware.bin").resolve()
    )
    assert result.metadata["build_root"] == str((project / "output").resolve())


def test_compiler_failure_uses_failure_classifier(tmp_path: Path) -> None:
    project = make_project(tmp_path / "firmware")
    failed_process = process_result(
        returncode=1,
        stderr="src/main.cpp:10:5: error: 'value' was not declared in this scope",
    )
    manager = FakeSubprocessManager(version_result(), failed_process)

    result = run_build(manager, BuildConfig(project_dir=project))

    assert result.success is False
    assert result.status is ResultStatus.FAILED
    assert result.process_result is failed_process
    assert result.failure is not None
    assert result.failure.category == "COMPILATION_ERROR"
    assert result.failure.retryable is False
    assert result.failure.stage == "build"
    assert "not declared" in (result.failure.raw_output or "")
    assert result.failure.metadata["confidence"] >= 0.9


def test_timeout_returns_timeout_build_result(tmp_path: Path) -> None:
    project = make_project(tmp_path / "firmware")
    timed_out = process_result(
        returncode=-1,
        stderr="build interrupted",
        timed_out=True,
        elapsed_s=12.5,
    )

    result = run_build(
        FakeSubprocessManager(version_result(), timed_out),
        BuildConfig(project_dir=project),
    )

    assert result.success is False
    assert result.status is ResultStatus.TIMEOUT
    assert result.failure is not None
    assert result.failure.exception_type == "ProcessTimeoutError"
    assert "12.5s" in result.message


def test_missing_platformio_executable_is_classified(tmp_path: Path) -> None:
    project = make_project(tmp_path / "firmware")

    result = run_build(
        FakeSubprocessManager(FileNotFoundError("pio was not found")),
        BuildConfig(project_dir=project),
    )

    assert result.success is False
    assert result.failure is not None
    assert result.failure.category == "TOOLCHAIN_MISSING"
    assert result.failure.retryable is False
    assert result.process_result is None
    assert len(result.metadata) > 0


def test_version_probe_failure_does_not_block_build(tmp_path: Path) -> None:
    project = make_project(tmp_path / "firmware")

    def successful_build(config: ProcessConfig) -> ProcessResult:
        create_artifact(project, "esp32dev")
        return process_result()

    result = run_build(
        FakeSubprocessManager(
            process_result(returncode=1, stderr="version unavailable"),
            successful_build,
        ),
        BuildConfig(project_dir=project),
    )

    assert result.success is True
    assert result.toolchain_version == ""


def test_success_without_artifact_is_reported_as_build_error(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path / "firmware")
    successful_process = process_result(stdout="SUCCESS")

    result = run_build(
        FakeSubprocessManager(version_result(), successful_process),
        BuildConfig(project_dir=project),
    )

    assert result.success is False
    assert result.status is ResultStatus.FAILED
    assert result.process_result is successful_process
    assert result.failure is not None
    assert result.failure.category == "BUILD_ERROR"
    assert result.failure.exception_type == "ArtifactNotFoundError"


def test_artifact_selection_prefers_flashable_format_and_selected_environment(
    tmp_path: Path,
) -> None:
    project = make_project(
        tmp_path / "firmware",
        ini=(
            "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\n\n"
            "[env:uno]\nplatform = atmelavr\nboard = uno\n"
        ),
    )
    create_artifact(project, "uno", "firmware.hex", b"old-other-env")

    def successful_build(config: ProcessConfig) -> ProcessResult:
        create_artifact(project, "esp32dev", "firmware.elf", b"elf")
        create_artifact(project, "esp32dev", "firmware.bin", b"binary")
        return process_result()

    result = run_build(
        FakeSubprocessManager(version_result(), successful_build),
        BuildConfig(project_dir=project, environment="esp32dev"),
    )

    assert result.success is True
    assert result.firmware_path is not None
    assert result.firmware_path.endswith("esp32dev\\firmware.bin") or (
        result.firmware_path.endswith("esp32dev/firmware.bin")
    )
    assert result.build_size_bytes == len(b"binary")
    assert all("uno" not in path for path in result.metadata["artifacts"])


def test_changed_artifact_wins_across_multiple_environments(
    tmp_path: Path,
) -> None:
    project = make_project(
        tmp_path / "firmware",
        ini=(
            "[env:one]\nplatform = native\n\n"
            "[env:two]\nplatform = native\n"
        ),
    )
    create_artifact(project, "one", "firmware.bin", b"stale")

    def successful_build(config: ProcessConfig) -> ProcessResult:
        create_artifact(project, "two", "firmware.hex", b"new")
        return process_result()

    result = run_build(
        FakeSubprocessManager(version_result(), successful_build),
        BuildConfig(project_dir=project),
    )

    assert result.success is True
    assert result.metadata["artifact_environment"] == "two"
    assert result.firmware_path is not None
    assert result.firmware_path.endswith("firmware.hex")


@pytest.mark.parametrize(
    "extra_args",
    [
        ("--target", "upload"),
        ("--target", "clean"),
        ("-t", "erase"),
        ("uploadfs",),
        ("--target=monitor",),
        ("--project-dir", "other"),
        ("--environment=other",),
    ],
)
def test_config_rejects_non_build_or_scope_changing_arguments(
    tmp_path: Path,
    extra_args: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        BuildConfig(project_dir=tmp_path, extra_args=extra_args)


def test_safe_extra_args_are_forwarded_as_separate_arguments(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path / "firmware")

    def successful_build(config: ProcessConfig) -> ProcessResult:
        create_artifact(project, "esp32dev")
        return process_result()

    manager = FakeSubprocessManager(version_result(), successful_build)
    result = run_build(
        manager,
        BuildConfig(project_dir=project, extra_args=("--verbose", "-j", "2")),
    )

    assert result.success is True
    assert manager.calls[1].args[-3:] == ["--verbose", "-j", "2"]


def test_cancellation_is_propagated(tmp_path: Path) -> None:
    project = make_project(tmp_path / "firmware")
    manager = FakeSubprocessManager(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        run_build(manager, BuildConfig(project_dir=project))


def test_convenience_function_uses_supplied_manager(tmp_path: Path) -> None:
    project = make_project(tmp_path / "firmware")

    def successful_build(config: ProcessConfig) -> ProcessResult:
        create_artifact(project, "esp32dev")
        return process_result()

    manager = FakeSubprocessManager(version_result(), successful_build)
    result = asyncio.run(
        build_firmware(BuildConfig(project_dir=project), manager)
    )

    assert result.success is True
    assert len(manager.calls) == 2
