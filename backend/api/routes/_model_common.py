"""Shared dependency accessors for model and bridge API routes."""

from __future__ import annotations

from fastapi import Request

from ...agent_runtime.api_coding_agent_service import ApiCodingAgentService
from ...agent_runtime.coding_workflow_store import CodingWorkflowStore
from ...bridges import (
    AntigravitySandboxRunner,
    BridgeDetectionService,
    BridgeDiffService,
    BridgePatchExportService,
    PatchApplyService,
    PatchPreflightService,
    RollbackRestoreApplyService,
    RollbackRestorePreflightService,
    RollbackSnapshotService,
)
from ...bridges.agy_execution_router import AGYExecutionRouter
from ...connection_registry import AuthState, ConnectionRegistry
from ...model_router import ModelRouterService
from ..dependencies import required_state


def connection_registry(request: Request) -> ConnectionRegistry:
    value = required_state(request, "connection_registry", "connection registry")
    assert isinstance(value, ConnectionRegistry)
    return value


def canonical_bridge_payload(request: Request, provider_id: str) -> dict[str, object] | None:
    mapping = {
        "codex": (ConnectionRegistry.CODEX_ID, "codex_cli_bridge", "Codex CLI"),
        "codex_cli_bridge": (ConnectionRegistry.CODEX_ID, "codex_cli_bridge", "Codex CLI"),
        "agy": (ConnectionRegistry.AGY_ID, "antigravity_cli_bridge", "Google Antigravity / AGY CLI"),
        "antigravity": (ConnectionRegistry.AGY_ID, "antigravity_cli_bridge", "Google Antigravity / AGY CLI"),
        "antigravity_cli_bridge": (ConnectionRegistry.AGY_ID, "antigravity_cli_bridge", "Google Antigravity / AGY CLI"),
    }
    target = mapping.get(provider_id)
    if target is None:
        return None
    record = connection_registry(request).refresh_status(target[0])
    authentication = (
        "authenticated" if record.auth_state is AuthState.AUTHENTICATED else
        "unauthenticated" if record.auth_state is AuthState.SIGNED_OUT else
        "unknown"
    )
    return {
        "provider_id": target[1], "display_name": target[2], "type": "tool_bridge",
        "provider_type": "tool_bridge", "provider_kind": "local_cli",
        "auth_mode": "official_cli_managed", "execution_mode": "detection_only",
        "workspace_mode": "none", "production_eligible": False, "qa_only": True,
        "installed": record.detected, "version": record.version, "executable_path": None,
        "auth_status": authentication, "auth_message": record.safe_message or "Connection status checked.",
        "status_confidence": "high" if record.auth_state is not AuthState.AUTH_UNKNOWN else "low",
        "setup_hint": None, "setup_action": "run_official_login_manually",
        "safe_status_checked": True, "checked_commands": [], "capabilities": {"detect": True, "run_prompt": False, "stream_prompt": False, "edit_files": False, "diff_review": False},
        "warnings": [], "can_run": False, "reason": "Agent execution authority is separate from authentication.",
        "detection_classification": record.diagnostic_code, "auth_classification": record.diagnostic_code,
        "executable_found": record.detected, "version_detected": record.version is not None,
        "login_command": None, "smoke_ready": False, "smoke_classification": "STANDALONE_SMOKE_NOT_READY",
        "last_checked_at": record.status_checked_at,
    }


def router_service(request: Request) -> ModelRouterService:
    value = required_state(request, "model_router_service", "model router")
    assert isinstance(value, ModelRouterService)
    return value


def api_coding_agent_service(request: Request) -> ApiCodingAgentService:
    value = required_state(request, "api_coding_agent_service", "fake API coding agent")
    assert isinstance(value, ApiCodingAgentService)
    return value


def coding_workflow_store(request: Request) -> CodingWorkflowStore:
    value = required_state(request, "coding_workflow_store", "coding workflow store")
    assert isinstance(value, CodingWorkflowStore)
    return value


def bridge_detection_service(request: Request) -> BridgeDetectionService:
    value = required_state(request, "bridge_detection_service", "bridge detection")
    assert isinstance(value, BridgeDetectionService)
    return value


def bridge_diff_service(request: Request) -> BridgeDiffService:
    value = required_state(request, "bridge_diff_service", "bridge review")
    assert isinstance(value, BridgeDiffService)
    return value


def bridge_patch_export_service(request: Request) -> BridgePatchExportService:
    value = required_state(request, "bridge_patch_export_service", "bridge patch export")
    assert isinstance(value, BridgePatchExportService)
    return value


def bridge_patch_apply_service(request: Request) -> PatchApplyService:
    value = required_state(request, "bridge_patch_apply_service", "bridge patch apply")
    assert isinstance(value, PatchApplyService)
    return value


def bridge_patch_preflight_service(request: Request) -> PatchPreflightService:
    value = required_state(request, "bridge_patch_preflight_service", "bridge patch preflight")
    assert isinstance(value, PatchPreflightService)
    return value


def bridge_rollback_snapshot_service(request: Request) -> RollbackSnapshotService:
    value = required_state(request, "bridge_rollback_snapshot_service", "bridge rollback snapshot")
    assert isinstance(value, RollbackSnapshotService)
    return value


def bridge_rollback_restore_preflight_service(request: Request) -> RollbackRestorePreflightService:
    value = required_state(request, "bridge_rollback_restore_preflight_service", "bridge rollback restore preflight")
    assert isinstance(value, RollbackRestorePreflightService)
    return value


def bridge_rollback_restore_apply_service(request: Request) -> RollbackRestoreApplyService:
    value = required_state(request, "bridge_rollback_restore_apply_service", "bridge rollback restore")
    assert isinstance(value, RollbackRestoreApplyService)
    return value


def bridge_agy_runner(request: Request) -> AntigravitySandboxRunner:
    value = required_state(request, "bridge_agy_runner", "AGY sandbox runner")
    assert isinstance(value, AntigravitySandboxRunner)
    return value


def agy_execution_router(request: Request) -> AGYExecutionRouter:
    value = required_state(request, "agy_execution_router", "AGY execution router")
    assert isinstance(value, AGYExecutionRouter)
    return value
