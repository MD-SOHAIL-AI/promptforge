"""Documentation evidence, saved-contract migration and bounded retry regressions."""
import asyncio
import copy
import json
import time

import pytest

from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.requirement_evidence import EVIDENCE_CONTRACT_VERSION, RejectedEvidenceTracker
from backend.agent_runtime.task_contract import create_task_contract, repair_task_contract, revise_task_contract, verify_requirement_evidence, requirement_feedback
from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan, RuntimeClassification
from backend.agent_runtime.tool_policy import product_agent_policy
from backend.agent_runtime.verification import review_requirements
from backend.contracts.engine import VerificationReport
from tests.unit.test_agent_verification_freshness import Builder, progress
from tests.unit.test_requirement_review_response import planner_for
from tests.unit.test_tool_runtime import SequencePlanner, make_runtime


def contract_for(root, request):
    return create_task_contract(request, project_id="p", session_id="s", workspace=root, intent="generate")


def citation(identity="req-1", path="README.md", quote="Configure Arduino IDE."):
    return {"id": identity, "status": "implemented", "evidence": [{"path": path, "quote": quote}]}


def tool_plan(call_id, tool, arguments):
    return ProductToolPlan.parse({"version": PRODUCT_TOOLPLAN_VERSION, "summary": "Verify the current requirement.",
        "tool_calls": [{"id": call_id, "type": tool, "arguments": arguments}], "final": False})


def wire_plan(plan):
    return {"version": plan.version, "summary": plan.summary, "final": plan.final,
            "tool_calls": [call.to_provider_dict() for call in plan.tool_calls]}


@pytest.mark.parametrize("prompt", [
    "4. Step-by-step Arduino IDE setup.", "6. Explanation of how the code works.",
    "Instructions for uploading and testing.", "Include a guide to installing Arduino IDE.",
    "Provide an explanation of the firmware implementation.",
    "Document the pin connections.",
    "Describe the module interfaces.", "Clearly state anything that cannot be verified without physical ESP32 hardware.",
])
def test_documentation_wording_has_persisted_documentation_type(tmp_path, prompt):
    contract = contract_for(tmp_path, prompt)
    assert contract["evidence_contract_version"] == EVIDENCE_CONTRACT_VERSION
    assert contract["requirements"][0]["evidence_kinds"] == ["documentation"]
    (tmp_path / "README.md").write_text("Configure Arduino IDE.\n")
    result = verify_requirement_evidence(contract, [citation()], tmp_path, "revision")
    assert result["references_valid"] and not result["valid"] and not result["hardware_verified"]


def test_documentation_heading_is_inherited_without_weakening_firmware_section(tmp_path):
    contract = contract_for(tmp_path, "## README.md\n1. Connection diagram.\n2. Example output.\n"
                            "## Firmware requirements\n1. Reconnect Wi-Fi automatically.")
    by_text = {item["text"]: item["evidence_kinds"] for item in contract["requirements"]}
    assert by_text["1. Connection diagram."] == ["documentation"]
    assert by_text["2. Example output."] == ["documentation"]
    assert by_text["1. Reconnect Wi-Fi automatically."] == ["source"]


def test_explanation_of_tests_does_not_require_running_tests(tmp_path):
    contract = contract_for(tmp_path, "Explain the manual test cases in README.md.")
    assert contract["requirements"][0]["evidence_kinds"] == ["documentation"]
    assert [item["kind"] for item in contract["acceptance_checks"]] == ["source_review"]


def test_duplicate_text_and_sibling_headings_keep_their_own_context(tmp_path):
    contract = contract_for(tmp_path, "## README.md\n### Status\n1. Status output.\n"
                            "## Functionality\n1. Status output.")
    duplicates = [item for item in contract["requirements"] if item["text"] == "1. Status output."]
    assert [item["evidence_kinds"] for item in duplicates] == [["documentation"], ["source"]]


def test_behavior_cannot_be_proved_by_readme_and_feedback_is_specific(tmp_path):
    contract = contract_for(tmp_path, "Reconnect Wi-Fi automatically.")
    (tmp_path / "README.md").write_text("Reconnect Wi-Fi automatically.\n")
    result = verify_requirement_evidence(contract, [citation(quote="Reconnect Wi-Fi automatically.")], tmp_path, "r")
    assert not result["references_valid"]
    feedback = requirement_feedback(contract, result)["checks"][0]
    assert feedback["issues"] == ["source_file_required"]
    assert feedback["requirement"] == "Reconnect Wi-Fi automatically."
    assert feedback["expected_evidence"] == ["source"]
    assert feedback["submitted_evidence"] == [{"path": "README.md"}]


