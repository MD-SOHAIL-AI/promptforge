from __future__ import annotations

import pytest

from backend.agent_runtime.slash_commands import parse_agent_slash_command


def test_non_command_is_unchanged() -> None:
    assert parse_agent_slash_command("build an ESP32 project") is None


def test_subagent_command_is_bounded_and_read_only() -> None:
    command = parse_agent_slash_command("/subagent review inspect pin safety")
    assert command is not None
    assert command.autonomy == "read_only"
    assert "'review' subagent" in command.instruction
    assert "without modifying files" in command.instruction


def test_plan_command_selects_plan_mode() -> None:
    command = parse_agent_slash_command("/plan add a sensor driver")
    assert command is not None
    assert command.autonomy == "plan_only"
    assert "sensor driver" in command.instruction


@pytest.mark.parametrize("content", ["/unknown thing", "/plan", "/subagent writer task"])
def test_invalid_commands_fail_before_model_routing(content: str) -> None:
    with pytest.raises(ValueError):
        parse_agent_slash_command(content)
