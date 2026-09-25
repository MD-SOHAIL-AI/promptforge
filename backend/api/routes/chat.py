"""Chat-only HTTP boundary. No Agent executor or intent router is reachable here."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.agent_runtime.tool_policy import product_agent_policy
from backend.security import has_high_confidence_secret
from ..dependencies import resolve_project_from_request, services
from ..errors import APIError
from ..local_security import require_local_http_request

router = APIRouter(prefix="/v1/chat", tags=["chat"])


class ChatSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=128)
    provider_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_id: str | None = Field(default=None, min_length=1, max_length=128)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=16_384)
    provider_id: str | None = Field(default=None, min_length=1, max_length=128)
    model_id: str | None = Field(default=None, min_length=1, max_length=128)
    context_project_id: str | None = Field(default=None, min_length=1, max_length=128)
    context_paths: list[str] = Field(default_factory=list, max_length=12)
    retry: bool = False


def chat_session(request: Request, session_id: str):
    require_local_http_request(request)
    try:
        session = services(request).chat_service.sessions.get(session_id)
    except (KeyError, ValueError) as exc:
        raise APIError(404, "CHAT_SESSION_NOT_FOUND", "Chat not found.") from exc
    if session.kind != "chat":
        raise APIError(404, "CHAT_SESSION_NOT_FOUND", "Chat not found.")
    return session


@router.get("/sessions")
async def list_sessions(request: Request):
    require_local_http_request(request)
    sessions = services(request).chat_service.sessions.list(kind="chat")
    return {"sessions": [s.to_dict(include_messages=False) for s in sessions], "count": len(sessions)}


@router.post("/sessions", status_code=201)
async def create_session(request: Request, body: ChatSettings):
    require_local_http_request(request)
    store = services(request).chat_service.sessions
    session = store.create(kind="chat", title=body.title)
    session = store.configure(session.session_id, provider_id=body.provider_id, model_id=body.model_id)
    return {"session": session.to_dict()}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request):
    return {"session": chat_session(request, session_id).to_dict()}


@router.patch("/sessions/{session_id}")
async def configure_session(session_id: str, body: ChatSettings, request: Request):
    chat_session(request, session_id)
    session = services(request).chat_service.sessions.configure(session_id, **body.model_dump())
    return {"session": session.to_dict()}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request):
    chat_session(request, session_id)
    service = services(request).chat_service
    await service.cancel(session_id)
    service.sessions.delete(session_id)
    return {"deleted": True}


@router.post("/sessions/{session_id}/cancel")
async def cancel_session(session_id: str, request: Request):
    chat_session(request, session_id)
    service = services(request).chat_service
    await service.cancel(session_id)
    return {"session": service.sessions.get(session_id).to_dict()}


@router.post("/sessions/{session_id}/messages", status_code=202)
async def send_message(session_id: str, body: ChatMessage, request: Request):
    chat_session(request, session_id)
    context = ""
    if body.context_paths and not body.context_project_id:
        raise APIError(422, "CHAT_CONTEXT_PROJECT_REQUIRED", "Choose a project for these files.")
    if body.context_project_id:
        metadata = await resolve_project_from_request(request, body.context_project_id)
        root = Path(str(metadata.project_path)).resolve(strict=True)
        context = f"Project: {metadata.project_name}\n"
        policy = product_agent_policy()
        for relative in dict.fromkeys(body.context_paths):
            try:
                path = policy.resolve_path(root, relative, must_exist=True)
                with path.open("rb") as source:
                    data = source.read(128001)
                if len(data) > 128000:
                    raise ValueError("context_file_too_large")
                content = data.decode("utf-8")
                if has_high_confidence_secret(content):
                    raise ValueError("secret_content_forbidden")
                context += f"\nFile: {relative}\n{content}"
                if len(context) > 16000:
                    raise ValueError("context_limit")
            except (ValueError, OSError, UnicodeError) as exc:
                raise APIError(422, "CHAT_CONTEXT_INVALID", "Selected context is unsafe or exceeds the attachment limit.") from exc
    try:
        session = services(request).chat_service.start(
            session_id, body.content, provider_id=body.provider_id,
            model_id=body.model_id, context=context, retry=body.retry,
        )
    except ValueError as exc:
        raise APIError(409, str(exc), "The Chat response could not start.") from exc
    return {"session": session.to_dict()}


@router.get("/sessions/{session_id}/events")
async def session_events(session_id: str, request: Request, after_sequence: int = Query(default=0, ge=0)):
    chat_session(request, session_id)
    events = services(request).chat_service.events

    async def stream():
        # Flush the response through the Next.js rewrite before the first model
        # event. Without an initial body chunk, short replies can remain buffered
        # until the page is reloaded and fetches the durable session snapshot.
        yield ": chat-connected\n\n"
        header = request.headers.get("last-event-id", "0")
        sequence = max(after_sequence, int(header) if header.isdigit() else 0)
        idle = 0
        while not await request.is_disconnected():
            for event in events.events(session_id, after_sequence=sequence):
                sequence = int(event["sequence"])
                if str(event.get("event_type", "")).startswith("message."):
                    yield f"id: {sequence}\ndata: {json.dumps(event)}\n\n"
                    idle = 0
            idle += 1
            if idle >= 50:
                yield ": chat-heartbeat\n\n"
                idle = 0
            await asyncio.sleep(0.1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
