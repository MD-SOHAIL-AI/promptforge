"""Canonical model-provider and selection API."""

from __future__ import annotations

from typing import Any

import httpx
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


@router.get("/providers/{provider_id}/endpoint-health")
async def endpoint_health(provider_id: str, request: Request, model_id: str | None = None):
    from ...contracts.recovery import EndpointHealth
    owner = _router_service(request)
    try:
        model = model_id or owner.registry.default_model(provider_id)
        identity = owner._circuit_identity(provider_id, model)
    except (KeyError, ValueError) as exc:
        raise APIError(404, "MODEL_PROVIDER_NOT_FOUND", "The selected model provider was not found.") from exc
    return EndpointHealth(provider_id=provider_id, model_id=model, **owner.circuit.health(identity)).model_dump(mode="json")


class ConfigureProviderRequest(APIModel):
    api_key: str | None = Field(default=None, min_length=1)
    base_url: str | None = Field(default=None, min_length=1)
    default_model: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None
    options: dict[str, Any] | None = None


class CreateCustomProviderRequest(APIModel):
    provider_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=128)
    protocol: str = Field(pattern=r"^(openai-completions|openai-responses|anthropic-messages)$")
    base_url: str = Field(min_length=8, max_length=2048)
    default_model: str = Field(min_length=1, max_length=256)
    models: list[str] = Field(default_factory=list, max_length=256)
    local: bool = False
    api_key: str | None = Field(default=None, min_length=1)


class RouteRequest(APIModel):
    task_type: str
    provider_id: str
    model_id: str
    fallback_enabled: bool = False
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
def list_providers(request: Request) -> dict[str, Any]:
    service = _router_service(request)
    return {"providers": [provider.to_dict() for provider in service.registry.list_providers()]}


@router.post("/providers", status_code=201, responses={**error_responses(409, 422, 500), "default": {"model": ErrorResponse}})
async def create_custom_provider(body: CreateCustomProviderRequest, request: Request) -> dict[str, Any]:
    service = _router_service(request)
    try:
        provider = service.registry.register_custom_provider(
            provider_id=body.provider_id,
            display_name=body.display_name,
            protocol=body.protocol,
            base_url=body.base_url,
            default_model=body.default_model,
            models=tuple(body.models),
            local=body.local,
            api_key=body.api_key,
        )
        service.install_custom_provider(body.provider_id)
    except ValueError as exc:
        raise APIError(422, "MODEL_PROVIDER_INVALID", str(exc)) from exc
    except CredentialStoreError as exc:
        raise APIError(503, "SECURE_CREDENTIAL_STORE_UNAVAILABLE", "The operating-system credential store is unavailable.") from exc
    return {"provider": provider.to_dict()}


@router.delete(
    "/providers/{provider_id}",
    responses={**error_responses(404, 409, 500), "default": {"model": ErrorResponse}},
)
async def delete_custom_provider(provider_id: str, request: Request) -> dict[str, Any]:
    service = _router_service(request)
    try:
        service.remove_custom_provider(provider_id)
    except CredentialStoreError as exc:
        raise APIError(503, "SECURE_CREDENTIAL_STORE_UNAVAILABLE", "The operating-system credential store is unavailable.") from exc
    except ValueError as exc:
        message = str(exc)
        status = 409 if "referenced by routes" in message or "Built-in" in message else 404
        code = "MODEL_PROVIDER_IN_USE" if status == 409 else "MODEL_PROVIDER_NOT_FOUND"
        raise APIError(status, code, message, {"provider_id": provider_id}) from exc
    except ModelRouterStorageError as exc:
        raise APIError(500, "MODEL_SETTINGS_WRITE_FAILED", "Model settings could not be saved safely.") from exc
    return {"provider_id": provider_id, "deleted": True}


@router.post(
    "/providers/{provider_id}/configure",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def configure_provider(provider_id: str, body: ConfigureProviderRequest, request: Request) -> dict[str, Any]:
    service = _router_service(request)
    try:
        service.registry.definition(provider_id)
    except ValueError as exc:
        raise APIError(404, "MODEL_PROVIDER_NOT_FOUND", str(exc), {"provider_id": provider_id}) from exc
    try:
        provider = service.registry.configure_provider(provider_id, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise APIError(422, "MODEL_PROVIDER_INVALID", str(exc), {"provider_id": provider_id}) from exc
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
        models = service.catalog.models(provider_id)
        return {"models": [model.to_dict() for model in models], "count": len(models), "source": "provider", "error": None}
    except Exception as exc:
        fallback = service.registry.list_models(provider_id)
        if isinstance(exc, httpx.HTTPStatusError):
            reason = f"HTTP {exc.response.status_code}"
        elif isinstance(exc, ValueError):
            reason = str(exc)
        else:
            reason = type(exc).__name__
        return {
            "models": [model.to_dict() for model in service.catalog.models(provider_id)],
            "count": len(fallback),
            "source": "fallback",
            "error": f"Could not fetch model list. Showing cached or starter models; enter a model ID manually if needed. ({reason})",
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
        fallback_enabled = False
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
        route = ModelRoute(
            task_type=body.task_type,  # type: ignore[arg-type]
            provider_id=body.provider_id,
            model_id=body.model_id,
            fallback_enabled=False,
            fallback_provider_id=None,
            local_only=body.local_only,
        )
        service.registry.save_route(route)
    except ValueError as exc:
        raise APIError(422, "MODEL_ROUTE_INVALID", str(exc), {"provider_id": body.provider_id}) from exc
    except ModelRouterStorageError as exc:
        raise APIError(500, "MODEL_SETTINGS_WRITE_FAILED", "Model route could not be saved safely.") from exc
    return {"route": route.to_dict()}


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
