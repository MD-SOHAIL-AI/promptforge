"""Application health route."""

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from ..schemas.health import HealthResponse, ServiceHealth

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Check API health")
async def health(request: Request) -> HealthResponse:
    workflow = all(
        getattr(request.app.state, name, None) is not None
        for name in (
            "execute_prompt",
            "code_generation_service",
            "project_service",
            "subprocess_manager",
        )
    )
    services = ServiceHealth(
        workflow=workflow,
        planner=True,
        coordinator=True,
    )
    return HealthResponse(
        status="healthy" if all(services.model_dump().values()) else "degraded",
        version=request.app.version,
        timestamp=datetime.now(timezone.utc),
        services=services,
    )
