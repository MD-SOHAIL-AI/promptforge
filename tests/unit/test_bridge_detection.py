from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from backend.bridges.base import MAX_VERSION_OUTPUT
from backend.bridges.detection import BridgeDetectionService
from backend.bridges.auth_policy import SafeStatusCommand
from backend.bridges.base import BridgeDetector
from backend.bridges.providers.antigravity_cli import AntigravityCliDetector
from backend.bridges.providers.codex import CodexDetector


class FakeRunner:
    def __init__(self, responses: dict[tuple[str, ...], subprocess.CompletedProcess[str] | BaseException]) -> None:
        self.responses = responses
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def __call__(self, args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((tuple(args), dict(kwargs)))
        assert kwargs.get("shell") is False
        response = self.responses.get(tuple(args))
        if isinstance(response, BaseException):
            raise response
        if response is not None:
            return response
        return subprocess.CompletedProcess(list(args), 1, "", "")


def completed(args: Sequence[str], returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(args), returncode, stdout, stderr)


def test_missing_tool_returns_not_installed() -> None:
    runner = FakeRunner({("where.exe", "codex"): completed(("where.exe", "codex"), returncode=1)})

    result = CodexDetector(command_runner=runner, platform_name="Windows").detect()

    assert result.installed is False
    assert result.auth_status == "not_installed"
    assert result.version is None
    assert result.detection_classification == "CODEX_CLI_NOT_FOUND"


def test_codex_discovery_failure_is_not_misreported_as_not_found() -> None:
    runner = FakeRunner({("where.exe", "codex"): OSError("discovery unavailable")})

    result = CodexDetector(command_runner=runner, platform_name="Windows").detect()

    assert result.installed is False
    assert result.detection_classification == "CODEX_CLI_DETECTION_FAILED"
    assert result.auth_classification == "CODEX_OAUTH_STATUS_FAILED"


def test_found_executable_returns_installed_and_version() -> None:
    exe = str(Path("C:/Tools/Codex/codex.exe"))
    runner = FakeRunner(
        {
            ("where.exe", "codex"): completed(("where.exe", "codex"), stdout=f"{exe}\n"),
            (exe, "--version"): completed((exe, "--version"), stdout="codex 1.2.3\n"),
            (exe, "login", "--help"): completed(
                (exe, "login", "--help"), stdout="Commands:\n  status  Show login status\n"
            ),
            (exe, "login", "status"): completed(
                (exe, "login", "status"), stdout="Logged in using ChatGPT\n"
            ),
        }
    )

    result = CodexDetector(command_runner=runner, platform_name="Windows").detect()

    assert result.installed is True
    assert result.version == "codex 1.2.3"
    assert result.provider_id == "codex_cli_oauth_bridge"
    assert result.display_name == "Codex CLI OAuth Bridge"
    assert result.auth_status == "authenticated"
    assert result.status_confidence == "high"
    assert result.safe_status_checked is True
    assert result.checked_commands == ("codex --version", "codex login --help", "codex login status")
    assert result.detection_classification == "CODEX_CLI_DETECTED"
    assert result.auth_classification == "CODEX_OAUTH_AUTHENTICATED"
    assert result.executable_found is True
    assert result.version_detected is True
    assert result.executable_path is None
    assert result.auth_mode == "official_codex_cli_oauth"
    assert result.execution_mode == "login_helper"
    assert result.workspace_mode == "none_for_login"
    assert result.production_eligible is False
    assert result.qa_only is True
    assert result.smoke_ready is True
    assert result.capabilities.run_prompt is False
    assert result.can_run is False


def test_version_command_timeout_is_handled_safely() -> None:
    exe = "/usr/local/bin/codex"
    runner = FakeRunner(
        {
            ("which", "codex"): completed(("which", "codex"), stdout=f"{exe}\n"),
            (exe, "--version"): subprocess.TimeoutExpired([exe, "--version"], timeout=4.0),
        }
    )

    result = CodexDetector(command_runner=runner, platform_name="Linux").detect()

    assert result.installed is True
    assert result.version is None
    assert "timed out" in result.warnings[0]


def test_unsafe_version_output_is_not_returned() -> None:
    exe = "/usr/local/bin/codex"
    long_output = "codex " + ("x" * (MAX_VERSION_OUTPUT + 50))
    runner = FakeRunner(
        {
            ("which", "codex"): completed(("which", "codex"), stdout=f"{exe}\n"),
            (exe, "--version"): completed((exe, "--version"), stdout=long_output),
        }
    )

    result = CodexDetector(command_runner=runner, platform_name="Linux").detect()

    assert result.version is None
    assert result.version_detected is False
    assert result.detection_classification == "CODEX_CLI_VERSION_UNSUPPORTED"
    assert "truncated" in result.warnings[0]


def test_detection_service_reports_all_supported_bridges_without_shell() -> None:
    runner = FakeRunner({})

    results = BridgeDetectionService(command_runner=runner, platform_name="Linux").detect_all()

    assert [result.provider_id for result in results] == [
        "codex_cli_oauth_bridge",
        "claude_code_bridge",
        "antigravity_cli_bridge",
    ]
    assert "gemini_cli_bridge" not in [result.provider_id for result in results]
    assert all(call_kwargs.get("shell") is False for _, call_kwargs in runner.calls)
    assert all(result.capabilities.run_prompt is False for result in results)


def test_codex_login_status_reports_not_authenticated_without_exposing_output() -> None:
    exe = "/usr/local/bin/codex"
    runner = FakeRunner(
        {
            ("which", "codex"): completed(("which", "codex"), stdout=f"{exe}\n"),
            (exe, "--version"): completed((exe, "--version"), stdout="codex-cli 0.142.5\n"),
            (exe, "login", "--help"): completed(
                (exe, "login", "--help"), stdout="Commands:\n  status  Show login status\n"
            ),
            (exe, "login", "status"): completed(
                (exe, "login", "status"), returncode=1, stdout="Not logged in\n"
            ),
        }
    )

    result = CodexDetector(command_runner=runner, platform_name="Linux").detect()

    assert result.auth_status == "unauthenticated"
    assert result.auth_classification == "CODEX_OAUTH_NOT_AUTHENTICATED"
    assert result.login_command == "codex login"
    assert result.smoke_ready is False
    assert "Not logged in" not in str(result.to_dict())


def test_codex_api_key_session_is_not_classified_as_oauth() -> None:
    exe = "/usr/local/bin/codex"
    runner = FakeRunner(
        {
            ("which", "codex"): completed(("which", "codex"), stdout=f"{exe}\n"),
            (exe, "--version"): completed((exe, "--version"), stdout="codex-cli 0.142.5\n"),
            (exe, "login", "--help"): completed(
                (exe, "login", "--help"), stdout="Commands:\n  status  Show login status\n"
            ),
            (exe, "login", "status"): completed(
                (exe, "login", "status"), stdout="Logged in using an API key - secret-redacted\n"
            ),
        }
    )

    result = CodexDetector(command_runner=runner, platform_name="Linux").detect()

    assert result.auth_status == "unknown"
    assert result.auth_classification == "CODEX_OAUTH_STATUS_UNKNOWN"
    assert "secret-redacted" not in str(result.to_dict())


def test_codex_without_login_status_is_version_unsupported() -> None:
    exe = "/usr/local/bin/codex"
    runner = FakeRunner(
        {
            ("which", "codex"): completed(("which", "codex"), stdout=f"{exe}\n"),
            (exe, "--version"): completed((exe, "--version"), stdout="codex-cli 0.20.0\n"),
            (exe, "login", "--help"): completed((exe, "login", "--help"), stdout="Manage login\n"),
        }
    )

    result = CodexDetector(command_runner=runner, platform_name="Linux").detect()

    assert result.detection_classification == "CODEX_CLI_VERSION_UNSUPPORTED"
    assert result.auth_status == "unknown"


def test_missing_antigravity_cli_returns_not_installed() -> None:
    runner = FakeRunner(
        {
            ("where.exe", "agy"): completed(("where.exe", "agy"), returncode=1),
            ("where.exe", "antigravity"): completed(("where.exe", "antigravity"), returncode=1),
        }
    )

    result = AntigravityCliDetector(command_runner=runner, platform_name="Windows").detect()

    assert result.provider_id == "antigravity_cli_bridge"
    assert result.display_name == "Google Antigravity / AGY CLI"
    assert result.installed is False
    assert result.auth_status == "not_installed"
    assert result.status_confidence == "high"


def test_found_agy_returns_installed_and_version_without_running_tui() -> None:
    exe = "/usr/local/bin/agy"
    runner = FakeRunner(
        {
            ("which", "agy"): completed(("which", "agy"), stdout=f"{exe}\n"),
            (exe, "--version"): completed((exe, "--version"), stdout="agy 0.47.0\n"),
            (exe, "--help"): completed((exe, "--help"), stdout="Usage: agy [OPTIONS]\n"),
        }
    )

    result = AntigravityCliDetector(command_runner=runner, platform_name="Linux").detect()

    assert result.installed is True
    assert result.version == "agy 0.47.0"
    assert result.auth_status == "unknown"
    assert result.status_confidence == "low"
    assert result.capabilities.run_prompt is False
    assert (exe,) not in [args for args, _ in runner.calls]


def test_antigravity_fallback_command_can_detect_version() -> None:
    exe = "/usr/local/bin/antigravity"
    runner = FakeRunner(
        {
            ("which", "agy"): completed(("which", "agy"), returncode=1),
            ("which", "antigravity"): completed(("which", "antigravity"), stdout=f"{exe}\n"),
            (exe, "--version"): completed((exe, "--version"), stdout="antigravity 0.47.0\n"),
            (exe, "--help"): completed((exe, "--help"), stdout="Usage: antigravity [OPTIONS]\n"),
        }
    )

    result = AntigravityCliDetector(command_runner=runner, platform_name="Linux").detect()

    assert result.installed is True
    assert result.version == "antigravity 0.47.0"
    assert result.checked_commands[:2] == ("antigravity --version", "antigravity --help")


def test_agy_version_output_is_capped() -> None:
    exe = "/usr/local/bin/agy"
    long_output = "agy " + ("x" * (MAX_VERSION_OUTPUT + 50))
    runner = FakeRunner(
        {
            ("which", "agy"): completed(("which", "agy"), stdout=f"{exe}\n"),
            (exe, "--version"): completed((exe, "--version"), stdout=long_output),
            (exe, "--help"): completed((exe, "--help"), stdout="Usage: agy\n"),
        }
    )

    result = AntigravityCliDetector(command_runner=runner, platform_name="Linux").detect()

    assert result.version is not None
    assert len(result.version) <= MAX_VERSION_OUTPUT
    assert "truncated" in result.warnings[0]


def test_detector_maps_declared_safe_status_to_authenticated() -> None:
    class TestDetector(BridgeDetector):
        provider_id = "test_bridge"
        display_name = "Test Bridge"
        command_names = ("tool",)
        safe_status_commands = (SafeStatusCommand(("status",), "documented status"),)

    runner = FakeRunner(
        {
            ("which", "tool"): completed(("which", "tool"), stdout="/bin/tool\n"),
            ("/bin/tool", "--version"): completed(("/bin/tool", "--version"), stdout="tool 1.0.0\n"),
            ("/bin/tool", "--help"): completed(("/bin/tool", "--help"), stdout="Commands:\n  status\n"),
            ("/bin/tool", "status"): completed(("/bin/tool", "status"), stdout="logged in"),
        }
    )

    result = TestDetector(command_runner=runner, platform_name="Linux").detect()

    assert result.auth_status == "authenticated"
    assert result.status_confidence == "high"
    assert result.checked_commands == ("tool --version", "tool --help", "tool status")


def test_detector_does_not_run_undiscoverable_status_command() -> None:
    class TestDetector(BridgeDetector):
        provider_id = "test_bridge"
        display_name = "Test Bridge"
        command_names = ("tool",)
        safe_status_commands = (SafeStatusCommand(("status",), "documented status"),)

    runner = FakeRunner(
        {
            ("which", "tool"): completed(("which", "tool"), stdout="/bin/tool\n"),
            ("/bin/tool", "--version"): completed(("/bin/tool", "--version"), stdout="tool 1.0.0\n"),
            ("/bin/tool", "--help"): completed(("/bin/tool", "--help"), stdout="Commands:\n  ask\n"),
        }
    )

    result = TestDetector(command_runner=runner, platform_name="Linux").detect()

    assert result.auth_status == "unknown"
    assert ("/bin/tool", "status") not in [args for args, _ in runner.calls]