def test_typed_documentation_cannot_be_reclassified_by_changing_its_text(tmp_path):
    contract = contract_for(tmp_path, "Step-by-step Arduino IDE setup.")
    contract["requirements"][0]["text"] = "Preparation steps."
    (tmp_path / "README.md").write_text("Configure Arduino IDE.\n")
    assert verify_requirement_evidence(contract, [citation()], tmp_path, "r")["references_valid"]
    forged = citation()
    forged["evidence_kinds"] = ["source"]
    with pytest.raises(ValueError, match="product_tool_arguments_invalid"):
        tool_plan("forge", "verify_requirements", {"checks": [forged]})


def test_mixed_requirement_needs_source_and_documentation(tmp_path):
    contract = contract_for(tmp_path, "Implement LED blinking and explain the wiring.")
    assert contract["requirements"][0]["evidence_kinds"] == ["documentation", "source"]
    (tmp_path / "main.cpp").write_text("int led = 2;\n")
    (tmp_path / "README.md").write_text("Connect the LED to GPIO 2.\n")
    only_source = citation(path="main.cpp", quote="int led = 2;")
    failed = verify_requirement_evidence(contract, [only_source], tmp_path, "r")
    assert failed["checks"][0]["issues"] == ["documentation_file_required"]
    both = {**only_source, "evidence": only_source["evidence"] + citation(quote="Connect the LED to GPIO 2.")["evidence"]}
    assert verify_requirement_evidence(contract, [both], tmp_path, "r")["references_valid"]


def test_documentation_first_clause_does_not_hide_implementation(tmp_path):
    contract = contract_for(tmp_path, "Explain the wiring and implement LED blinking.")
    assert contract["requirements"][0]["evidence_kinds"] == ["documentation", "source"]


def test_legacy_documented_test_instructions_do_not_require_execution(tmp_path):
    old = contract_for(tmp_path, "Explain the manual test cases in README.md.")
    old.pop("evidence_contract_version")
    old["requirements"][0].pop("evidence_kinds")
    old["acceptance_checks"].append({"id": "unit-tests", "kind": "unit_tests", "criterion": "Tests pass."})
    historical = copy.deepcopy(old)
    upgraded = repair_task_contract(old, workspace=tmp_path)
    assert [check["kind"] for check in upgraded["acceptance_checks"]] == ["source_review"]
    assert old == historical


@pytest.mark.parametrize("prompt,kind", [("Compile the firmware.", "build"), ("Run unit tests.", "unit_tests"), ("Run simulation.", "simulation")])
def test_execution_requirements_need_successful_current_host_results(tmp_path, prompt, kind):
    contract = contract_for(tmp_path, prompt)
    assert contract["requirements"][0]["evidence_kinds"] == [kind]
    (tmp_path / "README.md").write_text("All checks passed.\n")
    checks = [citation(quote="All checks passed.")]
    assert verify_requirement_evidence(contract, checks, tmp_path, "new")["checks"][0]["issues"] == [kind + "_result_required"]
    record = {"success": True, "workspace_revision": "old", "source_revision": "old", "kind": kind, "call_id": "host-result"}
    evidence = {"tests": [record]} if kind == "unit_tests" else {kind: record}
    assert not verify_requirement_evidence(contract, checks, tmp_path, "new", execution_evidence=evidence)["references_valid"]
    record.update(workspace_revision="new", source_revision="new")
    result = verify_requirement_evidence(contract, [{"id": "req-1", "status": "implemented", "evidence": []}],
        tmp_path, "new", execution_evidence=evidence)
    assert result["references_valid"] and not result["valid"]
    assert result["checks"][0]["execution_evidence"][0]["ref"] == "host-result"


