"""Durable publication and verified-memory provenance regressions."""
import asyncio
from pathlib import Path
import subprocess
import sys

import pytest

from backend.changes import ChangeSetService
from tests.unit.test_changeset_backup_safety import prepare


def test_failed_metadata_publication_can_recover_without_restarting(tmp_path, monkeypatch):
    active, stage, service, change = prepare(tmp_path,
        originals={"a.cpp": b"before"}, staged={"a.cpp": b"after"})
    persist = service._persist
    def failing_persist(value):
        if value.status == "applied":
            raise OSError("simulated full disk")
        persist(value)
    monkeypatch.setattr(service, "_persist", failing_persist)
    with pytest.raises(OSError, match="full disk"):
        service.apply(change.change_set_id)
    assert service.get(change.change_set_id).status == "pending"
    monkeypatch.setattr(service, "_persist", persist)
    service.apply(change.change_set_id)
    assert service.get(change.change_set_id).status == "applied"
    service.undo(change.change_set_id)
    assert (active / "a.cpp").read_bytes() == b"before"


@pytest.mark.parametrize("operation", ["apply", "undo"])
def test_death_after_durable_commit_preserves_the_committed_operation(tmp_path, operation):
    active, stage, service, change = prepare(tmp_path,
        originals={"a.cpp": b"before"}, staged={"a.cpp": b"after"})
    if operation == "undo":
        service.apply(change.change_set_id)
    script = """
import os, sys
from backend.changes import ChangeSetService
service = ChangeSetService(state_root=sys.argv[1], staging_root=sys.argv[2])
service._finish_publish = lambda identifier: os._exit(73)
getattr(service, sys.argv[4])(sys.argv[3])
"""
    result = subprocess.run([sys.executable, "-B", "-c", script, str(service.state_root),
        str(service.staging_root), change.change_set_id, operation], capture_output=True, text=True, timeout=15)
    assert result.returncode == 73, result.stderr
    reopened = ChangeSetService(state_root=service.state_root, staging_root=service.staging_root)
    assert reopened.get(change.change_set_id).status == ("applied" if operation == "apply" else "undone")
    assert (active / "a.cpp").read_bytes() == (b"after" if operation == "apply" else b"before")
    assert reopened.recovery_issues == {}


def test_model_prose_cannot_become_verified_build_memory(tmp_path):
    from backend.agent_runtime.orchestrator import AgentOrchestrator
    from backend.agent_runtime.product_agent_service import ProductAgentRun
    from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
    from tests.unit.test_agent_orchestrator_v2 import _providers
    async def scenario():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        changes = ChangeSetService(state_root=tmp_path / "state", staging_root=tmp_path / "stages")
        async def executor(request):
            return {"success": True, "build_verified": True,
                "message": "The model claims the physical board was tested and flashed.",
                "build_result": {"success": True, "environment": "esp32dev", "hardware_verified": False}}
        owner = AgentOrchestrator(change_service=changes, provider_registry=_providers(tmp_path),
            store=AgentOrchestrationStore(tmp_path / "agent.sqlite3"),
            run_factory=ProductAgentRun, autonomous_executor=executor)
        try:
            run = await owner.start_run(project_id="project", active_workspace_root=workspace,
                instruction="Compile only", provider_id="verified_template", autonomy="build_only")
            await owner._tasks[run.run_id]
            memory = [item for item in owner.memory.active("project") if item["kind"] == "verified_build"]
            assert len(memory) == 1
            assert "physical board" not in memory[0]["value"]
            assert "flashed" not in memory[0]["value"]
            assert '"hardware_verified": false' in memory[0]["value"]
            assert '"success": true' in memory[0]["value"]
        finally:
            await owner.close()
    asyncio.run(scenario())
