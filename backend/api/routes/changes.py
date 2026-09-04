"""Explicit review/apply API for provider-independent ChangeSets."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ...changes import ChangeSetError, ChangeSetService
from ..dependencies import required_state
from ..errors import APIError

router = APIRouter(prefix="/changes", tags=["changes"])


def _service(request: Request) -> ChangeSetService:
    value = required_state(request, "change_set_service", "ChangeSet service")
    assert isinstance(value, ChangeSetService)
    return value


@router.get("")
def list_change_sets(request: Request, status: str | None = Query(default=None)) -> dict[str, object]:
    return {"change_sets": [item.to_dict() for item in _service(request).list(status=status)]}


@router.get("/{change_set_id}")
def get_change_set(change_set_id: str, request: Request) -> dict[str, object]:
    try:
        item = _service(request).get(change_set_id)
    except ChangeSetError as exc:
        raise _api_error(exc) from exc
    return {"change_set": item.to_dict()}


@router.post("/{change_set_id}/apply")
def apply_change_set(change_set_id: str, request: Request) -> dict[str, object]:
    _require_local_request(request)
    try:
        item = _service(request).apply(change_set_id)
    except ChangeSetError as exc:
        raise _api_error(exc) from exc
    return {"change_set": item.to_dict()}


@router.post("/{change_set_id}/discard")
def discard_change_set(change_set_id: str, request: Request) -> dict[str, object]:
    _require_local_request(request)
    try:
        item = _service(request).discard(change_set_id)
    except ChangeSetError as exc:
        raise _api_error(exc) from exc
    return {"change_set": item.to_dict()}


@router.post("/{change_set_id}/undo")
def undo_change_set(change_set_id: str, request: Request) -> dict[str, object]:
    _require_local_request(request)
    try:
        item = _service(request).undo(change_set_id)
    except ChangeSetError as exc:
        raise _api_error(exc) from exc
    return {"change_set": item.to_dict()}


def _api_error(exc: ChangeSetError) -> APIError:
    if exc.code == "CHANGE_SET_NOT_FOUND":
        status = 404
    elif exc.code in {"CHANGE_CONFLICT", "CHANGE_WORKSPACE_REVISION_CONFLICT", "CHANGE_SET_NOT_PENDING", "CHANGE_SET_NOT_APPLIED", "CHANGE_STAGE_TAMPERED", "CHANGE_UNDO_CONFLICT"}:
        status = 409
    elif exc.code in {"CHANGE_PATH_UNSAFE", "CHANGE_PATH_FORBIDDEN", "CHANGE_PATH_ESCAPE", "CHANGE_PATH_SYMLINK"}:
        status = 422
    else:
        status = 500
    return APIError(status, exc.code, str(exc), exc.details)


def _require_local_request(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin or origin == "null" or origin.startswith("file://"):
        return
    if origin.startswith("http://127.0.0.1:") or origin.startswith("http://localhost:") or origin.startswith("http://[::1]:"):
        return
    raise APIError(403, "REMOTE_ORIGIN_REJECTED", "ChangeSet operations are restricted to the local desktop application.")
