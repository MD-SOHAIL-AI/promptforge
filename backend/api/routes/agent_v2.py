"""Versioned aliases for the chat-first AgentOrchestrator API."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from ..errors import APIError

from . import agent_runtime
from .agent_runtime import (
    AgentRuntimeFlashConfirmRequest,
    AgentSessionCreateRequest,
    AgentSessionMessageRequest,
)


router = APIRouter(prefix="/api/v2/agents", tags=["agent-orchestrator-v2"])


class MemoryInvalidateRequest(BaseModel):
    source_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(default="user_correction", min_length=1, max_length=256)


@router.get("/providers")
async def providers(request: Request):
    return await agent_runtime.agent_runtime_providers(request)


@router.get("/sessions")
async def sessions(request: Request, project_id: str | None = Query(default=None)):
    return await agent_runtime.list_agent_sessions(request, project_id)


@router.post("/sessions", status_code=201)
async def create_session(body: AgentSessionCreateRequest, request: Request):
    return await agent_runtime.create_agent_session(body, request)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    return await agent_runtime.get_agent_session(session_id, request)


@router.post("/sessions/{session_id}/messages", status_code=202)
async def message(session_id: str, body: AgentSessionMessageRequest, request: Request):
    return await agent_runtime.append_agent_session_message(session_id, body, request)


@router.post("/sessions/{session_id}/cancel")
async def cancel_session(session_id: str, request: Request):
    return await agent_runtime.cancel_agent_session(session_id, request)


@router.get("/sessions/{session_id}/events")
async def session_events(session_id: str, request: Request, after_sequence: int = Query(default=0, ge=0)):
    return await agent_runtime.agent_session_events(session_id, request, after_sequence)


@router.get("/runs/{run_id}")
async def run(run_id: str, request: Request):
    return await agent_runtime.get_agent_runtime_run(run_id, request)


@router.get("/runs/{run_id}/graph")
async def graph(run_id: str, request: Request):
    return await agent_runtime.agent_runtime_graph(run_id, request)


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, after_sequence: int = Query(default=0, ge=0)):
    return await agent_runtime.agent_runtime_events(run_id, request, after_sequence)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request):
    return await agent_runtime.cancel_agent_runtime_run(run_id, request)


@router.post("/runs/{run_id}/confirm-flash", status_code=202)
async def confirm_flash(run_id: str, body: AgentRuntimeFlashConfirmRequest, request: Request):
    return await agent_runtime.confirm_agent_runtime_flash(run_id, body, request)


@router.get("/projects/{project_id}/memory")
async def project_memory(project_id: str, request: Request):
    orchestrator = getattr(request.app.state, "agent_orchestrator", None)
    if orchestrator is None:
        raise APIError(404, "AGENT_MEMORY_UNAVAILABLE", "Project memory is available only in the v2 orchestrator.")
    metadata = await agent_runtime.resolve_project_from_request(request, project_id)
    revision = orchestrator.change_service.inspect_workspace(getattr(metadata, "project_path")).revision
    return {
        "workspace_revision": revision,
        "memories": list(orchestrator.memory.active(project_id, current_workspace_revision=revision)),
    }


@router.post("/projects/{project_id}/memory/invalidate")
async def invalidate_project_memory(project_id: str, body: MemoryInvalidateRequest, request: Request):
    agent_runtime._require_local_request(request)
    orchestrator = getattr(request.app.state, "agent_orchestrator", None)
    if orchestrator is None:
        raise APIError(404, "AGENT_MEMORY_UNAVAILABLE", "Project memory is available only in the v2 orchestrator.")
    invalidated = orchestrator.memory.invalidate_source(project_id, body.source_id, reason=body.reason)
    return {"invalidated_memory_ids": list(invalidated), "count": len(invalidated)}
