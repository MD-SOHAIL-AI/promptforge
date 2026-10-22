import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from backend.agent_runtime.task_contract import (
    create_task_contract, repair_task_contract, verify_requirement_evidence,
    _has_noncomment_quote, _logical_requirement_groups,
)
from backend.agent_runtime.tool_contracts import ProductToolCall, ToolName, provider_function_schema
from backend.agent_runtime.task_state import task_state_v1
from backend.agent_runtime.tool_runtime import _tool_result_summary


@pytest.mark.parametrize("content,quote,suffix,accepted", [
    ("// threshold\nint threshold = 500;", "// threshold\nint threshold = 500;", ".cpp", True),
    ("/* description */\nvoid loop() {}", "/* description */\nvoid loop() {}", ".cpp", True),
    ("/* start\n fake();\n*/", "fake();", ".cpp", False),
    ("// fake();\nint x;", "fake();", ".cpp", False),
    ('const char* url = "http://host/*path*/";', '"http://host/*path*/"', ".cpp", True),
    ("; board config\nboard = esp32dev", "; board config\nboard = esp32dev", ".ini", True),
    ("# board = esp32dev", "board = esp32dev", ".ini", False),
])
def test_comment_scan_uses_entire_file_and_span(content, quote, suffix, accepted):
    assert _has_noncomment_quote(content, quote, suffix) is accepted


def test_line_evidence_extracts_current_source_but_never_claims_review(tmp_path):
    path = tmp_path / "src/main.cpp"
    path.parent.mkdir()
    path.write_text("// threshold\nint threshold = 500;\nvoid loop() {}\n")
    contract = create_task_contract("Configure threshold", project_id="p", session_id="s", workspace=tmp_path, intent="edit")
    check = {"id": "req-1", "status": "implemented", "evidence": [{"path": "src/main.cpp", "start_line": 1, "end_line": 2}]}
    parsed = ProductToolCall.parse({"id": "v", "type": "verify_requirements", "arguments": {"checks": json.dumps([check])}})
    report = verify_requirement_evidence(contract, parsed.arguments["checks"], tmp_path, "revision-1")
    assert report["references_valid"] and not report["valid"]
    assert report["checks"][0]["evidence"][0]["quote"] == "// threshold\nint threshold = 500;"
    assert len(report["checks"][0]["evidence"][0]["content_sha256"]) == 64
    path.write_text("// threshold\n// int threshold = 500;\n")
    assert not verify_requirement_evidence(contract, [check], tmp_path, "revision-2")["references_valid"]
    check["evidence"][0]["end_line"] = 99
    assert "line_range_invalid" in verify_requirement_evidence(contract, [check], tmp_path, "revision-2")["checks"][0]["issues"]


@pytest.mark.parametrize("checks", ['{}', 'null', '[] trailing', '"[]"', '[{"id":"req-1","id":"req-2"}]', '[NaN]', '[]'])
def test_encoded_checks_still_reject_invalid_schema(checks):
    with pytest.raises(ValueError):
        ProductToolCall.parse({"id": "v", "type": "verify_requirements", "arguments": {"checks": checks}})


def test_span_schema_matches_runtime_validation():
    schema = provider_function_schema(ToolName.VERIFY_REQUIREMENTS)["function"]["parameters"]
    evidence_schema = schema["properties"]["checks"]["items"]["properties"]["evidence"]["items"]
    assert "additionalProperties" not in evidence_schema
    for evidence in ({"path": "src/main.cpp", "start_line": 1, "end_line": 2}, {"path": "src/main.cpp", "quote": "void loop() {}"}):
        args = {"checks": [{"id": "req-1", "status": "implemented", "evidence": [evidence]}]}
        branch = next(item for item in evidence_schema["anyOf"] if set(item["required"]) == set(evidence))
        assert set(branch["properties"]) == set(evidence) and branch["additionalProperties"] is False
        ProductToolCall.parse({"id": "v", "type": "verify_requirements", "arguments": args})
    with pytest.raises(ValueError):
        ProductToolCall.parse({"id": "v", "type": "verify_requirements", "arguments": {"checks": [
            {"id": "req-1", "status": "implemented", "evidence": [{"path": "src/main.cpp", "start_line": True, "end_line": 3}]}]}})


def test_logical_groups_preserve_all_clauses_and_complete_examples(tmp_path):
    lines = ["Hardware:", "ESP32", "piezo", "Suggested states:", "IDLE", "down arrow", "WAIT", "DONE", "Requirements:"]
    lines += [f"{i}. Implement behavior {i}." for i in range(1, 41)]
    groups = _logical_requirement_groups(lines)
    assert "\n".join(groups) == "\n".join(lines)
    assert any("Suggested states:\nIDLE\ndown arrow\nWAIT\nDONE" in group for group in groups)
    assert not any(group.strip() == "Requirements:" for group in groups)
    old = create_task_contract("\n".join(lines), project_id="p", session_id="s", workspace=tmp_path, intent="edit")
    old.update(partition_version=3, requirements=[{"id": f"req-{i}", "text": text, "stable_key": str(i)} for i, text in enumerate(lines, 1)])
    snapshot = copy.deepcopy(old)
    upgraded = repair_task_contract(old, workspace=tmp_path)
    assert old == snapshot
    assert upgraded["partition_version"] == 5 and upgraded["parent_revision"] == old["revision"]
    assert "\n".join(item["text"] for item in upgraded["requirements"]) == "\n".join(line for line in lines if line != "Requirements:")
    assert "Requirements:" in upgraded["request_context"]
    assert any(item["reason"] == "logical_requirement_grouping" for item in upgraded["superseded_requirements"])
    unchanged = next(item for item in upgraded["requirements"] if item["text"] == "10. Implement behavior 10.")
    assert unchanged["id"] == "req-19"


