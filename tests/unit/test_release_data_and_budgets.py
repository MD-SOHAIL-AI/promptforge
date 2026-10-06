import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from backend.agent_runtime.capabilities import ToolPolicyEngine
from backend.agent_runtime.tool_contracts import ToolName
from backend.agent_runtime.tool_policy import ToolPolicyError
from backend.model_router.models import ModelRequest
from backend.model_router.run_budget import charge_attempt, new_budget, snapshot
from backend.runtime.serial_runtime import SerialConfig, SerialRuntime
from backend.tools.serial_monitor import SerialMonitor, SerialMonitorConfig
from backend.utils.data_migration import migrate_legacy_data
from backend.utils.paths import PathManager


def test_per_user_data_does_not_write_into_install_directory(tmp_path, monkeypatch):
    root, data = tmp_path / "installation", tmp_path / "user-data"
    monkeypatch.setenv("FORGEX_ROOT", str(root))
    monkeypatch.setenv("FORGEX_DATA_DIR", str(data))
    paths = PathManager()
    assert paths.project_root() == root
    assert paths.state_directory() == data / ".promptforge/state"
    assert paths.workspace_directory() == data / "workspace"
    assert not (root / ".promptforge").exists()


def test_migration_preserves_sqlite_wal_and_original_data(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    state = old / ".promptforge/state"
    state.mkdir(parents=True)
    workspace = old / "workspace/projects/p"
    workspace.mkdir(parents=True)
    (workspace / "main.cpp").write_text("original user work")
    database = state / "agent.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE evidence(value TEXT)")
        connection.execute("INSERT INTO evidence VALUES ('committed')")
        connection.commit()
        assert migrate_legacy_data(old, new)
    with closing(sqlite3.connect(new / ".promptforge/state/agent.sqlite3")) as copied:
        assert copied.execute("SELECT value FROM evidence").fetchone()[0] == "committed"
    assert (new / "workspace/projects/p/main.cpp").read_text() == "original user work"
    assert (workspace / "main.cpp").read_text() == "original user work"
    assert migrate_legacy_data(old, new)


def test_migration_refuses_to_overwrite_existing_user_data(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    (new / "workspace").mkdir(parents=True)
    with pytest.raises(ValueError, match="TARGET_NOT_EMPTY"):
        migrate_legacy_data(old, new)


def test_provider_budget_is_shared_and_cannot_be_refunded_on_resume():
    budget = new_budget({"requests": 119, "output_tokens": 4096, "input_bytes": 100})
    request = ModelRequest(prompt="hello", max_tokens=4096, metadata={"forgex_run_budget": budget})
    charge_attempt(request)
    assert snapshot(budget)["requests"] == 120
    restored = new_budget(snapshot(budget))
    with pytest.raises(ValueError, match="BUDGET_EXCEEDED"):
        charge_attempt(ModelRequest(prompt="child work", max_tokens=4096, metadata={"forgex_run_budget": restored}))


def test_cancellation_prevents_future_capability_grants(tmp_path):
    engine = ToolPolicyEngine()
    engine.fence_run("cancelled-run")
    with pytest.raises(ToolPolicyError, match="run_fenced"):
        engine.issue_grant(run_id="cancelled-run", node_id="late-worker", workspace_root=tmp_path,
            allowed_tools=(ToolName.WRITE_FILE,), authorization_source="explicit_edit_request")


def test_context_budget_covers_native_history_and_tool_schemas_before_persisting():
    budget = new_budget()
    budget["_limits"] = {"requests": 10, "output_tokens": 10000, "input_bytes": 100}
    persisted = []
    budget["_persist"] = persisted.append
    request = ModelRequest(prompt="small", max_tokens=1024, metadata={
        "forgex_run_budget": budget,
        "forgex_messages": [{"role": "tool", "content": "x" * 200}],
        "forgex_native_tools": [{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
    })
    with pytest.raises(ValueError, match="BUDGET_EXCEEDED"):
        charge_attempt(request)
    assert persisted == []
    assert snapshot(budget)["requests"] == 0


def test_serial_monitor_rejects_injected_transport_mismatch():
    runtime = SerialRuntime(SerialConfig(port="COM7", baudrate=9600))
    with pytest.raises(ValueError, match="CONFIGURATION_MISMATCH"):
        SerialMonitor(SerialMonitorConfig(port="COM7", baudrate=115200), runtime=runtime)
