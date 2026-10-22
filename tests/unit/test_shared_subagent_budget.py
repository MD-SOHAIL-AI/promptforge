import asyncio

from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan


def _plan(calls=(), *, final=False, summary="working"):
    return ProductToolPlan.parse({
        "version": PRODUCT_TOOLPLAN_VERSION,
        "summary": summary,
        "tool_calls": list(calls),
        "final": final,
    })


class SequenceAsyncPlanner:
    provider_id = "test"
    model_id = "test-model"

    def __init__(self, turns, child=None):
        self.turns = list(turns)
        self.child = child

    def for_role(self, _role):
        return self.child or self

    async def request_turn_async(self, **_kwargs):
        return self.turns.pop(0)


async def _progress(_event, _payload):
    return None


def test_subagent_calls_share_the_parent_tool_budget(tmp_path):
    (tmp_path / "main.cpp").write_text("int main() {}\n", encoding="utf-8")
    child = SequenceAsyncPlanner([
        _plan([
            {"id": "child-list", "type": "list_files", "arguments": {}},
            {"id": "child-read", "type": "read_file", "arguments": {"path": "main.cpp"}},
        ]),
        _plan(final=True, summary="Child inspected the project."),
    ])
    parent = SequenceAsyncPlanner([
        _plan([{
            "id": "delegate",
            "type": "spawn_subagent",
            "arguments": {"role": "explore", "task": "Inspect main.cpp"},
        }]),
        _plan(final=True, summary="Inspection complete."),
    ], child=child)
    loop = AutonomousForgeAgentLoop(
        workspace_root=tmp_path,
        planner=parent,
        platformio=object(),
        progress=_progress,
        max_tool_calls=3,
    )

    result = asyncio.run(loop.run(task="Inspect the project", require_build=False))

    assert result.success
    assert result.tool_execution_count == 3
    assert loop.loop_guard.remaining_tool_calls == 0


def test_subagent_cannot_exceed_parent_remaining_budget(tmp_path):
    (tmp_path / "main.cpp").write_text("int main() {}\n", encoding="utf-8")
    child = SequenceAsyncPlanner([_plan([
        {"id": "child-list", "type": "list_files", "arguments": {}},
        {"id": "child-read", "type": "read_file", "arguments": {"path": "main.cpp"}},
    ])])
    parent = SequenceAsyncPlanner([
        _plan([{
            "id": "delegate",
            "type": "spawn_subagent",
            "arguments": {"role": "explore", "task": "Inspect main.cpp"},
        }]),
        _plan(final=True, summary="Delegation stopped at the shared limit."),
    ], child=child)
    loop = AutonomousForgeAgentLoop(
        workspace_root=tmp_path,
        planner=parent,
        platformio=object(),
        progress=_progress,
        max_tool_calls=2,
    )

    result = asyncio.run(loop.run(task="Inspect the project", require_build=False))

    assert result.success
    assert result.tool_execution_count == 2
    assert loop.loop_guard.remaining_tool_calls == 0
