"""Recover completion from pasted activity logs and rejected source evidence."""
import asyncio
import copy
import hashlib
import json

import pytest

from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.task_contract import (
    create_task_contract, repair_task_contract, revise_task_contract,
    verify_requirement_evidence, requirement_feedback,
)
from backend.agent_runtime.tool_contracts import ProductToolPlan
from tests.unit.test_agent_verification_freshness import Builder, progress


ACTIVITY = """ForgeX

Agent activity
2m 20s
Verbose
Reasoning summary
0s
thinking.summary
completed
Turn 1
Step 1
Writing staged source
src/old.cpp
View diff
Run unit tests and simulation.
Do not compile.
Request diagnostics
Retry selected model
Verification evidence
"""


def contract_for(tmp_path, request):
    return create_task_contract(request, project_id="p", session_id="s",
                                workspace=tmp_path, intent="generate")


def test_activity_transcript_is_context_without_creating_requirements(tmp_path):
    request = "Use LED GPIO 2.\nCompile the firmware.\nDo not flash.\n\n" + ACTIVITY
    contract = contract_for(tmp_path, request)
    assert [item["text"] for item in contract["requirements"]] == [
        "Use LED GPIO 2.", "Compile the firmware."]
    assert contract["exclusions"] == ["Do not flash."]
    assert {item["kind"] for item in contract["acceptance_checks"]} == {"source_review", "build"}
    assert contract["request"] == request
    assert "thinking.summary" in contract["diagnostic_context"]
    assert not any(item.get("path") == "src/old.cpp" for item in contract["deliverables"])


@pytest.mark.parametrize("prompt", [
    "Implement ForgeX.\nAgent activity\nDisplay a Turn 1 label.",
    "Show this literal example in README.md:\n```text\n" + ACTIVITY + "```",
])
def test_ordinary_text_and_fenced_examples_are_preserved(tmp_path, prompt):
    contract = contract_for(tmp_path, prompt)
    assert not contract.get("diagnostic_context")
    assert "Agent activity" in "\n".join(item["text"] for item in contract["requirements"])


def test_explicit_user_guidance_after_activity_is_still_required(tmp_path):
    contract = contract_for(tmp_path, "Use LED GPIO 2.\n" + ACTIVITY +
                            "\nUser guidance:\nInclude README instructions.")
    assert any("README instructions" in item["text"] for item in contract["requirements"])
    assert "Retry selected model" not in "\n".join(item["text"] for item in contract["requirements"])


