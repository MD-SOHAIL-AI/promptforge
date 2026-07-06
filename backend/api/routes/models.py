"""Model provider and route settings API."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import Field

from ...bridges import AntigravitySandboxRunner, BridgeDetectionService, BridgeDiffService, BridgePatchExportService, PatchApplyService, PatchPreflightService, RollbackRestoreApplyService, RollbackRestorePreflightService, RollbackSnapshotService
from ...bridges.diff_service import BridgeDiffError
from ...bridges.patch_apply_service import PATCH_APPLY_DISABLED_MESSAGE, PATCH_APPLY_REQUIRES_RESTORE_MESSAGE, PatchApplyError
from ...bridges.patch_export_service import BridgePatchExportError
from ...bridges.patch_preflight_service import PatchPreflightError
from ...bridges.rollback_service import RollbackSnapshotError
from ...bridges.rollback_restore_apply_service import ROLLBACK_RESTORE_DISABLED_MESSAGE, RollbackRestoreApplyError
from ...bridges.rollback_restore_service import RollbackRestorePreflightError
from ...bridges.providers.antigravity_runner import AntigravityRunnerError
from ...bridges.agy_execution_router import AGYExecutionRouter
from ...model_router import ModelRequest, ModelRoute, ModelRouterService
from ...model_router import CredentialStoreError
from ...services.code_generation_service import CodeGenerationService
from ...services.llm_service import LLMConfigurationError, LLMError
from ..dependencies import required_state
from ..errors import APIError, error_responses
from ..schemas.common import APIModel, ErrorResponse

router = APIRouter(prefix="/models", tags=["models"])


class ConfigureProviderRequest(APIModel):
    api_key: str | None = Field(default=None, min_length=1)
    base_url: str | None = Field(default=None, min_length=1)
    default_model: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None


class RouteRequest(APIModel):
    task_type: str
    provider_id: str
    model_id: str
    fallback_enabled: bool = True
    fallback_provider_id: str | None = None
    local_only: bool = False


class TestModelRequest(APIModel):
    prompt: str = Field(default="Reply with OK.", min_length=1)
    system_prompt: str | None = None
    task_type: str = "general_chat"
    provider_id: str | None = None
    model_id: str | None = None
    local_only: bool = False


class BridgeReviewSnapshotRequest(APIModel):
    workspace_root: str = Field(min_length=1)


class BridgeReviewDiffRequest(APIModel):
    provider_id: str = Field(min_length=1)
    workspace_root: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)


class AntigravitySandboxRunRequest(APIModel):
    workspace_root: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    timeout_seconds: int = Field(default=300, ge=1, le=900)
    idempotency_key: str | None = Field(default=None, min_length=3, max_length=127)


class BridgePatchCleanupRequest(APIModel):
    older_than_days: int = Field(default=30, ge=0)
    include_missing: bool = True


class BridgePatchPreflightRequest(APIModel):
    workspace_root: str | None = Field(default=None, min_length=1)


class BridgePatchApplyRequest(APIModel):
    workspace_root: str = Field(min_length=1)
    confirmation: str = Field(min_length=1)


class RollbackSnapshotRequest(APIModel):
    workspace_root: str = Field(min_length=1)


class RollbackSnapshotCleanupRequest(APIModel):
    older_than_days: int = Field(default=30, ge=0)


class RollbackRestorePreflightRequest(APIModel):
    workspace_root: str = Field(min_length=1)


class RollbackRestoreRequest(APIModel):
    workspace_root: str = Field(min_length=1)
    confirmation: str = Field(min_length=1)


@router.get(
    "/bridges",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def list_bridges(request: Request) -> dict[str, Any]:
    bridge_detection = _bridge_detection_service(request)
    return {"bridges": [result.to_dict() for result in bridge_detection.detect_all()]}


@router.post(
    "/bridges/refresh",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def refresh_bridges(request: Request) -> dict[str, Any]:
    bridge_detection = _bridge_detection_service(request)
    return {"bridges": [result.to_dict() for result in bridge_detection.detect_all()]}


@router.get(
    "/bridges/antigravity/sandbox-status",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def antigravity_sandbox_status(request: Request) -> dict[str, Any]:
    runner = _bridge_agy_runner(request)
    execution_router = _agy_execution_router(request)
    return {
        "enabled": runner.is_enabled(),
        "feature_flag": "FORGEX_ENABLE_AGY_BRIDGE",
        "provider_id": "antigravity_cli_bridge",
        "execution_mode": execution_router.effective_mode.value,
    }


@router.get(
    "/bridges/safety-status",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def bridge_safety_status(request: Request) -> dict[str, Any]:
    runner = _bridge_agy_runner(request)
    generic_registry = required_state(request, "generic_bridge_registry", "generic bridge registry")
    agy_adapter = required_state(request, "agy_generic_provider", "AGY generic provider")
    execution_router = _agy_execution_router(request)
    generic_api_flags = required_state(request, "generic_run_feature_flags", "generic run policy")
    restore = _bridge_rollback_restore_apply_service(request)
    apply = _bridge_patch_apply_service(request)
    restore_enabled = restore.is_enabled()
    patch_apply_feature_flag = apply.patch_apply_feature_flag_enabled()
    rollback_restore_feature_flag = apply.rollback_restore_feature_flag_enabled()
    patch_apply_enabled = patch_apply_feature_flag and rollback_restore_feature_flag
    return {
        "rollback_snapshots_enabled": True,
        "restore_preflight_enabled": True,
        "restore_enabled": restore_enabled,
        "restore_feature_flag": restore_enabled,
        "apply_enabled": patch_apply_enabled,
        "patch_apply_enabled": patch_apply_enabled,
        "patch_apply_feature_flag": patch_apply_feature_flag,
        "rollback_restore_enabled": restore_enabled,
        "rollback_restore_feature_flag": rollback_restore_feature_flag,
        "apply_requires_restore": True,
        "agy_bridge_enabled": runner.is_enabled(),
        "agy_provider_state": "paused",
        "agy_execution_allowed": False,
        "agy_routing_allowed": False,
        "codex_provider_state": "paused",
        "codex_routing_allowed": False,
        "claude_cli_provider_state": "disabled",
        "opencode_provider_state": "reference_only",
        "qa_mode_enabled": os.getenv("FORGEX_QA_MODE", "").strip() == "1",
        "agy_trusted_workspace_enabled": (
            os.getenv("FORGEX_QA_MODE", "").strip() == "1"
            and os.getenv("FORGEX_ENABLE_AGY_TRUSTED_WORKSPACE", "").strip() == "1"
        ),
        "generic_bridge_contracts_available": True,
        "generic_bridge_coordinator_available": True,
        "generic_bridge_routing_enabled": execution_router.flags.generic_routing_enabled,
        "agy_generic_provider_enabled": execution_router.flags.generic_provider_enabled,
        "agy_compatibility_router_available": True,
        "agy_effective_execution_mode": execution_router.effective_mode.value,
        "agy_generic_adapter_registered": any(
            getattr(provider, "provider_id", None) == "agy"
            for provider in generic_registry.list_registered()
        ),
        "agy_generic_adapter_enabled": bool(getattr(agy_adapter, "execution_enabled", False)),
        "bridge_routing_enabled": False,
        "codex_execution_enabled": False,
        "claude_execution_enabled": False,
        "opencode_execution_enabled": False,
        "generic_bridge_sandbox_required": True,
        "generic_bridge_event_transport_internal_only": False,
        "public_generic_run_api_enabled": bool(getattr(generic_api_flags, "api_enabled", False)),
        "generic_api_status": "enabled" if getattr(generic_api_flags, "api_enabled", False) else "available_disabled",
        "generic_sse_status": "available",
        "generic_provider_scope": "agy_only",
        "agy_generic_execution_default": "disabled",
        "generic_run_store_status": getattr(request.app.state, "generic_run_store_status", "initializing"),
        "raw_instructions_persisted": False,
        "auto_apply_enabled": False,
        "auto_build_after_apply": False,
        "auto_flash_after_apply": False,
    }


@router.post(
    "/bridges/antigravity/sandbox-run",
    responses={**error_responses(403, 422, 500), "default": {"model": ErrorResponse}},
)
async def start_antigravity_sandbox_run(body: AntigravitySandboxRunRequest, request: Request) -> dict[str, Any]:
    execution_router = _agy_execution_router(request)
    try:
        run = await execution_router.start_run(
            workspace_root=body.workspace_root,
            prompt=body.prompt,
            timeout_seconds=body.timeout_seconds,
            idempotency_key=body.idempotency_key,
        )
    except AntigravityRunnerError as exc:
        status = 403 if any(word in str(exc).casefold() for word in ("disabled", "blocked")) else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return {"run": run.to_dict()}


@router.get(
    "/bridges/runs",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def list_bridge_runs(request: Request) -> dict[str, Any]:
    execution_router = _agy_execution_router(request)
    return {"runs": [run.to_dict() for run in execution_router.list_runs()]}


@router.get(
    "/bridges/runs/{run_id}",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def bridge_run_detail(run_id: str, request: Request) -> dict[str, Any]:
    execution_router = _agy_execution_router(request)
    try:
        run = execution_router.get_run(run_id)
    except AntigravityRunnerError as exc:
        raise APIError(404, exc.code, str(exc), exc.details) from exc
    return {"run": run.to_dict()}


@router.post(
    "/bridges/runs/{run_id}/cancel",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def cancel_bridge_run(run_id: str, request: Request) -> dict[str, Any]:
    execution_router = _agy_execution_router(request)
    try:
        run = await execution_router.cancel_run(run_id)
    except AntigravityRunnerError as exc:
        raise APIError(404, exc.code, str(exc), exc.details) from exc
    return {"run": run.to_dict()}


@router.post(
    "/bridges/reviews/snapshot",
    responses={**error_responses(422, 500), "default": {"model": ErrorResponse}},
)
async def bridge_review_snapshot(body: BridgeReviewSnapshotRequest, request: Request) -> dict[str, Any]:
    reviews = _bridge_diff_service(request)
    try:
        snapshot = reviews.snapshot_workspace(body.workspace_root)
    except BridgeDiffError as exc:
        raise APIError(422, exc.code, str(exc), exc.details) from exc
    return {"snapshot": snapshot.to_dict()}


@router.post(
    "/bridges/reviews/diff",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def bridge_review_diff(body: BridgeReviewDiffRequest, request: Request) -> dict[str, Any]:
    reviews = _bridge_diff_service(request)
    try:
        snapshot = reviews.get_snapshot(body.snapshot_id)
        review = reviews.create_review(
            provider_id=body.provider_id,
            workspace_root=body.workspace_root,
            snapshot=snapshot,
        )
    except BridgeDiffError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return {"review": review.to_dict()}


@router.get(
    "/bridges/reviews",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def list_bridge_reviews(
    request: Request,
    status: str | None = None,
    provider_id: str | None = None,
) -> dict[str, Any]:
    reviews = _bridge_diff_service(request)
    return {
        "reviews": [
            review.to_dict()
            for review in reviews.list_reviews(status=status, provider_id=provider_id)
        ],
        "counts": reviews.review_counts(),
    }


@router.post(
    "/bridges/reviews/cleanup",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def cleanup_bridge_reviews(request: Request) -> dict[str, Any]:
    reviews = _bridge_diff_service(request)
    result = reviews.cleanup_expired_reviews()
    return {"cleanup": result, "counts": reviews.review_counts()}


@router.get(
    "/bridges/reviews/{review_id}",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def bridge_review_detail(review_id: str, request: Request) -> dict[str, Any]:
    reviews = _bridge_diff_service(request)
    try:
        review = reviews.get_review(review_id)
    except BridgeDiffError as exc:
        raise APIError(404, exc.code, str(exc), exc.details) from exc
    return {"review": review.to_dict()}


@router.post(
    "/bridges/reviews/{review_id}/export-patch",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def export_bridge_review_patch(review_id: str, request: Request) -> dict[str, Any]:
    exporter = _bridge_patch_export_service(request)
    try:
        export = exporter.export_patch(review_id)
    except BridgePatchExportError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return {"patch": export.to_dict()}


@router.get(
    "/bridges/reviews/{review_id}/patch",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def bridge_review_patch(review_id: str, request: Request) -> Response:
    exporter = _bridge_patch_export_service(request)
    try:
        patch = exporter.patch_text(review_id)
    except BridgePatchExportError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return Response(
        patch,
        media_type="text/x-patch",
        headers={"Content-Disposition": f'attachment; filename="{review_id}.patch"'},
    )


@router.get(
    "/bridges/reviews/{review_id}/patch-metadata",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def bridge_review_patch_metadata(review_id: str, request: Request) -> dict[str, Any]:
    exporter = _bridge_patch_export_service(request)
    try:
        metadata = exporter.patch_metadata(review_id)
    except BridgePatchExportError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return {"patch": metadata.to_dict()}


@router.post(
    "/bridges/reviews/{review_id}/verify-patch",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def verify_bridge_review_patch(review_id: str, request: Request) -> dict[str, Any]:
    exporter = _bridge_patch_export_service(request)
    try:
        metadata = exporter.verify_patch(review_id)
    except BridgePatchExportError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return {"patch": metadata.to_dict()}


@router.post(
    "/bridges/reviews/{review_id}/patch-copied",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def bridge_review_patch_copied(review_id: str, request: Request) -> dict[str, Any]:
    exporter = _bridge_patch_export_service(request)
    try:
        exporter.record_patch_copied(review_id)
        metadata = exporter.verify_patch(review_id)
    except BridgePatchExportError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return {"patch": metadata.to_dict()}


@router.post(
    "/bridges/reviews/{review_id}/open-patch-folder",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def open_bridge_review_patch_folder(review_id: str, request: Request) -> dict[str, Any]:
    exporter = _bridge_patch_export_service(request)
    try:
        result = exporter.open_patch_folder(review_id)
    except BridgePatchExportError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return result


@router.get(
    "/bridges/patches",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def list_bridge_patches(
    request: Request,
    provider_id: str | None = None,
    review_id: str | None = None,
    integrity_status: str | None = None,
) -> dict[str, Any]:
    exporter = _bridge_patch_export_service(request)
    patches = exporter.list_patches(
        provider_id=provider_id,
        review_id=review_id,
        integrity_status=integrity_status,
    )
    return {"patches": [patch.to_dict() for patch in patches], "count": len(patches)}


@router.delete(
    "/bridges/patches/{patch_id}",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def delete_bridge_patch(patch_id: str, request: Request) -> dict[str, Any]:
    exporter = _bridge_patch_export_service(request)
    try:
        return exporter.delete_patch(patch_id)
    except BridgePatchExportError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc


@router.post(
    "/bridges/patches/{patch_id}/preflight",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def preflight_bridge_patch(patch_id: str, body: BridgePatchPreflightRequest, request: Request) -> dict[str, Any]:
    preflight = _bridge_patch_preflight_service(request)
    try:
        result = preflight.preflight_patch(patch_id, workspace_root=body.workspace_root)
    except PatchPreflightError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return result.to_dict()


@router.post(
    "/bridges/patches/{patch_id}/apply",
    responses={**error_responses(403, 404, 422, 500), "default": {"model": ErrorResponse}},
)
async def apply_bridge_patch(patch_id: str, body: BridgePatchApplyRequest, request: Request) -> dict[str, Any]:
    apply = _bridge_patch_apply_service(request)
    try:
        result = apply.apply_patch(patch_id, workspace_root=body.workspace_root, confirmation=body.confirmation)
    except PatchApplyError as exc:
        message = str(exc)
        if message in {PATCH_APPLY_DISABLED_MESSAGE, PATCH_APPLY_REQUIRES_RESTORE_MESSAGE}:
            status = 403
        elif "not found" in message.casefold():
            status = 404
        else:
            status = 422
        raise APIError(status, exc.code, message, exc.details) from exc
    return result.to_dict()


@router.get(
    "/bridges/patch-applies",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def list_bridge_patch_applies(request: Request) -> dict[str, Any]:
    apply = _bridge_patch_apply_service(request)
    applies = apply.list_applies()
    return {"applies": [item.to_dict() for item in applies], "count": len(applies)}


@router.get(
    "/bridges/patch-applies/{apply_id}",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def bridge_patch_apply_detail(apply_id: str, request: Request) -> dict[str, Any]:
    apply = _bridge_patch_apply_service(request)
    try:
        result = apply.get_apply(apply_id)
    except PatchApplyError as exc:
        raise APIError(404, exc.code, str(exc), exc.details) from exc
    return {"apply": result.to_dict()}


@router.post(
    "/bridges/patches/{patch_id}/rollback-snapshot",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def create_bridge_rollback_snapshot(patch_id: str, body: RollbackSnapshotRequest, request: Request) -> dict[str, Any]:
    rollback = _bridge_rollback_snapshot_service(request)
    try:
        snapshot = rollback.create_snapshot(patch_id, workspace_root=body.workspace_root)
    except RollbackSnapshotError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return {
        "rollback_id": snapshot.rollback_id,
        "patch_id": snapshot.patch_id,
        "review_id": snapshot.review_id,
        "status": snapshot.status,
        "files_backed_up": sum(1 for item in snapshot.files if item.existed_before and item.backup_path),
        "total_bytes": snapshot.total_bytes,
        "restore_enabled": snapshot.restore_enabled,
        "snapshot": snapshot.to_dict(),
    }


@router.get(
    "/bridges/rollback-snapshots",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def list_bridge_rollback_snapshots(request: Request) -> dict[str, Any]:
    rollback = _bridge_rollback_snapshot_service(request)
    snapshots = rollback.list_snapshots()
    return {"snapshots": [snapshot.to_dict() for snapshot in snapshots], "count": len(snapshots)}


@router.get(
    "/bridges/rollback-snapshots/{rollback_id}",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def bridge_rollback_snapshot_detail(rollback_id: str, request: Request) -> dict[str, Any]:
    rollback = _bridge_rollback_snapshot_service(request)
    try:
        snapshot = rollback.get_snapshot(rollback_id)
    except RollbackSnapshotError as exc:
        raise APIError(404, exc.code, str(exc), exc.details) from exc
    return {"snapshot": snapshot.to_dict()}


@router.delete(
    "/bridges/rollback-snapshots/{rollback_id}",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def delete_bridge_rollback_snapshot(rollback_id: str, request: Request) -> dict[str, Any]:
    rollback = _bridge_rollback_snapshot_service(request)
    try:
        snapshot = rollback.delete_snapshot(rollback_id)
    except RollbackSnapshotError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return {"deleted": True, "snapshot": snapshot.to_dict()}


@router.post(
    "/bridges/rollback-snapshots/{rollback_id}/restore-preflight",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def preflight_bridge_rollback_restore(rollback_id: str, body: RollbackRestorePreflightRequest, request: Request) -> dict[str, Any]:
    restore_preflight = _bridge_rollback_restore_preflight_service(request)
    try:
        result = restore_preflight.preflight_restore(rollback_id, workspace_root=body.workspace_root)
    except RollbackRestorePreflightError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return result.to_dict()


@router.post(
    "/bridges/rollback-snapshots/{rollback_id}/restore",
    responses={**error_responses(403, 404, 422, 500), "default": {"model": ErrorResponse}},
)
async def restore_bridge_rollback_snapshot(rollback_id: str, body: RollbackRestoreRequest, request: Request) -> dict[str, Any]:
    restore = _bridge_rollback_restore_apply_service(request)
    try:
        result = restore.restore_snapshot(rollback_id, workspace_root=body.workspace_root, confirmation=body.confirmation)
    except RollbackRestoreApplyError as exc:
        message = str(exc)
        if message == ROLLBACK_RESTORE_DISABLED_MESSAGE:
            status = 403
        elif "not found" in message.casefold():
            status = 404
        else:
            status = 422
        raise APIError(status, exc.code, message, exc.details) from exc
    return result.to_dict()


@router.get(
    "/bridges/rollback-restores",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def list_bridge_rollback_restores(request: Request) -> dict[str, Any]:
    restore = _bridge_rollback_restore_apply_service(request)
    restores = restore.list_restores()
    return {"restores": [item.to_dict() for item in restores], "count": len(restores)}


@router.get(
    "/bridges/rollback-restores/{restore_id}",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def bridge_rollback_restore_detail(restore_id: str, request: Request) -> dict[str, Any]:
    restore = _bridge_rollback_restore_apply_service(request)
    try:
        result = restore.get_restore(restore_id)
    except RollbackRestoreApplyError as exc:
        raise APIError(404, exc.code, str(exc), exc.details) from exc
    return {"restore": result.to_dict()}


@router.post(
    "/bridges/rollback-snapshots/cleanup",
    responses={**error_responses(422, 500), "default": {"model": ErrorResponse}},
)
async def cleanup_bridge_rollback_snapshots(body: RollbackSnapshotCleanupRequest, request: Request) -> dict[str, Any]:
    rollback = _bridge_rollback_snapshot_service(request)
    try:
        result = rollback.cleanup_snapshots(older_than_days=body.older_than_days)
    except RollbackSnapshotError as exc:
        raise APIError(422, exc.code, str(exc), exc.details) from exc
    return {"cleanup": result}


@router.post(
    "/bridges/patches/cleanup",
    responses={**error_responses(422, 500), "default": {"model": ErrorResponse}},
)
async def cleanup_bridge_patches(body: BridgePatchCleanupRequest, request: Request) -> dict[str, Any]:
    exporter = _bridge_patch_export_service(request)
    try:
        result = exporter.cleanup_patches(
            older_than_days=body.older_than_days,
            include_missing=body.include_missing,
        )
    except BridgePatchExportError as exc:
        raise APIError(422, exc.code, str(exc), exc.details) from exc
    return {"cleanup": result}


@router.post(
    "/bridges/reviews/{review_id}/approve",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def approve_bridge_review(review_id: str, request: Request) -> dict[str, Any]:
    reviews = _bridge_diff_service(request)
    try:
        review, decision = reviews.approve_review(review_id)
    except BridgeDiffError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return {"review": review.to_dict(), "decision": decision.to_dict()}


@router.post(
    "/bridges/reviews/{review_id}/reject",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def reject_bridge_review(review_id: str, request: Request) -> dict[str, Any]:
    reviews = _bridge_diff_service(request)
    try:
        review, decision = reviews.reject_review(review_id)
    except BridgeDiffError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return {"review": review.to_dict(), "decision": decision.to_dict()}


@router.get(
    "/bridges/{provider_id}",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def bridge_detail(provider_id: str, request: Request) -> dict[str, Any]:
    bridge_detection = _bridge_detection_service(request)
    try:
        result = bridge_detection.detect_provider(provider_id)
    except ValueError as exc:
        raise APIError(404, "BRIDGE_PROVIDER_NOT_FOUND", str(exc), {"provider_id": provider_id}) from exc
    return {"bridge": result.to_dict()}


@router.get(
    "/providers",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def list_providers(request: Request) -> dict[str, Any]:
    router_service = _router_service(request)
    return {
        "providers": [
            provider.to_dict()
            for provider in router_service.registry.list_providers()
        ]
    }


@router.post(
    "/providers/{provider_id}/configure",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def configure_provider(
    provider_id: str,
    body: ConfigureProviderRequest,
    request: Request,
) -> dict[str, Any]:
    router_service = _router_service(request)
    try:
        provider = router_service.registry.configure_provider(
            provider_id,
            body.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise APIError(404, "MODEL_PROVIDER_NOT_FOUND", str(exc), {"provider_id": provider_id}) from exc
    except CredentialStoreError as exc:
        raise APIError(503, "SECURE_CREDENTIAL_STORE_UNAVAILABLE", "The operating-system credential store is unavailable.") from exc
    return {"provider": provider.to_dict()}


@router.post(
    "/providers/{provider_id}/health",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def provider_health(provider_id: str, request: Request) -> dict[str, Any]:
    router_service = _router_service(request)
    provider = router_service._providers.get(provider_id)
    if provider is None:
        raise APIError(
            404,
            "MODEL_PROVIDER_NOT_FOUND",
            f"Unknown model provider: {provider_id}",
            {"provider_id": provider_id},
        )
    health = await provider.health()
    payload = health.to_dict()
    try:
        router_service.registry.save_health(provider_id, payload)
    except OSError:
        # Health checks remain useful in read-only or locked-down runtimes;
        # persistence is secondary to returning the live result.
        pass
    return {"health": payload}


@router.get(
    "/providers/{provider_id}/models",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def list_provider_models(provider_id: str, request: Request) -> dict[str, Any]:
    router_service = _router_service(request)
    provider = router_service._providers.get(provider_id)
    if provider is None:
        raise APIError(
            404,
            "MODEL_PROVIDER_NOT_FOUND",
            f"Unknown model provider: {provider_id}",
            {"provider_id": provider_id},
        )
    try:
        models = await provider.list_models()
    except Exception as exc:
        fallback = router_service.registry.list_models(provider_id)
        router_service.registry.save_models(provider_id, fallback)
        return {
            "models": [model.to_dict() for model in fallback],
            "count": len(fallback),
            "source": "fallback",
            "error": f"Could not fetch model list. Enter model ID manually. ({type(exc).__name__})",
        }
    router_service.registry.save_models(provider_id, models)
    return {
        "models": [model.to_dict() for model in models],
        "count": len(models),
        "source": "provider",
        "error": None,
    }


@router.get("/routes", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def list_routes(request: Request) -> dict[str, Any]:
    router_service = _router_service(request)
    return {"routes": [route.to_dict() for route in router_service.registry.list_routes()]}


@router.post(
    "/routes",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def save_route(body: RouteRequest, request: Request) -> dict[str, Any]:
    router_service = _router_service(request)
    try:
        route = ModelRoute(
            task_type=body.task_type,  # type: ignore[arg-type]
            provider_id=body.provider_id,
            model_id=body.model_id,
            fallback_enabled=body.fallback_enabled,
            fallback_provider_id=body.fallback_provider_id,
            local_only=body.local_only,
        )
        router_service.registry.save_route(route)
    except ValueError as exc:
        raise APIError(404, "MODEL_PROVIDER_NOT_FOUND", str(exc), {"provider_id": body.provider_id}) from exc
    return {"route": route.to_dict()}


@router.post(
    "/test",
    responses={**error_responses(422, 502, 500), "default": {"model": ErrorResponse}},
)
async def test_model(body: TestModelRequest, request: Request) -> dict[str, Any]:
    router_service = _router_service(request)
    try:
        response = await router_service.generate_model(
            ModelRequest(
                prompt=body.prompt,
                system_prompt=body.system_prompt,
                task_type=body.task_type,  # type: ignore[arg-type]
                provider_id=body.provider_id,
                model_id=body.model_id,
                local_only=body.local_only,
                max_tokens=64,
            )
        )
    except LLMConfigurationError as exc:
        raise APIError(422, exc.code, exc.message, dict(exc.details)) from exc
    except LLMError as exc:
        raise APIError(502, exc.code, exc.message, dict(exc.details)) from exc
    except Exception as exc:
        raise APIError(502, "MODEL_PROVIDER_ERROR", str(exc), {"exception_type": type(exc).__name__}) from exc
    return {
        "response": {
            "content": response.content,
            "provider_id": response.provider_id,
            "model_id": response.model_id,
            "latency_ms": response.latency_ms,
            "usage": response.token_usage(),
        }
    }


@router.get("/usage", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def usage(request: Request, limit: int = 100) -> dict[str, Any]:
    router_service = _router_service(request)
    records = router_service.usage.list_records(limit=max(1, min(limit, 1000)))
    return {"usage": records, "count": len(records)}


@router.get("/generation-attempts", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def generation_attempts(
    request: Request,
    task_id: str | None = None,
    execution_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    generation = required_state(request, "code_generation_service", "code generation service")
    assert isinstance(generation, CodeGenerationService)
    attempts = generation.list_attempts(
        task_id=task_id,
        execution_id=execution_id,
        limit=max(1, min(limit, 1000)),
    )
    return {"attempts": attempts, "count": len(attempts)}


@router.get("/chunked-generation-runs", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def chunked_generation_runs(
    request: Request,
    execution_id: str | None = None,
    project_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    generation = required_state(request, "code_generation_service", "code generation service")
    assert isinstance(generation, CodeGenerationService)
    runs = generation.list_chunked_runs(
        execution_id=execution_id,
        project_id=project_id,
        limit=max(1, min(limit, 1000)),
    )
    return {"runs": runs, "count": len(runs)}


@router.post("/generation-diagnostics/clear", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def clear_generation_diagnostics(request: Request) -> dict[str, Any]:
    generation = required_state(request, "code_generation_service", "code generation service")
    assert isinstance(generation, CodeGenerationService)
    generation.clear_generation_diagnostics()
    return {"cleared": True}


def _router_service(request: Request) -> ModelRouterService:
    value = required_state(request, "model_router_service", "model router")
    assert isinstance(value, ModelRouterService)
    return value


def _bridge_detection_service(request: Request) -> BridgeDetectionService:
    value = required_state(request, "bridge_detection_service", "bridge detection")
    assert isinstance(value, BridgeDetectionService)
    return value


def _bridge_diff_service(request: Request) -> BridgeDiffService:
    value = required_state(request, "bridge_diff_service", "bridge review")
    assert isinstance(value, BridgeDiffService)
    return value


def _bridge_patch_export_service(request: Request) -> BridgePatchExportService:
    value = required_state(request, "bridge_patch_export_service", "bridge patch export")
    assert isinstance(value, BridgePatchExportService)
    return value


def _bridge_patch_apply_service(request: Request) -> PatchApplyService:
    value = required_state(request, "bridge_patch_apply_service", "bridge patch apply")
    assert isinstance(value, PatchApplyService)
    return value


def _bridge_patch_preflight_service(request: Request) -> PatchPreflightService:
    value = required_state(request, "bridge_patch_preflight_service", "bridge patch preflight")
    assert isinstance(value, PatchPreflightService)
    return value


def _bridge_rollback_snapshot_service(request: Request) -> RollbackSnapshotService:
    value = required_state(request, "bridge_rollback_snapshot_service", "bridge rollback snapshot")
    assert isinstance(value, RollbackSnapshotService)
    return value


def _bridge_rollback_restore_preflight_service(request: Request) -> RollbackRestorePreflightService:
    value = required_state(request, "bridge_rollback_restore_preflight_service", "bridge rollback restore preflight")
    assert isinstance(value, RollbackRestorePreflightService)
    return value


def _bridge_rollback_restore_apply_service(request: Request) -> RollbackRestoreApplyService:
    value = required_state(request, "bridge_rollback_restore_apply_service", "bridge rollback restore")
    assert isinstance(value, RollbackRestoreApplyService)
    return value


def _bridge_agy_runner(request: Request) -> AntigravitySandboxRunner:
    value = required_state(request, "bridge_agy_runner", "AGY sandbox runner")
    assert isinstance(value, AntigravitySandboxRunner)
    return value


def _agy_execution_router(request: Request) -> AGYExecutionRouter:
    value = required_state(request, "agy_execution_router", "AGY execution router")
    assert isinstance(value, AGYExecutionRouter)
    return value
