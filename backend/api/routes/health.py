"""Application health route."""

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from backend.runtime_contract import BACKEND_RUNTIME_CONTRACT, BACKEND_SOURCE_FINGERPRINT

from ...services.platformio_service import PlatformIOService
from ..dependencies import services
from ..schemas.health import HealthResponse, ServiceHealth, ToolchainHealth

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Check API health")
async def health(request: Request) -> HealthResponse:
    platformio_service = services(request).platformio_service
    platformio = None
    if isinstance(platformio_service, PlatformIOService):
        platformio = ToolchainHealth.model_validate((await platformio_service.status()).to_dict())
    workflow = all(
        getattr(services(request), name, None) is not None
        for name in (
            "execute_prompt",
            "code_generation_service",
            "project_service",
            "subprocess_manager",
        )
    )
    service_health = ServiceHealth(
        workflow=workflow,
        planner=True,
        coordinator=True,
    )
    return HealthResponse(
        status="healthy" if all(service_health.model_dump().values()) else "degraded",
        service="forgex-backend",
        version=request.app.version,
        runtime_contract=BACKEND_RUNTIME_CONTRACT,
        source_fingerprint=BACKEND_SOURCE_FINGERPRINT,
        local_auth_required=bool(services(request).local_api_token),
        local_auth_fingerprint=services(request).local_auth_fingerprint,
        timestamp=datetime.now(timezone.utc),
        services=service_health,
        platformio=platformio,
        harness_configured=services(request).harness_client is not None,
    )