def test_diagnostic_prohibitions_do_not_retire_previously_requested_checks(tmp_path):
    previous = contract_for(tmp_path, "Compile the firmware.")
    current = revise_task_contract(previous, "Include README instructions.\n" + ACTIVITY,
                                  project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    assert any(item["kind"] == "build" for item in current["acceptance_checks"])
    assert not any(item["kind"] == "simulation" for item in current["acceptance_checks"])


def test_retry_repairs_polluted_groups_without_mutating_history_or_inherited_work(tmp_path):
    previous = contract_for(tmp_path, "Keep modules small.")
    previous.update(partition_version=2, request="Use LED GPIO 2.\nCompile the firmware.\n" + ACTIVITY)
    texts = ["Keep modules small.", "Use LED GPIO 2.",
             "Compile the firmware.\nForgeX", *[line for line in ACTIVITY.splitlines()[1:] if line.strip()]]
    # This legacy grouping also contains an inherited obligation absent from request.
    previous["requirements"] = [
        {"id": f"req-{index}", "text": text,
         "stable_key": hashlib.sha256(text.encode()).hexdigest()}
        for index, text in enumerate(texts, 1)]
    previous["acceptance_checks"].append({"id": "simulation", "kind": "simulation", "criterion": "legacy log text"})
    historical = copy.deepcopy(previous)
    repaired = repair_task_contract(previous, workspace=tmp_path)
    assert previous == historical
    assert [{key: item[key] for key in ("id", "text", "stable_key")} for item in repaired["requirements"][:2]] == previous["requirements"][:2]
    assert all(item["evidence_kinds"] == ["source"] for item in repaired["requirements"][:2])
    assert "Compile the firmware." in "\n".join(item["text"] for item in repaired["requirements"])
    assert "Retry selected model" not in "\n".join(item["text"] for item in repaired["requirements"])
    assert {item["kind"] for item in repaired["acceptance_checks"]} == {"source_review", "build"}
    assert repaired["parent_revision"] == historical["revision"]
    assert historical["revision"] in repaired["revision_history"]
    assert repaired["superseded_requirements"]
    assert repair_task_contract(repaired, workspace=tmp_path) == repaired


def test_legacy_repair_preserves_literal_transcript_before_real_diagnostics(tmp_path):
    literal = "Include this README example:\n```text\n" + ACTIVITY + "```\nUse LED GPIO 2.\n"
    previous = contract_for(tmp_path, literal)
    previous.update(partition_version=2, request=literal + ACTIVITY)
    next_id = len(previous["requirements"])
    for line in ACTIVITY.splitlines():
        if line.strip():
            next_id += 1
            previous["requirements"].append({"id": f"req-{next_id}", "text": line,
                "stable_key": hashlib.sha256(line.encode()).hexdigest()})
    repaired = repair_task_contract(previous, workspace=tmp_path)
    assert [item["text"] for item in repaired["requirements"]] == [
        item["text"] for item in contract_for(tmp_path, literal)["requirements"]]


@pytest.mark.parametrize("quote,source,issues", [
    ("Use LED GPIO 2.", "int led = 2;", {"quote_not_found"}),
    ("int led = 2;", "// int led = 2;", {"comment_only"}),
])
def test_rejected_quotes_explain_the_repair_and_all_missing_ids(tmp_path, quote, source, issues):
    (tmp_path / "main.cpp").write_text(source, encoding="utf-8")
    contract = contract_for(tmp_path, "Use LED GPIO 2.\nInclude README instructions.")
    report = verify_requirement_evidence(contract, [{"id": "req-1", "status": "implemented",
        "evidence": [{"path": "main.cpp", "quote": quote}]}], tmp_path, "revision")
    assert not report["references_valid"]
    assert set(report["checks"][0]["issues"]) == issues
    assert report["missing"] == ["req-2"]


def test_hardware_limit_disclosure_can_reference_documentation(tmp_path):
    contract = contract_for(tmp_path, "Clearly state anything that cannot be verified without physical ESP32 hardware.")
    quote = "Physical knock recognition still requires testing on an ESP32."
    (tmp_path / "README.md").write_text(quote, encoding="utf-8")
    report = verify_requirement_evidence(contract, [{"id": "req-1", "status": "implemented",
        "evidence": [{"path": "README.md", "quote": quote}]}], tmp_path, "revision")
    assert report["references_valid"] and not report["valid"]
    assert not report["hardware_verified"]


def test_large_rejected_evidence_keeps_every_id_and_reason_in_bounded_feedback(tmp_path):
    contract = contract_for(tmp_path, "\n".join(f"Implement behavior {index}." for index in range(64)))
    report = {"checks": [{"id": item["id"], "status": "unverified", "issues": ["quote_not_found"],
                         "evidence": [{"path": "main.cpp", "quote": "fabricated" * 2000}]}
                        for item in contract["requirements"]]}
    feedback = requirement_feedback(contract, report)
    assert feedback["unverified"] == [item["id"] for item in contract["requirements"]]
    assert len(json.dumps(feedback)) < 16000
    assert all(item["issues"] == ["quote_not_found"] for item in feedback["checks"])


def test_completion_repairs_quotes_and_rebuilds_after_readme_edit(tmp_path):
    contract = contract_for(tmp_path, "Use LED GPIO 2.\nInclude README instructions.\n" + ACTIVITY)
    builder = Builder()
    class Reviewer:
        def request_turn(self, **kwargs):
            assert not kwargs["allowed_tools"]
            return ProductToolPlan.parse({"version": "forgex.toolplan.v2", "final": True,
                "tool_calls": [], "summary": json.dumps({"checks": [
                    {"id": item["id"], "verdict": "satisfied", "reason": "Current source and instructions match."}
                    for item in contract["requirements"]]})})
    class Planner:
        def for_role(self, role):
            assert role == "review"
            return Reviewer()
        def request_turn(self, **kwargs):
            turn = kwargs["turn"]
            calls = []
            if turn == 1:
                calls = [{"id": "source", "type": "write_file", "arguments": {"path": "main.cpp", "content": "int led = 2;"}}]
            elif turn == 2:
                calls = [{"id": "build", "type": "build_firmware", "arguments": {}}]
            elif turn == 3:
                calls = [{"id": "docs", "type": "write_file", "arguments": {"path": "README.md", "content": "Connect the LED to GPIO 2."}}]
            elif turn == 4:
                calls = [{"id": "bad-quotes", "type": "verify_requirements", "arguments": {"checks": [
                    {"id": "req-1", "status": "implemented", "evidence": [{"path": "main.cpp", "quote": "Use LED GPIO 2."}]},
                    {"id": "req-2", "status": "implemented", "evidence": [{"path": "README.md", "quote": "Include README instructions."}]}]}}]
            elif turn == 6:
                feedback = next(item["output"]["report"] for item in kwargs["tool_results"] if item["tool"] == "requirement_validation")
                assert feedback["unverified"] == ["req-1", "req-2"]
                assert feedback["checks"][0]["issues"] == ["quote_not_found"]
                assert builder.calls == 2  # Finalization refreshed the stale build.
                calls = [{"id": "real-quotes", "type": "verify_requirements", "arguments": {"checks": [
                    {"id": "req-1", "status": "implemented", "evidence": [{"path": "main.cpp", "quote": "int led = 2;"}]},
                    {"id": "req-2", "status": "implemented", "evidence": [{"path": "README.md", "quote": "Connect the LED to GPIO 2."}]}]}}]
            return ProductToolPlan.parse({"version": "forgex.toolplan.v2", "summary": "Done",
                                          "final": not calls, "tool_calls": calls})
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=Planner(), platformio=builder,
                                   progress=progress, task_contract=contract)
    result = asyncio.run(loop.run(task=contract["request"], require_build=True))
    assert result.success, result.message
    assert builder.calls == 2 and result.build_verified
    assert result.requirement_report["valid"]
    assert result.requirement_report["new_evidence_count"] == 2
    assert result.requirement_report["workspace_revision"] == result.build_result["workspace_revision"]
