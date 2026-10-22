"""Isolated reproductions of the October 13 live-log failures."""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from backend.agent_runtime.build_readiness import local_header_readiness
from backend.agent_runtime.orchestrator import _emit_orchestrator_diagnostic
from backend.agent_runtime.tool_contracts import ToolCall, ToolName
from backend.agent_runtime.tool_executor import ForgeXToolExecutor, EditMatchError
from backend.agent_runtime.tool_policy import product_agent_policy
from backend.agent_runtime.workspace_io import workspace_io
from tests.unit.test_live_session_recovery import loop_for


def test_failed_whole_file_edit_exposes_exact_whitespace_without_applying_it(tmp_path):
    current = "class ButtonManager {\npublic:\n    void update();\n    \nprivate:\n    bool buttonPressed;\n};\n"
    source = tmp_path / "ButtonManager.h"
    source.write_bytes(current.encode())
    executor = ForgeXToolExecutor(write_root=tmp_path, policy=product_agent_policy())
    failed = ToolCall(ToolName.EDIT_FILE_SIMPLE, {"path": source.name,
        "old_text": current.replace("    \n", "\n"), "new_text": "unrequested replacement"})
    with pytest.raises(EditMatchError) as caught:
        executor.execute(failed)
    evidence = caught.value.details
    assert evidence["reason"] == "no_match"
    assert evidence["current_excerpt"]["content"] == current
    assert source.read_bytes() == current.encode()
    executor.execute(ToolCall(ToolName.READ_FILE, {"path": source.name}))
    assert executor.execute(ToolCall(ToolName.EDIT_FILE_SIMPLE, {"path": source.name,
        "old_text": "    bool buttonPressed;", "new_text": "    bool wasButtonPressed();"}))["modified"]
    assert source.read_text() == current.replace("    bool buttonPressed;", "    bool wasButtonPressed();")


def test_edit_repair_advice_survives_compaction_and_expires_after_a_source_change(tmp_path):
    source = tmp_path / "ButtonManager.h"
    source.write_bytes(b"bool buttonPressed;\n")
    current = source.read_text()
    loop = loop_for(tmp_path, object())
    details = EditMatchError(source.name, 0, current, "bool button;").details
    loop.observations = [{"tool": "edit_file_simple", "status": "failed", "output": {
        "error": "edit_match_must_be_exactly_one", **details}}] + [
        {"tool": "list_files", "status": "completed", "output": []} for _ in range(35)]
    context, _ = loop._compacted_observations()
    focus = next(item["output"] for item in context if item["tool"] == "edit_repair_focus")
    assert focus["current_excerpt"]["content"] == current
    source.write_text("bool wasButtonPressed();\n")
    context, _ = loop._compacted_observations()
    assert not any(item["tool"] == "edit_repair_focus" for item in context)


def test_current_headers_are_pinned_before_implementation_exists(tmp_path):
    (tmp_path / "include").mkdir()
    (tmp_path / "include/ButtonManager.h").write_text("class ButtonManager { public: void update(); };\n")
    loop = loop_for(tmp_path, object())
    context, _ = loop._compacted_observations()
    focus = next(item["output"] for item in context if item["tool"] == "interface_repair_focus")
    assert focus["missing_local_implementations"][0]["methods"] == ["update"]
    assert "update()" in focus["declarations"][0]["declaration"]


def test_inline_definitions_and_initialized_constructors_have_precise_advice():
    files = {"include/AlarmManager.h": "class AlarmManager {\npublic:\n AlarmManager(int pin);\n bool muted() const { return check(); }\n};\n",
        "src/AlarmManager.cpp": "AlarmManager::AlarmManager(int pin) : pin(pin) {}\nbool AlarmManager::muted() const { return false; }\nvoid AlarmManager::muteBuzzer() {}\n"}
    result = local_header_readiness(files, files.__getitem__)
    assert result["ready"]
    assert len(result["interface_conflicts"]) == 2
    assert any("defined inline" in item["issue"] for item in result["interface_conflicts"])
    assert any("muteBuzzer" in item["issue"] for item in result["interface_conflicts"])
    inline = {"include/HeaderOnly.h": "class HeaderOnly { public: void update() { helper(); } };\n"}
    assert not local_header_readiness(inline, inline.__getitem__)["missing_local_implementations"]
    overloads = {"include/Relay.h": "class Relay { public: bool state() const { return true; } bool state(); bool state(int pin); };",
        "src/Relay.cpp": "bool Relay::state() { return false; } bool Relay::state(int pin) { return false; }"}
    assert not local_header_readiness(overloads, overloads.__getitem__)["interface_conflicts"]


