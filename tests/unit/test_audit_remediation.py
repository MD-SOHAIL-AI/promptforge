"""Regressions for independently reproduced complete-audit defects."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from backend.agent_runtime.context_assembler import ContextAssembler
from backend.agent_runtime.skills import SkillRegistry
from backend.agent_runtime.workspace_index import WorkspaceIndex
from backend.changes import ChangeSetService
from tests.unit.test_changeset_backup_safety import directory_alias, prepare


@pytest.mark.parametrize("component", [".agents", ".agents/skills", ".agents/skills/escape"])
def test_skill_junction_cannot_escape_selected_workspace(tmp_path, component):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    suffix = {".agents": "skills/escape", ".agents/skills": "escape", ".agents/skills/escape": ""}[component]
    skill = outside / suffix / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("# External marker", encoding="utf-8")
    link = workspace / component
    link.parent.mkdir(parents=True, exist_ok=True)
    with directory_alias(link, outside):
        registry = SkillRegistry(workspace)
        assert "escape" not in {item.name for item in registry.discover()}
        with pytest.raises(KeyError):
            registry.load("escape")


def test_index_and_snapshot_do_not_follow_directory_junction(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.cpp").write_text("int external_marker;", encoding="utf-8")
    changes = ChangeSetService(state_root=tmp_path / "state", staging_root=tmp_path / "stages")
    with directory_alias(workspace / "external", outside):
        assert WorkspaceIndex(workspace).query("external_marker") == ()
        assert changes.inspect_workspace(workspace).files == {}


def test_secrets_and_oversized_instructions_are_not_model_context(tmp_path):
    secret = "sk-" + "a" * 40  # Synthetic, never an actual credential.
    (tmp_path / "AGENTS.md").write_text("Credentials: " + secret, encoding="utf-8")
    (tmp_path / "FORGEX.md").write_text("x" * 64_001, encoding="utf-8")
    skills = tmp_path / ".agents/skills/unsafe"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text(secret, encoding="utf-8")
    (tmp_path / "ordinary.cpp").write_text(secret, encoding="utf-8")
    changes = ChangeSetService(state_root=tmp_path / "state", staging_root=tmp_path / "stages")
    context = ContextAssembler(tmp_path).assemble(task="inspect", snapshot=changes.inspect_workspace(tmp_path))
    assert context.project_instructions == ()
    assert secret not in context.render()
    assert "unsafe" not in {item.name for item in SkillRegistry(tmp_path).discover()}
    assert WorkspaceIndex(tmp_path).get("ordinary.cpp") is None


def test_index_skips_large_files_without_hashing_them(tmp_path, monkeypatch):
    large = tmp_path / "large.cpp"
    large.write_bytes(b"x" * 300_000)
    original = Path.open
    def checked_open(path, *args, **kwargs):
        assert path != large, "oversized file must not be opened"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", checked_open)
    assert WorkspaceIndex(tmp_path).files() == ()


def test_native_tests_fail_closed_without_a_security_sandbox(tmp_path, monkeypatch):
    from backend.services.platformio_service import PlatformIOService
    from tests.unit.test_platformio_service import FakeSubprocessManager
    (tmp_path / "platformio.ini").write_text("[env:native]\nplatform = native\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.cpp").write_text("int main() {}", encoding="utf-8")
    manager = FakeSubprocessManager()
    result = asyncio.run(PlatformIOService(manager).test_native(tmp_path, environment="native", trusted=True))
    assert result["error"] == "NATIVE_TEST_SANDBOX_UNAVAILABLE"
    assert result["success"] is False and manager.calls == []


CRASH_PUBLISH = r'''
import os, sys
from pathlib import Path
import backend.changes.service as module
root, state, stages, identifier, operation, limit = sys.argv[1:]
root = Path(root)
service = module.ChangeSetService(state_root=state, staging_root=stages)
write, unlink = module._atomic_write_bytes, Path.unlink
count = 0
def after(path):
    global count
    if path.parent == root:
        count += 1
        if count == int(limit): os._exit(73)
def crash_write(path, data):
    write(path, data)
    after(path)
def crash_unlink(path, *args, **kwargs):
    result = unlink(path, *args, **kwargs)
    after(path)
    return result
module._atomic_write_bytes = crash_write
Path.unlink = crash_unlink
getattr(service, operation)(identifier)
'''


def interrupted_publication(tmp_path, operation, step):
    active, stage, service, change = prepare(tmp_path,
        originals={"a.cpp": b"old-a", "b.cpp": b"old-b"},
        staged={"a.cpp": b"new-a", "c.cpp": b"new-c"}, deleted=("b.cpp",))
    if operation == "undo":
        service.apply(change.change_set_id)
    result = subprocess.run([sys.executable, "-B", "-c", CRASH_PUBLISH, str(active),
        str(service.state_root), str(service.staging_root), change.change_set_id, operation, str(step)],
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 73, result.stderr
    return active, stage, service, change


@pytest.mark.parametrize("operation", ["apply", "undo"])
@pytest.mark.parametrize("step", [1, 2, 3])
def test_changeset_recovers_after_process_death_at_each_file(tmp_path, operation, step):
    active, stage, service, change = interrupted_publication(tmp_path, operation, step)
    reopened = ChangeSetService(state_root=service.state_root, staging_root=service.staging_root)
    assert reopened.recovery_issues == {}
    if operation == "apply":
        assert reopened.get(change.change_set_id).status == "pending"
        assert (active / "a.cpp").read_bytes() == b"old-a"
        assert (active / "b.cpp").read_bytes() == b"old-b"
        assert not (active / "c.cpp").exists()
        reopened.apply(change.change_set_id)
    else:
        assert reopened.get(change.change_set_id).status == "applied"
        assert (active / "a.cpp").read_bytes() == b"new-a"
        assert (active / "c.cpp").read_bytes() == b"new-c"
        assert not (active / "b.cpp").exists()
    reopened.undo(change.change_set_id)
    assert (active / "a.cpp").read_bytes() == b"old-a"
    assert (active / "b.cpp").read_bytes() == b"old-b"


def test_crash_recovery_preserves_post_crash_manual_edit(tmp_path):
    active, stage, service, change = interrupted_publication(tmp_path, "apply", 1)
    (active / "a.cpp").write_bytes(b"manual correction")
    reopened = ChangeSetService(state_root=service.state_root, staging_root=service.staging_root)
    assert reopened.get(change.change_set_id).status == "conflicted"
    assert reopened.recovery_issues[change.change_set_id] == "CHANGE_RECOVERY_CONFLICT"
    assert (active / "a.cpp").read_bytes() == b"manual correction"


def test_terminal_shutdown_has_a_finite_deadline_and_cleans_exited_pumps():
    from backend.services.terminal_service import TerminalService
    from tests.unit.test_terminal_service import make_session, RecordingDriver
    class Stuck(RecordingDriver):
        killed = False
        async def terminate(self): pass
        async def kill(self): self.killed = True
        async def wait(self): await asyncio.Event().wait()
    async def scenario():
        for already_exited in (False, True):
            driver = Stuck()
            if already_exited: driver.process.returncode = 0
            session = make_session()
            session.driver = driver
            pump = asyncio.create_task(asyncio.Event().wait())
            session.tasks.add(pump)
            await asyncio.wait_for(TerminalService()._stop_session(session), timeout=3.2)
            await asyncio.sleep(0)
            assert session.closed and pump.cancelled() and driver.killed
    asyncio.run(scenario())


def test_model_worker_write_and_reap_are_bounded(tmp_path, monkeypatch):
    from backend.contracts.transport import TransportPolicy
    from backend.model_router.models import ModelRequest, RequestExecutionContext
    from backend.model_router.providers.opencode import OpenCodeProvider
    from backend.services.llm_service import LLMTimeoutError
    from tests.unit.test_opencode_engine import registry
    class Pipe:
        def write(self, data): pass
        def close(self): pass
        async def drain(self): await asyncio.Event().wait()
        async def read(self, size): await asyncio.Event().wait()
    class Process:
        returncode = None
        stdin, stderr = Pipe(), Pipe()
        killed = False
        def kill(self): self.killed = True
        async def wait(self): await asyncio.Event().wait()
    process = Process()
    async def spawn(*args, **kwargs): return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    reg = registry(tmp_path)
    reg.configure_provider("deepseek", {"api_key": "fixture"})
    context = RequestExecutionContext(transport_policy=TransportPolicy(total_seconds=0.1, write_seconds=0.05))
    started = time.monotonic()
    with pytest.raises(LLMTimeoutError) as caught:
        asyncio.run(OpenCodeProvider(reg, "deepseek").generate(ModelRequest(prompt="hi",
            provider_id="deepseek", model_id="deepseek-chat", execution_context=context)))
    assert caught.value.details["timeout_stage"] == "worker_write"
    assert process.killed and context.diagnostics["worker_cleanup_incomplete"]
    assert time.monotonic() - started < 3.2
