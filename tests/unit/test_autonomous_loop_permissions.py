from __future__ import annotations

import asyncio

from backend.agent_runtime.api_planner_provider import API_PRODUCT_SMOKE_TASK, _is_smoke_task
from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.tool_contracts import ProductToolPlan


class FinalPlanner:
    def __init__(self) -> None:
        self.allowed_tools: tuple[str, ...] = ()

    def request_turn(self, *, allowed_tools, **_kwargs):
        self.allowed_tools = tuple(allowed_tools)
        return ProductToolPlan.parse({
            "version": "forgex.toolplan.v2",
            "summary": "workspace inspected",
            "tool_calls": [],
            "final": True,
        })


class FailedBuild:
    success = False
    firmware_path = None
    process_result = None
    status = "BUILD_ERROR"
    message = "compile failed"
    build_size_bytes = 0
    board = "esp32dev"
    platform = "espressif32"
    warnings_count = 0

    def api_dict(self):
        return {"success": False, "message": self.message}


class FailedPlatformIO:
    async def build(self, *_args, **_kwargs):
        return FailedBuild()


def test_build_only_loop_has_no_write_tools_and_does_not_repair(tmp_path) -> None:
    async def progress(*_args):
        return None

    planner = FinalPlanner()
    loop = AutonomousForgeAgentLoop(
        workspace_root=tmp_path,
        planner=planner,  # type: ignore[arg-type]
        platformio=FailedPlatformIO(),  # type: ignore[arg-type]
        progress=progress,
    )
    result = asyncio.run(loop.run(task="Build only", allow_write=False, allow_repair=False))
    assert "write_file" not in planner.allowed_tools
    assert "edit_file_simple" not in planner.allowed_tools
    assert result.classification == "BUILD_ERROR"
    assert result.repair_attempt_count == 0


def test_smoke_mode_requires_internal_exact_task() -> None:
    assert _is_smoke_task(API_PRODUCT_SMOKE_TASK)
    assert not _is_smoke_task("Fix smoke sensor calibration in production firmware")
