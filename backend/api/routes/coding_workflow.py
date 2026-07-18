"""Internal/dev routes for the experimental unified coding workflow."""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import Field

from ...agent_runtime.api_coding_context import build_api_coding_context, list_api_coding_context_files
from ...agent_runtime.coding_provider_contracts import CodingContextMode
from ...agent_runtime.coding_workflow_build_service import CodingWorkflowBuildService
from ...agent_runtime.coding_workflow_flash_service import CodingWorkflowFlashService
from ...agent_runtime.coding_workflow_locks import (
    LOCK_NOT_FOUND,
    LOCK_NOT_STALE,
    OPERATION_IN_PROGRESS as LOCK_OPERATION_IN_PROGRESS,
    CodingWorkflowLockError,
    CodingWorkflowLockManager,
    CodingWorkflowRunOperationLock,
)
from ...agent_runtime.coding_workflow_monitor_service import CodingWorkflowMonitorService
from ...agent_runtime.coding_workflow_repair_service import (
    CODING_AGENT_REPAIR_LOOP_FLAG,
    CodingWorkflowRepairService,
)
from ...agent_runtime.coding_workflow_service import CodingWorkflowApplyService
from ...agent_runtime.coding_workflow_store import (
    CANCELLABLE_STATUSES,
    IN_PROGRESS_STAGE_BY_STATUS,
    IN_PROGRESS_STATUSES,
    OPERATION_IN_PROGRESS,
    STALE_RECOVERY_CODE,
    CodingWorkflowEventRecord,
    CodingWorkflowRunRecord,
    CodingWorkflowStore,
    CodingWorkflowStoreError,
)
from ...agent_runtime.real_api_coding_provider_adapter import (
    REAL_API_CODING_AGENT_FLAG,
    REAL_API_CODING_AGENT_PROVIDER_ID,
    RealApiCodingProviderAdapter,
)
from ...tools.flash_firmware import FirmwareFlasher
from ...workflow.adapters.coding_agent_adapter import CodingAgentGenerationAdapter
from ..dependencies import required_state
from ..errors import APIError, error_responses
from ..schemas.common import APIModel, ErrorResponse
from ._model_common import (
    api_coding_agent_service,
    bridge_diff_service,
    bridge_patch_apply_service,
    bridge_patch_export_service,
    coding_workflow_store,
    router_service,
)


router = APIRouter(prefix="/coding-workflow", tags=["coding-workflow"])

UNIFIED_CODING_WORKFLOW_FLAG = "FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW"
FAKE_API_CODING_AGENT_FLAG = "FORGEX_ENABLE_FAKE_API_CODING_AGENT"
DEFAULT_STALE_THRESHOLD_SECONDS = 30 * 60
CANCEL_CONFIRMATION_REQUIRED = "CODING_WORKFLOW_CANCEL_CONFIRMATION_REQUIRED"
CANCEL_NOT_ALLOWED = "CODING_WORKFLOW_CANCEL_NOT_ALLOWED"
RECOVERY_CONFIRMATION_REQUIRED = "CODING_WORKFLOW_RECOVERY_CONFIRMATION_REQUIRED"
RECOVERY_NOT_ALLOWED = "CODING_WORKFLOW_RECOVERY_NOT_ALLOWED"
LOCK_CLEAR_CONFIRMATION_REQUIRED = "CODING_WORKFLOW_LOCK_CLEAR_CONFIRMATION_REQUIRED"
LOCK_CLEAR_NOT_ALLOWED = "CODING_WORKFLOW_LOCK_CLEAR_NOT_ALLOWED"
REAL_API_CONFIRMATION_REQUIRED = "REAL_API_CODING_AGENT_CONFIRMATION_REQUIRED"


