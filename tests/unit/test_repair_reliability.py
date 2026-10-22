import json
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.agent_runtime.diagnostic_progress import diagnostic_fingerprint
from backend.agent_runtime.progress_state import ProgressState
from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.tool_contracts import ProductToolPlan
from backend.agent_runtime.router_planner import RouterProductPlanner
from backend.agent_runtime.api_planner_provider import ApiPlannerError
from backend.agent_runtime.run_models import ProductAgentRun
from backend.model_router.models import ModelRequest
from backend.model_router.run_budget import (
    AgentBudgetExceeded, AgentBudgetPersistenceError, charge_attempt, new_budget,
)


@pytest.mark.parametrize("dimension,starting,limit", [
    ("requests", 1, 1),
    ("output_tokens", 4096, 4096),
    ("input_bytes", 3, 4),
])
def test_budget_exhaustion_identifies_dimension(dimension, starting, limit):
    budget = new_budget({dimension: starting})
    budget["_limits"] = {"requests": 10, "output_tokens": 10000, "input_bytes": 100}
    budget["_limits"][dimension] = limit
    with pytest.raises(AgentBudgetExceeded) as captured:
        charge_attempt(ModelRequest(prompt="hello", max_tokens=1024, metadata={"forgex_run_budget": budget}))
    assert captured.value.details["dimension"] == dimension
    assert captured.value.details["consumed"] == starting
    assert captured.value.details["limit"] == limit
    assert captured.value.details["attempted_charge"] > 0


def test_budget_persistence_error_is_not_reported_as_exhaustion():
    budget = new_budget()
    budget["_persist"] = lambda _: (_ for _ in ()).throw(ValueError("disk unavailable"))
    with pytest.raises(AgentBudgetPersistenceError):
        charge_attempt(ModelRequest(prompt="hello", max_tokens=1024, metadata={"forgex_run_budget": budget}))
    assert budget["requests"] == 0


def test_budget_reason_is_published_on_run_and_failure_evidence():
    reason = {"dimension": "input_bytes", "consumed": 7_870_595,
              "limit": 8_000_000, "attempted_charge": 145_000, "origin": "job_budget"}
    run = ProductAgentRun(run_id="r", project_id="p", provider_id="nvidia_nim",
        status="failed", classification="AGENT_PROVIDER_BUDGET_EXCEEDED",
        budget_failure=reason)
    public = run.to_safe_dict()
    assert public["provider_diagnostics"]["budget_failure"] == reason
    assert public["failure"]["evidence"]["budget"] == reason


def test_repair_incident_keeps_same_semantic_errors_after_line_shift():
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/repair-budget-incident.json").read_text())
    first, second = fixture["builds"]
    assert diagnostic_fingerprint(first) == diagnostic_fingerprint(second)
    state = ProgressState()
    observation = lambda build, build_id: {"tool": "build_firmware", "status": "completed",
        "output": {**build, "build_id": build_id, "timestamp": build_id}}
    assert state.observe(observation(first, "build-a"))
    assert state.observe(observation(first, "build-b")) is False


def test_distinct_compiler_error_remains_new_evidence():
    one = {"diagnostics": [{"path": "src/main.cpp", "severity": "error", "message": "name X missing"}]}
    two = {"diagnostics": [{"path": "src/main.cpp", "severity": "error", "message": "name Y missing"}]}
    assert diagnostic_fingerprint(one) != diagnostic_fingerprint(two)


def test_provider_turn_stops_before_consuming_verification_reserve():
    async def scenario():
        planner = object.__new__(RouterProductPlanner)
        planner.turn_timeout_s = 30
        planner._verification_reserve_s = 45
        planner._metadata = {}
        planner._diagnostic_parent = None
        planner._deadline = time.monotonic() + 0.01
        async def slow_request(**_kwargs):
            await asyncio.sleep(1)
        planner._request_turn = slow_request
        with pytest.raises(ApiPlannerError) as captured:
            await planner._bounded_turn()
        assert captured.value.safe_code == "AGENT_VERIFICATION_RESERVE_REACHED"
    asyncio.run(scenario())


