import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from backend.agent_runtime.orchestrator import AgentOrchestrator
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.product_agent_service import ProductAgentRun
from backend.agent_runtime.tool_contracts import ToolCall, ToolName
from backend.agent_runtime.tool_policy import ToolPolicyError, product_agent_policy
from backend.changes import ChangeSetService
from backend.observability.log_reader import read_run_logs
from backend.services.partition_preflight import PartitionPreflightError, partition_input
from tests.unit.test_agent_orchestrator_v2 import _providers
from tests.unit.test_tool_runtime import make_runtime, SequencePlanner, plan


def test_csv_policy_and_structured_extension_denial(tmp_path):
    policy = product_agent_policy()
    policy.validate_call(ToolCall(ToolName.WRITE_FILE, {"path": "partitions.csv", "content": "# partitions\n"}), tmp_path)
    assert ".csv" in policy.to_provider_dict()["allowed_extensions"]
    with pytest.raises(ToolPolicyError) as failure:
        policy.validate_call(ToolCall(ToolName.WRITE_FILE, {"path": "partitions.exe", "content": "text"}), tmp_path)
    assert failure.value.details["reason"] == "extension_not_allowed"
    assert ".csv" in failure.value.details["allowed_extensions"]


def test_stop_after_write_retains_checkpoint_before_terminal_and_restart(tmp_path):
    async def scenario():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        changes = ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "staging")
        ready = asyncio.Event()
        async def executor(request):
            stage = Path(request.active_workspace_root)
            (stage / "partitions.csv").write_text("saved work")
            await request.progress_callback("tool.checkpoint", {"turn": 1, "call_id": "write",
                "_resume_checkpoint": {"turn": 1, "observations": [], "authorized_paths": ["partitions.csv"],
                    "deleted_paths": [], "tool_calls_used": 1, "elapsed_seconds": 1}})
            ready.set()
            await asyncio.Event().wait()
        def owner():
            return AgentOrchestrator(change_service=changes, provider_registry=_providers(tmp_path),
                store=AgentOrchestrationStore(tmp_path / "journal.sqlite3"), run_factory=ProductAgentRun,
                autonomous_executor=executor)
        original = owner()
        run = await original.start_run(project_id="p", active_workspace_root=workspace,
            instruction="Generate ESP32 firmware", provider_id="verified_template", autonomy="staged_changes",
            active_workspace={"agent_requires_build": True}, session_id="s")
        await asyncio.wait_for(ready.wait(), 5)
        started = time.monotonic()
        responses = await asyncio.gather(original.cancel(run.run_id), original.cancel(run.run_id))
        assert time.monotonic() - started < 10
        assert all(item.status == "cancelled" for item in responses)
        assert run.created_file_count == 1 and run.change_set_id and run.recoverable
        draft = changes.get(run.change_set_id)
        assert draft.status == "draft" and Path(draft.staging_root, "partitions.csv").read_text() == "saved work"
        terminals = [event for event in run.events if event.get("event_type") == "runtime.cancelled"]
        assert len(terminals) == 1 and terminals[0]["changed_files"] == 1
        persisted = original.store.list_runs(session_id="s")[0]
        assert persisted["change_set_id"] == draft.change_set_id
        await original.close()
        restarted = owner()
        restored = restarted.get_run(run.run_id)
        assert restored.change_set_id == draft.change_set_id
        assert restarted.recovery_info(run.run_id)["eligible"]
        await restarted.discard_draft(run.run_id)
        assert not restored.recoverable and not restarted.recovery_info(run.run_id)["eligible"]
        assert changes.get(draft.change_set_id).status == "discarded"
        assert not list(workspace.iterdir())
        await restarted.close()
    asyncio.run(scenario())


