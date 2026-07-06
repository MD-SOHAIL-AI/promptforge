from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Any

import pytest

from backend.bridges.auth_policy import (
    BridgeAuthPolicy,
    MAX_STATUS_OUTPUT,
    SafeStatusCommand,
    is_allowed_probe_args,
    parse_auth_status,
)


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


def test_parse_auth_status_prefers_unauthenticated_phrases() -> None:
    assert parse_auth_status("not authenticated") == "unauthenticated"
    assert parse_auth_status("authenticated") == "authenticated"
    assert parse_auth_status("status unavailable") == "unknown"


def test_safe_status_command_can_map_to_authenticated() -> None:
    runner = FakeRunner(
        {
            ("/bin/tool", "--help"): completed(("/bin/tool", "--help"), stdout="Commands:\n  status\n"),
            ("/bin/tool", "status"): completed(("/bin/tool", "status"), stdout="Authenticated as user@example.com"),
        }
    )
    policy = BridgeAuthPolicy(command_runner=runner)

    result = policy.detect_auth_status(
        executable="/bin/tool",
        command_name="tool",
        safe_status_commands=(SafeStatusCommand(("status",), "documented status"),),
        unknown_message="unknown",
    )

    assert result.auth_status == "authenticated"
    assert result.status_confidence == "high"
    assert result.setup_action == "none"
    assert result.checked_commands == ("tool --help", "tool status")


def test_safe_status_command_can_map_to_unauthenticated() -> None:
    runner = FakeRunner(
        {
            ("/bin/tool", "--help"): completed(("/bin/tool", "--help"), stdout="Commands:\n  auth status\n"),
            ("/bin/tool", "auth", "status"): completed(
                ("/bin/tool", "auth", "status"),
                returncode=1,
                stderr="Login required",
            ),
        }
    )
    policy = BridgeAuthPolicy(command_runner=runner)

    result = policy.detect_auth_status(
        executable="/bin/tool",
        command_name="tool",
        safe_status_commands=(SafeStatusCommand(("auth", "status"), "documented auth status"),),
        unknown_message="unknown",
    )

    assert result.auth_status == "unauthenticated"
    assert result.status_confidence == "high"
    assert result.setup_action == "run_official_login_manually"


def test_unsafe_interactive_command_is_not_allowed() -> None:
    assert is_allowed_probe_args(("/bin/tool",)) is False
    assert is_allowed_probe_args(("/bin/tool", "login")) is False
    assert is_allowed_probe_args(("/bin/tool", "auth", "status")) is True


def test_status_timeout_returns_unknown_safely() -> None:
    runner = FakeRunner(
        {
            ("/bin/tool", "--help"): completed(("/bin/tool", "--help"), stdout="Commands:\n  status\n"),
            ("/bin/tool", "status"): subprocess.TimeoutExpired(["/bin/tool", "status"], timeout=4.0),
        }
    )
    policy = BridgeAuthPolicy(command_runner=runner)

    result = policy.detect_auth_status(
        executable="/bin/tool",
        command_name="tool",
        safe_status_commands=(SafeStatusCommand(("status",), "documented status"),),
        unknown_message="unknown",
    )

    assert result.auth_status == "unknown"
    assert "timed out" in result.warnings[0]


def test_status_output_is_capped_before_parsing_unknown() -> None:
    long_output = "x" * (MAX_STATUS_OUTPUT + 100)
    runner = FakeRunner(
        {
            ("/bin/tool", "--help"): completed(("/bin/tool", "--help"), stdout="Commands:\n  status\n"),
            ("/bin/tool", "status"): completed(("/bin/tool", "status"), stdout=long_output),
        }
    )
    policy = BridgeAuthPolicy(command_runner=runner)

    result = policy.detect_auth_status(
        executable="/bin/tool",
        command_name="tool",
        safe_status_commands=(SafeStatusCommand(("status",), "documented status"),),
        unknown_message="unknown",
    )

    assert result.auth_status == "unknown"
    assert "truncated" in result.warnings[0]


def test_policy_rejects_unsafe_runner_invocation() -> None:
    runner = FakeRunner({})
    policy = BridgeAuthPolicy(command_runner=runner)

    with pytest.raises(ValueError):
        policy._run(("/bin/tool",))
