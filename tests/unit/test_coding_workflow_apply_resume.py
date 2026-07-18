from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from backend.agent_runtime.api_coding_agent_service import ApiCodingAgentService
from backend.agent_runtime.coding_workflow_service import (
    ALREADY_APPLIED,
    APPROVAL_REQUIRED,
    PATCH_APPLY_DISABLED,
    PREFLIGHT_FAILED,
    REVIEW_NOT_FOUND,
    REVIEW_MISMATCH,
    ROLLBACK_RESTORE_DISABLED,
    RUN_NOT_AWAITING_APPLY,
    WORKFLOW_DISABLED,
    CodingWorkflowApplyService,
)
from backend.agent_runtime.coding_workflow_store import CodingWorkflowStore
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.patch_apply_service import PatchApplyService
from backend.bridges.patch_export_service import BridgePatchExportService
from backend.bridges.patch_preflight_service import PatchPreflightService
from backend.bridges.review_store import BridgeReviewStore
from backend.bridges.rollback_restore_apply_service import RollbackRestoreApplyService
from backend.bridges.rollback_restore_service import RollbackRestorePreflightService
from backend.bridges.rollback_service import RollbackSnapshotService
from backend.bridges.sandbox_service import BridgeSandboxService
from backend.workflow.adapters.coding_agent_adapter import CodingAgentGenerationAdapter


ENABLED = {
    "FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW": "1",
    "FORGEX_ENABLE_FAKE_API_CODING_AGENT": "1",
}


def make_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("FORGEX_ENABLE_PATCH_APPLY", "1")
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    state = tmp_path / "state"
    active = tmp_path / "active"
    active.mkdir()
    (active / "README.md").write_text("active workspace\n", encoding="utf-8")
    reviews = BridgeDiffService(store=BridgeReviewStore(
        snapshots_path=state / "snapshots.jsonl",
        reviews_path=state / "reviews.jsonl",
    ))
    provider_service = ApiCodingAgentService(
        sandbox_service=BridgeSandboxService(state / "sandboxes"),
        review_service=reviews,
        enabled=True,
    )
    store = CodingWorkflowStore.from_state_directory(state)
    generated = CodingAgentGenerationAdapter(
        fake_service=provider_service,
        store=store,
        env=ENABLED,
    ).generate_review("basic esp32 blink", active)
    exporter = BridgePatchExportService(review_service=reviews, patch_directory=state / "patches")
    preflight = PatchPreflightService(patch_store=exporter.patch_store, review_service=reviews)
    rollback = RollbackSnapshotService(rollback_directory=state / "rollback", preflight_service=preflight)
    restore_preflight = RollbackRestorePreflightService(rollback_service=rollback)
    restore = RollbackRestoreApplyService(
        restore_directory=state / "restores",
        rollback_service=rollback,
        restore_preflight_service=restore_preflight,
    )
    patch_apply = PatchApplyService(
        apply_directory=state / "applies",
        patch_store=exporter.patch_store,
        review_service=reviews,
        preflight_service=preflight,
        rollback_service=rollback,
        restore_apply_service=restore,
    )
    resume = CodingWorkflowApplyService(
        store=store,
        review_service=reviews,
        patch_export_service=exporter,
        patch_apply_service=patch_apply,
        env=ENABLED,
    )
    return active, provider_service, reviews, store, generated, resume


def test_apply_requires_enabled_workflow_and_explicit_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active, _, reviews, store, generated, resume = make_rig(tmp_path, monkeypatch)
    disabled = CodingWorkflowApplyService(
        store=store,
        review_service=reviews,
        patch_export_service=resume._patches,
        patch_apply_service=resume._apply,
        env={},
    )
    assert disabled.approve_and_apply_review(generated.run_id, workspace_root=active, approval_confirmed=True).failure_code == WORKFLOW_DISABLED
    refused = resume.approve_and_apply_review(generated.run_id, workspace_root=active)
    assert refused.failure_code == APPROVAL_REQUIRED
    assert store.get_run(generated.run_id).status == "awaiting_apply"
    assert not (active / "platformio.ini").exists()


