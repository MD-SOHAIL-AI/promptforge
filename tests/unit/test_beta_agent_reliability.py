from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from backend.agent_runtime.api_planner_provider import ApiPlannerProvider, ApiPlannerError, API_PLANNER_CONFIGS
from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
from backend.agent_runtime.failure_contract import failure_details
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.orchestrator import AgentOrchestrator
from backend.agent_runtime.projection import SessionProjectionEngine
from backend.agent_runtime.retention import prune_orphan_stages
from backend.agent_runtime.run_models import ProductAgentRun
from backend.agent_runtime.session_store import AgentSessionStore
from backend.agent_runtime.task_contract import create_task_contract, clarification_choices, resolve_clarification, verify_requirement_evidence
from backend.agent_runtime.tool_contracts import ProductToolPlan
from backend.agent_runtime.tool_policy import product_agent_policy
from backend.changes import ChangeSetService


def plan(calls=(), final=False):
    return {"version": "forgex.toolplan.v2", "summary": "Project completed successfully", "tool_calls": list(calls), "final": final}


class SemanticReviewer:
    def request_turn(self, **kwargs):
        assert not kwargs["allowed_tools"]
        return ProductToolPlan.parse({**plan(final=True), "summary": json.dumps({"checks": [
            {"id": "req-1", "verdict": "satisfied", "reason": "The cited implementation uses GPIO 2."}]})})


@pytest.mark.parametrize("task", ["Create a smoke sensor project", "Do not add smoke sensors", "Read smoke.cpp", "History: smoke test\nLatest user request: Create ESP32 firmware"])
def test_prose_cannot_activate_diagnostic_mode(task):
    output = plan([{"id": "write", "type": "write_file", "arguments": {"path": "src/main.cpp", "content": "void setup() {}\nvoid loop() {}"}}])
    provider = ApiPlannerProvider(API_PLANNER_CONFIGS["openai"], api_key="test", model_id="test", transport=lambda *args: {"choices": [{"message": {"content": json.dumps(output)}}]})
    assert provider.request_turn(task=task, tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1).tool_calls[0].path == "src/main.cpp"
    diagnostic = ApiPlannerProvider(API_PLANNER_CONFIGS["openai"], api_key="test", model_id="test", diagnostic_smoke=True, transport=lambda *args: {"choices": [{"message": {"content": json.dumps(output)}}]})
    with pytest.raises(ApiPlannerError, match="provider_smoke_toolplan_unsafe"):
        diagnostic.request_turn(task="ordinary prompt", tool_results=(), allowed_tools=("write_file",), run_id="run", turn=1)


def test_policy_failure_is_not_misreported_as_provider_outage():
    run = ProductAgentRun("run", "project", "nvidia_nim", status="failed", classification="provider_smoke_toolplan_unsafe", model_id="selected")
    safe = run.to_safe_dict()
    assert safe["summary"]["errors"][0]["code"] == "POLICY_DENIED"
    assert safe["summary"]["selected_model"] == "selected"
    assert safe["failure"]["category"] == "policy_rejection"
    assert failure_details("SOMETHING_UNKNOWN", status="failed")["category"] == "runtime"


def test_clarification_survives_restart_and_resolves_all_choices(tmp_path):
    request = "Create an ESP32 monitor with OLED or TFT and MQTT or ThingSpeak."
    pending = {"request": request, "choices": clarification_choices(request)}
    assert len(pending["choices"]) == 2
    store = AgentSessionStore(tmp_path / "sessions.sqlite3")
    session = store.create(project_id="project")
    store.set_clarification(session.session_id, pending)
    restored = AgentSessionStore(store.path).get(session.session_id)
    assert restored.status == "awaiting_clarification"
    resolved, missing = resolve_clarification(restored.pending_clarification, "OLED and MQTT")
    assert not missing and "MQTT or ThingSpeak" not in resolved
    assert not clarification_choices(resolved)
    assert resolve_clarification(pending, "OLED")[1]
    assert not clarification_choices("Future: MQTT or ThingSpeak. Do not implement this.")


