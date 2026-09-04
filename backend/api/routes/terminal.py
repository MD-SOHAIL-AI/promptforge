"""Integrated terminal session routes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from pydantic import Field
from starlette.websockets import WebSocketState

from ..dependencies import required_state, resolve_project_from_request
from ..errors import APIError, error_responses
from ..schemas.common import APIModel, ErrorResponse
from ...services.terminal_service import TerminalService

router = APIRouter(prefix="/terminal", tags=["terminal"])


class TerminalStartRequest(APIModel):
    project_id: str = Field(min_length=1, max_length=128)
    profile_id: str | None = Field(default=None, min_length=1, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=80)
    cols: int = Field(default=80, ge=20, le=300)
    rows: int = Field(default=24, ge=5, le=120)


class TerminalInputRequest(APIModel):
    data: str = Field(min_length=1, max_length=20_000)


class TerminalResizeRequest(APIModel):
    cols: int = Field(default=80, ge=20, le=300)
    rows: int = Field(default=24, ge=5, le=120)


class TerminalRenameRequest(APIModel):
    title: str = Field(min_length=1, max_length=80)


@router.get(
    "/profiles",
    responses={**error_responses(500, 503), "default": {"model": ErrorResponse}},
)
async def terminal_profiles(request: Request) -> dict[str, object]:
    service = _terminal_service(request)
    profiles, default_profile_id = service.profiles()
    capability = service.default_process_capability
    return {
        "profiles": [
            profile.to_dict(
                default=profile.profile_id == default_profile_id,
                capability=capability,
            )
            for profile in profiles
        ],
        "default_profile_id": default_profile_id,
    }


@router.post(
    "/sessions",
    responses={**error_responses(404, 422, 500, 503), "default": {"model": ErrorResponse}},
)
async def start_terminal(body: TerminalStartRequest, request: Request) -> dict[str, object]:
    service = _terminal_service(request)
    project = await resolve_project_from_request(request, body.project_id)
    cwd = Path(str(project.project_path)).expanduser().resolve()
    try:
        session = await service.start(
            cwd,
            cols=body.cols,
            rows=body.rows,
            profile_id=body.profile_id,
            title=body.title,
        )
    except ValueError as exc:
        raise APIError(422, "TERMINAL_START_INVALID", str(exc), {"project_id": body.project_id}) from exc
    return _session_response(session)


@router.get(
    "/sessions/{session_id}/output",
    responses={**error_responses(404, 422, 500, 503), "default": {"model": ErrorResponse}},
)
async def terminal_output(session_id: str, request: Request, after: int = 0) -> dict[str, object]:
    if after < 0:
        raise APIError(422, "TERMINAL_CURSOR_INVALID", "Terminal cursor must be non-negative")
    service = _terminal_service(request)
    try:
        session, events = await service.output(session_id, after)
    except KeyError as exc:
        raise APIError(404, "TERMINAL_NOT_FOUND", "Terminal session was not found", {"session_id": session_id}) from exc
    return {
        **_session_response(session),
        "events": [event.to_dict() for event in events],
    }


@router.post(
    "/sessions/{session_id}/input",
    responses={**error_responses(404, 422, 500, 503), "default": {"model": ErrorResponse}},
)
async def terminal_input(session_id: str, body: TerminalInputRequest, request: Request) -> dict[str, object]:
    service = _terminal_service(request)
    try:
        session = await service.write(session_id, body.data)
    except KeyError as exc:
        raise APIError(404, "TERMINAL_NOT_FOUND", "Terminal session was not found", {"session_id": session_id}) from exc
    except ValueError as exc:
        raise APIError(422, "TERMINAL_INPUT_INVALID", str(exc), {"session_id": session_id}) from exc
    return _session_response(session)


@router.post(
    "/sessions/{session_id}/clear",
    responses={**error_responses(404, 500, 503), "default": {"model": ErrorResponse}},
)
async def clear_terminal(session_id: str, request: Request) -> dict[str, object]:
    service = _terminal_service(request)
    try:
        session = await service.clear(session_id)
    except KeyError as exc:
        raise APIError(404, "TERMINAL_NOT_FOUND", "Terminal session was not found", {"session_id": session_id}) from exc
    return _session_response(session)


@router.post(
    "/sessions/{session_id}/resize",
    responses={**error_responses(404, 422, 500, 503), "default": {"model": ErrorResponse}},
)
async def resize_terminal(session_id: str, body: TerminalResizeRequest, request: Request) -> dict[str, object]:
    service = _terminal_service(request)
    try:
        session = await service.resize(session_id, body.cols, body.rows)
    except KeyError as exc:
        raise APIError(404, "TERMINAL_NOT_FOUND", "Terminal session was not found", {"session_id": session_id}) from exc
    return _session_response(session)


@router.patch(
    "/sessions/{session_id}",
    responses={**error_responses(404, 422, 500, 503), "default": {"model": ErrorResponse}},
)
async def rename_terminal(
    session_id: str,
    body: TerminalRenameRequest,
    request: Request,
) -> dict[str, object]:
    service = _terminal_service(request)
    try:
        session = await service.rename(session_id, body.title)
    except KeyError as exc:
        raise APIError(404, "TERMINAL_NOT_FOUND", "Terminal session was not found", {"session_id": session_id}) from exc
    except ValueError as exc:
        raise APIError(422, "TERMINAL_TITLE_INVALID", str(exc), {"session_id": session_id}) from exc
    return _session_response(session)


@router.delete(
    "/sessions/{session_id}",
    responses={**error_responses(404, 500, 503), "default": {"model": ErrorResponse}},
)
async def stop_terminal(session_id: str, request: Request) -> dict[str, object]:
    service = _terminal_service(request)
    try:
        await service.stop(session_id)
    except KeyError as exc:
        raise APIError(404, "TERMINAL_NOT_FOUND", "Terminal session was not found", {"session_id": session_id}) from exc
    return {"session_id": session_id, "closed": True}


@router.websocket("/sessions/{session_id}/stream")
async def terminal_stream(websocket: WebSocket, session_id: str) -> None:
    service = getattr(websocket.app.state, "terminal_service", None)
    if not isinstance(service, TerminalService):
        await websocket.close(code=1011, reason="Terminal service is unavailable")
        return
    try:
        after = int(websocket.query_params.get("after", "0"))
        if after < 0:
            raise ValueError
    except ValueError:
        await websocket.close(code=1008, reason="Invalid terminal replay cursor")
        return

    await websocket.accept()
    try:
        session, replay, queue = await service.subscribe(session_id, after)
    except KeyError:
        await websocket.send_json({"type": "error", "code": "TERMINAL_NOT_FOUND"})
        await websocket.close(code=1008, reason="Terminal session was not found")
        return

    async def sender() -> None:
        for event in replay:
            await _send_terminal_event(websocket, session, event)
        if session.closed:
            await websocket.close(code=1000)
            return
        while True:
            event = await queue.get()
            await _send_terminal_event(websocket, session, event)
            if session.closed:
                await websocket.close(code=1000)
                return

    async def receiver() -> None:
        while websocket.client_state is WebSocketState.CONNECTED:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message["type"] != "websocket.receive":
                continue
            raw = message.get("text")
            if raw is None:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "code": "TERMINAL_MESSAGE_INVALID"})
                continue
            if not isinstance(payload, dict):
                await websocket.send_json({"type": "error", "code": "TERMINAL_MESSAGE_INVALID"})
                continue
            command = payload.get("type")
            try:
                if command == "input":
                    data = payload.get("data")
                    if isinstance(data, str):
                        await service.write(session_id, data)
                elif command == "resize":
                    cols = int(payload.get("cols", session.cols))
                    rows = int(payload.get("rows", session.rows))
                    resized = await service.resize(session_id, cols, rows)
                    await websocket.send_json({"type": "resized", "session": _session_response(resized)})
                elif command == "clear":
                    cleared = await service.clear(session_id)
                    await websocket.send_json({"type": "cleared", "session": _session_response(cleared)})
            except (KeyError, ValueError):
                await websocket.send_json({"type": "error", "code": "TERMINAL_COMMAND_FAILED"})

    sender_task = asyncio.create_task(sender())
    receiver_task = asyncio.create_task(receiver())
    try:
        done, pending = await asyncio.wait(
            {sender_task, receiver_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            try:
                task.result()
            except RuntimeError:
                if websocket.client_state is not WebSocketState.DISCONNECTED:
                    raise
    except WebSocketDisconnect:
        pass
    finally:
        sender_task.cancel()
        receiver_task.cancel()
        await asyncio.gather(sender_task, receiver_task, return_exceptions=True)
        await service.unsubscribe(session_id, queue)


def _terminal_service(request: Request) -> TerminalService:
    service = required_state(request, "terminal_service", "terminal service")
    assert isinstance(service, TerminalService)
    return service


def _session_response(session: object) -> dict[str, object]:
    return {
        "session_id": getattr(session, "session_id"),
        "shell": getattr(session, "shell"),
        "cwd": getattr(session, "cwd"),
        "closed": getattr(session, "closed"),
        "cols": getattr(session, "cols", 80),
        "rows": getattr(session, "rows", 24),
        "profile_id": getattr(session, "profile_id", "default"),
        "title": getattr(session, "title", getattr(session, "shell", "Terminal")),
        "process_capability": getattr(session, "process_capability", "pipe"),
        "lifecycle_status": getattr(session, "lifecycle_status", "running"),
        "exit_code": getattr(session, "exit_code", None),
    }


async def _send_terminal_event(websocket: WebSocket, session: object, event: object) -> None:
    await websocket.send_json(
        {
            "type": "output",
            "session": _session_response(session),
            "event": event.to_dict(),
        }
    )
