from __future__ import annotations

from backend.models.tool_result import ToolResult, ToolStatus


def test_tool_result_status_helpers_and_round_trip() -> None:
    result = ToolResult(
        tool_name="build_firmware",
        status=ToolStatus.SUCCESS,
        message="build completed",
        execution_time_ms=125,
        metadata={"artifacts": ["firmware.bin"]},
    )

    assert result.is_success() is True
    assert result.is_failure() is False
    assert ToolResult.from_json(result.to_json()) == result
    assert result.to_dict()["metadata"] == {
        "artifacts": ["firmware.bin"]
    }


def test_partial_tool_result_is_neither_success_nor_failure() -> None:
    result = ToolResult("monitor", ToolStatus.PARTIAL, "partial output", 10)

    assert result.is_success() is False
    assert result.is_failure() is False
