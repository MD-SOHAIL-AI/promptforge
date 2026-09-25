"""Local desktop API for the single ForgeX product-agent runtime."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ...agent_runtime.activity import AgentActivity, AgentActivityBroker
from ...agent_runtime.product_agent_service import (
    AUTONOMY_BUILD_ONLY,
    AUTONOMY_BUILD_THEN_CONFIRM_FLASH,
    AUTONOMY_PLAN_ONLY,
    AUTONOMY_SIMULATE,
    AUTONOMY_READ_ONLY,
    AUTONOMY_STAGED_CHANGES,
    ProductAgentService,
)
from ...agent_runtime.session_store import AgentSession, AgentSessionStore
from ...agent_runtime.slash_commands import parse_agent_slash_command
from ...agent_runtime.turn_router import (
    AgentTurnContext,
    AgentTurnDecision,
    AgentTurnIntent,
    AgentTurnRouter,
    approved_plan_execution_decision,
)
from ...observability.diagnostics import emit_diagnostic_event
from ...services.serial_service import SerialConfiguration
from ...services.llm_service import LLMError
from ...harness import HarnessClient, HarnessClientError
from ..dependencies import required_state, resolve_project_from_request, services
from ..errors import APIError
from ..local_security import require_local_http_request
from .execute import _active_workspace, _apply_execute_overrides

router = APIRouter(prefix="/agent-runtime", tags=["agent-runtime"])
STABLE_STREAM_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "blocked",
    "timed_out",
    "awaiting_flash_confirmation",
    "paused_model_unavailable",
}


class AgentRuntimeStartRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=16_384)
    provider_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    autonomy: str = Field(default="staged_changes", min_length=1, max_length=64)
    board_port: str | None = Field(default=None, min_length=1, max_length=256)
    board_type: str | None = Field(default=None, min_length=1, max_length=64)
    environment: str | None = Field(default=None, min_length=1, max_length=128)
    start_monitor_after_flash: bool = False


class AgentRuntimeSteerRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8_000)


class AgentRuntimeQueueEditRequest(BaseModel):
    content: str = Field(min_length=1, max_length=8_000)


class AgentRuntimeFlashConfirmRequest(BaseModel):
    port: str | None = Field(default=None, min_length=1, max_length=256)
    board_type: str | None = Field(default=None, min_length=1, max_length=64)
    environment: str | None = Field(default=None, min_length=1, max_length=128)
    start_monitor_after_flash: bool | None = None


class AgentSessionCreateRequest(BaseModel):
    kind: Literal["agent", "chat"] = "agent"
    project_id: str | None = Field(default=None, min_length=1, max_length=128)
    title: str | None = Field(default=None, min_length=1, max_length=128)


class AgentSessionMessageRequest(BaseModel):
    model_id: str | None = Field(default=None, max_length=128)
    context_project_id: str | None = Field(default=None, max_length=128)
    context_paths: list[str] = Field(default_factory=list, max_length=12)
    retry: bool = False
    content: str = Field(min_length=1, max_length=16_384)
    provider_id: str | None = Field(default=None, min_length=1, max_length=128)
    autonomy: str = Field(default="auto", min_length=1, max_length=64)
    board_port: str | None = Field(default=None, min_length=1, max_length=256)
    board_type: str | None = Field(default=None, min_length=1, max_length=64)
    environment: str | None = Field(default=None, min_length=1, max_length=128)
    start_monitor_after_flash: bool = False
    approved_plan_id: str | None = Field(default=None, min_length=1, max_length=160)
    approved_plan_revision: int | None = Field(default=None, ge=1, le=10_000)
    approved_plan_hash: str | None = Field(default=None, min_length=64, max_length=64)


class SessionSettingsRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    provider_id: str | None = Field(default=None, max_length=128)
    model_id: str | None = Field(default=None, max_length=128)


class MemoryInvalidateRequest(BaseModel):
    source_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(default="user_correction", min_length=1, max_length=256)


@router.get("/projects/{project_id}/memory")
async def project_memory(project_id: str, request: Request):
    orchestrator = services(request).agent_orchestrator
    if orchestrator is None:
        raise APIError(404, "AGENT_MEMORY_UNAVAILABLE", "Project memory is unavailable.")
    metadata = await resolve_project_from_request(request, project_id)
    revision = orchestrator.change_service.inspect_workspace(getattr(metadata, "project_path")).revision
    return {
        "workspace_revision": revision,
        "memories": list(orchestrator.memory.active(project_id, current_workspace_revision=revision)),
    }


@router.post("/projects/{project_id}/memory/invalidate")
async def invalidate_project_memory(project_id: str, body: MemoryInvalidateRequest, request: Request):
    _require_local_request(request)
    orchestrator = services(request).agent_orchestrator
    if orchestrator is None:
        raise APIError(404, "AGENT_MEMORY_UNAVAILABLE", "Project memory is unavailable.")
    invalidated = orchestrator.memory.invalidate_source(project_id, body.source_id, reason=body.reason)
    return {"invalidated_memory_ids": list(invalidated), "count": len(invalidated)}


@router.patch("/sessions/{session_id}")
async def configure_session(
    session_id: str, body: SessionSettingsRequest, request: Request
):
    _require_local_request(request)
    try:
        session = _session_store(request).configure(session_id, **body.model_dump())
    except KeyError as exc:
        raise APIError(
            404, "AGENT_SESSION_NOT_FOUND", "Conversation not found."
        ) from exc
    return {"session": session.to_dict()}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    _require_local_request(request)
    store = _session_store(request)
    try:
        session = store.get(session_id)
        if session.kind == "chat":
            await services(request).chat_service.cancel(session_id)
        elif session.active_run_id:
            run = _active_session_run(_service(request), session.active_run_id)
            if _active_operation(run):
                raise APIError(
                    409,
                    "AGENT_SESSION_ACTIVE",
                    "Stop this task before deleting its conversation.",
                )
        store.delete(session_id)
    except KeyError as exc:
        raise APIError(
            404, "AGENT_SESSION_NOT_FOUND", "Conversation not found."
        ) from exc
    return {"deleted": True}


def _service(request: Request) -> ProductAgentService:
    value = required_state(request, "product_agent_service", "product agent runtime")
    assert isinstance(value, ProductAgentService)
    return value


def _session_store(request: Request) -> AgentSessionStore:
    value = required_state(request, "agent_session_store", "agent session store")
    assert isinstance(value, AgentSessionStore)
    return value


def _activity_broker(request: Request) -> AgentActivityBroker:
    value = required_state(request, "agent_activity_broker", "agent activity feed")
    assert isinstance(value, AgentActivityBroker)
    return value


@router.get("/providers")
async def agent_runtime_providers(request: Request) -> dict[str, object]:
    service = _service(request)
    return {
        "enabled": service.enabled,
        "providers": list(service.provider_registry.safe_statuses()),
    }


@router.get("/sessions")
async def list_agent_sessions(
    request: Request,
    project_id: str | None = Query(default=None),
    kind: Literal["agent", "chat"] = "agent",
) -> dict[str, object]:
    sessions = _session_store(request).list(project_id=project_id, kind=kind)
    return {
        "sessions": [session.to_dict(include_messages=False) for session in sessions],
        "count": len(sessions),
    }


@router.post("/sessions", status_code=201)
async def create_agent_session(
    body: AgentSessionCreateRequest, request: Request
) -> dict[str, object]:
    _require_local_request(request)
    if body.kind == "agent" and not body.project_id:
        raise APIError(422, "AGENT_PROJECT_REQUIRED", "Choose a project for Agent.")
    if body.project_id:
        await resolve_project_from_request(request, body.project_id)
    session = _session_store(request).create(
        project_id=body.project_id, title=body.title, kind=body.kind
    )
    emit_diagnostic_event(
        component="agent",
        event="agent.session.created",
        session_id=session.session_id,
        project_id=session.project_id,
        request_id=request.headers.get("X-Request-ID"),
        status="created",
    )
    await _mirror_harness(
        request,
        "sessions.create",
        {
            "sessionId": session.session_id,
            "projectId": session.project_id,
            "title": session.title,
        },
    )
    return {"session": session.to_dict(include_messages=True)}


@router.get("/sessions/{session_id}")
async def get_agent_session(session_id: str, request: Request) -> dict[str, object]:
    try:
        session = _session_store(request).get(session_id)
    except KeyError as exc:
        raise APIError(
            404, "AGENT_SESSION_NOT_FOUND", "Agent session was not found."
        ) from exc
    return {"session": session.to_dict(include_messages=True)}


@router.get("/sessions/{session_id}/activity")
async def agent_session_activity_events(
    session_id: str, request: Request, after_sequence: int = Query(default=0, ge=0)
) -> StreamingResponse:
    try:
        _session_store(request).get(session_id)
    except KeyError as exc:
        raise APIError(
            404, "AGENT_SESSION_NOT_FOUND", "Agent session was not found."
        ) from exc
    broker = _activity_broker(request)

    async def stream() -> AsyncIterator[str]:
        emit_diagnostic_event(
            component="backend",
            event="sse.connected",
            session_id=session_id,
            stream="agent_activity",
        )
        sequence = max(
            after_sequence,
            (
                int(request.headers.get("last-event-id", "0"))
                if request.headers.get("last-event-id", "0").isdigit()
                else 0
            ),
        )
        idle_ticks = 0
        try:
            while True:
                emitted = False
                for event in broker.recent(session_id, after_sequence=sequence):
                    sequence = int(event["sequence"])
                    emitted = True
                    yield f"id: {sequence}\ndata: {json.dumps(event, ensure_ascii=True, sort_keys=True)}\n\n"
                idle_ticks = 0 if emitted else idle_ticks + 1
                if await request.is_disconnected():
                    return
                if idle_ticks >= 50:
                    idle_ticks = 0
                    yield ": forgex-agent-activity-heartbeat\n\n"
                await asyncio.sleep(0.1)
        finally:
            emit_diagnostic_event(
                component="backend",
                event="sse.disconnected",
                session_id=session_id,
                stream="agent_activity",
                last_sequence=sequence,
            )

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/sessions/{session_id}/messages", status_code=202)
async def append_agent_session_message(
    session_id: str, body: AgentSessionMessageRequest, request: Request
) -> dict[str, object]:
    _require_local_request(request)
    store = _session_store(request)
    try:
        session = store.get(session_id)
    except KeyError as exc:
        raise APIError(
            404, "AGENT_SESSION_NOT_FOUND", "Agent session was not found."
        ) from exc
    if session.kind == "chat":
        context = ""
        if body.context_paths and not body.context_project_id:
            raise APIError(
                422,
                "CHAT_CONTEXT_PROJECT_REQUIRED",
                "Choose a project for these files.",
            )
        if body.context_project_id:
            metadata = await resolve_project_from_request(
                request, body.context_project_id
            )
            from ...agent_runtime.tool_policy import product_agent_policy

            policy = product_agent_policy()
            root = Path(str(metadata.project_path)).resolve(strict=True)
            context = f"Project: {metadata.project_name}\n"
            for relative in body.context_paths:
                try:
                    path = policy.resolve_path(root, relative, must_exist=True)
                    if path.stat().st_size > 128000:
                        raise ValueError("context_file_too_large")
                    from backend.security import has_high_confidence_secret

                    contents = path.read_text(encoding="utf-8")
                    if has_high_confidence_secret(contents):
                        raise ValueError("secret_content_forbidden")
                    context += f"\nFile: {relative}\n" + contents[:8000]
                except (ValueError, OSError, UnicodeError) as exc:
                    raise APIError(
                        422,
                        "CHAT_CONTEXT_INVALID",
                        "A selected context file cannot be read safely.",
                    ) from exc
                if len(context) > 16000:
                    raise APIError(
                        422,
                        "CHAT_CONTEXT_LIMIT",
                        "Select fewer or smaller context files.",
                    )
        try:
            updated = services(request).chat_service.start(
                session_id,
                body.content,
                provider_id=body.provider_id,
                model_id=body.model_id,
                context=context,
                retry=body.retry,
            )
        except ValueError as exc:
            raise APIError(409, str(exc), "The chat turn could not start.") from exc
        return {
            "session": updated.to_dict(),
            "run": None,
            "intent": "chat",
            "activity_events": [],
        }
    await resolve_project_from_request(request, session.project_id)
    approved_plan = None
    supplied_plan_fields = (
        body.approved_plan_id,
        body.approved_plan_revision,
        body.approved_plan_hash,
    )
    if any(value is not None for value in supplied_plan_fields):
        if not all(value is not None for value in supplied_plan_fields):
            raise APIError(
                422,
                "AGENT_PLAN_BINDING_INCOMPLETE",
                "Plan execution requires plan id, revision, and hash together.",
            )
        try:
            approved_plan = store.approve_plan(
                session_id,
                plan_id=str(body.approved_plan_id),
                revision=int(body.approved_plan_revision),
                plan_hash=str(body.approved_plan_hash),
            )
            session = store.get(session_id)
        except ValueError as exc:
            raise APIError(
                409,
                str(exc),
                "The approved plan no longer matches the current session plan.",
            ) from exc
    try:
        slash_command = parse_agent_slash_command(body.content)
    except ValueError as exc:
        raise APIError(
            422,
            str(exc),
            "That Agent slash command is unknown or incomplete. Open /help to see valid commands.",
        ) from exc
    effective_content = (
        slash_command.instruction if slash_command is not None else body.content
    )
    requested_autonomy = (
        slash_command.autonomy
        if slash_command is not None and slash_command.autonomy is not None
        else body.autonomy
    )
    try:
        session = store.append_message(session_id, role="user", content=body.content)
    except ValueError as exc:
        raise APIError(422, str(exc), "Agent message was rejected safely.") from exc
    await _mirror_harness(
        request,
        "sessions.append",
        {
            "sessionId": session_id,
            "type": "message/user",
            "payload": {
                "messageId": session.messages[-1].message_id,
                "content": body.content,
            },
        },
    )

    broker = _activity_broker(request)
    activity_start = broker.last_sequence(session_id)
    message_id = session.messages[-1].message_id if session.messages else None
    service = _service(request)
    metadata = await resolve_project_from_request(request, session.project_id)
    active_run = _active_session_run(service, session.active_run_id)
    continuation = _failed_run_continuation(session, active_run, effective_content)
    if continuation is not None:
        effective_content = continuation
    decision = AgentTurnRouter().route(
        effective_content, _turn_context(metadata, active_run)
    )
    if approved_plan is not None:
        # Plan execution is an explicit capability escalation authorised by the
        # exact plan id/revision/hash supplied above. Do not rely on the generic
        # wording of the Execute button being re-classified as an edit request.
        decision = approved_plan_execution_decision()

    if decision.requires_write and requested_autonomy == AUTONOMY_READ_ONLY:
        content = "This request needs file edits, but Ask mode is read-only. Switch to Agent mode to create or change files."
        session = store.append_message(
            session_id,
            role="assistant",
            content=content,
            metadata=_assistant_metadata(decision, run_id=None),
        )
        if not _active_operation(active_run):
            session = store.update_run(session_id, run_id=None, status="idle")
        return _session_message_payload(
            session, decision, run=None, broker=broker, after_sequence=activity_start
        )

    # Codex-style mid-turn steering: action guidance joins the current V3 turn
    # rather than creating a competing run. Chat/inspect/cancel/flash remain
    # independently routed so user control is preserved.
    if _steerable_operation(active_run) and decision.intent in {
        AgentTurnIntent.EDIT,
        AgentTurnIntent.GENERATE,
        AgentTurnIntent.BUILD,
        AgentTurnIntent.SIMULATE,
        AgentTurnIntent.REPAIR,
    }:
        try:
            steered = service.steer(active_run.run_id, effective_content)
        except ValueError as exc:
            raise APIError(
                409, str(exc), "The active Forge run could not accept steering."
            ) from exc
        broker.emit(
            session_id,
            AgentActivity.PLANNING,
            run_id=steered.run_id,
            message_id=message_id,
        )
        broker.complete(session_id, run_id=steered.run_id, message_id=message_id)
        content = "Guidance added to the active Forge run. It will be applied at the next safe tool boundary."
        session = store.append_message(
            session_id,
            role="assistant",
            content=content,
            run_id=steered.run_id,
            metadata=_assistant_metadata(
                decision, run_id=steered.run_id, status=steered.status
            ),
        )
        return _session_message_payload(
            session, decision, run=steered, broker=broker, after_sequence=activity_start
        )

    if decision.intent in {AgentTurnIntent.CHAT, AgentTurnIntent.UNKNOWN}:
        local = _local_session_response(effective_content, session)
        if local is None:
            local = _runtime_fact_response(effective_content, service, body)
        if local is not None:
            session = store.append_message(
                session_id,
                role="assistant",
                content=local,
                metadata=_assistant_metadata(decision, run_id=None),
            )
            if not _active_operation(active_run):
                session = store.update_run(session_id, run_id=None, status="idle")
            return _session_message_payload(
                session,
                decision,
                run=None,
                broker=broker,
                after_sequence=activity_start,
            )
        for activity in _chat_activities(effective_content, decision):
            broker.emit(session_id, activity, message_id=message_id)

    if decision.intent is AgentTurnIntent.INSPECT:
        for activity in _inspect_activities(effective_content, active_run):
            broker.emit(session_id, activity, message_id=message_id)

    if decision.intent is AgentTurnIntent.CANCEL:
        broker.emit(session_id, AgentActivity.FINISHING, message_id=message_id)
        if not session.active_run_id:
            content = "There is no active Forge run to cancel."
            broker.complete(session_id, message_id=message_id)
            session = store.append_message(
                session_id,
                role="assistant",
                content=content,
                metadata=_assistant_metadata(decision, run_id=None),
            )
            session = store.update_run(session_id, run_id=None, status="idle")
            return _session_message_payload(
                session,
                decision,
                run=None,
                broker=broker,
                after_sequence=activity_start,
            )
        try:
            run = await service.cancel(session.active_run_id)
        except KeyError as exc:
            raise APIError(
                404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found."
            ) from exc
        broker.complete(session_id, run_id=run.run_id, message_id=message_id)
        session = store.update_run(session_id, run_id=run.run_id, status=run.status)
        session = store.append_message(
            session_id,
            role="assistant",
            content=_cancel_response(run),
            run_id=run.run_id,
            metadata=_assistant_metadata(
                decision, run_id=run.run_id, status=run.status
            ),
        )
        return _session_message_payload(
            session, decision, run=run, broker=broker, after_sequence=activity_start
        )

    if decision.intent is AgentTurnIntent.CONFIRM:
        broker.emit(
            session_id,
            AgentActivity.CONNECTING,
            run_id=session.active_run_id,
            message_id=message_id,
        )
        if not session.active_run_id:
            content = "There is nothing waiting for confirmation."
            broker.complete(session_id, message_id=message_id)
            session = store.append_message(
                session_id,
                role="assistant",
                content=content,
                metadata=_assistant_metadata(decision, run_id=None),
            )
            session = store.update_run(session_id, run_id=None, status="idle")
            return _session_message_payload(
                session,
                decision,
                run=None,
                broker=broker,
                after_sequence=activity_start,
            )
        try:
            run = await service.confirm_flash(
                session.active_run_id,
                port=body.board_port,
                board_type=body.board_type,
                environment=body.environment,
                start_monitor_after_flash=body.start_monitor_after_flash,
            )
        except KeyError as exc:
            raise APIError(
                404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found."
            ) from exc
        except ValueError as exc:
            content = "There is no flash confirmation ready for this session."
            broker.complete(
                session_id, run_id=session.active_run_id, message_id=message_id
            )
            session = store.append_message(
                session_id,
                role="assistant",
                content=content,
                metadata=_assistant_metadata(decision, run_id=session.active_run_id),
            )
            return _session_message_payload(
                session,
                decision,
                run=active_run,
                broker=broker,
                after_sequence=activity_start,
            )
        session = store.update_run(session_id, run_id=run.run_id, status=run.status)
        session = store.append_message(
            session_id,
            role="assistant",
            content="Flash confirmed. Upload is starting.",
            run_id=run.run_id,
            metadata=_assistant_metadata(
                decision, run_id=run.run_id, status=run.status
            ),
        )
        return _session_message_payload(
            session, decision, run=run, broker=broker, after_sequence=activity_start
        )

    if decision.intent is AgentTurnIntent.FLASH:
        broker.emit(
            session_id,
            AgentActivity.PREPARING,
            run_id=getattr(active_run, "run_id", None),
            message_id=message_id,
        )
        if (
            active_run is not None
            and getattr(active_run, "status", None) == "completed"
        ):
            try:
                active_run = service.prepare_flash(
                    active_run.run_id,
                    port=body.board_port,
                    board_type=body.board_type,
                    environment=body.environment,
                    start_monitor_after_flash=body.start_monitor_after_flash,
                )
                session = store.update_run(
                    session_id, run_id=active_run.run_id, status=active_run.status
                )
            except ValueError:
                pass
        if (
            active_run is not None
            and getattr(active_run, "status", None) == "awaiting_flash_confirmation"
        ):
            broker.emit(
                session_id,
                AgentActivity.WAITING,
                run_id=active_run.run_id,
                message_id=message_id,
            )
            broker.complete(session_id, run_id=active_run.run_id, message_id=message_id)
            content = "The latest verified firmware is ready to flash. Say yes to confirm the one-time upload, or cancel to skip."
            session = store.append_message(
                session_id,
                role="assistant",
                content=content,
                run_id=active_run.run_id,
                metadata=_assistant_metadata(
                    decision, run_id=active_run.run_id, status=active_run.status
                ),
            )
            return _session_message_payload(
                session,
                decision,
                run=active_run,
                broker=broker,
                after_sequence=activity_start,
            )
        content = "There is no verified firmware artifact available to flash yet. Build the project successfully first."
        broker.complete(session_id, message_id=message_id)
        session = store.append_message(
            session_id,
            role="assistant",
            content=content,
            metadata=_assistant_metadata(decision, run_id=None),
        )
        if not _active_operation(active_run):
            session = store.update_run(session_id, run_id=None, status="idle")
        return _session_message_payload(
            session, decision, run=None, broker=broker, after_sequence=activity_start
        )

    if decision.intent is AgentTurnIntent.MONITOR:
        broker.emit(
            session_id,
            AgentActivity.CONNECTING,
            run_id=getattr(active_run, "run_id", None),
            message_id=message_id,
        )
        try:
            connection = await _start_agent_monitor(
                request, port=body.board_port or getattr(active_run, "flash_port", None)
            )
            content = f"Serial monitor connected on {connection['port'] or 'the detected device'} at {connection['baudrate']} baud."
        except Exception as exc:
            content = f"Serial monitor could not be started: {str(exc) or type(exc).__name__}."
        broker.complete(
            session_id,
            run_id=getattr(active_run, "run_id", None),
            message_id=message_id,
        )
        session = store.append_message(
            session_id,
            role="assistant",
            content=content,
            run_id=getattr(active_run, "run_id", None),
            metadata=_assistant_metadata(
                decision, run_id=getattr(active_run, "run_id", None)
            ),
        )
        return _session_message_payload(
            session,
            decision,
            run=active_run,
            broker=broker,
            after_sequence=activity_start,
        )

    if decision.intent is AgentTurnIntent.STOP_MONITOR:
        broker.emit(
            session_id,
            AgentActivity.FINISHING,
            run_id=getattr(active_run, "run_id", None),
            message_id=message_id,
        )
        await _stop_agent_monitor(request)
        broker.complete(
            session_id,
            run_id=getattr(active_run, "run_id", None),
            message_id=message_id,
        )
        session = store.append_message(
            session_id,
            role="assistant",
            content="Serial monitor stopped.",
            run_id=getattr(active_run, "run_id", None),
            metadata=_assistant_metadata(
                decision, run_id=getattr(active_run, "run_id", None)
            ),
        )
        return _session_message_payload(
            session,
            decision,
            run=active_run,
            broker=broker,
            after_sequence=activity_start,
        )

    for activity in _action_handoff_activities(decision):
        broker.emit(session_id, activity, message_id=message_id)
    autonomy = _autonomy_for_decision(
        decision, "auto" if approved_plan is not None else requested_autonomy
    )
    if slash_command is not None and slash_command.autonomy == AUTONOMY_PLAN_ONLY:
        autonomy = AUTONOMY_PLAN_ONLY
    contextual_instruction = _session_instruction(
        session,
        effective_content,
        metadata,
        decision=decision,
        active_run=active_run,
        approved_plan=approved_plan,
        force_read_only=(autonomy == AUTONOMY_READ_ONLY),
        force_plan_only=(autonomy == AUTONOMY_PLAN_ONLY),
    )
    run = await _start_product_run(
        request,
        project_id=session.project_id,
        instruction=contextual_instruction,
        provider_id=body.provider_id or session.provider_id,
        requested_model_id=body.model_id or (
            session.model_id if not body.provider_id or body.provider_id == session.provider_id else None
        ),
        autonomy=autonomy,
        board_port=body.board_port,
        board_type=body.board_type,
        environment=body.environment,
        start_monitor_after_flash=body.start_monitor_after_flash,
        session_id=session_id,
        explicit_edit_authorized=decision.requires_write,
        turn_decision=decision,
    )
    broker.complete(session_id, run_id=run.run_id, message_id=message_id)
    session = store.update_run(session_id, run_id=run.run_id, status=run.status)
    return _session_message_payload(
        session, decision, run=run, broker=broker, after_sequence=activity_start
    )


@router.post("/runs/{run_id}/steer")
async def steer_agent_runtime_run(
    run_id: str, body: AgentRuntimeSteerRequest, request: Request
) -> dict[str, object]:
    _require_local_request(request)
    try:
        run = _service(request).steer(run_id, body.content)
    except KeyError as exc:
        raise APIError(
            404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found."
        ) from exc
    except ValueError as exc:
        raise APIError(
            409, str(exc), "Agent runtime run is not accepting steering."
        ) from exc
    return {"run": run.to_safe_dict(), "steering_queued": True}


@router.get("/runs/{run_id}/queue")
async def list_agent_runtime_queue(run_id: str, request: Request) -> dict[str, object]:
    _require_local_request(request)
    try:
        items = _service(request).queued_guidance(run_id)
    except KeyError as exc:
        raise APIError(404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found.") from exc
    return {"items": list(items)}


@router.patch("/runs/{run_id}/queue/{item_id}")
async def edit_agent_runtime_queue(
    run_id: str, item_id: str, body: AgentRuntimeQueueEditRequest, request: Request
) -> dict[str, object]:
    _require_local_request(request)
    try:
        item = _service(request).edit_guidance(run_id, item_id, body.content)
    except KeyError as exc:
        raise APIError(404, "AGENT_QUEUE_ITEM_NOT_FOUND", "Queued guidance was not found.") from exc
    except ValueError as exc:
        raise APIError(409, str(exc), "Queued guidance can no longer be edited.") from exc
    return {"item": item}


@router.delete("/runs/{run_id}/queue/{item_id}")
async def remove_agent_runtime_queue(run_id: str, item_id: str, request: Request) -> dict[str, object]:
    _require_local_request(request)
    try:
        item = _service(request).remove_guidance(run_id, item_id)
    except KeyError as exc:
        raise APIError(404, "AGENT_QUEUE_ITEM_NOT_FOUND", "Queued guidance was not found.") from exc
    except ValueError as exc:
        raise APIError(409, str(exc), "Queued guidance can no longer be removed.") from exc
    return {"item": item}


@router.post("/sessions/{session_id}/cancel")
async def cancel_agent_session(session_id: str, request: Request) -> dict[str, object]:
    _require_local_request(request)
    store = _session_store(request)
    try:
        session = store.get(session_id)
    except KeyError as exc:
        raise APIError(
            404, "AGENT_SESSION_NOT_FOUND", "Agent session was not found."
        ) from exc
    if session.kind == "chat":
        await services(request).chat_service.cancel(session_id)
        return {
            "session": store.get(session_id).to_dict(),
            "run": None,
            "activity_events": [],
        }
    broker = _activity_broker(request)
    activity_start = broker.last_sequence(session_id)
    broker.emit(session_id, AgentActivity.FINISHING, run_id=session.active_run_id)
    if not session.active_run_id:
        broker.complete(session_id)
        return {
            "session": session.to_dict(include_messages=True),
            "run": None,
            "activity_events": list(
                broker.recent(session_id, after_sequence=activity_start)
            ),
        }
    try:
        run = await _service(request).cancel(session.active_run_id)
    except KeyError as exc:
        raise APIError(
            404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found."
        ) from exc
    broker.complete(session_id, run_id=run.run_id)
    session = store.update_run(session_id, run_id=run.run_id, status=run.status)
    return {
        "session": session.to_dict(include_messages=True),
        "run": run.to_safe_dict(),
        "activity_events": list(
            broker.recent(session_id, after_sequence=activity_start)
        ),
    }


@router.post("/runs", status_code=202)
async def start_agent_runtime_run(
    body: AgentRuntimeStartRequest, request: Request
) -> dict[str, object]:
    _require_local_request(request)
    decision = AgentTurnRouter().route(body.instruction)
    if body.autonomy == AUTONOMY_READ_ONLY and decision.requires_write:
        raise APIError(
            422, "AGENT_MODE_READ_ONLY",
            "This request needs file edits. Select an editing mode to create or change files.",
        )
    run = await _start_product_run(
        request,
        project_id=body.project_id,
        instruction=body.instruction,
        provider_id=body.provider_id,
        requested_model_id=body.model_id,
        autonomy=(
            _autonomy_for_decision(decision, body.autonomy)
            if body.autonomy == "auto" else body.autonomy
        ),
        board_port=body.board_port,
        board_type=body.board_type,
        environment=body.environment,
        start_monitor_after_flash=body.start_monitor_after_flash,
        turn_decision=decision if decision.starts_product_run else None,
    )
    return {"run": run.to_safe_dict()}


async def _start_product_run(
    request: Request,
    *,
    project_id: str,
    instruction: str,
    provider_id: str | None,
    autonomy: str,
    board_port: str | None,
    board_type: str | None,
    environment: str | None,
    start_monitor_after_flash: bool,
    session_id: str | None = None,
    explicit_edit_authorized: bool = False,
    turn_decision: AgentTurnDecision | None = None,
    requested_model_id: str | None = None,
):
    service = _service(request)
    if not service.enabled:
        raise APIError(
            403, "AGENT_RUNTIME_DISABLED", "The product agent runtime is disabled."
        )
    metadata = await resolve_project_from_request(request, project_id)
    active_workspace = _active_workspace(metadata)
    _apply_execute_overrides(
        active_workspace,
        selected_board=board_type,
        selected_framework="PlatformIO",
        generation_mode=(
            "modify_existing_project"
            if active_workspace.get("has_platformio_ini") is True
            else "generate_into_open_folder"
        ),
        workspace_root=str(getattr(metadata, "project_path")),
    )
    active_workspace["agent_autonomy"] = autonomy
    if turn_decision is not None:
        # This is trusted routing metadata produced by AgentTurnRouter.  The
        # embedded planner must not infer a conflicting action from the
        # contextual chat transcript that wraps the user's instruction.
        active_workspace.update(
            {
                "agent_turn_intent": turn_decision.intent.value,
                "agent_requested_actions": list(turn_decision.requested_actions),
                "agent_prohibited_actions": list(turn_decision.prohibited_actions),
                "agent_requires_write": turn_decision.requires_write,
                "agent_requires_build": turn_decision.requires_build,
            }
        )
    fallback = None
    # Validate every action route synchronously. Unknown, disabled, unconfigured,
    # or model-less providers must never be queued only to fail asynchronously.
    try:
        session_model = service.provider_registry.session_model(session_id) if provider_id is None else None
        selected_provider_id = (
            session_model[0] if session_model else
            provider_id or service.provider_registry.default_provider_id()
        )
        model_id = service.provider_registry.selected_model_id(selected_provider_id)
        route_metadata = service.provider_registry.workflow_route_metadata(
            selected_provider_id, fallback_provider_id=None,
            model_id=requested_model_id or (session_model[1] if session_model else None),
        )
        active_workspace.update(route_metadata)
        resolved_model = route_metadata.get("model_id")
        if isinstance(resolved_model, str) and resolved_model.strip():
            model_id = resolved_model.strip()
    except (ValueError, LLMError) as exc:
        code = (
            str(exc)
            if str(exc).startswith(("PRODUCT_PROVIDER_", "API_"))
            else "AGENT_RUNTIME_REQUEST_INVALID"
        )
        raise APIError(
            422, code, "The product agent request was rejected safely."
        ) from exc
    try:
        run = await service.start_run(
            project_id=project_id,
            active_workspace_root=str(getattr(metadata, "project_path")),
            instruction=instruction,
            provider_id=selected_provider_id,
            model_id=model_id,
            fallback_provider_id=fallback,
            autonomy=autonomy,
            active_workspace=active_workspace,
            board_port=board_port,
            board_type=board_type,
            environment=environment,
            start_monitor_after_flash=start_monitor_after_flash,
            session_id=session_id,
            explicit_edit_authorized=explicit_edit_authorized,
        )
    except PermissionError as exc:
        raise APIError(
            403, "AGENT_RUNTIME_DISABLED", "The product agent runtime is disabled."
        ) from exc
    except ValueError as exc:
        code = (
            str(exc)
            if str(exc).startswith(("PRODUCT_PROVIDER_", "API_"))
            else "AGENT_RUNTIME_REQUEST_INVALID"
        )
        raise APIError(
            422, code, "The product agent request was rejected safely."
        ) from exc
    if session_id:
        await _mirror_harness(
            request,
            "sessions.append",
            {
                "sessionId": session_id,
                "runId": run.run_id,
                "type": "run/started",
                "payload": {
                    "projectId": project_id,
                    "providerId": selected_provider_id,
                    "modelId": model_id,
                    "autonomy": autonomy,
                },
            },
        )
    return run


async def _mirror_harness(
    request: Request, method: str, params: dict[str, object]
) -> None:
    """Mirror compatibility events without making the shadow host authoritative."""

    client = services(request).harness_client
    if not isinstance(client, HarnessClient):
        return
    try:
        await client.call(method, params)
    except HarnessClientError as exc:
        emit_diagnostic_event(
            component="harness_bridge",
            event="harness.shadow_mirror.failed",
            status="failed",
            error_code=exc.code,
            retryable=exc.retryable,
        )


@router.post("/runs/{run_id}/confirm-flash", status_code=202)
async def confirm_agent_runtime_flash(
    run_id: str, body: AgentRuntimeFlashConfirmRequest, request: Request
) -> dict[str, object]:
    _require_local_request(request)
    service = _service(request)
    try:
        run = await service.confirm_flash(
            run_id,
            port=body.port,
            board_type=body.board_type,
            environment=body.environment,
            start_monitor_after_flash=body.start_monitor_after_flash,
        )
    except KeyError as exc:
        raise APIError(
            404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found."
        ) from exc
    except ValueError as exc:
        code = str(exc)
        status = (
            409 if code.endswith("_NOT_READY") or code.endswith("_UNAVAILABLE") else 422
        )
        raise APIError(status, code, "Flash confirmation was rejected safely.") from exc
    return {"run": run.to_safe_dict()}


@router.get("/runs/{run_id}")
async def get_agent_runtime_run(run_id: str, request: Request) -> dict[str, object]:
    try:
        run = _service(request).get_run(run_id)
    except KeyError as exc:
        raise APIError(
            404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found."
        ) from exc
    return {"run": run.to_safe_dict()}


@router.post("/runs/{run_id}/cancel")
async def cancel_agent_runtime_run(run_id: str, request: Request) -> dict[str, object]:
    try:
        run = await _service(request).cancel(run_id)
    except KeyError as exc:
        raise APIError(
            404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found."
        ) from exc
    return {"run": run.to_safe_dict()}


@router.post("/runs/{run_id}/retry", status_code=202)
async def retry_agent_runtime_run(run_id: str, request: Request) -> dict[str, object]:
    _require_local_request(request)
    service = _service(request)
    try:
        run = await service.retry(run_id)
    except KeyError as exc:
        raise APIError(
            404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found."
        ) from exc
    except ValueError as exc:
        code = str(exc) or "AGENT_RUNTIME_RUN_NOT_RECOVERABLE"
        raise APIError(
            409, code, "Only an interrupted, recoverable run can be retried."
        ) from exc

    store = _session_store(request)
    for session in store.list():
        if session.active_run_id == run_id:
            store.update_run(session.session_id, run_id=run.run_id, status=run.status)
            break
    return {"run": run.to_safe_dict()}


@router.post("/runs/{run_id}/apply-changes")
async def apply_agent_runtime_changes(
    run_id: str, request: Request
) -> dict[str, object]:
    _require_local_request(request)
    service = _service(request)
    try:
        run = service.apply_pending_changes(run_id)
    except KeyError as exc:
        raise APIError(
            404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found."
        ) from exc
    except ValueError as exc:
        code = (
            getattr(exc, "code", None)
            or str(exc)
            or "AGENT_RUNTIME_CHANGESET_APPLY_FAILED"
        )
        status = (
            409
            if "NOT_READY" in code or "NOT_PENDING" in code or "CONFLICT" in code
            else 422
        )
        raise APIError(
            status, code, "The staged changes could not be applied safely."
        ) from exc
    return {"run": run.to_safe_dict()}


@router.get("/runs/{run_id}/changes")
async def get_agent_runtime_changes(run_id: str, request: Request) -> dict[str, object]:
    try:
        run = _service(request).get_run(run_id)
    except KeyError as exc:
        raise APIError(
            404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found."
        ) from exc
    if not run.change_set_id:
        raise APIError(
            409,
            "AGENT_RUNTIME_CHANGESET_NOT_READY",
            "A ChangeSet is not available yet.",
        )
    change_service = required_state(request, "change_set_service", "ChangeSet service")
    try:
        change_set = change_service.get(run.change_set_id)
    except Exception as exc:
        raise APIError(
            404, "CHANGE_SET_NOT_FOUND", "The ChangeSet was not found."
        ) from exc
    return {"change_set": change_set.to_dict()}


@router.get("/runs/{run_id}/events")
async def agent_runtime_events(
    run_id: str, request: Request, after_sequence: int = Query(default=0, ge=0)
) -> StreamingResponse:
    service = _service(request)
    try:
        service.get_run(run_id)
    except KeyError as exc:
        raise APIError(
            404, "AGENT_RUNTIME_RUN_NOT_FOUND", "Agent runtime run was not found."
        ) from exc

    async def stream() -> AsyncIterator[str]:
        emit_diagnostic_event(
            component="backend",
            event="sse.connected",
            run_id=run_id,
            stream="agent_run",
        )
        sequence = max(
            after_sequence,
            (
                int(request.headers.get("last-event-id", "0"))
                if request.headers.get("last-event-id", "0").isdigit()
                else 0
            ),
        )
        transient_sequence = 0
        idle_ticks = 0
        try:
            while True:
                emitted = False
                for event in service.events(run_id, after_sequence=sequence):
                    sequence = int(event["sequence"])
                    emitted = True
                    yield f"id: {sequence}\ndata: {json.dumps(event, ensure_ascii=True, sort_keys=True)}\n\n"
                for event in service.transient_events(
                    run_id, after_transient_sequence=transient_sequence
                ):
                    transient_sequence = max(
                        transient_sequence, int(event.get("transient_sequence") or 0)
                    )
                    emitted = True
                    # Deliberately omit SSE id: reconnect cursors are durable session_seq only.
                    yield f"data: {json.dumps(event, ensure_ascii=True, sort_keys=True)}\n\n"
                idle_ticks = 0 if emitted else idle_ticks + 1
                run = service.get_run(run_id)
                if run.status in STABLE_STREAM_STATUSES and not emitted:
                    return
                if await request.is_disconnected():
                    return
                if idle_ticks >= 50:
                    idle_ticks = 0
                    yield ": forgex-agent-heartbeat\n\n"
                await asyncio.sleep(0.1)
        finally:
            emit_diagnostic_event(
                component="backend",
                event="sse.disconnected",
                run_id=run_id,
                stream="agent_run",
                last_sequence=sequence,
            )

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/runs/{run_id}/graph")
async def agent_runtime_graph(run_id: str, request: Request) -> dict[str, object]:
    orchestrator = services(request).agent_orchestrator
    if orchestrator is None or not orchestrator.owns(run_id):
        raise APIError(
            404,
            "AGENT_GRAPH_NOT_FOUND",
            "An orchestration graph is not available for this run.",
        )
    return {"graph": orchestrator.graph(run_id)}


@router.get("/runs/{run_id}/context-artifacts/{artifact_id}")
async def agent_runtime_context_artifact(
    run_id: str,
    artifact_id: str,
    request: Request,
) -> dict[str, object]:
    _require_local_request(request)
    orchestrator = services(request).agent_orchestrator
    if orchestrator is None or not orchestrator.owns(run_id):
        raise APIError(
            404,
            "AGENT_RUN_NOT_FOUND",
            "The requested agent run was not found.",
        )
    try:
        artifact = orchestrator.context_artifact(run_id, artifact_id)
    except KeyError as exc:
        raise APIError(
            404,
            "AGENT_CONTEXT_ARTIFACT_NOT_FOUND",
            "The requested context artifact was not found.",
        ) from exc
    except ValueError as exc:
        raise APIError(
            409,
            "AGENT_CONTEXT_ARTIFACT_CORRUPT",
            "The requested context artifact failed integrity verification.",
        ) from exc
    return {"artifact": artifact}


@router.get("/sessions/{session_id}/projection")
async def agent_session_projection(session_id: str, request: Request) -> dict[str, object]:
    _require_local_request(request)
    try:
        _session_store(request).get(session_id)
    except KeyError as exc:
        raise APIError(404, "AGENT_SESSION_NOT_FOUND", "Agent session was not found.") from exc
    return {"projection": _service(request).session_projection(session_id)}


@router.get("/sessions/{session_id}/events")
async def agent_session_events(
    session_id: str,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
) -> StreamingResponse:
    try:
        _session_store(request).get(session_id)
    except KeyError as exc:
        raise APIError(
            404, "AGENT_SESSION_NOT_FOUND", "Agent session was not found."
        ) from exc
    orchestrator = services(request).agent_orchestrator
    event_store = services(request).agent_event_store
    if orchestrator is None and _session_store(request).get(session_id).kind != "chat":
        return await agent_session_activity_events(session_id, request, after_sequence)

    async def stream() -> AsyncIterator[str]:
        emit_diagnostic_event(
            component="backend",
            event="sse.connected",
            session_id=session_id,
            stream="agent_session",
        )
        sequence = max(
            after_sequence,
            (
                int(request.headers.get("last-event-id", "0"))
                if request.headers.get("last-event-id", "0").isdigit()
                else 0
            ),
        )
        idle_ticks = 0
        try:
            while True:
                emitted = False
                for event in event_store.events(session_id, after_sequence=sequence):
                    sequence = int(event["sequence"])
                    emitted = True
                    yield f"id: {sequence}\ndata: {json.dumps(event, ensure_ascii=True, sort_keys=True)}\n\n"
                idle_ticks = 0 if emitted else idle_ticks + 1
                if await request.is_disconnected():
                    return
                if idle_ticks >= 50:
                    idle_ticks = 0
                    yield ": forgex-agent-session-heartbeat\n\n"
                await asyncio.sleep(0.1)
        finally:
            emit_diagnostic_event(
                component="backend",
                event="sse.disconnected",
                session_id=session_id,
                stream="agent_session",
                last_sequence=sequence,
            )

    return StreamingResponse(stream(), media_type="text/event-stream")


async def _start_agent_monitor(
    request: Request, *, port: str | None
) -> dict[str, object]:
    lock: asyncio.Lock = services(request).monitor_lock
    async with lock:
        current = services(request).serial_service
        if current is not None and current.is_connected():
            return {
                "port": current.active_port,
                "baudrate": current.configuration.baudrate,
                "connected": True,
            }
        configuration = SerialConfiguration(port=port, baudrate=115_200, timeout_s=1.0)
        service = services(request).serial_service_factory(configuration)
        connection = await service.connect()
        services(request).replace_serial_service(service)
        return {
            "port": connection.port,
            "baudrate": connection.baudrate,
            "connected": connection.connected,
        }


async def _stop_agent_monitor(request: Request) -> None:
    lock: asyncio.Lock = services(request).monitor_lock
    async with lock:
        current = services(request).serial_service
        if current is not None:
            await current.disconnect()


def _assistant_message_for_run(run: object) -> str:
    safe = run.to_safe_dict()
    message = safe.get("assistant_message")
    if isinstance(message, str) and message.strip():
        return message
    status = str(safe.get("status") or "completed").replace("_", " ")
    summary = safe.get("summary")
    if isinstance(summary, dict):
        changed = summary.get("changed_files")
        if isinstance(changed, list) and changed:
            return f"Run {status}. Changed {len(changed)} file{'s' if len(changed) != 1 else ''}."
    return f"Run {status}."


def _assistant_metadata_for_run(
    run: object, *, decision: AgentTurnDecision | None = None
) -> dict[str, object]:
    safe = run.to_safe_dict()
    summary = safe.get("summary")
    changed_files = ""
    build_status = ""
    if isinstance(summary, dict):
        changed = summary.get("changed_files")
        if isinstance(changed, list):
            changed_files = ", ".join(str(item) for item in changed[:20])
        build = summary.get("build_status")
        build_status = str(build) if build is not None else ""
    metadata = _assistant_metadata_for_run_status(
        str(safe.get("status") or ""),
        decision=decision,
        run_id=str(safe.get("run_id") or "") or None,
    )
    metadata.update(
        {
            "status": str(safe.get("status") or ""),
            "classification": str(safe.get("classification") or ""),
            "changed_files": changed_files,
            "build_status": build_status,
        }
    )
    return metadata


def _assistant_metadata_for_run_status(
    status: str,
    *,
    decision: AgentTurnDecision | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {"status": status}
    if decision is not None:
        metadata.update(_assistant_metadata(decision, run_id=run_id))
    return metadata


def _session_message_payload(
    session: AgentSession,
    decision: AgentTurnDecision,
    *,
    run: object | None,
    broker: AgentActivityBroker | None = None,
    after_sequence: int = 0,
) -> dict[str, object]:
    payload = {
        "intent": decision.intent.value,
        "decision": decision.to_safe_dict(),
        "session": session.to_dict(include_messages=True),
        "run": run.to_safe_dict() if run is not None else None,
    }
    if broker is not None:
        payload["activity_events"] = list(
            broker.recent(session.session_id, after_sequence=after_sequence)
        )
    return payload


def _assistant_metadata(
    decision: AgentTurnDecision,
    *,
    run_id: str | None,
    status: str | None = None,
) -> dict[str, object]:
    metadata = decision.metadata()
    metadata["run_id"] = run_id
    if status is not None:
        metadata["status"] = status
    return metadata


def _active_session_run(
    service: ProductAgentService, run_id: str | None
) -> object | None:
    if not run_id:
        return None
    try:
        return service.get_run(run_id)
    except KeyError:
        return None


def _active_operation(run: object | None) -> bool:
    status = getattr(run, "status", None)
    return bool(
        status
        and status not in {"completed", "failed", "cancelled", "blocked", "timed_out"}
    )


def _steerable_operation(run: object | None) -> bool:
    """Only live reasoning/execution turns accept steering.

    Awaiting flash confirmation is intentionally not steerable: a new edit/build
    request must start a fresh turn while the old verified artifact remains as
    history rather than being injected into a completed turn.
    """
    return getattr(run, "status", None) in {"queued", "running"}


def _turn_context(metadata: object, active_run: object | None) -> AgentTurnContext:
    project_path = Path(str(getattr(metadata, "project_path"))).expanduser()
    build_status = ""
    if active_run is not None:
        stage_statuses = getattr(active_run, "stage_statuses", None)
        if isinstance(stage_statuses, dict):
            build_status = str(stage_statuses.get("build") or "")
        build_result = getattr(active_run, "build_result", None)
        if isinstance(build_result, dict) and build_result.get("success") is False:
            build_status = "failed"
    return AgentTurnContext(
        active_run_status=str(getattr(active_run, "status", "")) or None,
        last_run_status=str(getattr(active_run, "status", "")) or None,
        last_build_status=build_status or None,
        has_workspace=project_path.exists(),
        has_platformio_ini=(project_path / "platformio.ini").is_file(),
    )


def _failed_run_continuation(
    session: AgentSession, active_run: object | None, content: str
) -> str | None:
    """Bind a short retry to the last explicit task in this same session."""
    if content.casefold().strip(" .!?") not in {
        "continue", "continue this", "retry", "retry this", "try again", "resume this"
    }:
        return None
    if getattr(active_run, "status", None) not in {"failed", "blocked", "timed_out"}:
        return None
    router = AgentTurnRouter()
    for message in reversed(session.messages[:-1]):
        if message.role != "user":
            continue
        prior = message.content.strip()
        decision = router.route(prior)
        if decision.starts_product_run and not decision.requires_hardware:
            return prior + "\n\nContinue this request after the failed attempt."
    return None


def _autonomy_for_decision(decision: AgentTurnDecision, requested_autonomy: str) -> str:
    if decision.intent in {
        AgentTurnIntent.CHAT,
        AgentTurnIntent.UNKNOWN,
        AgentTurnIntent.INSPECT,
    }:
        return AUTONOMY_READ_ONLY
    if requested_autonomy == AUTONOMY_READ_ONLY:
        return AUTONOMY_READ_ONLY
    if requested_autonomy == AUTONOMY_PLAN_ONLY:
        return AUTONOMY_PLAN_ONLY
    if requested_autonomy == AUTONOMY_STAGED_CHANGES:
        return AUTONOMY_STAGED_CHANGES
    if (
        "build" in decision.prohibited_actions
        or "execute" in decision.prohibited_actions
    ):
        return AUTONOMY_STAGED_CHANGES
    if requested_autonomy == AUTONOMY_BUILD_THEN_CONFIRM_FLASH:
        return AUTONOMY_BUILD_THEN_CONFIRM_FLASH
    if (
        decision.intent is AgentTurnIntent.SIMULATE
        or "simulate" in decision.requested_actions
    ):
        return AUTONOMY_SIMULATE
    if decision.intent is AgentTurnIntent.BUILD and not decision.requires_write:
        # The existing autonomous build executor owns the trusted PlatformIO handler.
        # Capability profiling makes this strict read+build only when no repair/write
        # intent was routed, so a plain build can never mutate the staged workspace.
        return AUTONOMY_BUILD_ONLY
    if requested_autonomy == AUTONOMY_BUILD_ONLY:
        return AUTONOMY_BUILD_ONLY
    if decision.intent is AgentTurnIntent.EDIT and not decision.requires_build:
        return AUTONOMY_STAGED_CHANGES
    return AUTONOMY_BUILD_ONLY


def _chat_activities(
    content: str, decision: AgentTurnDecision
) -> tuple[AgentActivity, ...]:
    normalized = " ".join(content.casefold().strip().rstrip(".!?").split())
    if normalized in {
        "hi",
        "hii",
        "hello",
        "hey",
        "hi there",
        "hello there",
        "hey there",
        "thanks",
        "thank you",
        "thank you very much",
    }:
        return ()
    if (
        "model" in normalized
        or "provider" in normalized
        or "board" in normalized
        or "environment" in normalized
    ):
        return (AgentActivity.CHECKING,)
    if decision.intent is AgentTurnIntent.UNKNOWN:
        return (AgentActivity.THINKING,)
    return (AgentActivity.THINKING, AgentActivity.EXPLAINING)


def _inspect_activities(
    content: str, active_run: object | None
) -> tuple[AgentActivity, ...]:
    normalized = content.casefold()
    if _asks_for_build_diagnostics(normalized) and active_run is not None:
        return (
            AgentActivity.INSPECTING,
            AgentActivity.SLEUTHING,
            AgentActivity.ANALYZING,
        )
    if any(
        token in normalized
        for token in ("main.cpp", "platformio.ini", "src/", ".cpp", ".h", ".ino")
    ):
        return (
            AgentActivity.INSPECTING,
            AgentActivity.READING,
            AgentActivity.ANALYZING,
        )
    return (AgentActivity.INSPECTING, AgentActivity.ANALYZING)


def _action_handoff_activities(
    decision: AgentTurnDecision,
) -> tuple[AgentActivity, ...]:
    if decision.intent is AgentTurnIntent.EDIT:
        return (AgentActivity.INSPECTING, AgentActivity.PLANNING)
    if decision.intent is AgentTurnIntent.GENERATE:
        return (AgentActivity.PLANNING, AgentActivity.GENERATING)
    if decision.intent is AgentTurnIntent.BUILD:
        return (AgentActivity.PREPARING,)
    if decision.intent is AgentTurnIntent.SIMULATE:
        return (AgentActivity.PREPARING, AgentActivity.ANALYZING)
    if decision.intent is AgentTurnIntent.REPAIR:
        return (AgentActivity.INSPECTING, AgentActivity.DIAGNOSING)
    if decision.intent is AgentTurnIntent.MONITOR:
        return (AgentActivity.CONNECTING,)
    return (AgentActivity.PREPARING,)


def _runtime_fact_response(
    content: str, service: ProductAgentService, body: AgentSessionMessageRequest
) -> str | None:
    """Answer deterministic runtime-state questions without asking a model to guess."""
    normalized = " ".join(content.casefold().strip().rstrip(".!?").split())
    if any(
        phrase in normalized
        for phrase in (
            "what model",
            "which model",
            "selected model",
            "what provider",
            "which provider",
            "selected provider",
        )
    ):
        return _model_status_response(service, body.provider_id, body.autonomy)
    if "board" in normalized and any(
        token in normalized for token in ("selected", "using", "current")
    ):
        return f"Selected board: {body.board_type or 'not selected'}. Selected port: {body.board_port or 'not selected'}."
    if "environment" in normalized and any(
        token in normalized for token in ("selected", "using", "current")
    ):
        return f"Selected PlatformIO environment: {body.environment or 'automatic/default'}."
    return None


def _conversation_response(
    content: str,
    session: AgentSession,
    service: ProductAgentService,
    provider_id: str | None,
    body: AgentSessionMessageRequest,
    decision: AgentTurnDecision,
) -> str:
    local = _local_session_response(content, session)
    if local:
        return local
    normalized = " ".join(content.casefold().strip().rstrip(".!?").split())
    if "model" in normalized or "provider" in normalized:
        return _model_status_response(service, provider_id, body.autonomy)
    if "board" in normalized and "selected" in normalized:
        return f"Selected board: {body.board_type or 'not selected'}. Selected port: {body.board_port or 'not selected'}."
    if "environment" in normalized and "selected" in normalized:
        return f"Selected PlatformIO environment: {body.environment or 'automatic/default'}."
    concept = _concept_response(normalized)
    if concept:
        return concept
    if decision.intent is AgentTurnIntent.UNKNOWN:
        return "I am not going to run anything for that. Ask me to inspect, edit, build, repair, flash, or monitor when you want an action."
    return "I can talk through the project, inspect files read-only, stage firmware edits, build, repair build failures, prepare flash confirmation, and monitor serial output when you explicitly ask."


def _model_status_response(
    service: ProductAgentService, provider_id: str | None, autonomy: str
) -> str:
    try:
        selected_provider_id = (
            provider_id or service.provider_registry.default_provider_id()
        )
    except ValueError:
        return "No routeable Forge model provider is currently configured."
    providers = service.provider_registry.list()
    provider = next(
        (item for item in providers if item.provider_id == selected_provider_id), None
    )
    model_id = service.provider_registry.selected_model_id(selected_provider_id)
    fallback = service.provider_registry.configured_fallback(selected_provider_id)
    provider_label = (
        provider.display_name if provider is not None else selected_provider_id
    )
    state = provider.state if provider is not None else "unknown"
    route = "Auto turn routing" if autonomy == "auto" else autonomy
    return (
        f"Selected provider: {provider_label} ({selected_provider_id}).\n"
        f"Selected model: {model_id or 'not configured'}.\n"
        f"Provider state: {state}.\n"
        f"Fallback provider: {fallback or 'none'}.\n"
        f"Routing mode: {route}."
    )


def _concept_response(normalized: str) -> str | None:
    if "pwm" in normalized:
        return "PWM means pulse-width modulation. On ESP32 firmware, it rapidly toggles an output with a controlled duty cycle so LEDs, motors, and similar devices can receive an average power level."
    if "platformio" in normalized:
        return "PlatformIO is the build and project system ForgeX uses here for embedded firmware. A typical project has platformio.ini plus source files under src/."
    if "esp-idf" in normalized or "esp idf" in normalized:
        return "ESP-IDF is Espressif's native SDK for ESP32 chips. ForgeX can discuss it, but this workspace flow defaults to PlatformIO unless the project configuration says otherwise."
    if "arduino" in normalized:
        return "Arduino is a firmware framework with setup() and loop() entry points. In this workspace, Arduino projects are usually built through PlatformIO."
    return None


def _inspect_response(content: str, metadata: object, active_run: object | None) -> str:
    project_path = Path(str(getattr(metadata, "project_path"))).expanduser()
    normalized = content.casefold()
    if _asks_for_build_diagnostics(normalized):
        return _build_diagnostics_response(active_run)
    files = _requested_workspace_files(content, project_path)
    if not files:
        listed = _workspace_file_list(project_path)
        preview = (
            "\n".join(f"- {item}" for item in listed[:20]) or "- No files detected."
        )
        return f"I inspected the workspace read-only. No files were modified, built, or flashed.\n\nWorkspace files:\n{preview}"
    sections = [
        "I inspected the requested file content read-only. No files were modified, built, or flashed."
    ]
    for relative in files[:3]:
        path = _safe_workspace_file(project_path, relative)
        if path is None:
            sections.append(
                f"\n{relative}: skipped because the path is not a safe workspace file."
            )
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            sections.append(
                f"\n{relative}: could not be read: {str(exc) or type(exc).__name__}."
            )
            continue
        snippet = _bounded_text(text, limit=5_000)
        sections.append(f"\n{relative}:\n```text\n{snippet}\n```")
    return "\n".join(sections)


def _asks_for_build_diagnostics(normalized: str) -> bool:
    return any(
        phrase in normalized
        for phrase in (
            "last build fail",
            "build fail",
            "build error",
            "compiler error",
            "compile error",
            "linker error",
            "explain the error",
            "explain it",
        )
    )


def _build_diagnostics_response(active_run: object | None) -> str:
    if active_run is None:
        return "I do not have build diagnostics for this session yet. No files were modified, built, or flashed."
    safe = active_run.to_safe_dict()
    build = safe.get("build_result")
    stage_messages = safe.get("stage_messages")
    status = safe.get("status")
    lines = [f"Latest run status: {status}."]
    if isinstance(stage_messages, dict):
        build_message = stage_messages.get("build") or stage_messages.get("repair")
        if build_message:
            lines.append(f"Build stage: {build_message}")
    if isinstance(build, dict):
        message = build.get("message")
        if message:
            lines.append(f"Build message: {message}")
        for key in ("error", "stderr", "stdout"):
            value = build.get(key)
            if isinstance(value, str) and value.strip():
                lines.append(f"{key}: {_bounded_text(value.strip(), limit=2_000)}")
    if len(lines) == 1:
        lines.append("No detailed build error payload is available.")
    lines.append(
        "I only inspected diagnostics; I did not modify files, build, or flash."
    )
    return "\n".join(lines)


def _requested_workspace_files(content: str, root: Path) -> list[str]:
    normalized = content.replace("\\", "/").casefold()
    files = _workspace_file_list(root)
    selected: list[str] = []
    for relative in files:
        lower = relative.casefold()
        name = Path(relative).name.casefold()
        if lower in normalized or name in normalized:
            selected.append(relative)
    if not selected and "main" in normalized:
        selected.extend(
            relative
            for relative in files
            if Path(relative).name.casefold() in {"main.cpp", "main.ino", "main.c"}
        )
    if not selected and "platformio" in normalized:
        selected.extend(
            relative
            for relative in files
            if Path(relative).name.casefold() == "platformio.ini"
        )
    return list(dict.fromkeys(selected))


def _safe_workspace_file(root: Path, relative: str) -> Path | None:
    try:
        resolved_root = root.resolve(strict=True)
        path = (resolved_root / relative).resolve(strict=True)
        path.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    if not path.is_file() or path.is_symlink():
        return None
    if any(part.startswith(".") for part in Path(relative).parts):
        return None
    if path.suffix.casefold() not in {
        ".c",
        ".cc",
        ".cpp",
        ".h",
        ".hpp",
        ".ino",
        ".ini",
        ".json",
        ".md",
        ".txt",
        ".yaml",
        ".yml",
    }:
        return None
    return path


def _bounded_text(value: str, *, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore") + "\n[truncated]"


def _cancel_response(run: object) -> str:
    status = str(getattr(run, "status", "cancelled")).replace("_", " ")
    if getattr(run, "classification", None) == "FLASH_SKIPPED":
        return "Flash skipped. I did not upload firmware to hardware."
    return f"Cancelled the active Forge run. Current status: {status}."


def _local_session_response(content: str, session: AgentSession) -> str | None:
    normalized = " ".join(content.casefold().strip().rstrip(".!?").split())
    if normalized in {
        "hi",
        "hii",
        "hello",
        "hey",
        "hi there",
        "hello there",
        "hey there",
    }:
        return "Hello. Tell me what you want to build, change, fix, or inspect in this workspace."
    if normalized in {"thanks", "thank you", "thank you very much"}:
        return "You are welcome."
    if normalized in {"what can you do", "help", "help me"}:
        return "I can inspect the workspace, edit project files through staged changes, run builds, repair build errors, prepare firmware for flashing, and continue from this conversation."
    if (
        "what changed" in normalized
        or "explain changes" in normalized
        or "explain what changed" in normalized
    ):
        for message in reversed(session.messages[:-1]):
            if message.role != "assistant":
                continue
            status = message.metadata.get("status")
            changed = message.metadata.get("changed_files")
            if changed:
                return f"The last agent turn ended with {status or 'a completed run'} and changed: {changed}."
        return "I do not have a previous changed-file summary in this session yet."
    return None


def _session_instruction(
    session: AgentSession,
    latest_message: str,
    metadata: object,
    *,
    decision: AgentTurnDecision,
    active_run: object | None,
    approved_plan: object | None = None,
    force_read_only: bool = False,
    force_plan_only: bool = False,
) -> str:
    project_path = Path(str(getattr(metadata, "project_path"))).expanduser()
    file_list = _workspace_file_list(project_path)
    recent_messages = [
        f"{message.role}: {message.content}"
        for message in session.messages[-10:]
        if message.role in {"user", "assistant"}
    ]
    sections = [
        "You are ForgeX, a persistent IDE coding agent for embedded firmware projects.",
        "Continue the existing conversation instead of treating this as an isolated one-shot task.",
        "When the user asks for changes, fixes, or improvements, inspect and edit the existing workspace files through the available safe editing flow.",
        "Prefer minimal, targeted edits. Preserve unrelated user code. Use PlatformIO conventions.",
        "Do not flash hardware unless the user explicitly requests flashing and the separate confirmation flow confirms it.",
        "Respect prohibited actions exactly.",
        "",
        f"Project ID: {session.project_id}",
        f"Project path: {project_path}",
        f"Turn intent: {decision.intent.value}",
        f"Requested actions: {', '.join(decision.requested_actions) or 'none'}",
        f"Prohibited actions: {', '.join(decision.prohibited_actions) or 'none'}",
        f"Build requested: {'yes' if decision.requires_build else 'no'}",
        "",
        "Recent conversation:",
        *recent_messages,
        "",
        "Workspace files:",
        *(file_list or ["No files detected."]),
    ]
    if approved_plan is not None:
        plan_steps = getattr(approved_plan, "steps", ())
        sections.extend(
            [
                "",
                "APPROVED EXECUTION PLAN (immutable binding):",
                f"Plan ID: {getattr(approved_plan, 'plan_id', '')}",
                f"Revision: {getattr(approved_plan, 'revision', '')}",
                f"SHA-256: {getattr(approved_plan, 'plan_hash', '')}",
                *[
                    f"{index}. {str(step.get('text') or '').strip()} [{str(step.get('status') or 'pending')}]"
                    for index, step in enumerate(plan_steps, start=1)
                ],
                "Execute this exact approved plan. Preserve its constraints. If a material change is required, stop safely and explain why instead of silently substituting a different plan.",
            ]
        )
    diagnostics = _latest_run_diagnostics(active_run)
    if diagnostics and decision.intent in {
        AgentTurnIntent.REPAIR,
        AgentTurnIntent.BUILD,
        AgentTurnIntent.SIMULATE,
    }:
        sections.extend(["", "Latest build/run diagnostics:", diagnostics])
    if force_plan_only:
        sections.extend(
            [
                "",
                "Execution constraint: inspect the project and produce a concrete plan only. You may update the plan, but do not edit files, build, flash, upload, or monitor hardware.",
            ]
        )
    elif force_read_only or decision.intent in {
        AgentTurnIntent.CHAT,
        AgentTurnIntent.UNKNOWN,
        AgentTurnIntent.INSPECT,
    }:
        sections.extend(
            [
                "",
                "Execution constraint: answer using read-only project tools. Do not edit, build, flash, upload, or monitor hardware. Your final summary must directly answer the user. If the user asked for a modification in Ask mode, explain the exact recommended change without applying it.",
            ]
        )
    elif decision.intent is AgentTurnIntent.EDIT and not decision.requires_build:
        sections.extend(
            [
                "",
                "Execution constraint: stage minimal safe file changes only. Do not build, flash, upload, or monitor.",
            ]
        )
    elif decision.intent in {
        AgentTurnIntent.GENERATE,
        AgentTurnIntent.BUILD,
        AgentTurnIntent.REPAIR,
        AgentTurnIntent.SIMULATE,
    }:
        extra = (
            " Run Wokwi simulation after a successful build when simulation was requested."
            if decision.intent is AgentTurnIntent.SIMULATE
            else ""
        )
        sections.extend(
            [
                "",
                "Execution constraint: perform the requested firmware workflow and build validation only. Do not flash or upload hardware."
                + extra,
            ]
        )
    sections.extend(["", "Latest user request:", latest_message.strip()])
    return _bounded_instruction("\n".join(sections))


def _latest_run_diagnostics(active_run: object | None) -> str:
    if active_run is None:
        return ""
    safe = active_run.to_safe_dict()
    parts: list[str] = []
    status = safe.get("status")
    if status:
        parts.append(f"status: {status}")
    stage_messages = safe.get("stage_messages")
    if isinstance(stage_messages, dict):
        for key in ("build", "repair", "generation"):
            value = stage_messages.get(key)
            if value:
                parts.append(f"{key}: {value}")
    build = safe.get("build_result")
    if isinstance(build, dict):
        message = build.get("message")
        if message:
            parts.append(f"build_message: {message}")
    return "\n".join(parts)


def _workspace_file_list(root: Path) -> list[str]:
    ignored = {".git", ".pio", ".next", "node_modules", "dist", "build", "__pycache__"}
    try:
        resolved = root.resolve(strict=True)
    except OSError:
        return []
    paths: list[str] = []
    for current, directories, filenames in os.walk(resolved):
        current_path = Path(current)
        directories[:] = sorted(
            name
            for name in directories
            if name not in ignored and not (current_path / name).is_symlink()
        )
        for filename in sorted(filenames):
            path = current_path / filename
            if path.is_symlink():
                continue
            try:
                relative = path.relative_to(resolved).as_posix()
            except ValueError:
                continue
            paths.append(relative)
            if len(paths) >= 80:
                return paths
    return paths


def _bounded_instruction(value: str) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= 16_000:
        return value
    # Preserve the latest task and its execution constraints at the end. Losing
    # those while retaining older conversation can execute the wrong request.
    head = encoded[:4_000].decode("utf-8", errors="ignore")
    tail = encoded[-11_800:].decode("utf-8", errors="ignore")
    return head + "\n\n[Older context omitted; latest request retained.]\n\n" + tail


def _require_local_request(request: Request) -> None:
    require_local_http_request(request, purpose="Agent execution")
