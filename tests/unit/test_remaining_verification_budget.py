"""Continuations reserve verification from remaining time, preserving job limits."""
import asyncio
import time

import pytest

from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.tool_contracts import ProductToolPlan


@pytest.mark.parametrize("spent", [0, 700, 850])
def test_continuation_verification_reserve_uses_remaining_job_time(tmp_path, spent):
    class Planner:
        def bind_deadline(self, deadline): self.deadline = deadline
        def bind_verification_reserve(self, seconds): self.reserve = seconds
        async def request_turn_async(self, **kwargs):
            assert 0 < self.reserve <= max(0, 900 - spent - 5) / 3
            assert self.deadline - time.monotonic() <= 900 - spent
            return ProductToolPlan.parse({"version": "forgex.toolplan.v2", "summary": "Inspection complete.",
                "tool_calls": [], "final": True})
    planner = Planner()
    async def progress(*args): pass
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=planner, platformio=object(),
        progress=progress, job_limits={"elapsed_seconds": 900}, job_consumed={"elapsed_seconds": spent},
        resume_checkpoint={"turn": 1, "elapsed_seconds": spent, "observations": []})
    result = asyncio.run(loop.run(task="Inspect this workspace", require_build=False))
    assert result.success
    assert loop.max_runtime_seconds == 900
