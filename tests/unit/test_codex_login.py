from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from backend.bridges.base import DetectedExecutable
from backend.bridges.codex_login import CodexLoginService


class FakeDetector:
    def __init__(self, result: SimpleNamespace, executable: DetectedExecutable | None = None) -> None:
        self.result = result
        self.executable = executable
        self.detect_calls = 0

    def detect(self) -> SimpleNamespace:
        self.detect_calls += 1
        return self.result

    def resolve_executable(self) -> DetectedExecutable | None:
        return self.executable


def detection(*, installed: bool = True, version: str | None = "codex-cli 0.142.5", auth_status: str = "authenticated", status_available: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        installed=installed,
        version=version,
        auth_status=auth_status,
        checked_commands=("codex --version", "codex login --help", "codex login status") if status_available else ("codex --version", "codex login --help"),
    )


def test_status_maps_only_official_cli_result_to_ready(tmp_path) -> None:
    service = CodexLoginService(
        detector=FakeDetector(detection()),  # type: ignore[arg-type]
        temp_root=tmp_path,
    )

    result = service.status().to_safe_dict()

    assert result["auth_status"] == "signed_in"
    assert result["auth_classification"] == "CODEX_AUTH_STATUS_SIGNED_IN"
    assert result["bridge_classification"] == "CODEX_OAUTH_BRIDGE_READY"
    assert result["oauth_bridge_ready"] is True
    assert result["tokens_read"] is False
    assert result["auth_files_read"] is False
    assert result["production_routing_enabled"] is False


def test_launch_refuses_without_confirmation_before_detection(tmp_path) -> None:
    detector = FakeDetector(detection())
    service = CodexLoginService(detector=detector, temp_root=tmp_path)  # type: ignore[arg-type]

    result = service.launch(confirm_launch_codex_login=False)

    assert result.classification == "CODEX_LOGIN_CONFIRMATION_REQUIRED"
    assert result.launched is False
    assert detector.detect_calls == 0


def test_launch_uses_direct_login_argv_and_neutral_temp_cwd(tmp_path) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_popen(args: list[str], **kwargs: Any) -> object:
        calls.append((args, kwargs))
        return object()

    detector = FakeDetector(
        detection(),
        DetectedExecutable("C:/Tools/node.exe", "codex", ("C:/Tools/codex.js",)),
    )
    service = CodexLoginService(
        detector=detector,  # type: ignore[arg-type]
        popen_factory=fake_popen,
        temp_root=tmp_path,
    )

    result = service.launch(confirm_launch_codex_login=True)

    assert result.classification == "CODEX_LOGIN_LAUNCHED"
    assert result.launched is True
    assert calls[0][0] == ["C:/Tools/node.exe", "C:/Tools/codex.js", "login"]
    assert calls[0][1]["shell"] is False
    assert str(calls[0][1]["cwd"]).startswith(str(tmp_path))
    assert "stdout" in calls[0][1] and "stderr" in calls[0][1]


def test_missing_cli_returns_not_found_without_spawning(tmp_path) -> None:
    spawned = False

    def fake_popen(*args: Any, **kwargs: Any) -> object:
        nonlocal spawned
        spawned = True
        return object()

    service = CodexLoginService(
        detector=FakeDetector(detection(installed=False, version=None, auth_status="not_installed")),  # type: ignore[arg-type]
        popen_factory=fake_popen,
        temp_root=tmp_path,
    )

    result = service.launch(confirm_launch_codex_login=True)

    assert result.classification == "CODEX_CLI_NOT_FOUND"
    assert spawned is False


def test_launch_rejects_temp_directory_inside_current_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    spawned = False

    def fake_popen(*args: Any, **kwargs: Any) -> object:
        nonlocal spawned
        spawned = True
        return object()

    detector = FakeDetector(
        detection(),
        DetectedExecutable("/usr/local/bin/codex", "codex"),
    )
    service = CodexLoginService(
        detector=detector,  # type: ignore[arg-type]
        popen_factory=fake_popen,
        temp_root=tmp_path,
    )

    result = service.launch(confirm_launch_codex_login=True)

    assert result.classification == "CODEX_LOGIN_UNSAFE_ABORTED"
    assert spawned is False
