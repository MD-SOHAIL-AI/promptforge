"""Explicit approved-apply resume gate for persisted coding workflows."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..bridges.audit_log import hash_workspace_root
from ..bridges.diff_service import BridgeDiffError, BridgeDiffService
from ..bridges.patch_apply_service import (
    PATCH_APPLY_DISABLED_MESSAGE,
    PATCH_APPLY_REQUIRES_RESTORE_MESSAGE,
    PatchApplyError,
    PatchApplyService,
)
from ..bridges.patch_export_service import BridgePatchExportError, BridgePatchExportService
from .coding_workflow_store import (
    RUN_NOT_FOUND,
    RUN_STATUS_INVALID,
    CodingWorkflowEventRecord,
    CodingWorkflowRunRecord,
    CodingWorkflowStore,
    CodingWorkflowStoreError,
)


UNIFIED_CODING_WORKFLOW_FLAG = "FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW"

WORKFLOW_DISABLED = "UNIFIED_CODING_WORKFLOW_DISABLED"
APPROVAL_REQUIRED = "CODING_WORKFLOW_APPROVAL_REQUIRED"
RUN_NOT_AWAITING_APPLY = "CODING_WORKFLOW_NOT_AWAITING_APPLY"
REVIEW_NOT_FOUND = "CODING_WORKFLOW_REVIEW_NOT_FOUND"
REVIEW_MISMATCH = "CODING_WORKFLOW_REVIEW_MISMATCH"
ALREADY_APPLIED = "CODING_WORKFLOW_ALREADY_APPLIED"
PREFLIGHT_FAILED = "CODING_WORKFLOW_PREFLIGHT_FAILED"
APPLY_FAILED = "CODING_WORKFLOW_APPLY_FAILED"
PATCH_APPLY_DISABLED = "CODING_WORKFLOW_PATCH_APPLY_DISABLED"
ROLLBACK_RESTORE_DISABLED = "CODING_WORKFLOW_ROLLBACK_RESTORE_DISABLED"
PERSISTENCE_FAILED = "CODING_WORKFLOW_PERSISTENCE_FAILED"


@dataclass(frozen=True, slots=True)
class CodingWorkflowApplyResult:
    run_id: str
    status: str
    review_id: str | None = None
    next_action: str | None = None
    files_changed: tuple[str, ...] = ()
    apply_id: str | None = None
    rollback_id: str | None = None
    failure_code: str | None = None
    safe_message: str = ""

    @property
    def applied(self) -> bool:
        return self.status == "awaiting_build" and self.failure_code is None

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "run_id": self.run_id,
            "review_id": self.review_id,
            "next_action": self.next_action,
            "files_changed": list(self.files_changed),
            "apply_id": self.apply_id,
            "rollback_id": self.rollback_id,
            "failure_code": self.failure_code,
            "safe_message": self.safe_message,
        }


class CodingWorkflowApplyService:
    """Resumes one persisted run without invoking a coding provider or downstream stages."""

    def __init__(
        self,
        *,
        store: CodingWorkflowStore,
        review_service: BridgeDiffService,
        patch_export_service: BridgePatchExportService,
        patch_apply_service: PatchApplyService,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._store = store
        self._reviews = review_service
        self._patches = patch_export_service
        self._apply = patch_apply_service
        source = os.environ if env is None else env
        self._enabled = source.get(UNIFIED_CODING_WORKFLOW_FLAG, "").strip() == "1"

    def approve_and_apply_review(
        self,
        run_id: str,
        *,
        workspace_root: str | Path,
        approved_by: str | None = None,
        approval_confirmed: bool = False,
    ) -> CodingWorkflowApplyResult:
        del approved_by  # Deliberately not persisted; the approval call is the authority boundary.
        if not self._enabled:
            return self._rejected(run_id, WORKFLOW_DISABLED, "Unified coding workflow is disabled.")
        if approval_confirmed is not True:
            return self._rejected(run_id, APPROVAL_REQUIRED, "Explicit approval is required before apply.")

        try:
            run = self._store.get_run(run_id)
        except CodingWorkflowStoreError as exc:
            code = RUN_NOT_FOUND if exc.code == RUN_NOT_FOUND else PERSISTENCE_FAILED
            return self._rejected(run_id, code, "Coding workflow run was not found." if code == RUN_NOT_FOUND else "Coding workflow state could not be read safely.")

        if run.status == "awaiting_build":
            return self._from_run(run, failure_code=ALREADY_APPLIED, message="Coding workflow review was already applied.")
        if run.status != "awaiting_apply":
            return self._from_run(run, failure_code=RUN_NOT_AWAITING_APPLY, message="Coding workflow run is not awaiting apply.")
        if not run.review_id:
            return self._persist_failure(run, REVIEW_NOT_FOUND, "Coding workflow review was not found.", expected="awaiting_apply")
        if not self._apply.patch_apply_feature_flag_enabled():
            return self._from_run(run, failure_code=PATCH_APPLY_DISABLED, message=PATCH_APPLY_DISABLED_MESSAGE)
        if not self._apply.rollback_restore_feature_flag_enabled():
            return self._from_run(run, failure_code=ROLLBACK_RESTORE_DISABLED, message=PATCH_APPLY_REQUIRES_RESTORE_MESSAGE)

        try:
            review = self._reviews.get_review(run.review_id)
        except BridgeDiffError:
            return self._persist_failure(run, REVIEW_NOT_FOUND, "Coding workflow review was not found.", expected="awaiting_apply")

        review_files = tuple(item.path for item in review.changed_files)
        if (
            review.review_id != run.review_id
            or review.provider_id != run.provider_id
            or {path.casefold() for path in review_files} != {path.casefold() for path in run.files_changed}
            or review.status not in {"pending", "approved"}
        ):
            return self._persist_failure(run, REVIEW_MISMATCH, "Coding workflow review no longer matches the persisted run.", expected="awaiting_apply")

        try:
            if review.status == "pending":
                review, _ = self._reviews.approve_review(review.review_id)
            active_root = Path(workspace_root).expanduser().resolve()
            patch = self._patches.export_patch(
                review.review_id,
                workspace_root_hash=hash_workspace_root(str(active_root)),
            )
            if patch.provider_id != run.provider_id or patch.review_id != run.review_id:
                return self._persist_failure(run, REVIEW_MISMATCH, "Coding workflow patch no longer matches the persisted run.", expected="awaiting_apply")
            self._store.transition_run(
                run.run_id,
                expected_statuses=("awaiting_apply",),
                status="applying",
                generation_status="review_created",
                next_action=None,
                safe_message="Approved review apply is in progress.",
            )
            self._append_event(run.run_id, "apply.started", "apply", "applying", "Approved review apply started.")
        except (BridgeDiffError, BridgePatchExportError) as exc:
            del exc
            return self._persist_failure(run, REVIEW_MISMATCH, "Coding workflow review could not be prepared safely.", expected="awaiting_apply")
        except CodingWorkflowStoreError:
            return self._from_run(run, failure_code=PERSISTENCE_FAILED, message="Coding workflow apply state could not be persisted safely.")

        try:
            applied = self._apply.apply_patch(patch.patch_id, workspace_root=workspace_root, confirmation="APPLY")
        except PatchApplyError:
            return self._persist_failure(run, PREFLIGHT_FAILED, "Approved review failed ForgeX patch preflight or apply checks.", expected="applying")
        if applied.status != "applied":
            return self._persist_failure(run, APPLY_FAILED, "Approved review patch apply did not complete safely.", expected="applying")

        metadata = dict(run.metadata)
        metadata.update({
            "apply_id": applied.apply_id,
            "patch_id": applied.patch_id,
            "rollback_id": applied.rollback_id,
            "downstream_stages_started": False,
        })
        try:
            completed = self._store.transition_run(
                run.run_id,
                expected_statuses=("applying",),
                status="awaiting_build",
                generation_status="review_created",
                next_action="run_build",
                safe_message="Approved review was applied and is waiting for an explicit build action.",
                metadata=metadata,
            )
            self._append_event(completed.run_id, "apply.completed", "apply", "awaiting_build", "Approved review apply completed.", metadata={"apply_id": applied.apply_id, "rollback_id": applied.rollback_id})
            self._append_event(completed.run_id, "build.waiting_to_start", "build", "awaiting_build", "Build is waiting for an explicit start action.")
        except CodingWorkflowStoreError:
            return self._from_run(run, failure_code=PERSISTENCE_FAILED, message="Patch applied, but workflow completion state could not be persisted safely.", apply_id=applied.apply_id, rollback_id=applied.rollback_id)
        return self._from_run(completed, message="Approved review applied successfully.", apply_id=applied.apply_id, rollback_id=applied.rollback_id)

    def _persist_failure(self, run: CodingWorkflowRunRecord, code: str, message: str, *, expected: str) -> CodingWorkflowApplyResult:
        try:
            failed = self._store.transition_run(
                run.run_id,
                expected_statuses=(expected,),
                status="failed",
                generation_status="failed",
                next_action=None,
                failure_code=code,
                safe_message=message,
            )
            self._append_event(failed.run_id, "apply.failed", "apply", "failed", message, metadata={"failure_code": code})
            return self._from_run(failed, failure_code=code, message=message)
        except CodingWorkflowStoreError as exc:
            fallback = RUN_NOT_AWAITING_APPLY if exc.code == RUN_STATUS_INVALID else PERSISTENCE_FAILED
            return self._from_run(run, failure_code=fallback, message="Coding workflow failure state could not be persisted safely.")

    def _append_event(self, run_id: str, event_type: str, stage: str, status: str, message: str, *, metadata: Mapping[str, object] | None = None) -> None:
        sequence = len(self._store.list_events(run_id)) + 1
        digest = hashlib.sha256(f"{run_id}:{sequence}:{event_type}".encode("utf-8")).hexdigest()
        self._store.append_event(CodingWorkflowEventRecord(
            event_id=f"event-{digest}",
            run_id=run_id,
            sequence=sequence,
            event_type=event_type,
            stage=stage,
            status=status,
            safe_message=message,
            metadata=dict(metadata or {}),
        ))

    @staticmethod
    def _rejected(run_id: str, code: str, message: str) -> CodingWorkflowApplyResult:
        return CodingWorkflowApplyResult(run_id=run_id, status="rejected", failure_code=code, safe_message=message)

    @staticmethod
    def _from_run(run: CodingWorkflowRunRecord, *, failure_code: str | None = None, message: str, apply_id: str | None = None, rollback_id: str | None = None) -> CodingWorkflowApplyResult:
        return CodingWorkflowApplyResult(
            run_id=run.run_id,
            status=run.status,
            review_id=run.review_id,
            next_action=run.next_action,
            files_changed=run.files_changed,
            apply_id=apply_id,
            rollback_id=rollback_id,
            failure_code=failure_code,
            safe_message=message,
        )
