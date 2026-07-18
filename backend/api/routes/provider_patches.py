"""Bridge patch, apply, rollback snapshot, and restore routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field

from ...bridges.patch_apply_service import (
    PATCH_APPLY_DISABLED_MESSAGE,
    PATCH_APPLY_REQUIRES_RESTORE_MESSAGE,
    PatchApplyError,
)
from ...bridges.patch_export_service import BridgePatchExportError
from ...bridges.patch_preflight_service import PatchPreflightError
from ...bridges.rollback_restore_apply_service import (
    ROLLBACK_RESTORE_DISABLED_MESSAGE,
    RollbackRestoreApplyError,
)
from ...bridges.rollback_restore_service import RollbackRestorePreflightError
from ...bridges.rollback_service import RollbackSnapshotError
from ..errors import APIError, error_responses
from ..schemas.common import APIModel, ErrorResponse
from ._model_common import (
    bridge_patch_apply_service,
    bridge_patch_export_service,
    bridge_patch_preflight_service,
    bridge_rollback_restore_apply_service,
    bridge_rollback_restore_preflight_service,
    bridge_rollback_snapshot_service,
)

router = APIRouter()


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
    "/bridges/patches",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def list_bridge_patches(
    request: Request,
    provider_id: str | None = None,
    review_id: str | None = None,
    integrity_status: str | None = None,
) -> dict[str, Any]:
    exporter = bridge_patch_export_service(request)
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
    exporter = bridge_patch_export_service(request)
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
    preflight = bridge_patch_preflight_service(request)
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
    apply = bridge_patch_apply_service(request)
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
    apply = bridge_patch_apply_service(request)
    applies = apply.list_applies()
    return {"applies": [item.to_dict() for item in applies], "count": len(applies)}


@router.get(
    "/bridges/patch-applies/{apply_id}",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def bridge_patch_apply_detail(apply_id: str, request: Request) -> dict[str, Any]:
    apply = bridge_patch_apply_service(request)
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
    rollback = bridge_rollback_snapshot_service(request)
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
    rollback = bridge_rollback_snapshot_service(request)
    snapshots = rollback.list_snapshots()
    return {"snapshots": [snapshot.to_dict() for snapshot in snapshots], "count": len(snapshots)}


@router.get(
    "/bridges/rollback-snapshots/{rollback_id}",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def bridge_rollback_snapshot_detail(rollback_id: str, request: Request) -> dict[str, Any]:
    rollback = bridge_rollback_snapshot_service(request)
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
    rollback = bridge_rollback_snapshot_service(request)
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
    restore_preflight = bridge_rollback_restore_preflight_service(request)
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
    restore = bridge_rollback_restore_apply_service(request)
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
    restore = bridge_rollback_restore_apply_service(request)
    restores = restore.list_restores()
    return {"restores": [item.to_dict() for item in restores], "count": len(restores)}


@router.get(
    "/bridges/rollback-restores/{restore_id}",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def bridge_rollback_restore_detail(restore_id: str, request: Request) -> dict[str, Any]:
    restore = bridge_rollback_restore_apply_service(request)
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
    rollback = bridge_rollback_snapshot_service(request)
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
    exporter = bridge_patch_export_service(request)
    try:
        result = exporter.cleanup_patches(
            older_than_days=body.older_than_days,
            include_missing=body.include_missing,
        )
    except BridgePatchExportError as exc:
        raise APIError(422, exc.code, str(exc), exc.details) from exc
    return {"cleanup": result}
