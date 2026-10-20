"""A compiled task must not wait forever for its generator to request review."""
import asyncio
import copy

import pytest

from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.generation_controller import GenerationController
from backend.agent_runtime.task_contract import requirement_feedback
from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, RuntimeClassification
from backend.agent_runtime.tool_policy import product_agent_policy
from backend.agent_runtime.verification import repeated_reference_handoff
from tests.unit.test_agent_verification_freshness import Builder, progress
from tests.unit.test_requirement_review_response import REVIEW, planner_for, source_report
from tests.unit.test_tool_runtime import make_runtime


def repeated_plans():
    checks = [{"id": "req-1", "status": "implemented",
        "evidence": [{"path": "main.cpp", "quote": "const int LED_PIN = 2;"}]}]
    return [{"version": PRODUCT_TOOLPLAN_VERSION, "summary": "Submit source evidence.", "final": False,
        "tool_calls": [{"id": f"references-{index}", "type": "verify_requirements",
            "arguments": {"checks": checks}}]} for index in range(2)]


@pytest.mark.parametrize("overlay", [False, True])
@pytest.mark.parametrize("native,json_schema", [(True, False), (False, True), (False, False)])
def test_repeated_accepted_references_enter_real_review_without_a_generator_final(tmp_path, monkeypatch, overlay, native, json_schema):
    if overlay:
        runtime, workspace, _ = make_runtime(tmp_path)
    else:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
    contract, _ = source_report(workspace)
    planner, adapter = planner_for(tmp_path, monkeypatch, [*repeated_plans(), REVIEW],
        native=native, json_schema=json_schema)
    events = []
    if overlay:
        result = runtime.run_product(task=contract["request"], planner=planner, policy=product_agent_policy(),
            task_contract=contract, allow_no_changes_success=True, event_callback=events.append)
        assert result.classification is RuntimeClassification.PASS, result
    else:
        async def capture(event, payload):
            events.append({"event_type": event, **payload})
        loop = AutonomousForgeAgentLoop(workspace_root=workspace, planner=planner, platformio=Builder(),
            progress=capture, task_contract=contract)
        result = asyncio.run(loop.run(task=contract["request"], require_build=False))
        assert result.success, (result.classification, result.message)
    assert result.requirement_report["valid"]
    assert result.requirement_report["verification_level"] == "model_review"
    assert not result.requirement_report["semantic_review"]["execution_verified"]
    assert len(adapter.requests) == 3
    assert adapter.requests[-1].structured_output.name == "requirement_review"
    assert any("handoff" in event["event_type"].lower() for event in events)


def handoff_inputs(root):
    contract, report = source_report(root)
    feedback = requirement_feedback(contract, report)
    observations = [{"tool": "verify_requirements", "status": "completed", "output": copy.deepcopy(feedback)} for _ in range(2)]
    return contract, report, observations


@pytest.mark.parametrize("change", ["source", "task", "unaccepted", "one_submission", "mutation", "failed_review"])
def test_handoff_does_not_promote_stale_rejected_or_changed_evidence(tmp_path, change):
    contract, report, observations = handoff_inputs(tmp_path)
    assert repeated_reference_handoff(contract, report, observations, "revision-1")
    if change == "source":
        report["workspace_revision"] = "old"
    elif change == "task":
        report["task_revision"] = "old"
    elif change == "unaccepted":
        report["references_valid"] = False
    elif change == "one_submission":
        observations.pop()
    elif change == "mutation":
        observations.insert(1, {"tool": "write_file", "status": "completed"})
    else:
        report["semantic_review"] = {"checks": [{"id": "req-1", "verdict": "unverified"}]}
    assert not repeated_reference_handoff(contract, report, observations, "revision-1")


def test_handoff_requires_every_requested_execution_result_for_current_source(tmp_path):
    contract, report, observations = handoff_inputs(tmp_path)
    contract["acceptance_checks"].append({"id": "tests", "kind": "unit_tests", "criterion": "Tests pass."})
    build = {"success": True, "workspace_revision": "revision-1"}
    tests = [{"kind": "unit_tests", "success": True, "source_revision": "revision-1"}]
    assert not repeated_reference_handoff(contract, report, observations, "revision-1", build=build)
    assert not repeated_reference_handoff(contract, report, observations, "revision-1", evidence=tests, require_build=True)
    assert not repeated_reference_handoff(contract, report, observations, "revision-1", evidence=tests, build=build,
        require_simulation=True)
    assert repeated_reference_handoff(contract, report, observations, "revision-1", evidence=tests, build=build, require_build=True)
    build["workspace_revision"] = "old"
    assert not repeated_reference_handoff(contract, report, observations, "revision-1", evidence=tests, build=build, require_build=True)


def test_accepted_citations_are_not_displayed_as_missing_and_feedback_requests_review(tmp_path):
    contract, report, _ = handoff_inputs(tmp_path)
    controller = GenerationController(contract)
    focus = controller.focus(contract=contract, plan=[], files={"main.cpp": "hash"},
        source_revision="revision-1", report=report,
        build={"success": True, "workspace_revision": "revision-1"})
    assert focus["remaining_requirement_ids"] == []
    assert "no tool calls" in focus["next_action"]
    assert "no tool calls" in requirement_feedback(contract, report)["next_action"]
    report["workspace_revision"] = "old"
    assert controller.focus(contract=contract, plan=[], files={}, source_revision="new", report=report)["remaining_requirement_ids"] == ["req-1"]
