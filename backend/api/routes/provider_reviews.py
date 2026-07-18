"""Bridge review, decision, and review-patch routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import Field

from ...bridges.diff_service import BridgeDiffError
from ...bridges.patch_export_service import BridgePatchExportError
from ..errors import APIError, error_responses
from ..schemas.common import APIModel, ErrorResponse
from ._model_common import bridge_diff_service, bridge_patch_export_service

router = APIRouter()


class BridgeReviewSnapshotRequest(APIModel):
    workspace_root: str = Field(min_length=1)


class BridgeReviewDiffRequest(APIModel):
    provider_id: str = Field(min_length=1)
    workspace_root: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)


@router.post(
    "/bridges/reviews/snapshot",
    responses={**error_responses(422, 500), "default": {"model": ErrorResponse}},
)
async def bridge_review_snapshot(body: BridgeReviewSnapshotRequest, request: Request) -> dict[str, Any]:
    reviews = bridge_diff_service(request)
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
    reviews = bridge_diff_service(request)
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
    reviews = bridge_diff_service(request)
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
    reviews = bridge_diff_service(request)
    result = reviews.cleanup_expired_reviews()
    return {"cleanup": result, "counts": reviews.review_counts()}


@router.get(
    "/bridges/reviews/{review_id}",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def bridge_review_detail(review_id: str, request: Request) -> dict[str, Any]:
    reviews = bridge_diff_service(request)
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
    exporter = bridge_patch_export_service(request)
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
    exporter = bridge_patch_export_service(request)
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
    exporter = bridge_patch_export_service(request)
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
    exporter = bridge_patch_export_service(request)
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
    exporter = bridge_patch_export_service(request)
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
    exporter = bridge_patch_export_service(request)
    try:
        result = exporter.open_patch_folder(review_id)
    except BridgePatchExportError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return result


@router.post(
    "/bridges/reviews/{review_id}/approve",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def approve_bridge_review(review_id: str, request: Request) -> dict[str, Any]:
    reviews = bridge_diff_service(request)
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
    reviews = bridge_diff_service(request)
    try:
        review, decision = reviews.reject_review(review_id)
    except BridgeDiffError as exc:
        status = 404 if "not found" in str(exc).casefold() else 422
        raise APIError(status, exc.code, str(exc), exc.details) from exc
    return {"review": review.to_dict(), "decision": decision.to_dict()}
