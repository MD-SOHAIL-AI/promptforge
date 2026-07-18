"""Model-provider configuration, health, and model-list routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field

from ...model_router import CredentialStoreError
from ..errors import APIError, error_responses
from ..schemas.common import APIModel, ErrorResponse
from ._model_common import bridge_detection_service, canonical_bridge_payload, router_service

router = APIRouter()


class ConfigureProviderRequest(APIModel):
    api_key: str | None = Field(default=None, min_length=1)
    base_url: str | None = Field(default=None, min_length=1)
    default_model: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None


@router.get(
    "/bridges/{provider_id}",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def bridge_detail(provider_id: str, request: Request) -> dict[str, Any]:
    canonical = canonical_bridge_payload(request, provider_id)
    if canonical is not None:
        return {"bridge": canonical}
    bridge_detection = bridge_detection_service(request)
    try:
        result = bridge_detection.detect_provider(provider_id)
    except ValueError as exc:
        raise APIError(404, "BRIDGE_PROVIDER_NOT_FOUND", str(exc), {"provider_id": provider_id}) from exc
    return {"bridge": result.to_dict()}


@router.get(
    "/providers",
    responses={**error_responses(500), "default": {"model": ErrorResponse}},
)
async def list_providers(request: Request) -> dict[str, Any]:
    service = router_service(request)
    return {
        "providers": [
            provider.to_dict()
            for provider in service.registry.list_providers()
        ]
    }


@router.post(
    "/providers/{provider_id}/configure",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def configure_provider(
    provider_id: str,
    body: ConfigureProviderRequest,
    request: Request,
) -> dict[str, Any]:
    service = router_service(request)
    try:
        provider = service.registry.configure_provider(
            provider_id,
            body.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise APIError(404, "MODEL_PROVIDER_NOT_FOUND", str(exc), {"provider_id": provider_id}) from exc
    except CredentialStoreError as exc:
        raise APIError(503, "SECURE_CREDENTIAL_STORE_UNAVAILABLE", "The operating-system credential store is unavailable.") from exc
    return {"provider": provider.to_dict()}


@router.post(
    "/providers/{provider_id}/health",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def provider_health(provider_id: str, request: Request) -> dict[str, Any]:
    service = router_service(request)
    provider = service._providers.get(provider_id)
    if provider is None:
        raise APIError(
            404,
            "MODEL_PROVIDER_NOT_FOUND",
            f"Unknown model provider: {provider_id}",
            {"provider_id": provider_id},
        )
    health = await provider.health()
    payload = health.to_dict()
    try:
        service.registry.save_health(provider_id, payload)
    except OSError:
        # Health checks remain useful in read-only or locked-down runtimes;
        # persistence is secondary to returning the live result.
        pass
    return {"health": payload}


@router.get(
    "/providers/{provider_id}/models",
    responses={**error_responses(404, 500), "default": {"model": ErrorResponse}},
)
async def list_provider_models(provider_id: str, request: Request) -> dict[str, Any]:
    service = router_service(request)
    provider = service._providers.get(provider_id)
    if provider is None:
        raise APIError(
            404,
            "MODEL_PROVIDER_NOT_FOUND",
            f"Unknown model provider: {provider_id}",
            {"provider_id": provider_id},
        )
    try:
        models = await provider.list_models()
    except Exception as exc:
        fallback = service.registry.list_models(provider_id)
        service.registry.save_models(provider_id, fallback)
        return {
            "models": [model.to_dict() for model in fallback],
            "count": len(fallback),
            "source": "fallback",
            "error": f"Could not fetch model list. Enter model ID manually. ({type(exc).__name__})",
        }
    service.registry.save_models(provider_id, models)
    return {
        "models": [model.to_dict() for model in models],
        "count": len(models),
        "source": "provider",
        "error": None,
    }