def test_tool_boundary_is_sealed_when_stop_prevents_next_write(tmp_path):
    import threading
    from backend.agent_runtime.tool_contracts import ProductToolPlan, PRODUCT_TOOLPLAN_VERSION
    runtime, active, changes = make_runtime(tmp_path)
    cancelled = threading.Event()
    saved = []
    batch = ProductToolPlan.parse({"version": PRODUCT_TOOLPLAN_VERSION, "summary": "write files", "final": False,
        "tool_calls": [{"id": "a", "type": "write_file", "path": "a.csv", "content": "first"},
                       {"id": "b", "type": "write_file", "path": "b.csv", "content": "second"}]})
    def event(payload):
        if payload.get("event_type") == "tool.checkpoint":
            saved.append(payload["_resume_checkpoint"])
            cancelled.set()
    runtime.run_product(task="write files", planner=SequencePlanner([batch]), policy=product_agent_policy(),
        cancel_event=cancelled, event_callback=event)
    assert len(saved) == 1
    assert Path(saved[0]["stage_path"], "a.csv").read_text() == "first"
    assert not Path(saved[0]["stage_path"], "b.csv").exists()
    assert not (active / "a.csv").exists()


def test_stop_quarantines_unsettled_thread_and_never_replays_unknown_write(tmp_path):
    import threading

    async def scenario():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        changes = ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "staging")
        release_worker = threading.Event()
        worker_done = threading.Event()
        ready = asyncio.Event()
        stages = []

        async def executor(request):
            stage = Path(request.active_workspace_root)
            stages.append(stage)
            (stage / "saved.csv").write_text("committed")
            await request.progress_callback("tool.checkpoint", {"turn": 1, "_resume_checkpoint": {
                "turn": 1, "observations": [], "authorized_paths": ["saved.csv"], "deleted_paths": [],
                "tool_calls_used": 1, "elapsed_seconds": 1}})
            await request.progress_callback("tool.execution.started", {"call_id": "unsettled", "tool": "write_file"})
            ready.set()
            def unsettled_write():
                release_worker.wait(15)
                (stage / "late.csv").write_text("unknown outcome")
                worker_done.set()
            await asyncio.to_thread(unsettled_write)
            return {"success": True}

        owner = AgentOrchestrator(change_service=changes, provider_registry=_providers(tmp_path),
            store=AgentOrchestrationStore(tmp_path / "journal.sqlite3"), run_factory=ProductAgentRun,
            autonomous_executor=executor)
        try:
            run = await owner.start_run(project_id="p", active_workspace_root=workspace,
                instruction="Generate ESP32 firmware", provider_id="verified_template", autonomy="staged_changes",
                active_workspace={"agent_requires_build": True}, session_id="s")
            await asyncio.wait_for(ready.wait(), 5)
            started = time.monotonic()
            await owner.cancel(run.run_id)
            assert time.monotonic() - started < 10
            assert run.status == "blocked" and run.classification == "AGENT_RECOVERY_OUTCOME_UNKNOWN"
            assert stages[0].is_dir()
            assert owner._recovery_context[run.run_id]["quarantined_stage_path"] == str(stages[0])
            draft = changes.get(run.change_set_id)
            assert (Path(draft.staging_root) / "saved.csv").read_text() == "committed"
            assert not (Path(draft.staging_root) / "late.csv").exists()
            assert owner.recovery_info(run.run_id)["blocking_reason"] == "AGENT_RECOVERY_OUTCOME_UNKNOWN"
            release_worker.set()
            assert await asyncio.to_thread(worker_done.wait, 5)
            owner._runtime_event(run, {"event_type": "runtime.completed"})
            assert run.status == "blocked" and len([e for e in run.events if e["event_type"] == "runtime.blocked"]) == 1
            assert not list(workspace.iterdir())
        finally:
            release_worker.set()
            await owner.close()
    asyncio.run(scenario())


def test_missing_partition_uses_selected_inherited_environment(tmp_path):
    import configparser
    parser = configparser.RawConfigParser()
    parser.read_string("[env:base]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\nboard_build.partitions=missing.csv\n"
        "[env:release]\nextends=env:base\n[env:debug]\nplatform=native\n")
    assert partition_input(tmp_path, parser, "debug", tmp_path / "core") is None
    with pytest.raises(PartitionPreflightError) as failure:
        partition_input(tmp_path, parser, "release", tmp_path / "core")
    assert failure.value.code == "PARTITION_TABLE_MISSING"


