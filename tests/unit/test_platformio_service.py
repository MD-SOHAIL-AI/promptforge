from __future__ import annotations

import asyncio
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from backend.runtime.result import BuildResult, ResultStatus
from backend.runtime.subprocess_mgr import ProcessConfig, ProcessResult
from backend.services import platformio_service
from backend.services.platformio_service import (
    PlatformIOCommandError,
    PlatformIOEnvironment,
    PlatformIOProject,
    PlatformIOProjectError,
    PlatformIOResponseError,
    PlatformIOService,
)
from backend.tools.build_firmware import BuildConfig


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
) -> ProcessResult:
    return ProcessResult(
        returncode=returncode,
        stdout=stdout.encode(),
        stderr=stderr.encode(),
        args=["pio"],
        elapsed_s=0.1,
        timed_out=timed_out,
    )


def make_project(root: Path, ini: str | None = None) -> Path:
    root.mkdir()
    (root / "platformio.ini").write_text(
        ini
        or (
            "[platformio]\n"
            "default_envs = esp32dev\n\n"
            "[env]\n"
            "framework = arduino\n\n"
            "[env:base]\n"
            "platform = espressif32\n\n"
            "[env:esp32dev]\n"
            "extends = base\n"
            "board = esp32dev\n"
        ),
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "src" / "main.cpp").write_text("void setup() {}", encoding="utf-8")
    return root


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_environment_is_frozen_and_serializable() -> None:
    environment = PlatformIOEnvironment("esp32dev", "esp32dev", "arduino", "espressif32")

    assert PlatformIOEnvironment.from_dict(environment.to_dict()) == environment
    with pytest.raises(FrozenInstanceError):
        environment.name = "changed"  # type: ignore[misc]


def test_project_is_frozen_and_serializable(tmp_path: Path) -> None:
    root = make_project(tmp_path / "firmware")
    project = PlatformIOService(FakeSubprocessManager()).validate_project(root)

    assert PlatformIOProject.from_dict(project.to_dict()) == project
    with pytest.raises(FrozenInstanceError):
        project.project_path = "changed"  # type: ignore[misc]


def test_validate_project_resolves_environment_inheritance(tmp_path: Path) -> None:
    root = make_project(tmp_path / "firmware")

    project = PlatformIOService(FakeSubprocessManager()).validate_project(root)

    assert project.project_path == str(root.resolve())
    assert project.platformio_ini == str((root / "platformio.ini").resolve())
    assert project.environments == (
        PlatformIOEnvironment("base", "", "arduino", "espressif32"),
        PlatformIOEnvironment("esp32dev", "esp32dev", "arduino", "espressif32"),
    )


def test_discover_environments_preserves_declaration_order(tmp_path: Path) -> None:
    root = make_project(
        tmp_path / "firmware",
        "[env:two]\nboard = two\n\n[env:one]\nboard = one\n",
    )

    environments = PlatformIOService(FakeSubprocessManager()).discover_environments(root)

    assert [item.name for item in environments] == ["two", "one"]


@pytest.mark.parametrize("path_kind", ["missing", "file", "no_ini", "no_env", "no_src"])
def test_validate_project_rejects_invalid_projects(
    tmp_path: Path,
    path_kind: str,
) -> None:
    root = tmp_path / "firmware"
    if path_kind == "file":
        root.write_text("not a directory", encoding="utf-8")
    elif path_kind == "no_ini":
        root.mkdir()
    elif path_kind == "no_env":
        root.mkdir()
        (root / "platformio.ini").write_text("[platformio]\n", encoding="utf-8")
        (root / "src").mkdir()
    elif path_kind == "no_src":
        root.mkdir()
        (root / "platformio.ini").write_text("[env:test]\nboard = x\n", encoding="utf-8")

    with pytest.raises(PlatformIOProjectError):
        PlatformIOService(FakeSubprocessManager()).validate_project(root)


def test_validate_project_rejects_malformed_ini(tmp_path: Path) -> None:
    root = tmp_path / "firmware"
    root.mkdir()
    (root / "platformio.ini").write_text("[env:test\n", encoding="utf-8")

    with pytest.raises(PlatformIOProjectError, match="invalid platformio.ini"):
        PlatformIOService(FakeSubprocessManager()).validate_project(root)


def test_validate_project_rejects_undefined_and_cyclic_extends(tmp_path: Path) -> None:
    undefined = make_project(
        tmp_path / "undefined",
        "[env:test]\nextends = missing\n",
    )
    with pytest.raises(PlatformIOProjectError, match="undefined"):
        PlatformIOService(FakeSubprocessManager()).validate_project(undefined)

    cyclic = make_project(
        tmp_path / "cyclic",
        "[env:one]\nextends = two\n\n[env:two]\nextends = one\n",
    )
    with pytest.raises(PlatformIOProjectError, match="cyclic"):
        PlatformIOService(FakeSubprocessManager()).validate_project(cyclic)


def test_build_delegates_to_firmware_builder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_project(tmp_path / "firmware")
    manager = FakeSubprocessManager()
    seen: dict[str, Any] = {}
    expected = BuildResult(
        success=True,
        status=ResultStatus.SUCCESS,
        message="built",
        platform="platformio",
    )

    async def fake_build(self: Any, config: BuildConfig) -> BuildResult:
        seen["manager"] = self._subprocess_mgr
        seen["config"] = config
        return expected

    monkeypatch.setattr(platformio_service.FirmwareBuilder, "build", fake_build)
    service = PlatformIOService(
        manager,
        executable="custom-pio",
        timeout_s=90,
        env={"PLATFORMIO_HOME_DIR": "cache"},
        session_id="session-1",
    )

    result = run(service.build(root, environment="esp32dev", extra_args=("--verbose",)))

    assert result is expected
    assert seen["manager"] is manager
    config = seen["config"]
    assert config.project_dir == str(root.resolve())
    assert config.environment == "esp32dev"
    assert config.executable == "custom-pio"
    assert config.timeout_s == 90
    assert config.extra_args == ("--verbose",)
    assert config.env == {"PLATFORMIO_HOME_DIR": "cache"}
    assert config.session_id == "session-1"
    assert manager.calls == []