def test_slow_context_inspection_keeps_scheduled_observations_responsive(tmp_path, monkeypatch):
    from tests.unit.test_tool_runtime import SequencePlanner, plan
    loop = loop_for(tmp_path, SequencePlanner([plan(path="main.cpp", content="int main() {}")]),
        max_turns=1, max_progress_extension_turns=0)
    original = loop._compacted_observations
    started = threading.Event()
    def slow_context():
        started.set()
        time.sleep(.2)
        return original()
    monkeypatch.setattr(loop, "_compacted_observations", slow_context)
    async def scenario():
        task = asyncio.create_task(loop.run(task="Create one source file", require_build=False))
        assert await asyncio.to_thread(started.wait, 3)
        before = time.monotonic()
        await asyncio.sleep(.01)
        assert time.monotonic() - before < .15
        await task
    asyncio.run(scenario())


def test_workspace_cancellation_drains_worker_before_cleanup_even_when_cancelled_twice():
    entered, release = threading.Event(), threading.Event()
    outcomes = []
    def operation():
        entered.set()
        assert release.wait(3)
        outcomes.append("finished")
        return "new artifact"
    def cleanup(result):
        assert outcomes == ["finished"]
        outcomes.append(result)
    async def scenario():
        task = asyncio.create_task(workspace_io(operation, on_cancel=cleanup))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(.01)
        task.cancel()
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert outcomes == ["finished", "new artifact"]
    asyncio.run(scenario())


def test_health_stays_responsive_while_a_failed_workflow_saves_its_recovery_draft(tmp_path, monkeypatch):
    from dataclasses import fields
    from pathlib import Path
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from backend.api.routes.health import router
    from backend.api.service_container import ForgeXServices
    from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
    from backend.agent_runtime.orchestrator import AgentOrchestrator
    from backend.agent_runtime.product_agent_service import ProductAgentRun
    from tests.unit.test_agent_orchestrator_v2 import _changes, _providers
    workspace = tmp_path / "project"
    workspace.mkdir()
    changes = _changes(tmp_path)
    entered = threading.Event()
    original = changes.create_review_draft
    def slow_draft(**kwargs):
        entered.set()
        time.sleep(.25)
        return original(**kwargs)
    monkeypatch.setattr(changes, "create_review_draft", slow_draft)
    async def failing_executor(request):
        (request.active_workspace_root / "main.cpp").write_text("saved source\n")
        return {"success": False, "classification": "BUILD_REPAIR_EXHAUSTED", "message": "Compiler rejected the draft"}
    owner = AgentOrchestrator(change_service=changes, provider_registry=_providers(tmp_path),
        store=AgentOrchestrationStore(tmp_path / "fixture.sqlite3"), run_factory=ProductAgentRun,
        autonomous_executor=failing_executor)
    owner.auto_recovery_enabled = False
    app = FastAPI()
    app.include_router(router)
    values = {field.name: None for field in fields(ForgeXServices)}
    values.update(execute_prompt=object(), code_generation_service=object(), project_service=object(), subprocess_manager=object())
    app.state.services = ForgeXServices(**values)
    async def scenario():
        try:
            run = await owner.start_run(project_id="fixture", active_workspace_root=workspace,
                instruction="generate firmware", provider_id="verified_template", autonomy="build_only")
            assert await asyncio.to_thread(entered.wait, 3)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
                start = time.monotonic()
                await asyncio.sleep(.01)
                response = await client.get("/health")
                assert response.status_code == 200
                assert time.monotonic() - start < .15
            await owner._tasks[run.run_id]
            assert run.status == "failed" and run.classification == "BUILD_REPAIR_EXHAUSTED"
            draft = changes.get(run.change_set_id)
            assert (Path(draft.staging_root) / "main.cpp").read_text() == "saved source\n"
            assert not list(workspace.iterdir())
        finally:
            await owner.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("event,level", [("job.permissions.changed", "INFO"),
    ("runtime.event_loop.slow", "WARNING"), ("runtime.failed", "ERROR"), ("tool_failed", "ERROR")])
def test_diagnostic_severity_tracks_the_event_instead_of_the_old_run_status(monkeypatch, event, level):
    import backend.agent_runtime.orchestrator as module
    events = []
    monkeypatch.setattr(module, "emit_diagnostic_event", lambda **kwargs: events.append(kwargs))
    _emit_orchestrator_diagnostic(SimpleNamespace(status="failed", run_id="fixture"),
        {"event_type": event}, session_id="fixture-session")
    assert all(item["level"] == level for item in events)
