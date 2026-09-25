import asyncio

from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.capability_profiles import BUILD_REPAIR_PROFILE
from backend.agent_runtime.tool_contracts import ProductToolPlan
from tests.unit.test_tool_runtime import SequencePlanner, plan
from tests.api.test_api_routes import successful_build


class Builder:
    def __init__(self):
        self.calls = 0
        self.saw_stale_state = []

    async def build(self, root, *, environment=None):
        self.calls += 1
        self.saw_stale_state.append((root / ".pio" / "stale-state").exists())
        stale = root / ".pio" / "stale-state"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("generated", encoding="utf-8")
        return successful_build(root / ".pio" / "build" / "esp32dev" / "firmware.bin")


async def progress(_event, _payload):
    pass


def test_build_then_edit_requires_fresh_build_at_final(tmp_path):
    build = ProductToolPlan.parse({"version": "forgex.toolplan.v2", "summary": "build", "final": False,
        "tool_calls": [{"id": "build", "type": "build_firmware", "arguments": {}}]})
    builder = Builder()
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, platformio=builder, progress=progress,
        policy=BUILD_REPAIR_PROFILE.policy(), planner=SequencePlanner([
            build, plan(path="main.cpp", content="int changed;\n"), plan(final=True),
        ]))
    result = asyncio.run(loop.run(task="Update and verify the project", require_build=True))
    assert result.success, result.message
    assert builder.calls == 2
    assert builder.saw_stale_state == [False, False]
    assert result.build_result["success"]


def test_external_source_change_or_artifact_change_invalidates_result(tmp_path):
    async def scenario():
        builder = Builder()
        loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, platformio=builder,
            progress=progress, planner=SequencePlanner([]))
        await loop._build(None)
        assert loop.build_passed
        (tmp_path / "main.cpp").write_text("int external;\n", encoding="utf-8")
        result = loop._result(True, "done", "done")
        assert not result.success and result.classification == "VERIFICATION_STALE"
        assert not result.build_result
        await loop._build(None)
        artifact = tmp_path / ".pio" / "build" / "esp32dev" / "firmware.bin"
        artifact.write_bytes(b"changed firmware")
        assert not loop._refresh_verification()
        assert not loop.build_passed and not loop.last_build
    asyncio.run(scenario())


def test_verified_build_finishes_after_one_read_only_followup(tmp_path):
    build = ProductToolPlan.parse({"version": "forgex.toolplan.v2", "summary": "build", "final": False,
        "tool_calls": [{"id": "build", "type": "build_firmware", "arguments": {}}]})
    inspect = ProductToolPlan.parse({"version": "forgex.toolplan.v2", "summary": "inspect files", "final": False,
        "tool_calls": [{"id": "inspect", "type": "list_files", "arguments": {"path": "."}}]})
    planner = SequencePlanner([build, inspect, plan(path="test.txt", content="unwanted\n")])
    builder = Builder()
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, platformio=builder, progress=progress,
        policy=BUILD_REPAIR_PROFILE.policy(), planner=planner)
    result = asyncio.run(loop.run(task="Build and verify the project", require_build=True))
    assert result.success, result.message
    assert builder.calls == 1
    assert len(planner.seen_results) == 2
    assert not (tmp_path / "test.txt").exists()