def test_apply_flags_are_reported_before_review_is_consumed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active, _, reviews, store, generated, resume = make_rig(tmp_path, monkeypatch)

    monkeypatch.delenv("FORGEX_ENABLE_PATCH_APPLY", raising=False)
    patch_disabled = resume.approve_and_apply_review(generated.run_id, workspace_root=active, approval_confirmed=True)
    assert patch_disabled.failure_code == PATCH_APPLY_DISABLED
    assert "FORGEX_ENABLE_PATCH_APPLY=1" in patch_disabled.safe_message
    assert store.get_run(generated.run_id).status == "awaiting_apply"
    assert reviews.get_review(generated.review_id).status == "pending"
    assert [event.event_type for event in store.list_events(generated.run_id)][-1] == "apply.waiting_for_approval"

    monkeypatch.setenv("FORGEX_ENABLE_PATCH_APPLY", "1")
    monkeypatch.delenv("FORGEX_ENABLE_ROLLBACK_RESTORE", raising=False)
    restore_disabled = resume.approve_and_apply_review(generated.run_id, workspace_root=active, approval_confirmed=True)
    assert restore_disabled.failure_code == ROLLBACK_RESTORE_DISABLED
    assert "FORGEX_ENABLE_ROLLBACK_RESTORE=1" in restore_disabled.safe_message
    assert store.get_run(generated.run_id).status == "awaiting_apply"
    assert reviews.get_review(generated.review_id).status == "pending"
    assert not (active / "platformio.ini").exists()


def test_missing_and_invalid_run_states_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active, _, _, store, generated, resume = make_rig(tmp_path, monkeypatch)
    assert resume.approve_and_apply_review("missing-run", workspace_root=active, approval_confirmed=True).failure_code == "CODING_WORKFLOW_RUN_NOT_FOUND"
    store.transition_run(
        generated.run_id,
        expected_statuses=("awaiting_apply",),
        status="failed",
        generation_status="failed",
        next_action=None,
        failure_code="TEST_FAILURE",
        safe_message="Test failure.",
    )
    result = resume.approve_and_apply_review(generated.run_id, workspace_root=active, approval_confirmed=True)
    assert result.failure_code == RUN_NOT_AWAITING_APPLY


def test_missing_review_is_persisted_as_apply_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active, _, reviews, store, generated, resume = make_rig(tmp_path, monkeypatch)
    reviews._sessions.pop(generated.review_id)
    result = resume.approve_and_apply_review(generated.run_id, workspace_root=active, approval_confirmed=True)
    assert result.failure_code == REVIEW_NOT_FOUND
    assert store.get_run(generated.run_id).status == "failed"
    assert store.list_events(generated.run_id)[-1].event_type == "apply.failed"


def test_review_provider_or_file_mismatch_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active, _, reviews, store, generated, resume = make_rig(tmp_path, monkeypatch)
    review = reviews.get_review(generated.review_id)
    reviews._sessions[review.review_id] = replace(review, provider_id="different_provider")
    result = resume.approve_and_apply_review(generated.run_id, workspace_root=active, approval_confirmed=True)
    assert result.failure_code == REVIEW_MISMATCH
    assert store.get_run(generated.run_id).failure_code == REVIEW_MISMATCH
    assert not (active / "platformio.ini").exists()


def test_approved_apply_uses_existing_safe_apply_path_and_stops_before_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active, provider_service, _, store, generated, resume = make_rig(tmp_path, monkeypatch)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider, commands, build, flash, and monitor must not run during apply")

    monkeypatch.setattr(provider_service.provider, "generate", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = resume.approve_and_apply_review(
        generated.run_id,
        workspace_root=active,
        approval_confirmed=True,
        approved_by="user",
    )

    assert result.applied is True
    assert result.status == "awaiting_build"
    assert result.next_action == "run_build"
    assert result.files_changed == ("platformio.ini", "src/main.cpp")
    assert result.apply_id and result.rollback_id
    assert (active / "platformio.ini").is_file()
    assert (active / "src" / "main.cpp").is_file()
    persisted = store.get_run(generated.run_id)
    assert persisted.status == "awaiting_build"
    assert persisted.metadata["apply_id"] == result.apply_id
    assert persisted.metadata["rollback_id"] == result.rollback_id
    assert persisted.metadata["downstream_stages_started"] is False
    event_types = [event.event_type for event in store.list_events(generated.run_id)]
    assert event_types[-3:] == ["apply.started", "apply.completed", "build.waiting_to_start"]

    repeated = resume.approve_and_apply_review(generated.run_id, workspace_root=active, approval_confirmed=True)
    assert repeated.failure_code == ALREADY_APPLIED
    assert [event.event_type for event in store.list_events(generated.run_id)] == event_types


def test_stale_workspace_fails_preflight_without_partial_store_corruption(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active, _, _, store, generated, resume = make_rig(tmp_path, monkeypatch)
    (active / "platformio.ini").write_text("stale\n", encoding="utf-8")
    result = resume.approve_and_apply_review(generated.run_id, workspace_root=active, approval_confirmed=True)
    assert result.failure_code == PREFLIGHT_FAILED
    failed = store.get_run(generated.run_id)
    assert failed.status == "failed"
    assert failed.failure_code == PREFLIGHT_FAILED
    assert failed.safe_message
    assert [event.event_type for event in store.list_events(generated.run_id)][-2:] == ["apply.started", "apply.failed"]
    assert (active / "platformio.ini").read_text(encoding="utf-8") == "stale\n"
