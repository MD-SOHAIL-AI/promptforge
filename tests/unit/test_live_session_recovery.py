"""Regressions for the 10 October deadline/configuration/interface incident."""
import asyncio
import hashlib
import time

import pytest

from backend.agent_runtime.api_planner_provider import ApiPlannerError
from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.build_readiness import local_header_readiness
from backend.agent_runtime.capability_profiles import BUILD_REPAIR_PROFILE
from backend.agent_runtime.engine import PhaseBudget
from backend.agent_runtime.router_planner import RouterProductPlanner
from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan
from backend.model_router.router import ModelRouterService
from backend.services.platformio_service import PlatformIOService
from tests.unit.test_agent_verification_freshness import Builder, progress
from tests.unit.test_platformio_service import FakeSubprocessManager
from tests.unit.test_router_product_planner import Adapter, registry
from tests.unit.test_tool_runtime import SequencePlanner, plan


VALID_INI = "[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n"
INVALID_INI = VALID_INI + "lib_deps=\n  Preferences\n  ESP32 Arduino OTA\n  Arduino_FreeRTOS\n  timers\n  semaphores\n  queues\n  event_groups\n"


def loop_for(root, planner, builder=None, **kwargs):
    return AutonomousForgeAgentLoop(workspace_root=root, planner=planner,
        platformio=builder or Builder(), progress=progress, policy=BUILD_REPAIR_PROFILE.policy(),
        trusted_project=True, task_contract={"revision": "test", "requirements": []}, **kwargs)


def test_cold_complex_job_reserves_a_share_and_retains_real_phase_limits():
    budget = PhaseBudget.for_task(True, review_count=3, cold=True)
    assert budget.generation_reserve == 480
    assert budget.generation_reserve_for(900) == 300
    assert budget.generation_reserve_for(2700) == 480
    assert budget.build_deadline(900, now=0) == 300


def test_four_second_generation_headroom_never_dispatches_a_request(tmp_path):
    adapter = Adapter([])
    planner = RouterProductPlanner(ModelRouterService(registry=registry(tmp_path), providers={"openai": adapter}),
        provider_id="openai", model_id="gpt-4.1-mini")
    planner.bind_deadline(time.monotonic() + 484)
    planner.bind_verification_reserve(480)
    with pytest.raises(ApiPlannerError) as caught:
        planner.request_turn(task="continue", tool_results=(), allowed_tools=("read_file",), run_id="r", turn=39)
    assert caught.value.safe_code == "AGENT_VERIFICATION_RESERVE_REACHED"
    assert planner.to_safe_metadata()["timeout_stage"] == "generation_reserve"
    assert not adapter.requests


def test_running_request_cancelled_at_reserve_reports_the_reserve(monkeypatch, tmp_path):
    import backend.agent_runtime.router_planner as module
    monkeypatch.setattr(module, "MIN_MODEL_REPAIR_SECONDS", .01)
    planner = RouterProductPlanner(ModelRouterService(registry=registry(tmp_path), providers={}),
        provider_id="openai", model_id="gpt-4.1-mini")
    stopped = []
    async def blocked(**kwargs):
        try:
            await asyncio.sleep(1)
        finally:
            stopped.append(True)
    monkeypatch.setattr(planner, "_request_turn", blocked)
    planner.bind_deadline(time.monotonic() + .2 + .08)
    planner.bind_verification_reserve(.2)
    with pytest.raises(ApiPlannerError) as caught:
        asyncio.run(planner.request_turn_async(task="continue", tool_results=(), allowed_tools=(), run_id="r", turn=1))
    assert caught.value.safe_code == "AGENT_VERIFICATION_RESERVE_REACHED"
    assert stopped == [True]


def test_local_turn_timeout_with_no_verification_reserve_keeps_its_cause(monkeypatch, tmp_path):
    planner = RouterProductPlanner(ModelRouterService(registry=registry(tmp_path), providers={}),
        provider_id="openai", model_id="gpt-4.1-mini", turn_timeout_s=.03)
    monkeypatch.setattr(planner, "turn_timeout_s", .03)
    planner.bind_deadline(time.monotonic() + 5)
    planner.bind_verification_reserve(0)
    async def blocked(**kwargs):
        await asyncio.sleep(1)
    monkeypatch.setattr(planner, "_request_turn", blocked)
    with pytest.raises(ApiPlannerError) as caught:
        asyncio.run(planner.request_turn_async(task="continue", tool_results=(), allowed_tools=(), run_id="r", turn=1))
    assert caught.value.safe_code == "AGENT_TURN_LIMIT_EXCEEDED"


