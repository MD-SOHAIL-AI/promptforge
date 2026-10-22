"""Build-and-flash route using shared ForgeX flashing orchestration."""

from __future__ import annotations

from fastapi import APIRouter

from ..errors import APIError, error_responses
from ..schemas.flash import FlashRequest

router = APIRouter(tags=["flash"])


@router.post(
    "/flash",
    responses=error_responses(409),
    summary="Legacy direct flash endpoint (disabled)",
)
async def flash_project(body: FlashRequest) -> None:
    # This legacy endpoint has no server-side one-time approval binding. Flash
    # requests must go through the agent runtime's prepare/confirm flow.
    raise APIError(
        409,
        "FLASH_APPROVAL_REQUIRED",
        "Use a Forge task to prepare and confirm a verified firmware flash.",
    )
