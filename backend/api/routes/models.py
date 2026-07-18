"""Compatibility composition for model, provider, and bridge API routes."""

from __future__ import annotations

from fastapi import APIRouter

from . import api_coding_agent, bridge_safety, coding_workflow, model_providers, model_routes, provider_patches, provider_reviews
from ._model_common import (
    agy_execution_router as _agy_execution_router,
    bridge_agy_runner as _bridge_agy_runner,
    bridge_detection_service as _bridge_detection_service,
    bridge_diff_service as _bridge_diff_service,
    bridge_patch_apply_service as _bridge_patch_apply_service,
    bridge_patch_export_service as _bridge_patch_export_service,
    bridge_patch_preflight_service as _bridge_patch_preflight_service,
    bridge_rollback_restore_apply_service as _bridge_rollback_restore_apply_service,
    bridge_rollback_restore_preflight_service as _bridge_rollback_restore_preflight_service,
    bridge_rollback_snapshot_service as _bridge_rollback_snapshot_service,
    router_service as _router_service,
)
from .bridge_safety import AntigravitySandboxRunRequest
from .model_providers import ConfigureProviderRequest
from .model_routes import RouteRequest, TestModelRequest
from .provider_patches import (
    BridgePatchApplyRequest,
    BridgePatchCleanupRequest,
    BridgePatchPreflightRequest,
    RollbackRestorePreflightRequest,
    RollbackRestoreRequest,
    RollbackSnapshotCleanupRequest,
    RollbackSnapshotRequest,
)
from .provider_reviews import BridgeReviewDiffRequest, BridgeReviewSnapshotRequest

router = APIRouter(prefix="/models", tags=["models"])
router.include_router(api_coding_agent.router)
router.include_router(coding_workflow.router)
router.include_router(bridge_safety.router)
router.include_router(provider_reviews.router)
router.include_router(provider_patches.router)
router.include_router(model_providers.router)
router.include_router(model_routes.router)

__all__ = [
    "AntigravitySandboxRunRequest",
    "BridgePatchApplyRequest",
    "BridgePatchCleanupRequest",
    "BridgePatchPreflightRequest",
    "BridgeReviewDiffRequest",
    "BridgeReviewSnapshotRequest",
    "ConfigureProviderRequest",
    "RollbackRestorePreflightRequest",
    "RollbackRestoreRequest",
    "RollbackSnapshotCleanupRequest",
    "RollbackSnapshotRequest",
    "RouteRequest",
    "TestModelRequest",
    "router",
]