def test_build_cycle_a_b_a_stops_without_claiming_verification(tmp_path):
    class FailingBuilder:
        async def build(self, root, *, environment=None):
            return SimpleNamespace(success=False, status="failed", message="compiler error",
                firmware_path=None, build_size_bytes=None, board="esp32dev", platform="espressif32",
                warnings_count=0, metadata={}, api_dict=lambda: {},
                process_result=SimpleNamespace(returncode=1, stdout_text="",
                    stderr_text="src/main.cpp:1:1: error: missing X"))
    async def progress(*_args):
        pass
    async def scenario():
        source = tmp_path / "main.cpp"
        loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=object(),
                                        platformio=FailingBuilder(), progress=progress)
        source.write_text("A")
        await loop._build_once("esp32dev")
        source.write_text("B")
        await loop._build_once("esp32dev")
        source.write_text("A")
        result = await loop._build_once("esp32dev")
        assert result["output"]["error_code"] == "BUILD_REPAIR_EXHAUSTED"
        assert result["output"]["terminal"] is True
        assert loop.build_passed is False
    asyncio.run(scenario())


def test_model_edit_repair_history_links_diagnostic_and_next_build(tmp_path):
    class FailingBuilder:
        async def build(self, root, *, environment=None):
            return SimpleNamespace(success=False, status="failed", message="compiler error",
                firmware_path=None, build_size_bytes=None, board="esp32dev", platform="espressif32",
                warnings_count=0, metadata={}, api_dict=lambda: {},
                process_result=SimpleNamespace(returncode=1, stdout_text="",
                    stderr_text="src/main.cpp:1:1: error: missing X"))

    async def progress(*_args):
        pass

    async def scenario():
        source = tmp_path / "main.cpp"
        source.write_text("A")
        loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=object(),
            platformio=FailingBuilder(), progress=progress)
        await loop._build_once("esp32dev")
        prior = dict(loop.last_build)
        source.write_text("B")
        loop._record_model_repair("edit-1", {"path": "main.cpp"}, prior)
        loop._invalidate_verification()
        await loop._build_once("esp32dev")
        repair = loop.repair_history[-1]
        assert repair["diagnostic_fingerprint"] == diagnostic_fingerprint(prior)
        assert repair["result_success"] is False
        assert repair["build_id"] == loop.last_build["build_id"]
        assert repair["changed_paths"] == ["main.cpp"]

    asyncio.run(scenario())


def test_repair_cannot_spend_indefinite_turns_reading_without_rebuild(tmp_path):
    class Reader:
        def request_turn(self, **kwargs):
            turn = kwargs["turn"]
            return ProductToolPlan.parse({"version": "forgex.toolplan.v2", "summary": "inspect",
                "final": False, "tool_calls": [{"id": f"read-{turn}", "type": "read_file",
                "arguments": {"path": f"{turn}.cpp"}}]})
    for turn in range(1, 9):
        (tmp_path / f"{turn}.cpp").write_text(f"int value_{turn};")
    async def progress(*_args):
        pass
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=Reader(),
                                    platformio=object(), progress=progress,
                                    max_turns=10, max_progress_extension_turns=0)
    loop.build_attempted = True
    loop.last_build = {"success": False, "diagnostics": "src/main.cpp: error: missing symbol"}
    result = asyncio.run(loop.run(task="Repair failed firmware", require_build=True))
    assert result.classification == "BUILD_REPAIR_EXHAUSTED"
    assert result.tool_execution_count == 6
    assert not result.build_verified


def test_deadline_cancels_build_and_keeps_result_unverified(tmp_path):
    class SlowBuilder:
        cancelled = False
        async def build(self, root, *, environment=None):
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
    async def progress(*_args):
        pass
    async def scenario():
        builder = SlowBuilder()
        loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=object(),
                                        platformio=builder, progress=progress)
        loop._deadline = time.monotonic() + 0.03
        result = await loop._build_once("esp32dev")
        assert builder.cancelled
        assert result["output"]["error_code"] == "AGENT_RUNTIME_LIMIT_EXCEEDED"
        assert not loop.build_passed
    asyncio.run(scenario())


def test_registry_outage_retries_once_without_rewriting_source(tmp_path):
    class RegistryOutage:
        checks = 0
        builds = 0

        async def validate_dependencies(self, _root):
            self.checks += 1
            raise RuntimeError("DEPENDENCY_REGISTRY_UNAVAILABLE")

        async def build(self, _root, *, environment=None):
            self.builds += 1
            raise AssertionError("build should wait for dependency validation")

    async def progress(*_args):
        pass

    async def scenario():
        registry = RegistryOutage()
        loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=object(),
            platformio=registry, progress=progress, task_contract={"revision": "r"})
        result = await loop._build("esp32dev")
        assert registry.checks == 2
        assert registry.builds == 0
        assert result["output"]["terminal"] is True
        assert result["output"]["error_code"] == "DEPENDENCY_RESOLUTION_UNAVAILABLE"
        assert loop._repair_groups == {}
        assert loop.repair_attempts == 0

    asyncio.run(scenario())