class CodingWorkflowGenerateRequest(APIModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    workspace_path: str | None = Field(default=None, min_length=1)
    context_mode: str = "selected_files"
    run_id: str | None = Field(default=None, min_length=1, max_length=128)


class CodingWorkflowApiGenerateRequest(APIModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    workspace_path: str | None = Field(default=None, min_length=1)
    context_mode: str = "selected_files"
    selected_files: list[str] | None = None
    provider_id: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    live_api_confirmed: bool = False


class CodingWorkflowContextPreviewRequest(APIModel):
    workspace_path: str | None = Field(default=None, min_length=1)
    prompt: str | None = Field(default="", max_length=20_000)
    context_mode: str = "selected_files"
    selected_files: list[str] | None = None
    max_files: int = Field(default=20, ge=1, le=100)
    max_file_bytes: int = Field(default=32 * 1024, ge=1, le=512 * 1024)
    max_total_bytes: int = Field(default=128 * 1024, ge=1, le=1024 * 1024)


class CodingWorkflowContextFilesRequest(APIModel):
    workspace_path: str | None = Field(default=None, min_length=1)
    max_files: int = Field(default=200, ge=1, le=500)
    max_file_bytes: int = Field(default=32 * 1024, ge=1, le=512 * 1024)


class CodingWorkflowApproveApplyRequest(APIModel):
    approval_confirmed: bool = False
    approved_by: str | None = Field(default=None, max_length=128)
    workspace_path: str | None = Field(default=None, min_length=1)


class CodingWorkflowBuildRequest(APIModel):
    build_confirmed: bool = False
    workspace_path: str | None = Field(default=None, min_length=1)
    environment: str | None = Field(default=None, min_length=1, max_length=128)


class CodingWorkflowFlashRequest(APIModel):
    flash_confirmed: bool = False
    workspace_path: str | None = Field(default=None, min_length=1)
    port: str | None = Field(default=None, min_length=1, max_length=256)
    board_id: str | None = Field(default=None, min_length=1, max_length=128)


class CodingWorkflowMonitorRequest(APIModel):
    monitor_confirmed: bool = False
    port: str | None = Field(default=None, min_length=1, max_length=256)
    baud_rate: int | None = Field(default=None, gt=0, le=4_000_000)
    duration_seconds: float | None = Field(default=None, gt=0, le=10)
    max_output_bytes: int | None = Field(default=None, ge=1, le=16 * 1024)


class CodingWorkflowRepairBuildRequest(APIModel):
    workspace_path: str | None = Field(default=None, min_length=1)
    provider_id: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    selected_files: list[str] | None = None


class CodingWorkflowCancelRequest(APIModel):
    cancel_confirmed: bool = False
    reason: str | None = Field(default=None, max_length=512)


class CodingWorkflowRecoveryMarkFailedRequest(APIModel):
    recovery_confirmed: bool = False
    reason: str | None = Field(default=None, max_length=512)


class CodingWorkflowRecoveryClearLockRequest(APIModel):
    clear_lock_confirmed: bool = False
    reason: str | None = Field(default=None, max_length=512)


@router.post(
    "/context/preview",
    responses={**error_responses(403, 404, 409, 422, 500), "default": {"model": ErrorResponse}},
)
async def preview_coding_workflow_context(
    body: CodingWorkflowContextPreviewRequest,
    request: Request,
) -> dict[str, Any]:
    _require_flag(UNIFIED_CODING_WORKFLOW_FLAG, "UNIFIED_CODING_WORKFLOW_DISABLED")
    try:
        context = build_api_coding_context(
            _workspace_path(request, body.workspace_path),
            prompt=body.prompt or "",
            context_mode=_real_context_mode(body.context_mode),
            selected_files=body.selected_files,
            max_files=body.max_files,
            max_file_bytes=body.max_file_bytes,
            max_total_bytes=body.max_total_bytes,
        )
    except ValueError as exc:
        raise APIError(422, "API_CODING_CONTEXT_INVALID", "Coding workflow context preview request is invalid.", {}) from exc
    except OSError as exc:
        raise APIError(422, "CODING_WORKFLOW_WORKSPACE_INVALID", "Workspace path is invalid.", {}) from exc
    return _context_preview_payload(context)


@router.post(
    "/context/files",
    responses={**error_responses(403, 404, 409, 422, 500), "default": {"model": ErrorResponse}},
)
async def list_coding_workflow_context_files(
    body: CodingWorkflowContextFilesRequest,
    request: Request,
) -> dict[str, Any]:
    _require_flag(UNIFIED_CODING_WORKFLOW_FLAG, "UNIFIED_CODING_WORKFLOW_DISABLED")
    try:
        file_list = list_api_coding_context_files(
            _workspace_path(request, body.workspace_path),
            max_files=body.max_files,
            max_file_bytes=body.max_file_bytes,
        )
    except ValueError as exc:
        raise APIError(422, "API_CODING_CONTEXT_INVALID", "Coding workflow context file listing request is invalid.", {}) from exc
    except OSError as exc:
        raise APIError(422, "CODING_WORKFLOW_WORKSPACE_INVALID", "Workspace path is invalid.", {}) from exc
    return file_list.to_dict()


@router.post(
    "/fake/generate-review",
    responses={**error_responses(403, 404, 409, 422, 500), "default": {"model": ErrorResponse}},
)
async def generate_fake_coding_workflow_review(
    body: CodingWorkflowGenerateRequest,
    request: Request,
) -> dict[str, Any]:
    _require_flag(UNIFIED_CODING_WORKFLOW_FLAG, "UNIFIED_CODING_WORKFLOW_DISABLED")
    _require_flag(FAKE_API_CODING_AGENT_FLAG, "FAKE_API_CODING_AGENT_DISABLED")
    _deny_legacy_workflow_admission(request)
    if body.run_id is not None:
        raise APIError(
            422,
            "CODING_WORKFLOW_CALLER_RUN_ID_UNSUPPORTED",
            "Caller-supplied coding workflow run IDs are not supported by this experimental route.",
            {},
        )
    result = CodingAgentGenerationAdapter(
        fake_service=api_coding_agent_service(request),
        store=coding_workflow_store(request),
        env=_workflow_env(),
    ).generate_review(
        body.prompt,
        _workspace_path(request, body.workspace_path),
        context_mode=_context_mode(body.context_mode),
    )
    payload = result.to_safe_dict()
    if result.failure_code is not None:
        code = result.failure_code.value
        raise APIError(_status_for_failure(code), code, result.safe_message, payload)
    return payload


@router.post(
    "/api/generate-review",
    responses={**error_responses(403, 404, 409, 422, 500), "default": {"model": ErrorResponse}},
)
async def generate_api_coding_workflow_review(
    body: CodingWorkflowApiGenerateRequest,
    request: Request,
) -> dict[str, Any]:
    _require_flag(UNIFIED_CODING_WORKFLOW_FLAG, "UNIFIED_CODING_WORKFLOW_DISABLED")
    _require_flag(REAL_API_CODING_AGENT_FLAG, "REAL_API_CODING_AGENT_DISABLED")
    _deny_legacy_workflow_admission(request)
    if body.live_api_confirmed is not True:
        raise APIError(
            403,
            REAL_API_CONFIRMATION_REQUIRED,
            "Explicit confirmation is required before calling a configured real API model provider.",
            {},
        )
    run_id = f"coding-workflow-{uuid.uuid4().hex}"
    task_id = f"task-{uuid.uuid4().hex}"
    project_id = f"project-{uuid.uuid4().hex}"
    result = await RealApiCodingProviderAdapter(
        model_router=router_service(request),
        review_service=api_coding_agent_service(request),
        env=_workflow_env(),
    ).generate_review(
        prompt=body.prompt,
        workspace_path=_workspace_path(request, body.workspace_path),
        context_mode=_real_context_mode(body.context_mode),
        selected_files=body.selected_files,
        provider_id=body.provider_id,
        model_route=body.model,
        run_id=run_id,
    )
    try:
        payload = _persist_generation_result(
            coding_workflow_store(request),
            result,
            task_id=task_id,
            project_id=project_id,
        )
    except CodingWorkflowStoreError as exc:
        raise APIError(_store_error_status(exc.code), exc.code, str(exc), {}) from exc
    if result.failure_code is not None:
        code = result.failure_code.value
        raise APIError(_status_for_failure(code), code, result.safe_message, payload)
    return payload


@router.get(
    "/api/status",
    responses={**error_responses(422, 500), "default": {"model": ErrorResponse}},
)
async def real_api_coding_workflow_status(
    request: Request,
    provider_id: str | None = Query(default=None, min_length=1, max_length=128),
    model: str | None = Query(default=None, min_length=1, max_length=256),
) -> dict[str, Any]:
    return _real_api_status_payload(
        router_service(request),
        provider_id=provider_id,
        model=model,
    )


@router.post(
    "/{run_id}/approve-apply",
    responses={**error_responses(403, 404, 409, 422, 500), "default": {"model": ErrorResponse}},
)
async def approve_apply_coding_workflow(
    run_id: str,
    body: CodingWorkflowApproveApplyRequest,
    request: Request,
) -> dict[str, Any]:
    _require_flag(UNIFIED_CODING_WORKFLOW_FLAG, "UNIFIED_CODING_WORKFLOW_DISABLED")
    _deny_legacy_workflow_mutation(request)
    store = coding_workflow_store(request)
    operation_lock: CodingWorkflowRunOperationLock | None = None
    if body.approval_confirmed is True:
        operation_lock = _acquire_operation_or_error(store, run_id, "apply")
    service = CodingWorkflowApplyService(
        store=store,
        review_service=bridge_diff_service(request),
        patch_export_service=bridge_patch_export_service(request),
        patch_apply_service=bridge_patch_apply_service(request),
        env=_workflow_env(),
    )
    try:
        result = service.approve_and_apply_review(
            run_id,
            workspace_root=_workspace_path(request, body.workspace_path),
            approved_by=body.approved_by,
            approval_confirmed=body.approval_confirmed,
        )
    finally:
        if body.approval_confirmed is True:
            _release_operation_lock(store, run_id, "apply", operation_lock)
    return _result_or_error(result.to_safe_dict())


@router.post(
    "/{run_id}/build",
    responses={**error_responses(403, 404, 409, 422, 500), "default": {"model": ErrorResponse}},
)
async def build_coding_workflow(
    run_id: str,
    body: CodingWorkflowBuildRequest,
    request: Request,
) -> dict[str, Any]:
    _require_flag(UNIFIED_CODING_WORKFLOW_FLAG, "UNIFIED_CODING_WORKFLOW_DISABLED")
    _deny_legacy_workflow_mutation(request)
    store = coding_workflow_store(request)
    operation_lock: CodingWorkflowRunOperationLock | None = None
    if body.build_confirmed is True:
        operation_lock = _acquire_operation_or_error(store, run_id, "build")
    build_executor = getattr(request.app.state, "coding_workflow_build_executor", None)
    if build_executor is None:
        build_executor = required_state(request, "platformio_service", "PlatformIO")
    service = CodingWorkflowBuildService(
        store=store,
        build_service=build_executor,
        patch_apply_service=bridge_patch_apply_service(request),
        env=_workflow_env(),
    )
    try:
        result = await service.run_build_for_applied_workflow(
            run_id,
            workspace_root=_workspace_path(request, body.workspace_path),
            build_confirmed=body.build_confirmed,
            environment=body.environment,
        )
    finally:
        if body.build_confirmed is True:
            _release_operation_lock(store, run_id, "build", operation_lock)
    return _result_or_error(result.to_safe_dict())


@router.post(
    "/{run_id}/flash",
    responses={**error_responses(403, 404, 409, 422, 500), "default": {"model": ErrorResponse}},
)
async def flash_coding_workflow(
    run_id: str,
    body: CodingWorkflowFlashRequest,
    request: Request,
) -> dict[str, Any]:
    _require_flag(UNIFIED_CODING_WORKFLOW_FLAG, "UNIFIED_CODING_WORKFLOW_DISABLED")
    _deny_legacy_workflow_mutation(request)
    store = coding_workflow_store(request)
    operation_lock: CodingWorkflowRunOperationLock | None = None
    if body.flash_confirmed is True:
        operation_lock = _acquire_operation_or_error(store, run_id, "flash")
    flash_executor = getattr(request.app.state, "coding_workflow_flash_executor", None)
    if flash_executor is None:
        flash_executor = FirmwareFlasher(required_state(request, "subprocess_manager", "subprocess manager"))
    detector = getattr(request.app.state, "coding_workflow_board_detector", None)
    if detector is None:
        detector = required_state(request, "board_detector", "board detector")
    service = CodingWorkflowFlashService(
        store=store,
        flash_service=flash_executor,
        board_detector=detector,
        patch_apply_service=bridge_patch_apply_service(request),
        env=_workflow_env(),
    )
    try:
        result = await service.run_flash_for_built_workflow(
            run_id,
            flash_confirmed=body.flash_confirmed,
            workspace_path=_workspace_path(request, body.workspace_path),
            port=body.port,
            board_id=body.board_id,
        )
    finally:
        if body.flash_confirmed is True:
            _release_operation_lock(store, run_id, "flash", operation_lock)
    return _result_or_error(result.to_safe_dict())


@router.post(
    "/{run_id}/monitor",
    responses={**error_responses(403, 404, 409, 422, 500), "default": {"model": ErrorResponse}},
)
async def monitor_coding_workflow(
    run_id: str,
    body: CodingWorkflowMonitorRequest,
    request: Request,
) -> dict[str, Any]:
    _require_flag(UNIFIED_CODING_WORKFLOW_FLAG, "UNIFIED_CODING_WORKFLOW_DISABLED")
    _deny_legacy_workflow_mutation(request)
    store = coding_workflow_store(request)
    operation_lock: CodingWorkflowRunOperationLock | None = None
    if body.monitor_confirmed is True:
        operation_lock = _acquire_operation_or_error(store, run_id, "monitor")
    factory = getattr(request.app.state, "coding_workflow_monitor_factory", None)
    kwargs: dict[str, object] = {
        "store": store,
        "env": _workflow_env(),
    }
    if factory is not None:
        kwargs["monitor_factory"] = factory
    service = CodingWorkflowMonitorService(**kwargs)  # type: ignore[arg-type]
    try:
        result = await service.run_monitor_for_flashed_workflow(
            run_id,
            monitor_confirmed=body.monitor_confirmed,
            port=body.port,
            baud_rate=body.baud_rate,
            duration_seconds=body.duration_seconds,
            max_output_bytes=body.max_output_bytes,
        )
    finally:
        if body.monitor_confirmed is True:
            _release_operation_lock(store, run_id, "monitor", operation_lock)
    return _result_or_error(result.to_safe_dict())


@router.post(
    "/{run_id}/repair/build",
    responses={**error_responses(403, 404, 409, 422, 500), "default": {"model": ErrorResponse}},
)
async def repair_build_coding_workflow(
    run_id: str,
    body: CodingWorkflowRepairBuildRequest,
    request: Request,
) -> dict[str, Any]:
    _require_flag(UNIFIED_CODING_WORKFLOW_FLAG, "UNIFIED_CODING_WORKFLOW_DISABLED")
    _deny_legacy_workflow_mutation(request)
    _require_flag(REAL_API_CODING_AGENT_FLAG, "REAL_API_CODING_AGENT_DISABLED")
    _require_flag(CODING_AGENT_REPAIR_LOOP_FLAG, "CODING_WORKFLOW_REPAIR_DISABLED")
    store = coding_workflow_store(request)
    operation_lock = _acquire_operation_or_error(store, run_id, "repair", allow_failed=True)
    try:
        result = await CodingWorkflowRepairService(
            store=store,
            model_router=router_service(request),
            review_service=api_coding_agent_service(request),
            env=_workflow_env(),
        ).generate_build_repair_review(
            run_id,
            workspace_path=_workspace_path(request, body.workspace_path),
            provider_id=body.provider_id,
            model=body.model,
            selected_files=body.selected_files,
        )
    finally:
        _release_operation_lock(store, run_id, "repair", operation_lock)
    return _result_or_error(result.to_safe_dict())


@router.post(
    "/{run_id}/cancel",
    responses={**error_responses(403, 404, 409, 422, 500), "default": {"model": ErrorResponse}},
)
async def cancel_coding_workflow(
    run_id: str,
    body: CodingWorkflowCancelRequest,
    request: Request,
) -> dict[str, Any]:
    _require_flag(UNIFIED_CODING_WORKFLOW_FLAG, "UNIFIED_CODING_WORKFLOW_DISABLED")
    _deny_legacy_workflow_mutation(request)
    if body.cancel_confirmed is not True:
        raise APIError(403, CANCEL_CONFIRMATION_REQUIRED, "Explicit cancellation confirmation is required.", {})
    store = coding_workflow_store(request)
    operation_lock = _acquire_operation_or_error(store, run_id, "cancel", allow_failed=True)
    try:
        run = store.get_run(run_id)
        if run.status in IN_PROGRESS_STATUSES or run.status not in CANCELLABLE_STATUSES or run.status in {"completed", "cancelled"}:
            raise APIError(409, CANCEL_NOT_ALLOWED, "Coding workflow cannot be cancelled from its current state.", {"status": run.status})
        if run.status == "failed" and not _failed_run_safe_to_cancel(run):
            raise APIError(409, CANCEL_NOT_ALLOWED, "Coding workflow cannot be cancelled from its current failed state.", {"status": run.status})
        cancelling = store.transition_run(
            run.run_id,
            expected_statuses=(run.status,),
            status="cancelling",
            generation_status=run.generation_status or "cancelling",
            next_action=None,
            safe_message="Cancellation is in progress.",
            metadata={**dict(run.metadata), "cancel_reason_supplied": bool(body.reason and body.reason.strip())},
        )
        _append_event(store, cancelling.run_id, "cancel.started", "cancel", "cancelling", "Cancellation requested by the user.")
        cancelled = store.transition_run(
            run.run_id,
            expected_statuses=("cancelling",),
            status="cancelled",
            generation_status="cancelled",
            next_action=None,
            safe_message="Workflow was cancelled by the user.",
            metadata={**dict(cancelling.metadata), "cancelled": True},
        )
        _append_event(store, cancelled.run_id, "workflow.cancelled", "workflow", "cancelled", "Workflow was cancelled by the user.")
    except APIError:
        raise
    except CodingWorkflowStoreError as exc:
        raise APIError(_store_error_status(exc.code), exc.code, str(exc), {}) from exc
    finally:
        _release_operation_lock(store, run_id, "cancel", operation_lock)
    return {
        "run_id": cancelled.run_id,
        "status": cancelled.status,
        "next_action": cancelled.next_action,
        "failure_code": None,
        "safe_message": "Workflow was cancelled by the user.",
    }


@router.get(
    "/recovery/stale",
    responses={**error_responses(422, 500), "default": {"model": ErrorResponse}},
)
async def list_stale_coding_workflows(
    request: Request,
    threshold_seconds: int = Query(default=DEFAULT_STALE_THRESHOLD_SECONDS, ge=60, le=24 * 60 * 60),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    try:
        store = coding_workflow_store(request)
        stale = store.detect_stale_in_progress_runs(
            threshold_seconds=threshold_seconds,
            limit=limit,
        )
        stale_locks = _lock_manager(store).detect_stale_locks(limit=limit)
    except CodingWorkflowStoreError as exc:
        raise APIError(_store_error_status(exc.code), exc.code, str(exc), {}) from exc
    except CodingWorkflowLockError as exc:
        raise APIError(_lock_error_status(exc.code), exc.code, str(exc), {}) from exc
    return {
        "runs": list(stale),
        "count": len(stale),
        "stale_locks": list(stale_locks),
        "stale_lock_count": len(stale_locks),
        "threshold_seconds": threshold_seconds,
    }


@router.post(
    "/{run_id}/recovery/mark-failed",
    responses={**error_responses(403, 404, 409, 422, 500), "default": {"model": ErrorResponse}},
)
async def mark_stale_coding_workflow_failed(
    run_id: str,
    body: CodingWorkflowRecoveryMarkFailedRequest,
    request: Request,
    threshold_seconds: int = Query(default=DEFAULT_STALE_THRESHOLD_SECONDS, ge=60, le=24 * 60 * 60),
) -> dict[str, Any]:
    _require_flag(UNIFIED_CODING_WORKFLOW_FLAG, "UNIFIED_CODING_WORKFLOW_DISABLED")
    _deny_legacy_workflow_mutation(request)
    if body.recovery_confirmed is not True:
        raise APIError(403, RECOVERY_CONFIRMATION_REQUIRED, "Explicit recovery confirmation is required.", {})
    store = coding_workflow_store(request)
    operation_lock = _acquire_operation_or_error(store, run_id, "recovery", allow_in_progress=True)
    try:
        run = store.get_run(run_id)
        if run.status not in IN_PROGRESS_STATUSES or not store.is_stale_in_progress(run_id, threshold_seconds=threshold_seconds):
            raise APIError(409, RECOVERY_NOT_ALLOWED, "Only stale in-progress workflows can be manually marked failed.", {"status": run.status})
        failed = store.transition_run(
            run.run_id,
            expected_statuses=(run.status,),
            status="failed",
            generation_status="failed",
            next_action=None,
            failure_code=STALE_RECOVERY_CODE,
            safe_message="Workflow was manually marked failed after stale in-progress recovery.",
            metadata={**dict(run.metadata), "manual_recovery": True, "recovery_reason_supplied": bool(body.reason and body.reason.strip())},
        )
        _append_event(
            store,
            failed.run_id,
            "workflow.recovered_failed",
            "recovery",
            "failed",
            "Workflow was manually marked failed after stale in-progress recovery.",
            metadata={"failure_code": STALE_RECOVERY_CODE},
        )
    except APIError:
        raise
    except CodingWorkflowStoreError as exc:
        raise APIError(_store_error_status(exc.code), exc.code, str(exc), {}) from exc
    finally:
        _release_operation_lock(store, run_id, "recovery", operation_lock)
    return {
        "run_id": failed.run_id,
        "status": failed.status,
        "next_action": failed.next_action,
        "failure_code": failed.failure_code,
        "safe_message": failed.safe_message,
    }


@router.post(
    "/{run_id}/recovery/clear-lock",
    responses={**error_responses(403, 404, 409, 422, 500), "default": {"model": ErrorResponse}},
)
async def clear_stale_coding_workflow_lock(
    run_id: str,
    body: CodingWorkflowRecoveryClearLockRequest,
    request: Request,
) -> dict[str, Any]:
    _require_flag(UNIFIED_CODING_WORKFLOW_FLAG, "UNIFIED_CODING_WORKFLOW_DISABLED")
    _deny_legacy_workflow_mutation(request)
    if body.clear_lock_confirmed is not True:
        raise APIError(403, LOCK_CLEAR_CONFIRMATION_REQUIRED, "Explicit stale lock clear confirmation is required.", {})
    store = coding_workflow_store(request)
    try:
        store.get_run(run_id)
        cleared = _lock_manager(store).clear_stale_run_lock(run_id)
        _append_event(
            store,
            run_id,
            "workflow.lock_cleared",
            "recovery",
            "recovered",
            "Stale workflow operation lock was manually cleared.",
            metadata={"operation": str(cleared.get("operation") or ""), "manual_recovery": True},
        )
    except CodingWorkflowLockError as exc:
        code = LOCK_CLEAR_NOT_ALLOWED if exc.code in {LOCK_NOT_FOUND, LOCK_NOT_STALE} else exc.code
        raise APIError(_lock_error_status(code), code, str(exc), {}) from exc
    except CodingWorkflowStoreError as exc:
        raise APIError(_store_error_status(exc.code), exc.code, str(exc), {}) from exc
    return {
        "run_id": run_id,
        "status": "lock_cleared",
        "lock": {key: value for key, value in cleared.items() if key != "token"},
        "failure_code": None,
        "safe_message": "Stale workflow operation lock was manually cleared.",
    }


@router.get(
    "",
    responses={**error_responses(422, 500), "default": {"model": ErrorResponse}},
)
async def list_coding_workflow_runs(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    store = coding_workflow_store(request)
    try:
        runs = store.list_runs(limit=limit)
    except CodingWorkflowStoreError as exc:
        raise APIError(_store_error_status(exc.code), exc.code, str(exc), {}) from exc
    return {"runs": [_run_payload(store, run) for run in runs], "count": len(runs)}


@router.get(
    "/{run_id}",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def get_coding_workflow_run(run_id: str, request: Request) -> dict[str, Any]:
    store = coding_workflow_store(request)
    try:
        run = store.get_run(run_id)
    except CodingWorkflowStoreError as exc:
        raise APIError(_store_error_status(exc.code), exc.code, str(exc), {}) from exc
    return {"run": _run_payload(store, run)}


@router.get(
    "/{run_id}/events",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def get_coding_workflow_events(run_id: str, request: Request) -> dict[str, Any]:
    store = coding_workflow_store(request)
    try:
        store.get_run(run_id)
        events = store.list_events(run_id)
    except CodingWorkflowStoreError as exc:
        raise APIError(_store_error_status(exc.code), exc.code, str(exc), {}) from exc
    return {"events": [event.to_dict() for event in events], "count": len(events)}


def _deny_legacy_workflow_admission(request: Request) -> None:
    if coding_workflow_store(request).allow_new_runs:
        return
    raise APIError(
        503,
        "LEGACY_WORKFLOW_ADMISSION_DISABLED",
        "New legacy workflow admission is disabled; use the durable Agent Workspace.",
        {},
    )

def _deny_legacy_workflow_mutation(request: Request) -> None:
    if coding_workflow_store(request).allow_mutations:
        return
    raise APIError(
        503,
        "LEGACY_WORKFLOW_READ_ONLY",
        "Historical legacy workflows are read-only; use the durable Agent Workspace.",
        {},
    )

def _require_flag(name: str, code: str) -> None:
    if os.getenv(name, "").strip() != "1":
        raise APIError(403, code, "Unified coding workflow route is disabled.", {"feature_flag": name})


def _workflow_env() -> Mapping[str, str]:
    return {
        UNIFIED_CODING_WORKFLOW_FLAG: os.getenv(UNIFIED_CODING_WORKFLOW_FLAG, ""),
        FAKE_API_CODING_AGENT_FLAG: os.getenv(FAKE_API_CODING_AGENT_FLAG, ""),
        REAL_API_CODING_AGENT_FLAG: os.getenv(REAL_API_CODING_AGENT_FLAG, ""),
        CODING_AGENT_REPAIR_LOOP_FLAG: os.getenv(CODING_AGENT_REPAIR_LOOP_FLAG, ""),
    }


def _workspace_path(request: Request, value: str | None) -> Path:
    selected = value
    if selected is None:
        workspace = getattr(request.app.state, "workspace_manager", None)
        root = getattr(workspace, "workspace_root", None)
        selected = str(root) if root is not None else None
    if selected is None or "\x00" in selected:
        raise APIError(422, "CODING_WORKFLOW_WORKSPACE_REQUIRED", "A safe workspace path is required.", {})
    try:
        path = Path(selected).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise APIError(422, "CODING_WORKFLOW_WORKSPACE_INVALID", "Workspace path is invalid.", {}) from exc
    if not path.is_dir():
        raise APIError(422, "CODING_WORKFLOW_WORKSPACE_INVALID", "Workspace path must be an existing directory.", {})
    return path


def _real_api_status_payload(
    model_router: Any,
    *,
    provider_id: str | None,
    model: str | None,
) -> dict[str, object]:
    unified_enabled = os.getenv(UNIFIED_CODING_WORKFLOW_FLAG, "").strip() == "1"
    real_enabled = os.getenv(REAL_API_CODING_AGENT_FLAG, "").strip() == "1"
    repair_enabled = os.getenv(CODING_AGENT_REPAIR_LOOP_FLAG, "").strip() == "1"
    base: dict[str, object] = {
        "unified_workflow_enabled": unified_enabled,
        "real_api_coding_agent_enabled": real_enabled,
        "coding_agent_repair_loop_enabled": repair_enabled,
        "ready": False,
        "provider_id": None,
        "model": None,
        "model_router_available": False,
        "reason": None,
        "safe_message": "",
    }
    if not unified_enabled:
        base.update({
            "reason": "UNIFIED_CODING_WORKFLOW_DISABLED",
            "safe_message": "Enable FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW=1.",
        })
        return base
    if not real_enabled:
        base.update({
            "reason": "REAL_API_CODING_AGENT_DISABLED",
            "safe_message": "Enable FORGEX_ENABLE_REAL_API_CODING_AGENT=1.",
        })
        return base

    registry = getattr(model_router, "registry", None)
    if registry is None:
        base.update({
            "reason": "MODEL_ROUTER_UNAVAILABLE",
            "safe_message": "Model router metadata is unavailable.",
        })
        return base

    try:
        route = registry.route_for_task("code_generation")
        selected_provider_id = provider_id.strip() if isinstance(provider_id, str) and provider_id.strip() else route.provider_id
        provider = registry.provider(selected_provider_id)
        selected_model = model.strip() if isinstance(model, str) and model.strip() else (
            route.model_id if selected_provider_id == route.provider_id else registry.default_model(selected_provider_id)
        )
    except Exception:
        base.update({
            "reason": "MODEL_ROUTER_UNAVAILABLE",
            "safe_message": "Model router metadata is unavailable.",
        })
        return base

    connections = getattr(registry, "connections", None)
    if connections is None:
        base.update({
            "reason": "CONNECTION_REGISTRY_UNAVAILABLE",
            "safe_message": "Canonical provider connection status is unavailable.",
        })
        return base
    connection = connections.refresh_status(f"{selected_provider_id}.default")
    base.update({
        "credential_status": connection.credential_status.value,
        "authentication_status": connection.auth_state.value,
        "connection_health": connection.transport_status.value,
        "connection_ready": connection.connection_ready,
    })

    base.update({
        "provider_id": selected_provider_id,
        "model": selected_model,
        "model_router_available": True,
    })

    if not connection.connection_ready:
        missing = connection.credential_status.value == "credential_missing"
        base.update({
            "reason": "MODEL_PROVIDER_NOT_CONFIGURED" if missing else "MODEL_PROVIDER_NOT_READY",
            "safe_message": "No provider credential is configured." if missing else "Canonical provider authentication or connection health is not ready.",
        })
        return base
    if getattr(provider, "provider_type", None) != "api_provider" or getattr(provider, "auth_type", None) != "api_key" or bool(getattr(provider, "local", False)):
        base.update({
            "reason": "MODEL_PROVIDER_NOT_CONFIGURED",
            "safe_message": "Configure a real API model provider in Model Settings.",
        })
        return base
    if not bool(getattr(provider, "configured", False)) or not bool(getattr(provider, "enabled", False)):
        base.update({
            "reason": "MODEL_PROVIDER_NOT_CONFIGURED",
            "safe_message": "No healthy model provider is configured.",
        })
        return base
    if str(getattr(provider, "health_status", "unknown")).strip().casefold() in {
        "authentication_required",
        "error",
        "not_configured",
        "offline",
        "unavailable",
        "disabled",
    }:
        base.update({
            "reason": "MODEL_PROVIDER_UNHEALTHY",
            "safe_message": "Configured model provider health is not ready.",
        })
        return base
    try:
        known_models = {item.model_id for item in registry.list_models(selected_provider_id)}
    except Exception:
        known_models = set()
    if model and known_models and selected_model not in known_models:
        base.update({
            "reason": "MODEL_NOT_AVAILABLE",
            "safe_message": "Requested model is not available in cached provider metadata.",
        })
        return base

    base.update({
        "ready": True,
        "reason": "REAL_API_CODING_AGENT_READY",
        "safe_message": "Real API coding agent is ready.",
    })
    return base


def _context_mode(value: str) -> CodingContextMode:
    text = value.strip().casefold()
    for item in CodingContextMode:
        if item.value.casefold() == text:
            return item
    raise APIError(422, "CODING_WORKFLOW_CONTEXT_MODE_INVALID", "Coding workflow context mode is invalid.", {})


def _real_context_mode(value: str) -> str:
    text = value.strip().casefold()
    if text in {"selected_files", "project_summary"}:
        return text
    raise APIError(422, "CODING_WORKFLOW_CONTEXT_MODE_INVALID", "Coding workflow context mode is invalid.", {})


def _context_preview_payload(context: Any) -> dict[str, object]:
    return {
        "context_mode": context.context_mode,
        "workspace_label": context.workspace_label,
        "included_files": [
            {
                "path": item.path,
                "size_bytes": item.size_bytes,
                "truncated": item.truncated,
                "kind": item.kind,
            }
            for item in context.files
        ],
        "excluded_files": [
            {
                "path": item.path,
                "reason": item.reason,
            }
            for item in context.excluded_files
        ],
        "tree_summary": list(context.tree_summary),
        "total_bytes": context.total_bytes,
        "truncated": context.truncated,
        "limits": context.limits.to_dict(),
    }


def _persist_generation_result(
    store: CodingWorkflowStore,
    result: Any,
    *,
    task_id: str,
    project_id: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    awaiting_apply = result.status.value == "awaiting_apply"
    generation_status = "review_created" if awaiting_apply else "failed"
    record = CodingWorkflowRunRecord(
        run_id=result.events[0].run_id if result.events else f"coding-workflow-{uuid.uuid4().hex}",
        task_id=task_id,
        project_id=project_id,
        provider_id=REAL_API_CODING_AGENT_PROVIDER_ID,
        provider_type="api_coding_agent",
        status=result.status.value,
        generation_status=generation_status,
        review_id=result.review_id,
        next_action="await_user_approval" if awaiting_apply else None,
        files_changed=result.files_changed,
        created_at=now,
        updated_at=now,
        safe_summary=result.summary or None,
        failure_code=result.failure_code.value if result.failure_code else None,
        safe_message=result.safe_message or None,
        metadata=_generation_metadata(result.metadata),
    )
    events = tuple(CodingWorkflowEventRecord(
        event_id=event.event_id,
        run_id=event.run_id,
        sequence=event.sequence,
        event_type=event.event_type,
        stage=event.stage.value,
        status=event.status.value,
        safe_message=event.safe_message,
        created_at=event.timestamp,
        metadata=event.metadata,
    ) for event in result.events)
    if awaiting_apply:
        sequence = len(events) + 1
        digest = hashlib.sha256(f"{record.run_id}:{sequence}:apply.waiting_for_approval".encode("utf-8")).hexdigest()
        events = events + (CodingWorkflowEventRecord(
            event_id=f"event-{digest}",
            run_id=record.run_id,
            sequence=sequence,
            event_type="apply.waiting_for_approval",
            stage="review",
            status="awaiting_apply",
            safe_message="Generation created a review. Explicit approval is required before apply.",
            created_at=now,
            metadata={"provider_id": REAL_API_CODING_AGENT_PROVIDER_ID, "real_api_calls": True},
        ),)
    store.persist_run(record, events)
    payload = result.to_safe_dict()
    payload.update({
        "run_id": record.run_id,
        "task_id": task_id,
        "project_id": project_id,
        "provider_id": REAL_API_CODING_AGENT_PROVIDER_ID,
        "provider_type": "api_coding_agent",
        "generation_status": generation_status,
        "next_action": record.next_action,
        "review_created": awaiting_apply,
        "awaiting_apply": awaiting_apply,
        "events": [event.to_dict() for event in events],
    })
    return payload


def _generation_metadata(value: Mapping[str, Any]) -> dict[str, object]:
    allowed = {
        "context_mode",
        "context_file_count",
        "context_total_bytes",
        "context_truncated",
        "context_excluded_count",
        "model_provider_id",
        "model_id",
        "model_output_chars",
        "provider_id",
        "real_api_calls",
        "command_suggestion_count",
        "risk_count",
        "next_step_count",
        "validation_error_count",
    }
    return {key: item for key, item in value.items() if key in allowed}


def _result_or_error(payload: dict[str, Any]) -> dict[str, Any]:
    code = payload.get("failure_code")
    if isinstance(code, str) and code:
        raise APIError(_status_for_failure(code), code, str(payload.get("safe_message") or "Coding workflow request failed."), payload)
    return payload


def _acquire_operation_or_error(
    store: CodingWorkflowStore,
    run_id: str,
    operation: str,
    *,
    allow_failed: bool = False,
    allow_in_progress: bool = False,
) -> CodingWorkflowRunOperationLock:
    shared_lock: CodingWorkflowRunOperationLock | None = None
    try:
        run = store.get_run(run_id)
        if store.is_operation_locked(run_id):
            raise APIError(409, OPERATION_IN_PROGRESS, "A coding workflow operation is already in progress.", {"run_id": run_id})
        if run.status in IN_PROGRESS_STATUSES and not allow_in_progress:
            raise APIError(409, OPERATION_IN_PROGRESS, "A coding workflow operation is already in progress.", {"run_id": run_id, "status": run.status})
        if run.status == "failed" and not allow_failed and operation != "recovery":
            pass
        shared_lock = _lock_manager(store).acquire_run_operation_lock(run_id, operation)
        store.acquire_operation_lock(run_id, operation)
        return shared_lock
    except APIError:
        raise
    except CodingWorkflowLockError as exc:
        code = OPERATION_IN_PROGRESS if exc.code == LOCK_OPERATION_IN_PROGRESS else exc.code
        raise APIError(_lock_error_status(code), code, str(exc), {}) from exc
    except CodingWorkflowStoreError as exc:
        if shared_lock is not None:
            _lock_manager(store).release_run_operation_lock(shared_lock)
        raise APIError(_store_error_status(exc.code), exc.code, str(exc), {}) from exc


def _release_operation_lock(
    store: CodingWorkflowStore,
    run_id: str,
    operation: str,
    shared_lock: CodingWorkflowRunOperationLock | None,
) -> None:
    store.release_operation_lock(run_id, operation)
    if shared_lock is not None:
        _lock_manager(store).release_run_operation_lock(shared_lock)


def _lock_manager(store: CodingWorkflowStore) -> CodingWorkflowLockManager:
    return CodingWorkflowLockManager(store.runs_path.parent / "coding-workflow-locks")


def _append_event(
    store: CodingWorkflowStore,
    run_id: str,
    event_type: str,
    stage: str,
    status: str,
    message: str,
    *,
    metadata: Mapping[str, object] | None = None,
) -> None:
    sequence = len(store.list_events(run_id)) + 1
    digest = hashlib.sha256(f"{run_id}:{sequence}:{event_type}".encode("utf-8")).hexdigest()
    store.append_event(CodingWorkflowEventRecord(
        event_id=f"event-{digest}",
        run_id=run_id,
        sequence=sequence,
        event_type=event_type,
        stage=stage,
        status=status,
        safe_message=message,
        metadata=dict(metadata or {}),
    ))


def _run_payload(store: CodingWorkflowStore, run: CodingWorkflowRunRecord) -> dict[str, Any]:
    payload = run.to_dict()
    in_progress_stage = IN_PROGRESS_STAGE_BY_STATUS.get(run.status)
    lock_status = _safe_lock_status(store, run.run_id)
    locked = store.is_operation_locked(run.run_id) or bool(lock_status.get("locked"))
    started_at = _started_at_for_payload(store, run, in_progress_stage)
    payload.update({
        "in_progress_stage": in_progress_stage,
        "locked": locked,
        "lock_stale": bool(lock_status.get("stale")),
        "lock_operation": lock_status.get("operation") if lock_status.get("locked") else None,
        "started_at": started_at,
        "stale_candidate": bool(in_progress_stage and store.is_stale_in_progress(run.run_id, threshold_seconds=DEFAULT_STALE_THRESHOLD_SECONDS)),
    })
    return payload


def _safe_lock_status(store: CodingWorkflowStore, run_id: str) -> dict[str, object]:
    try:
        status = _lock_manager(store).get_run_lock_status(run_id)
    except CodingWorkflowLockError:
        return {"run_id": run_id, "locked": False, "stale": False}
    return {key: value for key, value in status.items() if key != "token"}


def _started_at_for_payload(store: CodingWorkflowStore, run: CodingWorkflowRunRecord, stage: str | None) -> str | None:
    if stage is None:
        return None
    expected = f"{stage}.started"
    for event in reversed(store.list_events(run.run_id)):
        if event.event_type == expected:
            return event.created_at
    return run.updated_at


def _failed_run_safe_to_cancel(run: CodingWorkflowRunRecord) -> bool:
    return isinstance(run.failure_code, str) and run.failure_code.startswith("CODING_WORKFLOW_BUILD")


def _status_for_failure(code: str) -> int:
    folded = code.casefold()
    if "disabled" in folded or "confirmation_required" in folded or "approval_required" in folded:
        return 403
    if "not_found" in folded:
        return 404
    if "not_awaiting" in folded or "already_" in folded or "operation_in_progress" in folded or "cancel_not_allowed" in folded or "recovery_not_allowed" in folded or "lock_clear_not_allowed" in folded or "invalid_state_transition" in folded or "repair_not_allowed" in folded or "repair_requires_build_failure" in folded or "repair_limit_exceeded" in folded:
        return 409
    if "persistence" in folded or "internal" in folded:
        return 500
    return 422


def _store_error_status(code: str) -> int:
    if code in {"LEGACY_WORKFLOW_ADMISSION_DISABLED", "LEGACY_WORKFLOW_READ_ONLY"}:
        return 503

    if "NOT_FOUND" in code:
        return 404
    if "STATUS_INVALID" in code or "OPERATION_IN_PROGRESS" in code or "INVALID_STATE_TRANSITION" in code:
        return 409
    if "CORRUPT" in code or "PERSISTENCE" in code:
        return 500
    return 422


def _lock_error_status(code: str) -> int:
    if code == OPERATION_IN_PROGRESS:
        return 409
    if code in {LOCK_CLEAR_NOT_ALLOWED, LOCK_NOT_FOUND, LOCK_NOT_STALE}:
        return 409
    if "PERSISTENCE" in code:
        return 500
    return 422


__all__ = [
    "CodingWorkflowApproveApplyRequest",
    "CodingWorkflowApiGenerateRequest",
    "CodingWorkflowBuildRequest",
    "CodingWorkflowContextPreviewRequest",
    "CodingWorkflowFlashRequest",
    "CodingWorkflowGenerateRequest",
    "CodingWorkflowMonitorRequest",
    "CodingWorkflowRepairBuildRequest",
    "router",
]