def test_source_evidence_rejects_missing_fabricated_and_readme_only_proof(tmp_path):
    contract = create_task_contract("Make an ESP32 blink LED project", project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.cpp").write_text("void loop() { digitalWrite(2, HIGH); }", encoding="utf-8")
    checks = [{"id": "req-1", "status": "implemented", "evidence": [{"path": "src/main.cpp", "quote": "digitalWrite(2, HIGH)"}]}]
    report = verify_requirement_evidence(contract, checks, tmp_path, "revision")
    assert report["references_valid"] and not report["valid"]
    checks[0]["evidence"][0]["quote"] = "not in source"
    assert not verify_requirement_evidence(contract, checks, tmp_path, "revision")["references_valid"]
    (tmp_path / "src/main.cpp").write_text("// digitalWrite(2, HIGH)\n", encoding="utf-8")
    checks[0]["evidence"] = [{"path": "src/main.cpp", "quote": "digitalWrite(2, HIGH)"}]
    assert not verify_requirement_evidence(contract, checks, tmp_path, "revision")["references_valid"]
    (tmp_path / "src/main.cpp").write_text("/*\ndigitalWrite(2, HIGH)\n*/\n", encoding="utf-8")
    assert not verify_requirement_evidence(contract, checks, tmp_path, "revision")["references_valid"]
    (tmp_path / "src/main.cpp").write_text("void loop() { digitalWrite(2, HIGH); }", encoding="utf-8")
    (tmp_path / "README.md").write_text("digitalWrite(2, HIGH)", encoding="utf-8")
    checks[0]["evidence"] = [{"path": "README.md", "quote": "digitalWrite(2, HIGH)"}]
    assert not verify_requirement_evidence(contract, checks, tmp_path, "revision")["references_valid"]
    checks[0]["evidence"][0]["path"] = "../escape.cpp"
    with pytest.raises(ValueError):
        verify_requirement_evidence(contract, checks, tmp_path, "revision")


def test_incident_prompt_corpus_preserves_long_specifications(tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures/beta-agent-prompts.json"
    for case in json.loads(fixture.read_text(encoding="utf-8"))["cases"]:
        contract = create_task_contract(case["request"], project_id="p", session_id="s", workspace=tmp_path, intent="generate")
        assert 1 <= len(contract["requirements"]) <= 64
        if case["id"] == "smoke-exclusion":
            assert any("smoke" in line.casefold() for line in contract["exclusions"])
            assert len(contract["request"]) > 15000
        if case["id"] == "cloud-monitor":
            assert clarification_choices(case["request"])[0]["id"] == "cloud"


def test_real_loop_cannot_claim_success_without_requirement_evidence(tmp_path):
    class Planner:
        def request_turn(self, **kwargs):
            if kwargs["turn"] == 1:
                return ProductToolPlan.parse(plan([{"id": "write", "type": "write_file", "arguments": {"path": "src/main.cpp", "content": "int led = 2;"}}]))
            return ProductToolPlan.parse(plan(final=True))
    async def progress(*args):
        pass
    contract = create_task_contract("Create an ESP32 LED project", project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    result = asyncio.run(AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=Planner(), platformio=object(), progress=progress,
        task_contract=contract).run(task=contract["request"], require_build=False))
    assert not result.success and result.classification == "REQUIREMENTS_UNVERIFIED"
    assert result.tool_execution_count == 1
    assert "completed successfully" not in result.message


def test_loop_accepts_verified_source_and_invalidates_evidence_after_edit(tmp_path):
    async def scenario(stale):
        class Planner:
            def for_role(self, role):
                assert role == "review"
                return SemanticReviewer()
            def request_turn(self, **kwargs):
                turn = kwargs["turn"]
                if turn == 1:
                    calls = [{"id": "write", "type": "write_file", "arguments": {"path": "src/main.cpp", "content": "int led = 2;"}}]
                elif turn == 2:
                    calls = [{"id": "verify", "type": "verify_requirements", "arguments": {"checks": [{"id": "req-1", "status": "implemented", "evidence": [{"path": "src/main.cpp", "quote": "int led = 2;"}]}]}}]
                elif stale and turn == 3:
                    calls = [{"id": "edit", "type": "write_file", "arguments": {"path": "src/main.cpp", "content": "int led = 3;"}}]
                else:
                    return ProductToolPlan.parse(plan(final=True))
                return ProductToolPlan.parse(plan(calls))
        async def progress(*args):
            pass
        contract = create_task_contract("Use LED GPIO 2", project_id="p", session_id="s", workspace=tmp_path, intent="edit")
        return await AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=Planner(), platformio=object(), progress=progress,
            task_contract=contract).run(task=contract["request"], require_build=False)
    good = asyncio.run(scenario(False))
    assert good.success and good.requirement_report["valid"]
    bad = asyncio.run(scenario(True))
    assert not bad.success and bad.classification == "REQUIREMENTS_UNVERIFIED"


def test_terminal_projection_settles_tools_and_ignores_late_content():
    engine = SessionProjectionEngine()
    state = engine.rebuild("s", [{"sequence": 1, "event_type": "turn.started", "turn": 1},
        {"sequence": 2, "event_type": "tool.execution.started", "call_id": "one"},
        {"sequence": 3, "event_type": "runtime.failed", "status": "failed"}])
    assert state.run_status == "failed" and not state.pending_tools and state.active_turn is None
    engine.apply_transient(state, {"event_type": "assistant.content.delta", "delta": "success!"})
    assert not state.assistant_content


def test_legacy_missing_terminal_is_reconciled_once_with_backup(tmp_path):
    store = AgentOrchestrationStore(tmp_path / "state.sqlite3")
    run = ProductAgentRun("run", "p", "provider", status="failed", classification="provider_smoke_toolplan_unsafe")
    store.save_run("run", "session", "p", "failed", {**run.to_safe_dict(), "session_id": "session"})
    store.append_event("session", {"event_type": "agent.model.requested", "run_id": "run"})
    changes = ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "stages")
    for _ in range(2):
        AgentOrchestrator(change_service=changes, provider_registry=SimpleNamespace(), store=store, run_factory=ProductAgentRun)
    events = list(store.snapshot_events("session"))
    assert sum(item["event_type"] == "runtime.failed" for item in events) == 1
    assert store.path.with_name(store.path.name + ".pre-settlement-reconciliation.bak").is_file()
    assert store.verify_journal("session")["healthy"]


