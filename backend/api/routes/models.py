"""Canonical model-provider and selection API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field

from ...model_router import CredentialStoreError, ModelRequest, ModelRoute, ModelRouterService
from ...model_router.registry import TASK_TYPES
from ...model_router.storage import ModelRouterStorageError
from ...services.code_generation_service import CodeGenerationService
from ...services.llm_service import LLMConfigurationError, LLMError
from ..dependencies import required_state
from ..errors import APIError, error_responses
from ..schemas.common import APIModel, ErrorResponse

router = APIRouter(prefix="/models", tags=["models"])


class ConfigureProviderRequest(APIModel):
    api_key: str | None = Field(default=None, min_length=1)
    base_url: str | None = Field(default=None, min_length=1)
    default_model: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None


class RouteRequest(APIModel):
    task_type: str
    provider_id: str
    model_id: str
    fallback_enabled: bool = True
    fallback_provider_id: str | None = None
    local_only: bool = False


class SelectionRequest(APIModel):
    provider_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    fallback_enabled: bool | None = None
    fallback_provider_id: str | None = None


class TestModelRequest(APIModel):
    prompt: str = Field(default="Reply with OK.", min_length=1)
    system_prompt: str | None = None
    task_type: str = "general_chat"
    provider_id: str | None = None
    model_id: str | None = None
    local_only: bool = False


@router.get("/providers", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def list_providers(request: Request) -> dict[str, Any]:
    service = _router_service(request)
    return {"providers": [provider.to_dict() for provider in service.registry.list_providers()]}


@router.post(
    "/providers/{provider_id}/configure",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def configure_provider(provider_id: str, body: ConfigureProviderRequest, request: Request) -> dict[str, Any]:
    service = _router_service(request)
    try:
        provider = service.registry.configure_provider(provider_id, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise APIError(404, "MODEL_PROVIDER_NOT_FOUND", str(exc), {"provider_id": provider_id}) from exc
    except CredentialStoreError as exc:
        raise APIError(503, "SECURE_CREDENTIAL_STORE_UNAVAILABLE", "The operating-system credential store is unavailable.") from exc
    except ModelRouterStorageError as exc:
        raise APIError(500, "MODEL_SETTINGS_WRITE_FAILED", "Model settings could not be saved safely.") from exc
    return {"provider": provider.to_dict()}


@router.post(
    "/providers/{provider_id}/health",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def provider_health(provider_id: str, request: Request) -> dict[str, Any]:
    service = _router_service(request)
    provider = service._providers.get(provider_id)
    if provider is None:
        raise APIError(404, "MODEL_PROVIDER_NOT_FOUND", f"Unknown model provider: {provider_id}", {"provider_id": provider_id})
    health = await provider.health()
    payload = health.to_dict()
    try:
        service.registry.save_health(provider_id, payload)
    except (OSError, ModelRouterStorageError):
        pass
    return {"health": payload}


@router.get(
    "/providers/{provider_id}/models",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def list_provider_models(provider_id: str, request: Request) -> dict[str, Any]:
    service = _router_service(request)
    provider = service._providers.get(provider_id)
    if provider is None:
        raise APIError(404, "MODEL_PROVIDER_NOT_FOUND", f"Unknown model provider: {provider_id}", {"provider_id": provider_id})
    try:
        discovered = await provider.list_models()
        service.registry.save_models(provider_id, discovered)
        return {"models": [model.to_dict() for model in discovered], "count": len(discovered), "source": "provider", "error": None}
    except Exception as exc:
        fallback = service.registry.list_models(provider_id)
        return {
            "models": [model.to_dict() for model in fallback],
            "count": len(fallback),
            "source": "fallback",
            "error": f"Could not fetch model list. Enter model ID manually. ({type(exc).__name__})",
        }


@router.get("/selection", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def get_selection(request: Request) -> dict[str, Any]:
    route = _router_service(request).registry.route_for_task("code_generation")
    return {"selection": _selection_payload(route)}


@router.put(
    "/selection",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def save_selection(body: SelectionRequest, request: Request) -> dict[str, Any]:
    service = _router_service(request)
    current = service.registry.route_for_task("code_generation")
    try:
        definition = service.registry.definition(body.provider_id)
        _validate_agent_model(service, body.provider_id, body.model_id)
        fallback_enabled = current.fallback_enabled if body.fallback_enabled is None else body.fallback_enabled
        fallback_provider_id = body.fallback_provider_id
        if "fallback_provider_id" not in body.model_fields_set:
            candidate = current.fallback_provider_id
            if candidate:
                try:
                    same_category = service.registry.definition(candidate).local == definition.local
                except ValueError:
                    same_category = False
                fallback_provider_id = candidate if same_category and candidate != body.provider_id else None
            if fallback_enabled and not fallback_provider_id:
                fallback_provider_id = next(
                    (candidate_id for candidate_id in service.registry.fallback_provider_ids(local_only=definition.local)
                     if candidate_id != body.provider_id),
                    None,
                )
        if not fallback_enabled:
            fallback_provider_id = None
        route = ModelRoute(
            task_type="code_generation",
            provider_id=body.provider_id,
            model_id=body.model_id,
            fallback_enabled=fallback_enabled,
            fallback_provider_id=fallback_provider_id,
            local_only=definition.local,
        )
        service.registry.save_route(route)
    except ValueError as exc:
        raise APIError(422, "MODEL_SELECTION_INVALID", str(exc), {"provider_id": body.provider_id}) from exc
    except ModelRouterStorageError as exc:
        raise APIError(500, "MODEL_SETTINGS_WRITE_FAILED", "Model selection could not be saved safely.") from exc
    return {"selection": _selection_payload(route)}


@router.get("/routes", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def list_routes(request: Request) -> dict[str, Any]:
    service = _router_service(request)
    return {"routes": [route.to_dict() for route in service.registry.list_routes()]}


@router.post(
    "/routes",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def save_route(body: RouteRequest, request: Request) -> dict[str, Any]:
    if body.task_type not in TASK_TYPES:
        raise APIError(422, "MODEL_TASK_TYPE_INVALID", "Unknown model task type.", {"task_type": body.task_type})
    service = _router_service(request)
    try:
        if body.task_type == "code_generation":
            _validate_agent_model(service, body.provider_id, body.model_id)
        route = ModelRoute(
            task_type=body.task_type,  # type: ignore[arg-type]
            provider_id=body.provider_id,
            model_id=body.model_id,
            fallback_enabled=body.fallback_enabled,
            fallback_provider_id=body.fallback_provider_id,
            local_only=body.local_only,
        )
        service.registry.save_route(route)
    except ValueError as exc:
        raise APIError(422, "MODEL_ROUTE_INVALID", str(exc), {"provider_id": body.provider_id}) from exc
    except ModelRouterStorageError as exc:
        raise APIError(500, "MODEL_SETTINGS_WRITE_FAILED", "Model route could not be saved safely.") from exc
    return {"route": route.to_dict()}


def _validate_agent_model(service: ModelRouterService, provider_id: str, model_id: str) -> None:
    model = service.registry.model_info(provider_id, model_id)
    if model is not None and model.agent_compatible is False:
        raise APIError(
            422,
            "MODEL_AGENT_CAPABILITY_UNSUPPORTED",
            "This model does not advertise structured JSON or tool-calling support required by Forge Agent.",
            {"provider_id": provider_id, "model_id": model_id},
        )


@router.post("/test", responses={**error_responses(422, 502, 500), "default": {"model": ErrorResponse}})
async def test_model(body: TestModelRequest, request: Request) -> dict[str, Any]:
    if body.task_type not in TASK_TYPES:
        raise APIError(422, "MODEL_TASK_TYPE_INVALID", "Unknown model task type.", {"task_type": body.task_type})
    service = _router_service(request)
    try:
        response = await service.generate_model(
            ModelRequest(
                prompt=body.prompt,
                system_prompt=body.system_prompt,
                task_type=body.task_type,  # type: ignore[arg-type]
                provider_id=body.provider_id,
                model_id=body.model_id,
                local_only=body.local_only,
                max_tokens=64,
            )
        )
    except LLMConfigurationError as exc:
        raise APIError(422, exc.code, exc.message, dict(exc.details)) from exc
    except LLMError as exc:
        raise APIError(502, exc.code, exc.message, dict(exc.details)) from exc
    except Exception as exc:
        raise APIError(502, "MODEL_PROVIDER_ERROR", str(exc), {"exception_type": type(exc).__name__}) from exc
    return {
        "response": {
            "content": response.content,
            "provider_id": response.provider_id,
            "model_id": response.model_id,
            "latency_ms": response.latency_ms,
            "usage": response.token_usage(),
        }
    }


@router.get("/usage", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def usage(request: Request, limit: int = 100) -> dict[str, Any]:
    service = _router_service(request)
    records = service.usage.list_records(limit=max(1, min(limit, 1000)))
    return {"usage": records, "count": len(records)}


@router.get("/generation-attempts", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def generation_attempts(request: Request, task_id: str | None = None, execution_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    generation = required_state(request, "code_generation_service", "code generation service")
    assert isinstance(generation, CodeGenerationService)
    attempts = generation.list_attempts(task_id=task_id, execution_id=execution_id, limit=max(1, min(limit, 1000)))
    return {"attempts": attempts, "count": len(attempts)}


@router.get("/chunked-generation-runs", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def chunked_generation_runs(request: Request, execution_id: str | None = None, project_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    generation = required_state(request, "code_generation_service", "code generation service")
    assert isinstance(generation, CodeGenerationService)
    runs = generation.list_chunked_runs(execution_id=execution_id, project_id=project_id, limit=max(1, min(limit, 1000)))
    return {"runs": runs, "count": len(runs)}


@router.post("/generation-diagnostics/clear", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def clear_generation_diagnostics(request: Request) -> dict[str, Any]:
    generation = required_state(request, "code_generation_service", "code generation service")
    assert isinstance(generation, CodeGenerationService)
    generation.clear_generation_diagnostics()
    return {"cleared": True}


def _selection_payload(route: ModelRoute) -> dict[str, Any]:
    return {
        "provider_id": route.provider_id,
        "model_id": route.model_id,
        "fallback_enabled": route.fallback_enabled,
        "fallback_provider_id": route.fallback_provider_id,
        "local_only": route.local_only,
    }


def _router_service(request: Request) -> ModelRouterService:
    value = required_state(request, "model_router_service", "model router")
    assert isinstance(value, ModelRouterService)
    return value
