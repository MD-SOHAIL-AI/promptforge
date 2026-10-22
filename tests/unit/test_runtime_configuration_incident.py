"""Runtime regressions; all firmware and persistence fixtures are isolated."""
import asyncio
import time
import threading
from pathlib import Path

import pytest

from backend.agent_runtime import checkpoint_io
from backend.agent_runtime.failure_contract import failure_details
from backend.agent_runtime.task_contract import create_task_contract
from backend.agent_runtime.task_state import task_state_v1
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.orchestrator import AgentOrchestrator
from backend.agent_runtime.product_agent_service import ProductAgentRun
from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan
from backend.changes import ChangeSetService
from backend.services.platformio_service import PlatformIOService, PlatformIOConfigurationError
from tests.unit.test_live_session_recovery import INVALID_INI, VALID_INI, loop_for
from tests.unit.test_agent_verification_freshness import Builder
from tests.unit.test_platformio_service import FakeSubprocessManager
from tests.unit.test_agent_recovery import _providers
from tests.unit.test_tool_runtime import SequencePlanner, plan


class ConfigurationBuilder(PlatformIOService):
    def __init__(self):
        super().__init__(FakeSubprocessManager())
        self.compiler = Builder()

    async def build(self, root, **kwargs):
        return await self.compiler.build(root, **kwargs)


@pytest.mark.parametrize("initial", [None, "broken ini", "[platformio]\ndefault_envs=esp32dev\n"])
def test_configuration_failure_repairs_then_builds_in_same_run(tmp_path, initial):
    if initial is not None:
        (tmp_path / "platformio.ini").write_text(initial)
    (tmp_path / "src").mkdir()
    source = tmp_path / "src/main.cpp"
    source.write_text("void setup() {}\nvoid loop() {}\n")
    builder = ConfigurationBuilder()
    build = ProductToolPlan.parse({"version": PRODUCT_TOOLPLAN_VERSION, "summary": "Verify repaired configuration", "final": False,
        "tool_calls": [{"id": "build-repaired", "type": "build_firmware", "arguments": {"environment": "esp32dev"}}]})
    planner = SequencePlanner([plan(path="platformio.ini", content=VALID_INI), build])
    loop = loop_for(tmp_path, planner, builder, max_turns=2, max_progress_extension_turns=0)
    asyncio.run(loop.run(task="Build the saved project for esp32dev", require_build=True, environment="esp32dev"))
    focus = next(item for item in planner.seen_results[0] if item.get("tool") == "build_repair_focus")
    assert focus["output"]["error_code"] == ("BUILD_CONFIGURATION_MISSING" if initial is None else "BUILD_CONFIGURATION_INVALID")
    assert builder.compiler.calls == 1 and loop.build_passed
    assert source.read_text() == "void setup() {}\nvoid loop() {}\n"
    assert loop.last_build["success"]


def test_missing_configuration_prevents_unrelated_writes_after_compaction(tmp_path):
    planner = SequencePlanner([plan(path="src/unrelated.cpp", content="int ignored;", call_id=f"ignore-{i}") for i in range(3)])
    loop = loop_for(tmp_path, planner, ConfigurationBuilder(), resume_checkpoint={"turn": 12,
        "observations": [{"tool": "read_file", "status": "completed", "output": {"path": f"old-{i}"}} for i in range(30)]})
    result = asyncio.run(loop.run(task="continue", require_build=True))
    assert result.classification == "BUILD_REPAIR_EXHAUSTED"
    assert not (tmp_path / "src/unrelated.cpp").exists()
    assert all(any(item.get("tool") == "build_repair_focus" for item in context) for context in planner.seen_results)
    assert loop.platformio.compiler.calls == 0


def test_missing_configuration_build_is_repairable_not_automatic_retry(tmp_path):
    loop = loop_for(tmp_path, object(), ConfigurationBuilder())
    result = asyncio.run(loop._build_once("esp32dev"))
    output = result["output"]
    assert output["error_code"] == "BUILD_CONFIGURATION_MISSING"
    assert output["repairable"] and not output["terminal"] and not output["retryable"]
    assert not output["execution_attempted"]


def test_repeated_unchanged_configuration_failures_stop_at_the_existing_repair_limit(tmp_path):
    (tmp_path / "platformio.ini").write_text("broken ini")
    builder = ConfigurationBuilder()
    loop = loop_for(tmp_path, object(), builder)
    async def scenario():
        results = [await loop._build_once("esp32dev") for _ in range(4)]
        assert all(not item["output"]["terminal"] for item in results[:3])
        assert results[-1]["output"]["terminal"] and results[-1]["output"]["repair_exhausted"]
        assert builder.compiler.calls == 0
    asyncio.run(scenario())


def test_wrong_environment_is_not_silently_replaced(tmp_path):
    (tmp_path / "platformio.ini").write_text(VALID_INI)
    with pytest.raises(PlatformIOConfigurationError) as caught:
        ConfigurationBuilder().inspect_configuration(tmp_path, environment="requested_board")
    assert caught.value.code == "BUILD_CONFIGURATION_INVALID"
    assert "requested_board" in str(caught.value)