def test_retention_preserves_referenced_stages_and_user_folders(tmp_path):
    root = tmp_path / "stages"
    root.mkdir()
    for name in ("checkpoint-orphan", "checkpoint-live", "user-project"):
        folder = root / name
        folder.mkdir()
        os.utime(folder, (time.time() - 30 * 86400,) * 2)
    assert prune_orphan_stages(root, [root / "checkpoint-live/file.cpp"]) == ["checkpoint-orphan"]
    assert (root / "checkpoint-live").exists() and (root / "user-project").exists()


def test_future_sections_do_not_trigger_choice_questions(tmp_path):
    request = "# Required\nUse OLED.\n# Future Expansion\nMQTT or ThingSpeak\n# Final Output\nInclude README instructions."
    assert clarification_choices(request) == []
    contract = create_task_contract(request, project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    assert "MQTT or ThingSpeak" in contract["exclusions"]
    assert any("README" in item["text"] for item in contract["requirements"])


def test_dependency_repair_budget_survives_continuation(tmp_path):
    class Registry:
        calls = 0
        async def validate_dependencies(self, root):
            self.calls += 1
            return {"valid": False, "missing": [{"requested": "invented/BH1750"}]}
    async def progress(*args):
        pass
    async def scenario():
        contract = create_task_contract("Build BH1750 firmware", project_id="p", session_id="s", workspace=tmp_path, intent="generate")
        registry = Registry()
        loop = AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=object(), platformio=registry, progress=progress, task_contract=contract)
        for attempt in range(4):
            (tmp_path / "main.cpp").write_text(f"int revision = {attempt};", encoding="utf-8")
            await loop._build("esp32dev")
        resumed = AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=object(), platformio=registry, progress=progress,
            task_contract=contract, resume_checkpoint={"repair_groups": dict(loop._repair_groups)})
        resumed.last_build = dict(loop.last_build)
        result = await resumed._build("esp32dev")
        assert result["output"]["error_code"] == "BUILD_REPAIR_EXHAUSTED"
        assert registry.calls == 4
    asyncio.run(scenario())