def test_concurrent_processes_rotate_separate_valid_jsonl_files(tmp_path):
    base = tmp_path / "live-runtime.jsonl"
    environment = {**os.environ, "FORGEX_LIVE_LOG_PATH": str(base), "FORGEX_LIVE_LOG_MAX_BYTES": "65536"}
    code = "from backend.observability.diagnostics import emit_diagnostic_event\nfor i in range(80): emit_diagnostic_event(component='test',event='record',run_id='r',index=i,padding='x'*2000,api_key='secret')"
    processes = [subprocess.Popen([sys.executable, "-c", code], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) for _ in range(3)]
    for process in processes:
        _, stderr = process.communicate(timeout=30)
        assert process.returncode == 0, stderr.decode()
    files = list(tmp_path.glob("live-runtime.backend-*.jsonl"))
    assert len(files) == 3
    identities = set()
    for file in files:
        entries = [json.loads(line) for line in file.read_text().splitlines()]
        assert entries[-1]["index"] == 79
        assert all(entry["api_key"] == "[redacted]" for entry in entries)
        identities.add(entries[-1]["backend_instance_id"])
        assert file.with_name(file.name + ".1").exists()
        for line in file.with_name(file.name + ".1").read_text().splitlines(): json.loads(line)
    assert len(identities) == 3
    base.write_text('{"run_id":"r","event":"legacy","timestamp":"2000"}\ninvalid fragment\n')
    exported = read_run_logs("r", base=base)
    assert exported["malformed_records_skipped"] == 1
    assert exported["events"][0]["event"] == "legacy"
    assert all("padding" not in event for event in exported["events"])


def test_locate_and_forget_recent_preserve_folder_and_project_identity(tmp_path):
    from backend.services.project_import_service import ProjectImportService
    async def scenario():
        folder = tmp_path / "original"
        folder.mkdir()
        (folder / "main.cpp").write_text("saved project")
        service = ProjectImportService(tmp_path / "recent.json")
        record = await service.import_project(folder)
        located = tmp_path / "moved"
        folder.rename(located)
        assert (await service.list_projects())[0].metadata["missing"] is True
        restored = await service.relocate_project(record.project_id, str(located))
        assert restored.project_id == record.project_id and restored.project_path == str(located)
        assert await service.forget_project(record.project_id)
        assert (located / "main.cpp").read_text() == "saved project"
        assert not await service.list_projects()
    asyncio.run(scenario())


@pytest.mark.parametrize("csv,valid", [
    ("nvs,data,nvs,,0x5000\notadata,data,ota,,0x2000\napp0,app,ota_0,,1M\napp1,app,ota_1,,1M\n", True),
    ("app0,app,ota_0,0x10001,1M\n", False),
    ("nvs,data,nvs,0x9000,0x6000\notadata,data,ota,0xA000,0x2000\n", False),
    ("app0,app,ota_0,0x10000,8M\n", False),
])
def test_installed_espressif_validator_checks_layout_and_capacity(tmp_path, csv, valid):
    import configparser
    from backend.services.platformio_service import PlatformIOService
    from backend.runtime.subprocess_mgr import ProcessConfig, SubprocessManager
    core = Path.home() / ".platformio"
    if not (core / "packages/framework-arduinoespressif32/tools/gen_esp32part.py").is_file():
        pytest.skip("Installed ESP32 partition utility required for real validation")
    parser = configparser.RawConfigParser()
    parser.read_string("[base]\nplatform=espressif32\nframework=arduino\nboard=esp32dev\nboard_build.partitions=${tables.file}\n"
        "[tables]\nfile=partitions.csv\n[env:release]\nextends=base\n")
    (tmp_path / "partitions.csv").write_text(csv)
    resolved = partition_input(tmp_path, parser, "release", core)
    async def scenario():
        process = await SubprocessManager().run(ProcessConfig(args=[sys.executable, resolved["tool"], "--flash-size",
            resolved["flash_size"], resolved["source"], str(tmp_path / "partitions.bin")], timeout_s=10))
        assert process.success is valid, process.stderr_text
    asyncio.run(scenario())
