"""Read-only product endpoints for the staged Forge Harness cutover."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...harness import HarnessClient, HarnessClientError
from ..dependencies import services
from ..errors import APIError
from ..local_security import require_local_http_request


router = APIRouter(prefix="/harness", tags=["harness"])


@router.get("/status")
async def status(request: Request) -> dict[str, object]:
    require_local_http_request(request, purpose="Forge Harness status")
    client = _client(request)
    if client is None:
        return {"configured": False, "healthy": False, "mode": "legacy"}
    try:
        health = await client.health()
        runtime = await client.call("harness.status")
    except HarnessClientError as exc:
        return {
            "configured": True,
            "healthy": False,
            "error": {"code": exc.code, "message": str(exc), "retryable": exc.retryable},
        }
    return {"configured": True, "healthy": True, "health": health, "runtime": runtime}


@router.get("/providers")
async def providers(request: Request) -> dict[str, object]:
    require_local_http_request(request, purpose="Forge Harness providers")
    client = _client(request)
    if client is None:
        raise APIError(503, "HARNESS_NOT_CONFIGURED", "Forge Harness is not configured.")
    try:
        result = await client.call("providers.list")
    except HarnessClientError as exc:
        raise APIError(503, exc.code, str(exc)) from exc
    if not isinstance(result, dict):
        raise APIError(502, "HARNESS_RESPONSE_INVALID", "Forge Harness provider response is invalid.")
    return {str(key): value for key, value in result.items()}


def _client(request: Request) -> HarnessClient | None:
    value = services(request).harness_client
    return value if isinstance(value, HarnessClient) else None
