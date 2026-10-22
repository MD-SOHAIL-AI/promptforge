import asyncio

from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.subagents import ForgeSubagentRunner
from tests.unit.test_tool_runtime import plan


class AsyncFinalPlanner:
    provider_id = "async-test"
    model_id = "async-model"

    def __init__(self):
        self.async_calls = 0
        self.summaries_awaited = 0

    def request_turn(self, **_kwargs):
        raise AssertionError("the synchronous planner path must not be used")

    def for_role(self, _role):
        return self

    async def request_turn_async(self, *, on_summary=None, **_kwargs):
        self.async_calls += 1
        if on_summary is not None:
            await on_summary("Awaited summary")
            self.summaries_awaited += 1
        return plan(final=True)


async def progress(_event, _payload):
    await asyncio.sleep(0)


def test_autonomous_loop_awaits_canonical_planner_directly(tmp_path):
    planner = AsyncFinalPlanner()
    loop = AutonomousForgeAgentLoop(
        workspace_root=tmp_path, planner=planner, platformio=object(), progress=progress,
    )
    result = asyncio.run(loop.run(task="Inspect the workspace", require_build=False))
    assert result.success
    assert planner.async_calls == planner.summaries_awaited == 1


def test_subagent_awaits_role_planner_directly(tmp_path):
    planner = AsyncFinalPlanner()
    runner = ForgeSubagentRunner(
        workspace_root=tmp_path, planner=planner, platformio=object(), progress=progress,
    )
    result = asyncio.run(runner.run(role="explore", task="Inspect the workspace"))
    assert result.success
    assert planner.async_calls == planner.summaries_awaited == 1
