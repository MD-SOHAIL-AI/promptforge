from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.agent_runtime.coding_workflow_store import (
    CodingWorkflowEventRecord,
    CodingWorkflowRunRecord,
    CodingWorkflowStore,
)


def make_store(tmp_path: Path) -> CodingWorkflowStore:
    return CodingWorkflowStore.from_state_directory(tmp_path / "state")


def in_progress_run(run_id: str, status: str, started_at: datetime) -> CodingWorkflowRunRecord:
    timestamp = started_at.isoformat().replace("+00:00", "Z")
    return CodingWorkflowRunRecord(
        run_id=run_id,
        provider_id="api_coding_agent",
        provider_type="api_coding_agent",
        status=status,
        generation_status="review_created",
        review_id="review-1",
        next_action=None,
        files_changed=("src/main.cpp",),
        created_at=timestamp,
        updated_at=timestamp,
        safe_summary="Workflow operation is in progress.",
        safe_message="Workflow operation is in progress.",
        metadata={"operation": status},
    )


def event(run_id: str, event_type: str, stage: str, status: str, created_at: datetime) -> CodingWorkflowEventRecord:
    return CodingWorkflowEventRecord(
        event_id=f"event-{run_id}-1",
        run_id=run_id,
        sequence=1,
        event_type=event_type,
        stage=stage,
        status=status,
        safe_message="Operation started.",
        created_at=created_at.isoformat().replace("+00:00", "Z"),
    )


def test_stale_in_progress_runs_are_detected_with_safe_metadata(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    old = now - timedelta(minutes=45)
    store.persist_run(in_progress_run("old-run", "building", old), (event("old-run", "build.started", "build", "building", old),))

    stale = store.detect_stale_in_progress_runs(threshold_seconds=30 * 60, now=now)

    assert stale == ({
        "run_id": "old-run",
        "status": "building",
        "stage": "build",
        "started_at": old.isoformat().replace("+00:00", "Z"),
        "age_seconds": 2700,
        "suggested_action": "manual_mark_failed",
    },)


def test_fresh_in_progress_runs_are_not_stale(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    fresh = now - timedelta(minutes=5)
    store.persist_run(in_progress_run("fresh-run", "flashing", fresh), (event("fresh-run", "flash.started", "flash", "flashing", fresh),))

    assert store.detect_stale_in_progress_runs(threshold_seconds=30 * 60, now=now) == ()
    assert store.is_stale_in_progress("fresh-run", threshold_seconds=30 * 60, now=now) is False

