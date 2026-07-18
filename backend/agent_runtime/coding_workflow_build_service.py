"""Explicit build resume gate for applied unified coding workflows."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..bridges.diff_service import hash_file
from ..bridges.patch_apply_models import PatchApplyResult
from ..runtime.result import BuildResult
from ..workflow.adapters.build_adapter import BuildAdapter, BuildAdapterError
from .coding_workflow_service import UNIFIED_CODING_WORKFLOW_FLAG, WORKFLOW_DISABLED
from .coding_workflow_store import (
    RUN_NOT_FOUND,
    RUN_STATUS_INVALID,
    CodingWorkflowEventRecord,
    CodingWorkflowRunRecord,
    CodingWorkflowStore,
    CodingWorkflowStoreError,
)


BUILD_CONFIRMATION_REQUIRED = "CODING_WORKFLOW_BUILD_CONFIRMATION_REQUIRED"
NOT_AWAITING_BUILD = "CODING_WORKFLOW_NOT_AWAITING_BUILD"
BUILD_ALREADY_COMPLETED = "CODING_WORKFLOW_BUILD_ALREADY_COMPLETED"
WORKSPACE_NOT_FOUND = "CODING_WORKFLOW_WORKSPACE_NOT_FOUND"
BUILD_PRECHECK_FAILED = "CODING_WORKFLOW_BUILD_PRECHECK_FAILED"
BUILD_FAILED = "CODING_WORKFLOW_BUILD_FAILED"
BUILD_PERSISTENCE_FAILED = "CODING_WORKFLOW_PERSISTENCE_FAILED"


class CodingWorkflowBuildExecutor(Protocol):
    async def build(
        self,
        project: str | Path,
        *,
        environment: str | None = None,
    ) -> BuildResult: ...


class CodingWorkflowApplyLookup(Protocol):
    def get_apply(self, apply_id: str) -> PatchApplyResult: ...


@dataclass(frozen=True, slots=True)
class CodingWorkflowBuildResult:
    run_id: str
    status: str
    review_id: str | None = None
    next_action: str | None = None
    build_status: str | None = None
    artifact_reference: str | None = None
    environment: str | None = None
    board: str | None = None
    duration_ms: int | None = None
    failure_code: str | None = None
    safe_message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "awaiting_flash" and self.build_status == "succeeded" and self.failure_code is None

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "review_id": self.review_id,
            "next_action": self.next_action,
            "build_status": self.build_status,
            "artifact_reference": self.artifact_reference,
            "environment": self.environment,
            "board": self.board,
            "duration_ms": self.duration_ms,
            "failure_code": self.failure_code,
            "safe_message": self.safe_message,
        }


class CodingWorkflowBuildService:
    """Runs ForgeX's canonical build path and stops at the flash gate."""

    def __init__(
        self,
        *,
        store: CodingWorkflowStore,
        build_service: CodingWorkflowBuildExecutor,
        patch_apply_service: CodingWorkflowApplyLookup,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._store = store
        self._build = build_service
        self._applies = patch_apply_service
        source = os.environ if env is None else env
        self._enabled = source.get(UNIFIED_CODING_WORKFLOW_FLAG, "").strip() == "1"

    async def run_build_for_applied_workflow(
        self,
        run_id: str,
        *,
        workspace_root: str | Path | None = None,
        build_confirmed: bool = False,
        environment: str | None = None,
    ) -> CodingWorkflowBuildResult:
        if not self._enabled:
            return self._rejected(run_id, WORKFLOW_DISABLED, "Unified coding workflow is disabled.")
        if build_confirmed is not True:
            return self._rejected(run_id, BUILD_CONFIRMATION_REQUIRED, "Explicit build confirmation is required.")
        try:
            run = self._store.get_run(run_id)
        except CodingWorkflowStoreError as exc:
            code = RUN_NOT_FOUND if exc.code == RUN_NOT_FOUND else BUILD_PERSISTENCE_FAILED
            message = "Coding workflow run was not found." if code == RUN_NOT_FOUND else "Coding workflow state could not be read safely."
            return self._rejected(run_id, code, message)

        if run.status == "awaiting_flash":
            return self._from_run(run, failure_code=BUILD_ALREADY_COMPLETED, message="Coding workflow build already completed.")
        if run.status != "awaiting_build":
            return self._from_run(run, failure_code=NOT_AWAITING_BUILD, message="Coding workflow run is not awaiting build.")

        if workspace_root is None:
            return self._persist_failure(run, WORKSPACE_NOT_FOUND, "Applied workflow workspace is unavailable for build.", expected="awaiting_build")
        try:
            root = Path(workspace_root).expanduser().resolve()
        except (OSError, TypeError, ValueError):
            return self._persist_failure(run, WORKSPACE_NOT_FOUND, "Applied workflow workspace is unavailable for build.", expected="awaiting_build")
        if not root.is_dir() or not (root / "platformio.ini").is_file():
            return self._persist_failure(run, WORKSPACE_NOT_FOUND, "Applied workflow workspace is unavailable for build.", expected="awaiting_build")

        try:
            applied = self._validated_apply(run, root)
        except Exception:
            return self._persist_failure(run, BUILD_PRECHECK_FAILED, "Applied workflow failed the build integrity precheck.", expected="awaiting_build")

        metadata = dict(run.metadata)
        metadata.update({"build_status": "running", "downstream_stages_started": True})
        try:
            building = self._store.transition_run(
                run.run_id,
                expected_statuses=("awaiting_build",),
                status="building",
                generation_status="review_created",
                next_action=None,
                safe_message="Confirmed ForgeX build is in progress.",
                metadata=metadata,
            )
            self._append_event(building.run_id, "build.started", "build", "building", "Confirmed ForgeX build started.")
        except CodingWorkflowStoreError:
            return self._from_run(run, failure_code=BUILD_PERSISTENCE_FAILED, message="Build start state could not be persisted safely.")

        try:
            built = await self._build.build(root, environment=environment)
        except Exception:
            return self._persist_failure(run, BUILD_FAILED, "ForgeX build did not complete successfully.", expected="building")
        if not isinstance(built, BuildResult) or not built.success:
            return self._persist_failure(run, BUILD_FAILED, "ForgeX build did not complete successfully.", expected="building")
        try:
            artifact = BuildAdapter.to_artifact(built, environment=environment)
            artifact_reference = self._artifact_reference(root, artifact.path)
            build_environment = self._bounded_display(artifact.environment, "build environment")
            build_board = self._bounded_display(built.board, "build board", allow_empty=True)
        except (BuildAdapterError, OSError, ValueError, TypeError):
            return self._persist_failure(run, BUILD_FAILED, "ForgeX build returned an invalid firmware artifact.", expected="building")

        metadata = dict(building.metadata)
        metadata.update({
            "build_status": "succeeded",
            "artifact_reference": artifact_reference,
            "artifact_type": artifact.artifact_type,
            "artifact_size_bytes": min(2_147_483_647, artifact.size_bytes),
            "artifact_sha256": hash_file(artifact.path),
            "build_environment": build_environment,
            "build_board": build_board or None,
            "build_duration_ms": min(2_147_483_647, max(0, built.duration_ms)),
            "build_warnings_count": min(2_147_483_647, max(0, built.warnings_count)),
        })
        try:
            completed = self._store.transition_run(
                run.run_id,
                expected_statuses=("building",),
                status="awaiting_flash",
                generation_status="review_created",
                next_action="confirm_flash",
                safe_message="Build succeeded and is waiting for explicit flash confirmation.",
                metadata=metadata,
            )
            event_metadata = {
                "artifact_reference": artifact_reference,
                "environment": build_environment,
                "board": build_board or None,
                "duration_ms": min(2_147_483_647, max(0, built.duration_ms)),
            }
            self._append_event(completed.run_id, "build.completed", "build", "awaiting_flash", "ForgeX build completed successfully.", metadata=event_metadata)
            self._append_event(completed.run_id, "flash.waiting_for_confirmation", "flash", "awaiting_flash", "Flash is waiting for explicit confirmation.")
        except CodingWorkflowStoreError:
            return self._from_run(run, failure_code=BUILD_PERSISTENCE_FAILED, message="Build succeeded, but workflow completion state could not be persisted safely.", build_status="succeeded")
        return self._from_run(
            completed,
            message="ForgeX build completed successfully.",
            build_status="succeeded",
            artifact_reference=artifact_reference,
            environment=build_environment,
            board=build_board or None,
            duration_ms=min(2_147_483_647, max(0, built.duration_ms)),
        )

    def _validated_apply(self, run: CodingWorkflowRunRecord, root: Path) -> PatchApplyResult:
        apply_id = run.metadata.get("apply_id")
        if not isinstance(apply_id, str) or not apply_id:
            raise ValueError("apply metadata missing")
        applied = self._applies.get_apply(apply_id)
        if applied.status != "applied" or applied.review_id != run.review_id or applied.provider_id != run.provider_id:
            raise ValueError("apply metadata mismatch")
        expected_paths = {path.casefold() for path in run.files_changed}
        actual_paths = {item.path.casefold() for item in applied.results if item.status == "success"}
        if expected_paths != actual_paths:
            raise ValueError("applied files mismatch")
        for item in applied.results:
            target = (root / item.path).resolve()
            target.relative_to(root)
            if item.operation == "delete":
                if target.exists():
                    raise ValueError("deleted file reappeared")
            elif not target.is_file() or target.is_symlink() or not item.after_hash or hash_file(target) != item.after_hash:
                raise ValueError("applied file drifted")
        return applied

    def _persist_failure(self, run: CodingWorkflowRunRecord, code: str, message: str, *, expected: str) -> CodingWorkflowBuildResult:
        metadata = dict(run.metadata)
        metadata.update({"build_status": "failed"})
        try:
            failed = self._store.transition_run(
                run.run_id,
                expected_statuses=(expected,),
                status="failed",
                generation_status="failed",
                next_action=None,
                failure_code=code,
                safe_message=message,
                metadata=metadata,
            )
            self._append_event(failed.run_id, "build.failed", "build", "failed", message, metadata={"failure_code": code})
            return self._from_run(failed, failure_code=code, message=message, build_status="failed")
        except CodingWorkflowStoreError as exc:
            fallback = NOT_AWAITING_BUILD if exc.code == RUN_STATUS_INVALID else BUILD_PERSISTENCE_FAILED
            return self._from_run(run, failure_code=fallback, message="Build failure state could not be persisted safely.", build_status="failed")

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
    def _artifact_reference(root: Path, artifact_path: Path) -> str:
        resolved = artifact_path.expanduser().resolve()
        if not resolved.is_file():
            raise ValueError("artifact missing")
        try:
            reference = resolved.relative_to(root).as_posix()
        except ValueError:
            reference = resolved.name
        if not reference or len(reference) > 512 or "\x00" in reference:
            raise ValueError("artifact reference is invalid")
        return reference

    @staticmethod
    def _bounded_display(value: str, field_name: str, *, allow_empty: bool = False) -> str:
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError(f"{field_name} is invalid")
        text = value.strip()
        if (not text and not allow_empty) or len(text) > 128:
            raise ValueError(f"{field_name} is invalid")
        return text

    @staticmethod
    def _rejected(run_id: str, code: str, message: str) -> CodingWorkflowBuildResult:
        return CodingWorkflowBuildResult(run_id=run_id, status="rejected", failure_code=code, safe_message=message)

    @staticmethod
    def _from_run(
        run: CodingWorkflowRunRecord,
        *,
        failure_code: str | None = None,
        message: str,
        build_status: str | None = None,
        artifact_reference: str | None = None,
        environment: str | None = None,
        board: str | None = None,
        duration_ms: int | None = None,
    ) -> CodingWorkflowBuildResult:
        return CodingWorkflowBuildResult(
            run_id=run.run_id,
            status=run.status,
            review_id=run.review_id,
            next_action=run.next_action,
            build_status=build_status,
            artifact_reference=artifact_reference,
            environment=environment,
            board=board,
            duration_ms=duration_ms,
            failure_code=failure_code,
            safe_message=message,
        )
