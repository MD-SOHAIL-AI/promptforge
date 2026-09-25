"""Explicit model catalog and persisted session/role choices."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from ...model_router.models import ModelRequest
from ...model_router.selection import ModelRef, ROLES, validate_selection_scope
from ...services.llm_service import LLMError
from ..dependencies import services
from ..errors import APIError
from ..local_security import require_local_http_request
from ..schemas.common import APIModel

def _require_local(request: Request):
    require_local_http_request(request, purpose="Model selection")


router = APIRouter(prefix="/v2/models", tags=["model-gateway"], dependencies=[Depends(_require_local)])


class SelectionBody(APIModel):
    provider_id: str = Field(min_length=1, max_length=128)
    model_id: str = Field(min_length=1, max_length=256)
    session_id: str | None = Field(default=None, min_length=1, max_length=256)


class RoleSelectionBody(SelectionBody):
    role: Literal["main", "explore", "review", "verify", "compact", "title"]


@router.get("/catalog")
async def catalog(request: Request):
    registry = services(request).model_router_service.registry
    return {"providers": [
        {**provider.to_dict(), "models": [model.to_dict() for model in registry.list_models(provider.provider_id)]}
        for provider in registry.list_providers()
    ]}


def _session(request: Request, session_id: str | None):
    try:
        validate_selection_scope(session_id)
    except ValueError as exc:
        raise APIError(422, "MODEL_SELECTION_INVALID", str(exc)) from exc
    if session_id is None:
        return None
    try:
        return services(request).agent_session_store.get(session_id)
    except (KeyError, ValueError) as exc:
        raise APIError(404, "AGENT_SESSION_NOT_FOUND", "Session not found.") from exc


def _sync_session(request: Request, session_id: str | None):
    session = _session(request, session_id)
    if session is None:
        return
    gateway = services(request).model_router_service
    # Older selectors still write the conversation store. Reconcile their latest
    # choice before reporting inherited bindings, including legacy resets.
    if session.provider_id is None and session.model_id is None:
        gateway.selections.clear(session_id=session_id)
        return
    try:
        resolved = gateway.resolver.resolve(ModelRequest(
            prompt="", provider_id=session.provider_id, model_id=session.model_id,
        ))
        model = ModelRef(resolved.provider_id, resolved.model_id)
        if gateway.selections.get(session_id=session_id) != model:
            gateway.selections.set(model, session_id=session_id)
    except (ValueError, LLMError) as exc:
        raise APIError(422, "MODEL_SELECTION_INVALID", str(exc)) from exc


def _save(request: Request, body: SelectionBody, role: str):
    gateway = services(request).model_router_service
    _session(request, body.session_id)
    try:
        ModelRef(body.provider_id, body.model_id)
        resolved = gateway.resolver.resolve(ModelRequest(
            prompt="", provider_id=body.provider_id, model_id=body.model_id,
        ))
        model = ModelRef(resolved.provider_id, resolved.model_id)
        if body.session_id is not None and role == "main":
            # Keep the existing conversation selector consistent with the new
            # selection endpoint while clients migrate to the gateway API.
            services(request).agent_session_store.configure(
                body.session_id, provider_id=model.provider_id, model_id=model.model_id,
            )
        gateway.selections.set(model, session_id=body.session_id, role=role)
    except KeyError as exc:
        raise APIError(404, "AGENT_SESSION_NOT_FOUND", "Session not found.") from exc
    except (ValueError, LLMError) as exc:
        raise APIError(422, "MODEL_SELECTION_INVALID", str(exc)) from exc
    return {"provider_id": model.provider_id, "model_id": model.model_id, "role": role, "session_id": body.session_id}


@router.put("/session-selection")
async def select_session(body: SelectionBody, request: Request):
    return _save(request, body, "main")


@router.put("/role-bindings")
async def select_role(body: RoleSelectionBody, request: Request):
    return _save(request, body, body.role)


@router.get("/role-bindings")
async def role_bindings(request: Request, session_id: str | None = None):
    _sync_session(request, session_id)
    gateway = services(request).model_router_service
    result = {}
    for role in sorted(ROLES):
        selected, source = gateway.selections.resolve(session_id=session_id, role=role)
        result[role] = {"model": selected.id if selected else None, "source": source}
    return {"bindings": result}


@router.get("/session-selection")
async def session_selection(request: Request, session_id: str | None = None):
    _sync_session(request, session_id)
    selected, source = services(request).model_router_service.selections.resolve(session_id=session_id)
    return {"model": selected.id if selected else None, "source": source}


@router.delete("/role-bindings/{role}")
async def clear_binding(
    request: Request,
    role: Literal["main", "explore", "review", "verify", "compact", "title"],
    session_id: str | None = None,
):
    session = _session(request, session_id)
    legacy_removed = False
    if role == "main" and session is not None:
        legacy_removed = session.provider_id is not None or session.model_id is not None
        try:
            services(request).agent_session_store.clear_model_selection(session_id)
        except (KeyError, ValueError) as exc:
            raise APIError(404, "AGENT_SESSION_NOT_FOUND", "Session not found.") from exc
    removed = services(request).model_router_service.selections.clear(session_id=session_id, role=role)
    return {"removed": removed or legacy_removed}