def test_utf8_bom_configuration_keeps_dependency_repair_focus(tmp_path):
    from backend.agent_runtime.build_recovery import dependency_repair_focus
    (tmp_path / "platformio.ini").write_text(INVALID_INI, encoding="utf-8-sig")
    loop = loop_for(tmp_path, object(), ConfigurationBuilder())
    result = asyncio.run(loop._build_once("esp32dev"))
    focus = dependency_repair_focus(result["output"], loop.executor._read_overlay_text)
    assert focus and focus["error_code"] == "DEPENDENCY_UNRESOLVED"
    assert focus["repair_paths"] == ["platformio.ini"]


def test_configuration_error_has_its_own_failure_category():
    details = failure_details("BUILD_CONFIGURATION_MISSING", status="failed")
    assert details["category"] == "project_configuration"
    assert details["normalized_code"] == "BUILD_CONFIGURATION_MISSING"
    assert not details["retryable"]


def test_read_failure_stays_terminal_instead_of_repairing_configuration(tmp_path, monkeypatch):
    service = ConfigurationBuilder()
    def unavailable(*args, **kwargs):
        raise OSError("fixture workspace cannot be read")
    monkeypatch.setattr(service, "inspect_configuration", unavailable)
    loop = loop_for(tmp_path, object(), service)
    result = asyncio.run(loop._build_once(None))
    assert result["output"]["terminal"]
    assert result["output"]["error_code"] == "DEPENDENCY_VALIDATION_FAILED"


def test_unfinished_milestone_does_not_hide_missing_configuration_or_readme(tmp_path):
    contract = create_task_contract("Build ESP32 firmware with platformio.ini and src/main.cpp. Provide README.md with wiring.",
        project_id="p", session_id="s", workspace=tmp_path, intent="generate")
    state = task_state_v1(contract=contract, plan=[{"title": "Create more source modules", "status": "pending"}],
        observations=[], build={}, report={}, repair_history=[], source_revision="current", source_paths=("src/main.cpp",))
    assert "platformio.ini" in state["next_action"] and "README.md" in state["next_action"]


def changes(tmp_path):
    return ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "staging")


def test_checkpoint_copy_keeps_event_loop_responsive_and_waits_for_completion(tmp_path, monkeypatch):
    service = changes(tmp_path)
    stage = service.create_stage("fixture-source")
    (stage / "main.cpp").write_text("isolated firmware")
    original = checkpoint_io.copy_checkpoint_tree
    def slow_copy(source, destination):
        time.sleep(.15)
        original(source, destination)
    monkeypatch.setattr(checkpoint_io, "copy_checkpoint_tree", slow_copy)
    async def scenario():
        task = asyncio.create_task(checkpoint_io.seal_checkpoint(service, stage, {"turn": 12}, "baseline"))
        ticks = 0
        while not task.done():
            await asyncio.sleep(.01)
            ticks += 1
        sealed = await task
        assert ticks >= 5
        assert Path(sealed["stage_path"]).joinpath("main.cpp").read_text() == "isolated firmware"
        assert sealed["io_timings"]["copy_ms"] >= 140
        (stage / "main.cpp").write_text("subsequent mutation")
        assert Path(sealed["stage_path"]).joinpath("main.cpp").read_text() == "isolated firmware"
    asyncio.run(scenario())


def test_health_route_remains_responsive_during_checkpoint_io(tmp_path, monkeypatch):
    from dataclasses import fields
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from backend.api.routes.health import router
    from backend.api.service_container import ForgeXServices
    service = changes(tmp_path)
    stage = service.create_stage("fixture-source")
    for number in range(20):
        (stage / f"module-{number}.cpp").write_text("isolated fixture\n" * 100)
    original = checkpoint_io.copy_checkpoint_tree
    def slow_copy(source, destination):
        time.sleep(.25)
        original(source, destination)
    monkeypatch.setattr(checkpoint_io, "copy_checkpoint_tree", slow_copy)
    app = FastAPI()
    app.include_router(router)
    values = {field.name: None for field in fields(ForgeXServices)}
    values.update(execute_prompt=object(), code_generation_service=object(), project_service=object(), subprocess_manager=object())
    app.state.services = ForgeXServices(**values)
    async def scenario():
        latencies = []
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            async def observe_health():
                started = time.monotonic()
                await asyncio.sleep(.01)
                response = await client.get("/health")
                latencies.append(time.monotonic() - started)
                assert response.status_code == 200 and response.json()["service"] == "forgex-backend"
            await asyncio.gather(checkpoint_io.seal_checkpoint(service, stage, {}, "baseline"),
                *(observe_health() for _ in range(6)))
        assert max(latencies) < .2, "checkpoint copy must not delay the scheduled health observations"
    asyncio.run(scenario())


