"""Bridge detection, safety status, and compatibility run routes."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field

from ...bridges.codex_status import CodexAlignedStatus, codex_state_semantics
from ...bridges.providers.antigravity_runner import AntigravityRunnerError
from ..dependencies import required_state
from ..errors import APIError, error_responses
from ..schemas.common import APIModel, ErrorResponse
from ._model_common import (
    agy_execution_router,
    bridge_agy_runner,
    bridge_detection_service,
    canonical_bridge_payload,
    connection_registry,
    bridge_patch_apply_service,
    bridge_rollback_restore_apply_service,
)

router = APIRouter()


class AntigravitySandboxRunRequest(APIModel):
    workspace_root: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    timeout_seconds: int = Field(default=300, ge=1, le=900)
    idempotency_key: str | None = Field(default=None, min_length=3, max_length=127)


@router.get(
    "/bridges",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def list_bridges(request: Request) -> dict[str, Any]:
    bridge_detection = bridge_detection_service(request)
    bridges = [canonical_bridge_payload(request, "codex"), canonical_bridge_payload(request, "agy")]
    try:
        bridges.append(bridge_detection.detect_provider("claude_code_cli_bridge").to_dict())
    except ValueError:
        pass
    return {"bridges": [item for item in bridges if item is not None]}


@router.post(
    "/bridges/refresh",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def refresh_bridges(request: Request) -> dict[str, Any]:
    bridge_detection = bridge_detection_service(request)
    bridges = [canonical_bridge_payload(request, "codex"), canonical_bridge_payload(request, "agy")]
    try:
        bridges.append(bridge_detection.detect_provider("claude_code_cli_bridge").to_dict())
    except ValueError:
        pass
    return {"bridges": [item for item in bridges if item is not None]}


@router.get(
    "/bridges/antigravity/sandbox-status",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def antigravity_sandbox_status(request: Request) -> dict[str, Any]:
    runner = bridge_agy_runner(request)
    execution_router = agy_execution_router(request)
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
    runner = bridge_agy_runner(request)
    generic_registry = required_state(request, "generic_bridge_registry", "generic bridge registry")
    agy_adapter = required_state(request, "agy_generic_provider", "AGY generic provider")
    execution_router = agy_execution_router(request)
    generic_api_flags = required_state(request, "generic_run_feature_flags", "generic run policy")
    restore = bridge_rollback_restore_apply_service(request)
    apply = bridge_patch_apply_service(request)
    restore_enabled = restore.is_enabled()
    patch_apply_feature_flag = apply.patch_apply_feature_flag_enabled()
    rollback_restore_feature_flag = apply.rollback_restore_feature_flag_enabled()
    patch_apply_enabled = patch_apply_feature_flag and rollback_restore_feature_flag
    codex_record = connection_registry(request).refresh_status(connection_registry(request).CODEX_ID)
    codex_status = CodexAlignedStatus(
        codex_installed=codex_record.detected,
        codex_version=codex_record.version,
        auth_status=("signed_in" if codex_record.authenticated else "signed_out" if codex_record.auth_state.value == "signed_out" else "unknown"),
        oauth_bridge_ready=codex_record.connection_ready,
    )
    codex_provider = required_state(request, "codex_app_server_provider", "Codex sandbox provider")
    codex_semantics = codex_state_semantics(
        codex_status,
        model_router_enabled=os.getenv("FORGEX_ENABLE_CODEX_PROVIDER", "").strip() == "1",
        sandbox_execution_enabled=bool(getattr(codex_provider, "execution_enabled", False)),
    )
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
        **codex_semantics,
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
    execution_router = agy_execution_router(request)
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
    execution_router = agy_execution_router(request)
    return {"runs": [run.to_dict() for run in execution_router.list_runs()]}


@router.get(
    "/bridges/runs/{run_id}",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def bridge_run_detail(run_id: str, request: Request) -> dict[str, Any]:
    execution_router = agy_execution_router(request)
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
    execution_router = agy_execution_router(request)
    try:
        run = await execution_router.cancel_run(run_id)
    except AntigravityRunnerError as exc:
        raise APIError(404, exc.code, str(exc), exc.details) from exc
    return {"run": run.to_dict()}
