"""Registry constraints and finite configuration progress from the live replay."""
import asyncio
from dataclasses import replace

import pytest

from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.generation_controller import GenerationController
from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, RuntimeClassification, ToolName
from backend.agent_runtime.tool_policy import product_agent_policy
from backend.services.platformio_service import PlatformIOService, PlatformIODependencyError
from tests.unit.test_agent_verification_freshness import Builder, progress
from tests.unit.test_platformio_service import FakeSubprocessManager, make_project, process_result
from tests.unit.test_requirement_review_response import REVIEW, planner_for, source_report
from tests.unit.test_review_handoff import repeated_plans
from tests.unit.test_tool_runtime import make_runtime


def registry_result(package="owner/Sensor"):
    return {"source": "platformio_registry", "packages": [{"id": package, "metadata_verified": True,
        "available_versions": ["1.4.7"], "checked_at": "now", "cached": False}]}


@pytest.mark.parametrize("constraint", ["^1.4.3", "~1.4.3", ">=1.4.0,<2.0.0"])
def test_registry_show_exact_lookup_does_not_reject_valid_installation_ranges(tmp_path, monkeypatch, constraint):
    root = make_project(tmp_path / "firmware", "[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\nlib_deps=owner/Sensor@" + constraint + "\n")
    ini = (root / "platformio.ini").read_bytes()
    manager = FakeSubprocessManager(
        process_result(returncode=1, stderr="Could not find package"),
        process_result(stdout="Version Size Released\n1.4.7 10 2026-10-01\n1.4.4 10 2025-01-01\n2.0.0 10 2026-10-02\n"),
        process_result(stdout="Compatible Frameworks arduino\nCompatible Platforms espressif32\n1.4.7 10 2026-10-01\n"))
    service = PlatformIOService(manager)
    async def search(*args, **kwargs):
        return ("owner/Sensor",)
    monkeypatch.setattr(service, "search_libraries", search)
    assert asyncio.run(service.validate_dependencies(root))["valid"]
    assert manager.calls[-1].args[-1] == "owner/Sensor@1.4.7"
    assert (root / "platformio.ini").read_bytes() == ini
    assert asyncio.run(service.validate_dependencies(root))["valid"]
    assert len(manager.calls) == 3


@pytest.mark.parametrize("constraint", ["^3.0.0", "invalid", "1.4.3"])
def test_no_matching_or_rejected_exact_version_remains_unresolved(tmp_path, monkeypatch, constraint):
    root = make_project(tmp_path / "firmware", "[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\nlib_deps=owner/Sensor@" + constraint + "\n")
    manager = FakeSubprocessManager(process_result(returncode=1, stderr="Could not find package"),
        process_result(stdout="1.4.7 10 2026-10-01\n"))
    service = PlatformIOService(manager)
    async def search(*args, **kwargs):
        return ("owner/Sensor",)
    monkeypatch.setattr(service, "search_libraries", search)
    result = asyncio.run(service.validate_dependencies(root))
    assert not result["valid"] and result["missing"][0]["reason"] == "version_unresolved"
    assert len(manager.calls) == 2


def test_range_lookup_preserves_registry_failure_instead_of_caching_a_missing_version(tmp_path, monkeypatch):
    root = make_project(tmp_path / "firmware", "[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\nlib_deps=owner/Sensor@^1.4.3\n")
    manager = FakeSubprocessManager(process_result(returncode=1, stderr="Could not find package"),
        process_result(stdout="1.4.7 10 2026-10-01\n"), process_result(returncode=1, stderr="HTTP 503 registry unavailable"))
    service = PlatformIOService(manager)
    async def search(*args, **kwargs):
        return ("owner/Sensor",)
    monkeypatch.setattr(service, "search_libraries", search)
    with pytest.raises(PlatformIODependencyError):
        asyncio.run(service.validate_dependencies(root))
    assert not service._dependency_version_cache


def test_distinct_verified_registry_facts_progress_but_cached_aliases_cannot_reset_guards():
    contract = {"revision": "task", "requirements": [{"id": "req-1"}]}
    controller = GenerationController(contract)
    observation = {"tool": "resolve_dependency", "status": "completed", "output": registry_result()}
    controller.observe(observation)
    controller.settle()
    assert controller.stalled_turns == 0 and controller.snapshot()["resolved_packages"] == ["owner/sensor"]
    for index in range(3):
        observation["output"]["packages"][0].update(id="OWNER/SENSOR", checked_at=str(index), cached=True)
        controller.observe(observation)
        controller.settle()
    assert controller.stalled_turns == 3
    restored = GenerationController(contract, controller.snapshot())
    restored.observe(observation)
    restored.settle()
    assert restored.stalled_turns == 4
    assert not restored.dirty_paths and not restored.completed and not restored.last_build_revision
    new = {"tool": "resolve_dependency", "status": "completed", "output": registry_result("owner/Display")}
    restored.observe(new)
    restored.settle()
    assert restored.stalled_turns == 0


def test_registry_progress_is_bounded_and_unverified_candidates_are_not_material():
    controller = GenerationController({"revision": "task", "requirements": []})
    for index in range(33):
        controller.observe({"tool": "resolve_dependency", "status": "completed", "output": registry_result(f"owner/Library{index}")})
        controller.settle()
    assert len(controller.resolved_packages) == 32 and controller.stalled_turns == 1
    result = registry_result("owner/Unverified")
    result["packages"][0]["metadata_verified"] = False
    controller.observe({"tool": "resolve_dependency", "status": "completed", "output": result})
    controller.settle()
    assert controller.stalled_turns == 2


@pytest.mark.parametrize("overlay", [False, True])
def test_generation_survives_six_distinct_dependency_resolutions_without_extra_limits(tmp_path, monkeypatch, overlay):
    if overlay:
        runtime, workspace, _ = make_runtime(tmp_path)
    else:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
    contract, _ = source_report(workspace)
    plans = [{"version": PRODUCT_TOOLPLAN_VERSION, "summary": "Verify a dependency.", "final": False,
        "tool_calls": [{"id": f"dependency-{index}", "type": "resolve_dependency", "arguments": {"query": f"owner/Library{index}"}}]} for index in range(6)]
    plans.extend([repeated_plans()[0], {"version": PRODUCT_TOOLPLAN_VERSION, "summary": "Ready for review.", "final": True, "tool_calls": []}, REVIEW])
    planner, adapter = planner_for(tmp_path, monkeypatch, plans)
    base_policy = product_agent_policy()
    policy = replace(base_policy, decisions={**base_policy.decisions, ToolName.RESOLVE_DEPENDENCY: "allow"})
    if overlay:
        result = runtime.run_product(task=contract["request"], planner=planner, policy=policy,
            task_contract=contract, allow_no_changes_success=True,
            service_handlers={ToolName.RESOLVE_DEPENDENCY: lambda arguments: registry_result(arguments["query"])})
        assert result.classification is RuntimeClassification.PASS, result
    else:
        builder = Builder()
        async def resolve(query, *, limit):
            return registry_result(query)
        builder.resolve_library = resolve
        loop = AutonomousForgeAgentLoop(workspace_root=workspace, planner=planner, platformio=builder,
            progress=progress, task_contract=contract, policy=policy)
        result = asyncio.run(loop.run(task=contract["request"], require_build=False))
        assert result.success, (result.classification, result.message)
    assert result.requirement_report["valid"] and len(adapter.requests) == 9