def test_checkpoint_cancellation_joins_worker_and_removes_only_unpublished_copy(tmp_path, monkeypatch):
    service = changes(tmp_path)
    stage = service.create_stage("fixture-source")
    (stage / "main.cpp").write_text("isolated firmware")
    entered = threading.Event()
    release = threading.Event()
    original = checkpoint_io.copy_checkpoint_tree
    def slow_copy(source, destination):
        entered.set()
        assert release.wait(5)
        original(source, destination)
    monkeypatch.setattr(checkpoint_io, "copy_checkpoint_tree", slow_copy)
    async def scenario():
        task = asyncio.create_task(checkpoint_io.seal_checkpoint(service, stage, {}, "baseline"))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done(), "caller must not finalize the live stage while copying"
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert list(service.staging_root.iterdir()) == [stage]
        assert stage.joinpath("main.cpp").read_text() == "isolated firmware"
    asyncio.run(scenario())


def test_failed_checkpoint_copy_cleans_only_its_own_snapshot(tmp_path, monkeypatch):
    service = changes(tmp_path)
    stage = service.create_stage("fixture-source")
    def fail(source, destination):
        (destination / "partial").write_text("partial")
        raise OSError("fixture storage failure")
    monkeypatch.setattr(checkpoint_io, "copy_checkpoint_tree", fail)
    with pytest.raises(OSError, match="storage failure"):
        asyncio.run(checkpoint_io.seal_checkpoint(service, stage, {}, "baseline"))
    assert list(service.staging_root.iterdir()) == [stage]


def test_cancelled_checkpoint_conflict_is_saved_once_and_rechecked_when_baseline_changes(tmp_path, caplog, monkeypatch):
    service = changes(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "main.cpp"
    source.write_text("baseline")
    expected = service.inspect_workspace(workspace).revision
    sealed = service.create_stage("checkpoint-fixture")
    (sealed / "main.cpp").write_text("saved edit")
    store = AgentOrchestrationStore(tmp_path / "fixture.sqlite3")
    run = ProductAgentRun("cancelled-fixture", "p", "fake_planner", status="cancelled", classification="AGENT_CANCELLED")
    payload = {**run.to_safe_dict(), "session_id": "s", "_recovery_context": {
        "workspace_root": str(workspace), "instruction": "Build firmware", "active_workspace": {}}}
    store.save_run(run.run_id, "s", "p", "cancelled", payload)
    event = store.append_event("s", {"event_type": "turn.settled", "run_id": run.run_id, "turn": 12})
    store.save_run_checkpoint(run.run_id, "s", turn=12, session_seq=event["sequence"], payload={"resume": {
        "turn": 12, "stage_path": str(sealed), "stage_revision": service.inspect_workspace(sealed).revision,
        "workspace_revision": expected, "stage_mode": "full", "observations": [], "plan": []}})
    source.write_text("user edit")
    caplog.set_level("INFO", logger="backend.agent_runtime.orchestrator")
    saves = []
    original = store.save_run
    def record(*args, **kwargs):
        saves.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "save_run", record)
    async def scenario():
        def owner():
            return AgentOrchestrator(change_service=service, provider_registry=_providers(tmp_path),
                store=store, run_factory=ProductAgentRun)
        initial = owner()
        first_count = len(saves)
        original_events = store.events("s", run_id=run.run_id)
        original_time = initial.get_run(run.run_id).updated_at
        assert initial.recovery_info(run.run_id)["blocking_reason"] == "AGENT_RESUME_WORKSPACE_CHANGED"
        assert initial.get_run(run.run_id).status == "cancelled"
        assert not initial.get_run(run.run_id).change_set_id
        await initial.close()
        restarted = owner()
        assert len(saves) == first_count
        assert restarted.get_run(run.run_id).updated_at == original_time
        assert store.events("s", run_id=run.run_id) == original_events
        await restarted.close()
        assert sum("Recovery retention conflict" in item.message for item in caplog.records) == 1
        assert not any("Cannot retain sealed recovery draft" in item.message for item in caplog.records)
        assert (sealed / "main.cpp").read_text() == "saved edit"
        source.write_text("baseline")
        restored = owner()
        try:
            assert restored.get_run(run.run_id).change_set_id
            assert not restored._recovery_context[run.run_id].get("retention_conflict")
            assert source.read_text() == "baseline"
            assert (sealed / "main.cpp").read_text() == "saved edit"
        finally:
            await restored.close()
    asyncio.run(scenario())


def test_slow_transaction_reports_timing_without_altering_commit(tmp_path, monkeypatch):
    from backend.observability import diagnostics
    store = AgentOrchestrationStore(tmp_path / "timings.sqlite3")
    events = []
    monkeypatch.setattr(diagnostics, "emit_diagnostic_event", lambda **event: events.append(event))
    with store._transaction() as connection:
        time.sleep(.11)
        connection.execute("INSERT INTO agent_runs(run_id,session_id,project_id,status,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            ("r", "s", "p", "cancelled", "{}", "now", "now"))
    assert store.load_run("r") is not None
    assert events[0]["event"] == "storage.transaction.slow"
    assert events[0]["transaction_ms"] >= 100
    assert events[0]["database_lock_wait_ms"] >= 0