def test_state_and_progress_report_evidence_rejection_after_successful_build():
    report = {"verification_level": "source_reference", "checks": [{"id": "req-1", "status": "unverified", "issues": ["quote_not_found"]}], "missing": ["req-2"]}
    state = task_state_v1(contract={"requirements": []}, plan=[], observations=[], build={"success": True}, report=report, repair_history=[], source_revision="r")
    assert "req-1" in state["next_action"] and "req-2" in state["next_action"]
    assert "dependency" not in state["next_action"]
    summary = _tool_result_summary(report)
    assert summary["rejected_count"] == 1 and summary["missing_count"] == 1
    assert "rejected" in summary["message"] and "pending" in summary["message"]


def test_verification_recovery_targets_ids_not_unnecessary_rebuild(tmp_path):
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from tests.unit.test_agent_verification_freshness import Builder, progress
    from tests.unit.test_tool_runtime import SequencePlanner
    loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, platformio=Builder(), planner=SequencePlanner([]), progress=progress,
        task_contract={"requirements": [{"id": "req-7", "text": "Detect knocks"}]})
    loop.build_passed = True
    assert "req-7" in loop._verification_recovery_hint()
    assert "start_line" in loop._verification_recovery_hint()
    assert "run the required build" not in loop._verification_recovery_hint()


def test_read_only_selects_only_intact_same_session_draft(tmp_path):
    from backend.agent_runtime.orchestrator import AgentOrchestrator
    from backend.changes.service import ChangeSetService
    workspace = tmp_path / "original"
    workspace.mkdir()
    changes = ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "stages")
    stage = changes.create_stage()
    (stage / "main.cpp").write_text("void loop() {}")
    draft = changes.create_from_stage(provider_id="test", workspace_root=workspace, stage_root=stage,
        baseline=changes.inspect_workspace(workspace), authorized_paths=["main.cpp"], draft=True)
    owner = AgentOrchestrator.__new__(AgentOrchestrator)
    owner.change_service = changes
    current = SimpleNamespace(run_id="current", project_id="p", autonomy="read_only")
    prior = SimpleNamespace(run_id="prior", project_id="p", change_set_id=draft.change_set_id, created_at="2026-10-07")
    owner._runs = {"prior": prior}
    owner._session_for_run = {"current": "s", "prior": "s"}
    assert owner._read_only_draft_root(current, workspace) == (stage, draft.change_set_id)
    owner._session_for_run["prior"] = "foreign"
    assert owner._read_only_draft_root(current, workspace) == (workspace, None)
    owner._session_for_run["prior"] = "s"
    (stage / "extra.cpp").write_text("unbound")
    assert owner._read_only_draft_root(current, workspace) == (workspace, None)
    (stage / "extra.cpp").unlink()
    (stage / "main.cpp").write_text("tampered")
    assert owner._read_only_draft_root(current, workspace) == (workspace, None)


def test_read_only_run_reads_saved_draft_without_mutating_original(tmp_path):
    from backend.agent_runtime.orchestrator import AgentOrchestrator
    from backend.agent_runtime.run_models import ProductAgentRun
    from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
    from tests.unit.test_agent_orchestrator_v2 import _changes, _providers
    from tests.unit.test_tool_runtime import SequencePlanner, plan
    from backend.agent_runtime.tool_contracts import ProductToolPlan

    async def scenario():
        workspace = tmp_path / "original"
        workspace.mkdir()
        changes, providers = _changes(tmp_path), _providers(tmp_path)
        store = AgentOrchestrationStore(tmp_path / "journal.sqlite3")
        stage = changes.create_stage()
        (stage / "src").mkdir()
        (stage / "src/main.cpp").write_text("int threshold = 500;\n")
        draft = changes.create_from_stage(provider_id="test", workspace_root=workspace, stage_root=stage,
            baseline=changes.inspect_workspace(workspace), authorized_paths=["src/main.cpp"], draft=True)
        owner = AgentOrchestrator(change_service=changes, provider_registry=providers, store=store, run_factory=ProductAgentRun)
        # Persisted historical failed run owns the draft in this project/session.
        prior = ProductAgentRun(run_id="prior", project_id="p", provider_id="fake_planner")
        prior.change_set_id = draft.change_set_id
        owner._runs[prior.run_id] = prior
        owner._session_for_run[prior.run_id] = "s"
        read = ProductToolPlan.parse({"version": "forgex.toolplan.v2", "summary": "Inspect draft", "final": False,
            "tool_calls": [{"id": "read", "type": "read_file", "arguments": {"path": "src/main.cpp"}}]})
        planner = SequencePlanner([read, plan(final=True)])
        providers.resolve = lambda *args, **kwargs: planner
        try:
            run = await owner.start_run(project_id="p", active_workspace_root=workspace, instruction="Inspect the firmware",
                provider_id="fake_planner", autonomy="read_only", session_id="s")
            await owner._tasks[run.run_id]
            assert run.classification == "READ_ONLY_COMPLETED", run.assistant_message_override
            assert not list(workspace.iterdir())
            assert "int threshold = 500;" in json.dumps(planner.seen_results)
            assert owner._recovery_context[run.run_id]["workspace_root"] == str(workspace)
            assert owner._recovery_context[run.run_id]["inspection_change_set_id"] == draft.change_set_id
            assert changes.get(draft.change_set_id).status == "draft"
        finally:
            await owner.close()
    asyncio.run(scenario())
