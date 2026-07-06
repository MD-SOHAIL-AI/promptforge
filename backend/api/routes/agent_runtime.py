"""Feature-gated product API for the ForgeX-owned agent runtime."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...agent_runtime.unified_agent_service import UnifiedAgentService
from ...bridges.generic.errors import BridgeDomainError
from ...bridges.codex_login import CodexLoginService
from ...bridges.codex_oauth_smoke import CodexOAuthSmokeService
from ...bridges.agy_scratch_project_import import AGYScratchImportError, AGYScratchProjectImportService
from ...bridges.agy_assisted_runner import AGYAssistedRunner
from ..dependencies import required_state, resolve_project_from_request
from ..errors import APIError


router = APIRouter(prefix="/agent-runtime", tags=["agent-runtime"])


class AgentRuntimeStartRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=16_384)
    provider_id: str = Field(default="fake_planner", min_length=1, max_length=128)
    timeout_seconds: int = Field(default=300, ge=10, le=900)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=127)


class AGYScratchImportRequest(BaseModel):
    source_path: str = Field(min_length=1, max_length=2048)


class AGYAssistedRunRequest(BaseModel):
    template: Literal["esp32-platformio-blink"]
    prompt: Literal["Create an ESP32 blink project"] = "Create an ESP32 blink project"


class CodexLoginLaunchRequest(BaseModel):
    confirm_launch_codex_login: bool = False


class CodexOAuthSmokeRequest(BaseModel):
    confirm_real_codex: bool = False


class AgentApprovalRequest(BaseModel):
    decision: Literal["approve_once", "approve_session", "decline", "cancel"]


def _service(request: Request) -> UnifiedAgentService:
    value = required_state(request, "unified_agent_service", "unified agent runtime")
    assert isinstance(value, UnifiedAgentService)
    return value


def _codex_login_service(request: Request) -> CodexLoginService:
    value = required_state(request, "codex_login_service", "Codex login service")
    assert isinstance(value, CodexLoginService)
    return value


def _codex_smoke_service(request: Request) -> CodexOAuthSmokeService:
    value = required_state(request, "codex_oauth_smoke_service", "Codex OAuth smoke service")
    assert isinstance(value, CodexOAuthSmokeService)
    return value


@router.get("/providers")
async def agent_runtime_providers(request: Request) -> dict[str, object]:
    service = _service(request)
    return {
        "enabled": service.enabled,
        "providers": list(await service.provider_statuses()),
    }


@router.get("/providers/codex-oauth/status")
def codex_oauth_status(request: Request) -> dict[str, object]:
    payload = _codex_login_service(request).status().to_safe_dict()
    payload["sandbox_smoke_enabled"] = _codex_smoke_service(request).feature_enabled
    return payload


@router.get("/providers/codex-oauth/status-diagnostics")
def codex_oauth_status_diagnostics(request: Request) -> dict[str, object]:
    return _codex_login_service(request).status_diagnostics()


@router.post("/providers/codex-oauth/login/launch")
def launch_codex_oauth_login(
    body: CodexLoginLaunchRequest,
    request: Request,
) -> dict[str, object]:
    return _codex_login_service(request).launch(
        confirm_launch_codex_login=body.confirm_launch_codex_login
    ).to_safe_dict()


@router.post("/providers/codex-oauth/standalone-smoke")
def run_codex_oauth_smoke(body: CodexOAuthSmokeRequest, request: Request) -> dict[str, object]:
    return _codex_smoke_service(request).run(
        confirm_real_codex=body.confirm_real_codex
    ).to_safe_dict()


@router.post("/agy-scratch-import")
def import_agy_scratch_project(body: AGYScratchImportRequest, request: Request) -> dict[str, object]:
    if os.getenv("FORGEX_ENABLE_AGY_SCRATCH_IMPORT", "").strip() != "1":
        raise APIError(403, "AGY_SCRATCH_IMPORT_DISABLED", "AGY scratch import is disabled.")
    value = required_state(request, "agy_scratch_import_service", "AGY scratch import service")
    assert isinstance(value, AGYScratchProjectImportService)
    try:
        result = value.import_project(body.source_path)
    except AGYScratchImportError as exc:
        raise APIError(422, exc.classification, "The selected AGY scratch project was rejected safely.", exc.to_result().to_safe_dict()) from exc
    return result.to_safe_dict()


@router.post("/agy-assisted-runs")
def start_agy_assisted_run(body: AGYAssistedRunRequest, request: Request) -> dict[str, object]:
    if os.getenv("FORGEX_ENABLE_AGY_ASSISTED_RUNNER", "").strip() != "1" or os.getenv("FORGEX_ENABLE_AGY_SCRATCH_IMPORT", "").strip() != "1":
        raise APIError(403, "AGY_ASSISTED_RUNNER_DISABLED", "AGY assisted scratch generation is disabled.")
    value = required_state(request, "agy_assisted_runner", "AGY assisted runner")
    assert isinstance(value, AGYAssistedRunner)
    result = value.run(body.template)
    return result.to_safe_dict()


@router.post("/runs", status_code=202)
async def start_agent_runtime_run(body: AgentRuntimeStartRequest, request: Request) -> dict[str, object]:
    _require_local_request(request)
    service = _service(request)
    if not service.enabled:
        raise APIError(403, "AGENT_RUNTIME_DISABLED", "The product agent runtime is disabled.")
    metadata = await resolve_project_from_request(request, body.project_id)
    try:
        run = await service.start_run(
            project_id=body.project_id,
            active_workspace_root=str(getattr(metadata, "project_path")),
            instruction=body.instruction,
            provider_id=body.provider_id,
            timeout_seconds=body.timeout_seconds,
            idempotency_key=body.idempotency_key,
        )
    except PermissionError as exc:
        raise APIError(403, "AGENT_RUNTIME_DISABLED", "The product agent runtime is disabled.") from exc
    except (ValueError, BridgeDomainError) as exc:
        code = str(exc) if str(exc).startswith(("PRODUCT_PROVIDER_", "API_")) else "AGENT_RUNTIME_REQUEST_INVALID"
        raise APIError(422, code, "The product agent request was rejected safely.") from exc
    return {"run": run}


@router.get("/runs/{run_id}")
async def get_agent_runtime_run(run_id: str, request: Request) -> dict[str, object]:
    try:
        run = _service(request).get_run(run_id)
    except (KeyError, BridgeDomainError) as exc:
        raise APIError(404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found.") from exc
    return {"run": run}


@router.post("/runs/{run_id}/cancel")
async def cancel_agent_runtime_run(run_id: str, request: Request) -> dict[str, object]:
    try:
        run = await _service(request).cancel(run_id)
    except (KeyError, BridgeDomainError) as exc:
        raise APIError(404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found.") from exc
    return {"run": run}


@router.post("/runs/{run_id}/approvals/{approval_id}")
async def resolve_agent_runtime_approval(
    run_id: str,
    approval_id: str,
    body: AgentApprovalRequest,
    request: Request,
) -> dict[str, object]:
    try:
        approval = _service(request).resolve_approval(
            run_id=run_id,
            approval_id=approval_id,
            decision=body.decision,
        )
    except (KeyError, BridgeDomainError) as exc:
        raise APIError(404, "AGENT_APPROVAL_NOT_FOUND", "Agent approval request was not found.") from exc
    except ValueError as exc:
        raise APIError(422, "AGENT_APPROVAL_DECISION_INVALID", "Agent approval decision is invalid.") from exc
    return {"approval": approval}


@router.get("/runs/{run_id}/review")
async def get_agent_runtime_review(run_id: str, request: Request) -> dict[str, object]:
    try:
        run = _service(request).get_run(run_id)
    except (KeyError, BridgeDomainError) as exc:
        raise APIError(404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found.") from exc
    review_id = run.get("review_id")
    if not isinstance(review_id, str):
        raise APIError(409, "AGENT_RUNTIME_REVIEW_NOT_READY", "A validated review is not available.")
    review_service = required_state(request, "bridge_diff_service", "bridge review service")
    review = review_service.get_review(review_id)
    return {"review": review.to_dict()}


@router.get("/runs/{run_id}/events")
async def agent_runtime_events(
    run_id: str,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
) -> StreamingResponse:
    service = _service(request)
    try:
        service.get_run(run_id)
    except (KeyError, BridgeDomainError) as exc:
        raise APIError(404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found.") from exc

    async def stream() -> AsyncIterator[str]:
        sequence = after_sequence
        idle_ticks = 0
        while True:
            emitted = False
            for event in service.events(run_id, after_sequence=sequence):
                sequence = int(event["sequence"])
                emitted = True
                yield f"data: {json.dumps(event, ensure_ascii=True, sort_keys=True)}\n\n"
            idle_ticks = 0 if emitted else idle_ticks + 1
            run = service.get_run(run_id)
            if str(run.get("status")) in {"completed", "failed", "cancelled", "blocked", "timed_out", "interrupted"}:
                return
            if await request.is_disconnected():
                return
            if idle_ticks >= 50:
                idle_ticks = 0
                yield ": forgex-agent-heartbeat\n\n"
            await asyncio.sleep(0.1)

    return StreamingResponse(stream(), media_type="text/event-stream")


def _require_local_request(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin or origin == "null" or origin.startswith("file://"):
        return
    if origin.startswith("http://127.0.0.1:") or origin.startswith("http://localhost:") or origin.startswith("http://[::1]:"):
        return
    raise APIError(403, "REMOTE_ORIGIN_REJECTED", "Agent execution is restricted to the local desktop application.")
