from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.agent_runtime.coding_workflow_locks import (
    CodingWorkflowLockError,
    CodingWorkflowLockManager,
    LOCK_NOT_STALE,
    OPERATION_IN_PROGRESS,
)


def test_acquire_duplicate_and_release_run_operation_lock(tmp_path):
    manager = CodingWorkflowLockManager(tmp_path / "locks")

    lock = manager.acquire_run_operation_lock("run-1", "build")
    status = manager.get_run_lock_status("run-1")

    assert status["locked"] is True
    assert status["operation"] == "build"
    assert status["run_id"] == "run-1"
    assert "token" in status

    with pytest.raises(CodingWorkflowLockError) as duplicate:
        manager.acquire_run_operation_lock("run-1", "flash")
    assert duplicate.value.code == OPERATION_IN_PROGRESS

    manager.release_run_operation_lock(lock)

    assert manager.get_run_lock_status("run-1") == {"run_id": "run-1", "locked": False, "stale": False}
    manager.acquire_run_operation_lock("run-1", "flash")


def test_stale_lock_detection_and_manual_clear(tmp_path):
    manager = CodingWorkflowLockManager(tmp_path / "locks")
    lock = manager.acquire_run_operation_lock("run-2", "monitor", ttl_seconds=1)

    now = datetime.now(timezone.utc)
    fresh_status = manager.get_run_lock_status("run-2", now=now)
    assert fresh_status["stale"] is False
    assert manager.detect_stale_locks(now=now) == ()

    stale_at = now + timedelta(seconds=2)
    stale = manager.detect_stale_locks(now=stale_at)

    assert stale == ({
        "run_id": "run-2",
        "operation": "monitor",
        "created_at": fresh_status["created_at"],
        "expires_at": fresh_status["expires_at"],
        "pid": fresh_status["pid"],
        "suggested_action": "manual_clear_stale_lock",
    },)

    cleared = manager.clear_stale_run_lock("run-2", now=stale_at)
    assert cleared["cleared"] is True
    assert "token" not in cleared
    assert manager.get_run_lock_status("run-2")["locked"] is False
    manager.release_run_operation_lock(lock)


def test_clear_requires_stale_lock_and_metadata_is_safe(tmp_path):
    manager = CodingWorkflowLockManager(tmp_path / "locks")
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    manager.acquire_run_operation_lock("run-3", "apply", ttl_seconds=1800)

    with pytest.raises(CodingWorkflowLockError) as exc:
        manager.clear_stale_run_lock("run-3", now=now)

    assert exc.value.code == LOCK_NOT_STALE
    raw = (tmp_path / "locks" / "run-3.lock").read_text(encoding="utf-8")
    assert "sk-" not in raw
    assert "OPENAI_API_KEY" not in raw
