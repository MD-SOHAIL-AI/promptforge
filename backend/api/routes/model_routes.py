"""Task/model route selection, testing, usage, and generation diagnostics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field

from ...model_router import ModelRequest, ModelRoute
from ...services.code_generation_service import CodeGenerationService
from ...services.llm_service import LLMConfigurationError, LLMError
from ..dependencies import required_state
from ..errors import APIError, error_responses
from ..schemas.common import APIModel, ErrorResponse
from ._model_common import router_service

router = APIRouter()


class RouteRequest(APIModel):
    task_type: str
    provider_id: str
    model_id: str
    fallback_enabled: bool = False
    fallback_provider_id: str | None = None
    local_only: bool = False


class TestModelRequest(APIModel):
    prompt: str = Field(default="Reply with OK.", min_length=1)
    system_prompt: str | None = None
    task_type: str = "general_chat"
    provider_id: str | None = None
    model_id: str | None = None
    local_only: bool = False


@router.get("/routes", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def list_routes(request: Request) -> dict[str, Any]:
    service = router_service(request)
    return {"routes": [route.to_dict() for route in service.registry.list_routes()]}


@router.post(
    "/routes",
    responses={**error_responses(404, 422, 500), "default": {"model": ErrorResponse}},
)
async def save_route(body: RouteRequest, request: Request) -> dict[str, Any]:
    service = router_service(request)
    try:
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
        raise APIError(404, "MODEL_PROVIDER_NOT_FOUND", str(exc), {"provider_id": body.provider_id}) from exc
    return {"route": route.to_dict()}


@router.post(
    "/test",
    responses={**error_responses(422, 502, 500), "default": {"model": ErrorResponse}},
)
async def test_model(body: TestModelRequest, request: Request) -> dict[str, Any]:
    service = router_service(request)
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
    service = router_service(request)
    records = service.usage.list_records(limit=max(1, min(limit, 1000)))
    return {"usage": records, "count": len(records)}


@router.get("/generation-attempts", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def generation_attempts(
    request: Request,
    task_id: str | None = None,
    execution_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    generation = required_state(request, "code_generation_service", "code generation service")
    assert isinstance(generation, CodeGenerationService)
    attempts = generation.list_attempts(
        task_id=task_id,
        execution_id=execution_id,
        limit=max(1, min(limit, 1000)),
    )
    return {"attempts": attempts, "count": len(attempts)}


@router.get("/chunked-generation-runs", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def chunked_generation_runs(
    request: Request,
    execution_id: str | None = None,
    project_id: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    generation = required_state(request, "code_generation_service", "code generation service")
    assert isinstance(generation, CodeGenerationService)
    runs = generation.list_chunked_runs(
        execution_id=execution_id,
        project_id=project_id,
        limit=max(1, min(limit, 1000)),
    )
    return {"runs": runs, "count": len(runs)}


@router.post("/generation-diagnostics/clear", responses={**error_responses(500), "default": {"model": ErrorResponse}})
async def clear_generation_diagnostics(request: Request) -> dict[str, Any]:
    generation = required_state(request, "code_generation_service", "code generation service")
    assert isinstance(generation, CodeGenerationService)
    generation.clear_generation_diagnostics()
    return {"cleared": True}