def test_legacy_retry_migration_preserves_ids_history_and_original_input(tmp_path):
    old = contract_for(tmp_path, "Step-by-step Arduino IDE setup.\nExplanation of how the code works.")
    old.pop("evidence_contract_version")
    for item in old["requirements"]:
        item.pop("evidence_kinds")
    historical = copy.deepcopy(old)
    new = repair_task_contract(old, workspace=tmp_path)
    assert old == historical
    assert [(q["id"], q["text"], q["stable_key"]) for q in new["requirements"]] == [(q["id"], q["text"], q["stable_key"]) for q in old["requirements"]]
    assert all(q["evidence_kinds"] == ["documentation"] for q in new["requirements"])
    assert new["parent_revision"] == old["revision"] and new["revision"] != old["revision"]
    assert new["revision_history"] == [old["revision"], new["revision"]]
    assert repair_task_contract(new, workspace=tmp_path) == new
    revised = revise_task_contract(new, "Use GPIO 2 for the LED.", project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    assert revised["requirements"][:2] == new["requirements"]
    assert revised["requirements"][-1]["evidence_kinds"] == ["source"]


def test_documentation_suggestions_and_public_schema_keep_rejection_details(tmp_path):
    contract = contract_for(tmp_path, "Step-by-step Arduino IDE setup.")
    (tmp_path / "README.md").write_text("# Project\n## Libraries\nWiFi and WebServer.\n## Arduino IDE Setup\nInstall the ESP32 board package.\n")
    result = verify_requirement_evidence(contract, [citation(quote="WiFi and WebServer.")], tmp_path, "r")
    assert result["references_valid"] and not result["valid"]
    section = result["checks"][0]["suggested_sections"][0]
    assert section == {"path": "README.md", "heading": "Arduino IDE Setup", "start_line": 4, "end_line": 5}
    serialized = VerificationReport.model_validate(result).model_dump()
    assert serialized["checks"][0]["expected_evidence"] == ["documentation"]
    assert serialized["checks"][0]["suggested_sections"][0] == section


def test_wrong_documentation_citation_still_needs_independent_review(tmp_path, monkeypatch):
    contract = contract_for(tmp_path, "Step-by-step Arduino IDE setup.")
    (tmp_path / "README.md").write_text("WiFi and WebServer are required libraries.\n")
    report = verify_requirement_evidence(contract, [citation(quote="WiFi and WebServer are required libraries.")], tmp_path, "r")
    assert report["references_valid"]
    planner, _ = planner_for(tmp_path, monkeypatch, [{"checks": [{"id": "req-1", "verdict": "unverified", "reason": "The citation lists libraries without setup steps."}]}])
    result = asyncio.run(review_requirements(planner, contract, report, lambda path: (tmp_path/path).read_text(), "r",
        deadline=time.monotonic()+60, run_id="wrong-doc-citation"))
    assert not result["valid"] and result["checks"][0]["status"] == "unverified"


def test_large_source_review_keeps_host_execution_evidence(tmp_path, monkeypatch):
    padding = "// source span padding\n" * 3500
    sources = {"main.cpp": "const int LED_PIN = 2;\n" + padding,
               "timer.cpp": "const int LED_INTERVAL = 500;\n" + padding}
    for path, source in sources.items():
        (tmp_path / path).write_text(source)
    contract = contract_for(tmp_path, "Use LED GPIO 2.\nSet the LED interval to 500 ms.\nCompile the firmware.")
    report = verify_requirement_evidence(contract, [citation(path="main.cpp", quote="const int LED_PIN = 2;"),
        citation("req-2", path="timer.cpp", quote="const int LED_INTERVAL = 500;"),
        {"id": "req-3", "status": "implemented", "evidence": []}], tmp_path, "large-revision",
        execution_evidence={"build": {"success": True, "workspace_revision": "large-revision", "build_id": "host-build"}})
    source_review = {"checks": [{"id": "req-1", "verdict": "satisfied", "reason": "The source sets GPIO 2."}]}
    timer_review = {"checks": [{"id": "req-2", "verdict": "satisfied", "reason": "The source sets the interval."}]}
    aggregate = {"checks": source_review["checks"] + timer_review["checks"] + [
        {"id": "req-3", "verdict": "satisfied", "reason": "The current host build passed."}]}
    planner, adapter = planner_for(tmp_path, monkeypatch, [source_review] * 2 + [timer_review] * 2 + [aggregate])
    result = asyncio.run(review_requirements(planner, contract, report, lambda path: (tmp_path/path).read_text(),
        "large-revision", deadline=time.monotonic()+60, run_id="large-build"))
    assert result["valid"] and result["semantic_review"]["coverage_complete"]
    assert len(adapter.requests) == 5
    prompt = adapter.requests[-1].prompt
    assert '"host_execution_evidence"' in prompt and '"ref": "host-build"' in prompt


def test_repeated_rejection_recovery_resets_after_content_or_task_changes(tmp_path):
    contract = contract_for(tmp_path, "Use LED GPIO 2.")
    (tmp_path / "main.cpp").write_text("int led = 2;\n")
    def rejected(revision="r"):
        return verify_requirement_evidence(contract, [citation(path="main.cpp", quote="int led = 4;")], tmp_path, revision)
    tracker = RejectedEvidenceTracker()
    tracker.observe(rejected(), {"req-1"})
    second = rejected()
    tracker.observe(second, {"req-1"})
    assert second["evidence_recovery"] == {"repeated_ids": ["req-1"], "stop_ids": [], "stop": False}
    assert "Repeated rejected evidence" in requirement_feedback(contract, second)["next_action"]
    tracker = RejectedEvidenceTracker(tracker.entries)
    third = rejected()
    tracker.observe(third, {"req-1"})
    assert third["evidence_recovery"]["stop"]
    edited = rejected("new-revision")
    tracker.observe(edited, {"req-1"})
    assert edited["checks"][0]["rejection_attempts"] == 1
    contract["revision"] = "new-task-revision"
    revised = rejected("new-revision")
    tracker.observe(revised, {"req-1"})
    assert revised["checks"][0]["rejection_attempts"] == 1


def test_autonomous_repeated_bad_citations_stop_before_another_model_request(tmp_path):
    (tmp_path / "main.cpp").write_text("int led = 2;\n")
    contract = contract_for(tmp_path, "Use LED GPIO 2.")
    checks = [citation(path="main.cpp", quote="int led = 4;")]
    planner = SequencePlanner([tool_plan(f"bad-{i}", "verify_requirements", {"checks": checks}) for i in range(5)])
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=planner, platformio=Builder(),
        progress=progress, task_contract=contract)
    result = asyncio.run(loop.run(task=contract["request"], require_build=False))
    assert result.classification == "REQUIREMENTS_UNVERIFIED" and not result.success
    assert result.tool_execution_count == 3
    assert len(planner.seen_results) == 3 and len(planner.turns) == 2
    assert result.requirement_report["evidence_recovery"]["stop_ids"] == ["req-1"]
    assert "Repeated unchanged evidence" in result.message
    assert (tmp_path / "main.cpp").read_text() == "int led = 2;\n"


