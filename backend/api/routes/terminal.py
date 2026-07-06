"""Integrated terminal session routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import Field

from ..dependencies import required_state, resolve_project_from_request
from ..errors import APIError, error_responses
from ..schemas.common import APIModel, ErrorResponse
from ...services.terminal_service import TerminalService

router = APIRouter(prefix="/terminal", tags=["terminal"])


class TerminalStartRequest(APIModel):
    project_id: str = Field(min_length=1, max_length=128)


class TerminalInputRequest(APIModel):
    data: str = Field(min_length=1, max_length=20_000)


class TerminalResizeRequest(APIModel):
    cols: int = Field(default=80, ge=20, le=300)
    rows: int = Field(default=24, ge=5, le=120)


@router.post(
    "/sessions",
    responses={**error_responses(404, 422, 500, 503), "default": {"model": ErrorResponse}},
)
async def start_terminal(body: TerminalStartRequest, request: Request) -> dict[str, object]:
    service = _terminal_service(request)
    project = await resolve_project_from_request(request, body.project_id)
    cwd = Path(str(project.project_path)).expanduser().resolve()
    try:
        session = await service.start(cwd)
    except ValueError as exc:
        raise APIError(422, "TERMINAL_CWD_INVALID", str(exc), {"project_id": body.project_id}) from exc
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
    del body
    service = _terminal_service(request)
    session = await service.get(session_id)
    if session is None:
        raise APIError(404, "TERMINAL_NOT_FOUND", "Terminal session was not found", {"session_id": session_id})
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
    }