def test_source_evidence_remains_current_when_build_only_adds_artifacts(tmp_path):
    from tests.unit.test_agent_verification_freshness import Builder
    from tests.unit.test_tool_runtime import SequencePlanner
    class ReviewedSequencePlanner(SequencePlanner):
        def for_role(self, role):
            return SemanticReviewer()
    async def progress(*args):
        pass
    contract = create_task_contract("Use LED GPIO 2", project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    plans = [
        plan([{"id": "write", "type": "write_file", "arguments": {"path": "src/main.cpp", "content": "int led = 2;"}}]),
        plan([{"id": "verify", "type": "verify_requirements", "arguments": {"checks": [{"id": "req-1", "status": "implemented", "evidence": [{"path": "src/main.cpp", "quote": "int led = 2;"}]}]}}]),
        plan(final=True),
    ]
    result = asyncio.run(AutonomousForgeAgentLoop(workspace_root=tmp_path, planner=ReviewedSequencePlanner([ProductToolPlan.parse(item) for item in plans]),
        platformio=Builder(), progress=progress, task_contract=contract).run(task=contract["request"], require_build=True))
    assert result.success, result.message
    assert result.build_result["success"]
    assert result.requirement_report["valid"]
    assert result.requirement_report["workspace_revision"] == result.build_result["workspace_revision"]


@pytest.mark.parametrize("uncertain", [False, True])
def test_legacy_smoke_incident_can_retry_with_current_contract_but_never_replay_effects(tmp_path, monkeypatch, uncertain):
    store = AgentOrchestrationStore(tmp_path / "agent.sqlite3")
    workspace = tmp_path / "project"
    workspace.mkdir()
    request = "Create an ESP32 controller. Do not add smoke sensors."
    run = ProductAgentRun("old", "p", "real-provider", status="failed", classification="provider_smoke_toolplan_unsafe", autonomy="staged_changes")
    store.save_run("old", "s", "p", "failed", {**run.to_safe_dict(), "session_id": "s",
        "_recovery_context": {"instruction": request, "workspace_root": str(workspace), "active_workspace": {"agent_turn_intent": "generate"}, "explicit_edit_authorized": True}})
    store.append_event("s", {"event_type": "agent.model.requested", "run_id": "old"})
    if uncertain:
        store.operation("apply", "old", "workspace.apply", status="prepared", payload={})
        store.operation("apply", "old", "workspace.apply", status="started", payload={})
    owner = AgentOrchestrator(change_service=ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "stages"),
        provider_registry=SimpleNamespace(), store=store, run_factory=ProductAgentRun)
    captured = []
    async def start(**kwargs):
        captured.append(kwargs)
        replacement = ProductAgentRun(kwargs["reserved_run_id"], "p", "real-provider")
        owner._runs[replacement.run_id] = replacement
        owner._session_for_run[replacement.run_id] = "s"
        store.save_run(replacement.run_id, "s", "p", "queued", replacement.to_safe_dict())
        return replacement
    monkeypatch.setattr(owner, "start_run", start)
    if uncertain:
        with pytest.raises(ValueError, match="OUTCOME_UNKNOWN"):
            asyncio.run(owner.retry("old"))
        assert not captured
    else:
        replacement = asyncio.run(owner.retry("old"))
        assert replacement.run_id != "old"
        contract = captured[0]["active_workspace"]["task_contract"]
        assert contract["request"] == request and contract["session_id"] == "s"
        assert owner._runs["old"].recovered_by_run_id == replacement.run_id
        assert store.verify_journal("s")["healthy"]