def test_build_accepts_existing_build_config_without_rewriting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_project(tmp_path / "firmware")
    manager = FakeSubprocessManager()
    config = BuildConfig(project_dir=root, environment="esp32dev", timeout_s=12)
    seen: list[BuildConfig] = []

    async def fake_build(self: Any, supplied: BuildConfig) -> BuildResult:
        seen.append(supplied)
        return BuildResult(True, ResultStatus.SUCCESS, message="built")

    monkeypatch.setattr(platformio_service.FirmwareBuilder, "build", fake_build)

    run(PlatformIOService(manager).build(config))

    assert seen == [config]


def test_build_rejects_unknown_environment_before_builder(tmp_path: Path) -> None:
    root = make_project(tmp_path / "firmware")

    with pytest.raises(PlatformIOProjectError, match="not defined"):
        run(PlatformIOService(FakeSubprocessManager()).build(root, environment="missing"))


def test_clean_uses_subprocess_manager_only(tmp_path: Path) -> None:
    root = make_project(tmp_path / "firmware")
    manager = FakeSubprocessManager(process_result(stdout="Done cleaning"))
    service = PlatformIOService(manager, timeout_s=30, session_id="session-1")

    result = run(service.clean(root, environment="esp32dev", timeout_s=15))

    assert result.success is True
    assert manager.calls[0].args == [
        "pio", "run", "--project-dir", str(root.resolve()),
        "--target", "clean", "--environment", "esp32dev",
    ]
    assert manager.calls[0].cwd == str(root.resolve())
    assert manager.calls[0].timeout_s == 15
    assert manager.calls[0].session_id == "session-1"


def test_install_dependencies_builds_canonical_command(tmp_path: Path) -> None:
    root = make_project(tmp_path / "firmware")
    manager = FakeSubprocessManager(process_result())

    result = run(
        PlatformIOService(manager).install_dependencies(
            root,
            environment="esp32dev",
            force=True,
        )
    )

    assert result.success is True
    assert manager.calls[0].args == [
        "pio", "pkg", "install", "--project-dir", str(root.resolve()),
        "--environment", "esp32dev", "--force",
    ]


def test_clean_and_install_return_nonzero_process_results(tmp_path: Path) -> None:
    root = make_project(tmp_path / "firmware")
    manager = FakeSubprocessManager(
        process_result(returncode=1, stderr="clean failed"),
        process_result(returncode=2, stderr="install failed"),
    )
    service = PlatformIOService(manager)

    assert run(service.clean(root)).returncode == 1
    assert run(service.install_dependencies(root)).returncode == 2


def test_list_boards_parses_and_freezes_json() -> None:
    payload = [
        {
            "id": "esp32dev",
            "name": "Espressif ESP32 Dev Module",
            "platform": "espressif32",
            "frameworks": ["arduino", "espidf"],
        }
    ]
    manager = FakeSubprocessManager(process_result(stdout=json.dumps(payload)))

    boards = run(
        PlatformIOService(manager).list_boards(
            "esp32",
            installed_only=True,
        )
    )

    assert manager.calls[0].args == [
        "pio", "boards", "--json-output", "--installed", "esp32",
    ]
    assert boards[0]["id"] == "esp32dev"
    assert boards[0]["frameworks"] == ("arduino", "espidf")
    assert isinstance(boards[0], MappingProxyType)
    with pytest.raises(TypeError):
        boards[0]["id"] = "changed"  # type: ignore[index]


@pytest.mark.parametrize(
    "result",
    [
        process_result(returncode=1, stderr="boards failed"),
        process_result(timed_out=True, returncode=-9),
    ],
)
def test_list_boards_raises_structured_command_error(result: ProcessResult) -> None:
    manager = FakeSubprocessManager(result)

    with pytest.raises(PlatformIOCommandError) as exc_info:
        run(PlatformIOService(manager).list_boards())

    assert exc_info.value.result is result
    assert exc_info.value.command[:2] == ("pio", "boards")


@pytest.mark.parametrize("stdout", ["not json", "{}", '["bad"]'])
def test_list_boards_rejects_malformed_json(stdout: str) -> None:
    manager = FakeSubprocessManager(process_result(stdout=stdout))

    with pytest.raises(PlatformIOResponseError):
        run(PlatformIOService(manager).list_boards())


def test_command_start_failure_is_structured(tmp_path: Path) -> None:
    root = make_project(tmp_path / "firmware")
    manager = FakeSubprocessManager(FileNotFoundError("pio missing"))

    with pytest.raises(PlatformIOCommandError, match="could not be started"):
        run(PlatformIOService(manager).clean(root))


def test_cancellation_propagates(tmp_path: Path) -> None:
    root = make_project(tmp_path / "firmware")
    manager = FakeSubprocessManager(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        run(PlatformIOService(manager).clean(root))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"executable": ""},
        {"timeout_s": 0},
        {"timeout_s": float("nan")},
        {"env": {"BAD\x00KEY": "value"}},
        {"session_id": ""},
    ],
)
def test_service_rejects_invalid_configuration(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        PlatformIOService(FakeSubprocessManager(), **kwargs)
