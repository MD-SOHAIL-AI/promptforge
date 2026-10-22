"""Adversarial release regressions: real persistence, processes and generators."""
import asyncio
from contextlib import closing
import sqlite3
import sys
import subprocess
import time
import json

import pytest

from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.projection import SessionProjectionEngine
from backend.runtime.subprocess_mgr import ProcessConfig, SubprocessManager
from backend.runtime.serial_runtime import SerialConfig, SerialRuntime, SerialConnectionState


@pytest.mark.parametrize("mutation", [
    "UPDATE agent_events SET payload_json=replace(payload_json,'original','modified')",
    "UPDATE agent_events SET event_checksum='broken'",
    "UPDATE agent_session_heads SET last_sequence=90",
])
def test_corruption_survives_restart_and_blocks_execution(tmp_path, mutation):
    path = tmp_path / "events.sqlite3"
    store = AgentOrchestrationStore(path)
    store.append_event("s", {"event_type": "runtime.started", "run_id": "r", "message": "original"})
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(mutation)
        connection.commit()
    assert not store.verify_journal("s")["healthy"]
    reopened = AgentOrchestrationStore(path)
    assert not reopened.verify_journal("s")["healthy"]
    with pytest.raises(ValueError, match="QUARANTINED"):
        reopened.acquire_lease("s", "r")
    with pytest.raises(ValueError, match="QUARANTINED"):
        reopened.append_event("s", {"event_type": "runtime.completed"})
    assert path.with_name(path.name + ".pre-hardening.bak").exists() is False  # fresh v2


def test_projection_reads_beyond_every_page_boundary(tmp_path):
    store = AgentOrchestrationStore(tmp_path / "events.sqlite3")
    for _ in range(1001):
        store.append_event("s", {"event_type": "runtime.started", "run_id": "r"})
    store.append_event("s", {"event_type": "runtime.completed", "run_id": "r"})
    state = SessionProjectionEngine().rebuild("s", store.snapshot_events("s"))
    assert state.session_seq == 1002
    assert state.run_status == "completed"


def test_process_without_captured_pipes_still_times_out():
    async def scenario():
        result = await SubprocessManager().run(ProcessConfig(
            args=[sys.executable, "-c", "import time; time.sleep(20)"],
            timeout_s=0.3, capture_output=False))
        assert result.timed_out
        assert result.elapsed_s < 6
    asyncio.run(scenario())


def test_real_serial_generator_can_close_and_restart():
    async def scenario():
        runtime = SerialRuntime(SerialConfig(port="COM99"))
        runtime._state = SerialConnectionState.CONNECTED
        async def fake_loop():
            yield runtime._make_runtime_obs("simulated observation", {})
        runtime._monitoring_loop = fake_loop
        for _ in range(2):
            stream = runtime.monitor()
            await stream.__anext__()
            await stream.__anext__()
            runtime._line_buffer.extend(b"partial")
            await stream.aclose()
            assert runtime._monitor_task is None
    asyncio.run(scenario())


def test_stream_close_cleans_full_output_queue_and_owned_process():
    async def scenario():
        manager = SubprocessManager()
        command = "import sys,time; sys.stdout.write('ready\\n'); sys.stdout.flush(); [print('x'*100,flush=True) for _ in range(2000)]; time.sleep(20)"
        stream = manager.stream(ProcessConfig(args=[sys.executable, "-c", command], timeout_s=10))
        await stream.__anext__()
        await asyncio.sleep(0.2)
        await asyncio.wait_for(stream.aclose(), timeout=5)
        assert not manager._active
        assert not manager._jobs
    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows directory junction regression")
def test_checkpoint_rejects_windows_directory_junction(tmp_path):
    from backend.agent_runtime.checkpoint_files import copy_checkpoint_tree
    source, outside, destination = tmp_path / "source", tmp_path / "outside", tmp_path / "destination"
    source.mkdir()
    outside.mkdir()
    destination.mkdir()
    (outside / "secret.txt").write_text("must not copy")
    link = source / "linked"
    # This creates a junction, requires no symlink privilege, and never deletes
    # or moves anything through cmd.exe.
    result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    try:
        with pytest.raises(ValueError, match="LINK|REPARSE"):
            copy_checkpoint_tree(source, destination)
        assert not (destination / "linked/secret.txt").exists()
    finally:
        link.rmdir()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object regression")
def test_windows_job_kills_detached_descendant_after_parent_exit(tmp_path):
    marker = tmp_path / "escaped.txt"
    child = f"import time,pathlib; time.sleep(1.2); pathlib.Path({str(marker)!r}).write_text('escaped')"
    parent = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}],creationflags=8|512,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
    async def scenario():
        result = await SubprocessManager().run(ProcessConfig(args=[sys.executable, "-c", parent], timeout_s=5))
        assert result.returncode == 0
        await asyncio.sleep(1.5)
        assert not marker.exists()
    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object regression")
def test_windows_job_kills_children_when_backend_process_dies(tmp_path):
    ready, escaped = tmp_path / "ready", tmp_path / "escaped"
    child = f"import pathlib,time; pathlib.Path({str(ready)!r}).write_text('ready'); time.sleep(2); pathlib.Path({str(escaped)!r}).write_text('escaped')"
    host = "import asyncio,sys; from backend.runtime.subprocess_mgr import SubprocessManager,ProcessConfig; asyncio.run(SubprocessManager().run(ProcessConfig(args=[sys.executable,'-c'," + repr(child) + "],timeout_s=10)))"
    process = subprocess.Popen([sys.executable, "-c", host], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        diagnostic = process.stderr.read().decode(errors="replace") if process.poll() is not None else "host still running"
        assert ready.exists(), diagnostic
        process.kill()
        process.wait(timeout=5)
        time.sleep(2.2)
        assert not escaped.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
