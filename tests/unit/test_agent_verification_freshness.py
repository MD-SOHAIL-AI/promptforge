import asyncio
import pytest

from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.capability_profiles import BUILD_REPAIR_PROFILE
from backend.agent_runtime.tool_contracts import ProductToolPlan
from tests.unit.test_tool_runtime import SequencePlanner, plan
from tests.api.test_api_routes import successful_build
from backend.agent_runtime.api_planner_provider import ApiPlannerError, ApiPlannerClassification


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


def test_saved_draft_verification_needs_no_planner_and_does_not_claim_completion(tmp_path):
    (tmp_path / "main.cpp").write_text("int original;\n")
    builder = Builder()
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, platformio=builder, planner=None,
        progress=progress, policy=BUILD_REPAIR_PROFILE.policy(), trusted_project=True)
    result = asyncio.run(loop.verify_saved_draft())
    assert not result.success and result.classification == "DRAFT_BUILD_VERIFIED"
    assert result.build_verified and result.build_result["firmware_hash"]
    assert builder.calls == 1 and result.tool_execution_count == 1
    assert (tmp_path / "main.cpp").read_text() == "int original;\n"


def test_saved_draft_verification_requires_trust(tmp_path):
    builder = Builder()
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, platformio=builder, planner=None,
        progress=progress, policy=BUILD_REPAIR_PROFILE.policy(), trusted_project=False)
    result = asyncio.run(loop.verify_saved_draft())
    assert result.classification == "AGENT_PROJECT_TRUST_REQUIRED"
    assert not result.build_verified and builder.calls == 0


def test_default_build_keeps_the_environment_selected_by_platformio(tmp_path):
    class DefaultEnvironmentBuilder(Builder):
        async def build(self, root, *, environment=None):
            assert environment is None
            result = await super().build(root, environment=environment)
            result.metadata["environment"] = "esp32dev"
            return result
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, platformio=DefaultEnvironmentBuilder(),
        planner=None, progress=progress, policy=BUILD_REPAIR_PROFILE.policy(), trusted_project=True)
    result = asyncio.run(loop.verify_saved_draft())
    assert result.build_verified and result.build_result["environment"] == "esp32dev"


@pytest.mark.parametrize("failure_code", ["PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "PROVIDER_BUSY",
    "MODEL_OUTPUT_TRUNCATED", "TOOL_ARGUMENTS_INVALID", "TOOLPLAN_SCHEMA_INVALID", "AGENT_RUNTIME_LIMIT_EXCEEDED"])
def test_provider_outage_builds_settled_firmware_locally(tmp_path, failure_code):
    (tmp_path / "platformio.ini").write_text("[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n")
    class OutagePlanner(SequencePlanner):
        supports_durable_continuation = True
        def request_turn(self, **kwargs):
            if len(self.seen_results):
                raise ApiPlannerError(ApiPlannerClassification.UNKNOWN_SAFE_FAILURE, failure_code,
                    {"origin": "job_time_reserve"} if failure_code == "AGENT_RUNTIME_LIMIT_EXCEEDED" else {})
            return super().request_turn(**kwargs)
    builder = Builder()
    planner = OutagePlanner([plan(path="src/main.cpp", content="void setup() {}\nvoid loop() {}\n")])
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, platformio=builder, planner=planner,
        progress=progress, policy=BUILD_REPAIR_PROFILE.policy(), trusted_project=True)
    result = asyncio.run(loop.run(task="write and build firmware", require_build=True))
    assert result.classification == failure_code, result.message
    assert not result.success and result.build_verified
    assert builder.calls == 1


def test_generation_compiles_after_three_settled_write_turns(tmp_path):
    (tmp_path / "platformio.ini").write_text("[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n")
    class DurablePlanner(SequencePlanner):
        supports_durable_continuation = True
        def request_turn(self, **kwargs):
            if len(self.seen_results) == 3:
                assert builder.calls == 1
            return super().request_turn(**kwargs)
    builder = Builder()
    planner = DurablePlanner([plan(path="src/main.cpp", content="int a;\n", call_id="write_main"),
        plan(path="include/module.h", content="int b;\n", call_id="write_header"),
        plan(path="README.md", content="firmware\n", call_id="write_readme"), plan(final=True)])
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, platformio=builder, planner=planner,
        progress=progress, policy=BUILD_REPAIR_PROFILE.policy(), trusted_project=True)
    result = asyncio.run(loop.run(task="write and build firmware", require_build=True))
    assert result.success, result.message
    assert builder.calls == 1 and result.build_verified


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
    assert builder.saw_stale_state == [False, True]  # Source edits keep the incremental cache.
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
        assert result.build_result["verification_stale"] and not result.build_result["success"]
        await loop._build(None)
        artifact = tmp_path / ".pio" / "build" / "esp32dev" / "firmware.bin"
        artifact.write_bytes(b"changed firmware")
        assert not loop._refresh_verification()
        assert not loop.build_passed and loop.last_build["verification_stale"]
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