def test_framework_facilities_are_diagnosed_without_registry_search(tmp_path):
    (tmp_path / "platformio.ini").write_text(INVALID_INI)
    service = PlatformIOService(FakeSubprocessManager())
    result = asyncio.run(service.validate_dependencies(tmp_path))
    assert not result["valid"] and len(result["missing"]) == 7
    assert all(item["reason"] == "framework_provided" and "Remove" in item["correction"] for item in result["missing"])
    (tmp_path / "platformio.ini").write_text(VALID_INI)
    assert asyncio.run(service.validate_dependencies(tmp_path))["valid"]


def test_dependencies_are_checked_before_missing_local_headers(tmp_path):
    (tmp_path / "platformio.ini").write_text(INVALID_INI)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.cpp").write_text('#include "sensors/missing.h"\n')
    loop = loop_for(tmp_path, object(), PlatformIOService(FakeSubprocessManager()))
    result = asyncio.run(loop._build_once("esp32dev"))
    assert result["output"]["error_code"] == "DEPENDENCY_UNRESOLVED"
    assert not result["output"]["dependencies"]["valid"]


def dependency_failure():
    return {"success": False, "error_code": "DEPENDENCY_UNRESOLVED", "dependencies": {
        "configuration_sha256": hashlib.sha256(INVALID_INI.encode()).hexdigest(),
        "missing": [{"requested": "queues", "reason": "framework_provided", "correction": "Remove queues from lib_deps."}]}}


def test_config_blocker_survives_compaction_and_rejects_unrelated_writes(tmp_path):
    (tmp_path / "platformio.ini").write_text(INVALID_INI)
    planner = SequencePlanner([plan(path="src/unrelated.cpp", content="int ignored;", call_id=f"ignore-{i}") for i in range(3)])
    loop = loop_for(tmp_path, planner, resume_checkpoint={"observations": [
        {"tool": "build_firmware", "status": "completed", "output": dependency_failure()},
        *[{"tool": "read_file", "status": "completed", "output": {"path": f"old-{i}"}} for i in range(30)]]})
    compacted, _ = loop._compacted_observations()
    assert compacted[-1]["tool"] == "build_repair_focus"
    assert compacted[-1]["output"]["missing"][0]["requested"] == "queues"
    result = asyncio.run(loop.run(task="continue the saved draft", require_build=True))
    assert result.classification == "BUILD_REPAIR_EXHAUSTED"
    assert not (tmp_path / "src/unrelated.cpp").exists()
    assert (tmp_path / "platformio.ini").read_text() == INVALID_INI


def test_configuration_repair_unlocks_source_and_immediately_revalidates(tmp_path):
    (tmp_path / "platformio.ini").write_text(INVALID_INI)
    class ValidationBuilder(Builder):
        validated = 0
        async def validate_dependencies(self, root, **kwargs):
            assert (root / "platformio.ini").read_text() == VALID_INI
            if not self.validated:
                assert not (root / "src/main.cpp").exists(), "configuration must be checked before another source write"
            self.validated += 1
            return {"valid": True}
    builder = ValidationBuilder()
    planner = SequencePlanner([plan(path="platformio.ini", content=VALID_INI, call_id="fix-config"),
        plan(path="src/main.cpp", content="void setup() {}\nvoid loop() {}\n", call_id="write-main")])
    loop = loop_for(tmp_path, planner, builder, max_turns=2, max_progress_extension_turns=0,
        resume_checkpoint={"observations": [{"tool": "build_firmware", "status": "completed", "output": dependency_failure()}]})
    asyncio.run(loop.run(task="continue", require_build=True))
    assert (tmp_path / "src/main.cpp").exists()
    assert builder.validated >= 1