def test_overlay_repeated_bad_citations_stop_and_leave_active_files_intact(tmp_path):
    runtime, active, _ = make_runtime(tmp_path)
    (active / "main.cpp").write_text("int led = 2;\n")
    contract = contract_for(active, "Use LED GPIO 2.")
    checks = [citation(path="main.cpp", quote="int led = 4;")]
    planner = SequencePlanner([tool_plan(f"bad-{i}", "verify_requirements", {"checks": checks}) for i in range(5)])
    result = runtime.run_product(task=contract["request"], planner=planner, policy=product_agent_policy(), task_contract=contract)
    assert result.classification is RuntimeClassification.REQUIREMENTS_UNVERIFIED
    assert result.tool_execution_count == 3
    assert len(planner.seen_results) == 3
    assert (active / "main.cpp").read_text() == "int led = 2;\n"


def test_autonomous_build_plus_documentation_review_can_complete(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    contract = contract_for(workspace, "Use LED GPIO 2.\nStep-by-step Arduino IDE setup.\nCompile the firmware.")
    source = "const int LED_PIN = 2;\n"
    readme = "# Arduino IDE Setup\nInstall the ESP32 board package.\nSelect ESP32 Dev Module and upload src/main.cpp.\n"
    outputs = [
        wire_plan(tool_plan("source", "write_file", {"path": "src/main.cpp", "content": source})),
        wire_plan(tool_plan("docs", "write_file", {"path": "README.md", "content": readme})),
        wire_plan(tool_plan("build", "build_firmware", {})),
        wire_plan(tool_plan("evidence", "verify_requirements", {"checks": [citation(path="src/main.cpp", quote=source.strip()),
            citation("req-2", quote=readme.strip()), {"id": "req-3", "status": "implemented", "evidence": []}]})),
        {"version": PRODUCT_TOOLPLAN_VERSION, "summary": "Complete.", "tool_calls": [], "final": True},
        {"checks": [{"id": f"req-{i}", "verdict": "satisfied", "reason": "The supplied source, documentation or host build result meets this requirement."} for i in range(1,4)]},
    ]
    planner, adapter = planner_for(tmp_path, monkeypatch, outputs)
    loop = AutonomousForgeAgentLoop(workspace_root=workspace, planner=planner, platformio=Builder(), progress=progress, task_contract=contract)
    result = asyncio.run(loop.run(task=contract["request"], require_build=True))
    assert result.success, (result.message, result.requirement_report)
    assert result.build_verified and result.requirement_report["valid"]
    assert not result.requirement_report["hardware_verified"]
    assert len(adapter.requests) == 6
