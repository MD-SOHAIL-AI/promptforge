from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.agent_runtime.coding_workflow_locks import CodingWorkflowLockManager
from backend.agent_runtime.coding_workflow_store import (
    CodingWorkflowEventRecord,
    CodingWorkflowRunRecord,
)
from tests.integration.test_coding_workflow_routes import (
    BASE,
    apply,
    build,
    configure_env,
    flash,
    generate,
    make_route_rig,
    make_workspace,
    monitor,
)


def seed_in_progress(rig: object, run_id: str, status: str, started_at: datetime) -> None:
    store = rig.api.app.state.coding_workflow_store
    stage = {
        "applying": "apply",
        "building": "build",
        "flashing": "flash",
        "monitoring": "monitor",
        "repairing": "repair",
        "cancelling": "cancel",
    }[status]
    timestamp = started_at.isoformat().replace("+00:00", "Z")
    store.persist_run(
        CodingWorkflowRunRecord(
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
            metadata={"seeded_for_recovery": True},
        ),
        (
            CodingWorkflowEventRecord(
                event_id=f"event-{run_id}-1",
                run_id=run_id,
                sequence=1,
                event_type=f"{stage}.started",
                stage=stage,
                status=status,
                safe_message="Operation started.",
                created_at=timestamp,
            ),
        ),
    )


def test_stale_recovery_route_lists_and_manually_marks_failed_without_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=True)
    rig = make_route_rig(tmp_path)
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("do not delete\n", encoding="utf-8")
    old = datetime.now(timezone.utc) - timedelta(minutes=45)
    seed_in_progress(rig, "stale-building", "building", old)

    stale = rig.api.get(f"{BASE}/recovery/stale?threshold_seconds=1800")
    assert stale.status_code == 200
    assert stale.json()["count"] == 1
    assert stale.json()["runs"][0]["run_id"] == "stale-building"
    assert stale.json()["runs"][0]["stage"] == "build"

    unconfirmed = rig.api.post(
        f"{BASE}/stale-building/recovery/mark-failed?threshold_seconds=1800",
        json={"reason": "manual recovery after app crash"},
    )
    assert unconfirmed.status_code == 403
    assert unconfirmed.json()["code"] == "CODING_WORKFLOW_RECOVERY_CONFIRMATION_REQUIRED"

    recovered = rig.api.post(
        f"{BASE}/stale-building/recovery/mark-failed?threshold_seconds=1800",
        json={"recovery_confirmed": True, "reason": "manual recovery after app crash"},
    )
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["status"] == "failed"
    assert recovered.json()["failure_code"] == "CODING_WORKFLOW_RECOVERED_FROM_STALE_STATE"
    assert sentinel.read_text(encoding="utf-8") == "do not delete\n"

    events = rig.api.get(f"{BASE}/stale-building/events").json()["events"]
    assert events[-1]["event_type"] == "workflow.recovered_failed"
    assert events[-1]["metadata"]["failure_code"] == "CODING_WORKFLOW_RECOVERED_FROM_STALE_STATE"


def test_recovery_rejects_fresh_and_non_in_progress_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=True)
    rig = make_route_rig(tmp_path)
    fresh = datetime.now(timezone.utc) - timedelta(minutes=2)
    seed_in_progress(rig, "fresh-flashing", "flashing", fresh)

    response = rig.api.post(
        f"{BASE}/fresh-flashing/recovery/mark-failed?threshold_seconds=1800",
        json={"recovery_confirmed": True, "reason": "too fresh"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "CODING_WORKFLOW_RECOVERY_NOT_ALLOWED"


def test_recovery_route_lists_and_clears_stale_operation_locks_without_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=True)
    rig = make_route_rig(tmp_path)
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("do not delete\n", encoding="utf-8")
    old = datetime.now(timezone.utc) - timedelta(minutes=45)
    seed_in_progress(rig, "locked-building", "building", old)
    store = rig.api.app.state.coding_workflow_store
    manager = CodingWorkflowLockManager(store.runs_path.parent / "coding-workflow-locks")
    lock = manager.acquire_run_operation_lock("locked-building", "build", ttl_seconds=1800)
    metadata = json.loads(lock.path.read_text(encoding="utf-8"))
    metadata["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
    lock.path.write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")

    stale = rig.api.get(f"{BASE}/recovery/stale?threshold_seconds=1800")
    assert stale.status_code == 200
    assert stale.json()["stale_lock_count"] == 1
    assert stale.json()["stale_locks"][0]["run_id"] == "locked-building"
    assert stale.json()["stale_locks"][0]["operation"] == "build"
    assert "token" not in json.dumps(stale.json()["stale_locks"])

    unconfirmed = rig.api.post(f"{BASE}/locked-building/recovery/clear-lock", json={"reason": "manual"})
    assert unconfirmed.status_code == 403
    assert unconfirmed.json()["code"] == "CODING_WORKFLOW_LOCK_CLEAR_CONFIRMATION_REQUIRED"

    cleared = rig.api.post(
        f"{BASE}/locked-building/recovery/clear-lock",
        json={"clear_lock_confirmed": True, "reason": "manual stale lock recovery"},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["status"] == "lock_cleared"
    assert cleared.json()["lock"]["cleared"] is True
    assert "token" not in cleared.text
    assert sentinel.read_text(encoding="utf-8") == "do not delete\n"
    assert manager.get_run_lock_status("locked-building")["locked"] is False

    events = rig.api.get(f"{BASE}/locked-building/events").json()["events"]
    assert events[-1]["event_type"] == "workflow.lock_cleared"
    assert events[-1]["metadata"]["operation"] == "build"


def test_cancel_route_requires_confirmation_and_blocks_completed_or_in_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=True)
    rig = make_route_rig(tmp_path)
    active = make_workspace(tmp_path)
    generated = generate(rig.api, active)

    unconfirmed = rig.api.post(f"{BASE}/{generated['run_id']}/cancel", json={"reason": "no confirmation"})
    assert unconfirmed.status_code == 403
    assert unconfirmed.json()["code"] == "CODING_WORKFLOW_CANCEL_CONFIRMATION_REQUIRED"

    old = datetime.now(timezone.utc) - timedelta(minutes=45)
    seed_in_progress(rig, "active-flashing", "flashing", old)
    active_cancel = rig.api.post(f"{BASE}/active-flashing/cancel", json={"cancel_confirmed": True})
    assert active_cancel.status_code == 409
    assert active_cancel.json()["code"] == "CODING_WORKFLOW_OPERATION_IN_PROGRESS"

    completed_id = generated["run_id"]
    apply(rig.api, completed_id, active)
    build(rig.api, completed_id, active)
    flash(rig.api, completed_id, active)
    monitor(rig.api, completed_id)
    completed_cancel = rig.api.post(f"{BASE}/{completed_id}/cancel", json={"cancel_confirmed": True})
    assert completed_cancel.status_code == 409
    assert completed_cancel.json()["code"] == "CODING_WORKFLOW_CANCEL_NOT_ALLOWED"