def test_invalid_configuration_blocks_the_next_write_in_the_same_model_batch(tmp_path):
    batch = ProductToolPlan.parse({"version": PRODUCT_TOOLPLAN_VERSION, "summary": "generate", "final": False,
        "tool_calls": [
            {"id": "write-config", "type": "write_file", "path": "platformio.ini", "content": INVALID_INI},
            {"id": "write-main", "type": "write_file", "path": "src/main.cpp", "content": "void setup() {}"}]})
    loop = loop_for(tmp_path, SequencePlanner([batch]), PlatformIOService(FakeSubprocessManager()),
        max_turns=1, max_progress_extension_turns=0)
    asyncio.run(loop.run(task="generate", require_build=True))
    assert (tmp_path / "platformio.ini").exists()
    assert not (tmp_path / "src/main.cpp").exists()
    blocked = next(item for item in loop.observations if item.get("call_id") == "write-main")
    assert blocked["error_code"] == "BUILD_CONFIGURATION_REPAIR_REQUIRED"


def test_legacy_dependency_checkpoint_is_revalidated_before_the_first_request(tmp_path):
    (tmp_path / "platformio.ini").write_text(INVALID_INI)
    failure = dependency_failure()
    failure["dependencies"].pop("configuration_sha256")
    planner = SequencePlanner([plan(path="src/unrelated.cpp", content="int ignored;")])
    loop = loop_for(tmp_path, planner, PlatformIOService(FakeSubprocessManager()),
        max_turns=1, max_progress_extension_turns=0,
        resume_checkpoint={"observations": [{"tool": "build_firmware", "status": "completed", "output": failure}]})
    asyncio.run(loop.run(task="continue", require_build=True))
    assert not (tmp_path / "src/unrelated.cpp").exists()
    focus = next(item for item in planner.seen_results[0] if item.get("tool") == "build_repair_focus")
    assert focus["output"]["configuration_sha256"]


def test_configuration_preflight_respects_an_already_admitted_tool_budget(tmp_path):
    batch = ProductToolPlan.parse({"version": PRODUCT_TOOLPLAN_VERSION, "summary": "configure", "final": False,
        "tool_calls": [
            {"id": "write-config", "type": "write_file", "path": "platformio.ini", "content": INVALID_INI},
            {"id": "read-config", "type": "read_file", "arguments": {"path": "platformio.ini"}}]})
    loop = loop_for(tmp_path, SequencePlanner([batch]), PlatformIOService(FakeSubprocessManager()),
        max_turns=1, max_progress_extension_turns=0, max_tool_calls=2)
    result = asyncio.run(loop.run(task="configure", require_build=True))
    assert not result.success and not loop.build_passed
    assert loop.tool_count == 2


def test_interface_drift_is_advisory_and_keeps_paired_paths():
    files = {"src/display/oled.h": "class OledManager {\npublic:\n static void update();\n};\n",
             "src/display/oled.cpp": '#include "oled.h"\nvoid OLEDManager::update() {}\n',
             "src/sensors/temp.h": "class TemperatureSensor {\npublic:\n bool isValidReading(float t);\n};\n",
             "src/sensors/temp.cpp": '#include "temp.h"\nbool TemperatureSensor::isValid() {return true;}\n'}
    result = local_header_readiness(files, files.__getitem__)
    assert result["ready"] is True
    assert len(result["interface_conflicts"]) == 2
    assert all(item["advisory"] and item["header"] in files and item["implementation"] in files for item in result["interface_conflicts"])
    files["src/display/oled.cpp"] = 'void OledManager::update() {}\n'
    files["src/sensors/temp.cpp"] = 'bool TemperatureSensor::isValidReading(float t) {return true;}\n'
    assert not local_header_readiness(files, files.__getitem__)["interface_conflicts"]


def test_compacted_context_keeps_current_interface_declarations(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "oled.h").write_text("class OledManager {\npublic:\n static void update();\n};\n")
    (source / "oled.cpp").write_text("void OLEDManager::update() {}\n")
    loop = loop_for(tmp_path, object())
    loop.observations = [{"tool": "read_file", "status": "completed", "output": {"path": f"old-{i}"}} for i in range(30)]
    context, _ = loop._compacted_observations()
    focus = context[-1]["output"]
    assert context[-1]["tool"] == "interface_repair_focus"
    assert focus["findings"][0]["implementation"] == "src/oled.cpp"
    assert any("update()" in item["declaration"] for item in focus["declarations"])
