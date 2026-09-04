"""Build-and-flash route using shared ForgeX flashing orchestration."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Request

from ...services.flash_service import FlashServiceError, ProjectFlashService
from ..dependencies import required_state, resolve_project_from_request
from ..errors import APIError, error_responses
from ..schemas.build import BuildResultResponse
from ..schemas.flash import FlashRequest, FlashResponse, FlashResultResponse

router = APIRouter(tags=["flash"])
logger = logging.getLogger(__name__)


@router.post(
    "/flash",
    response_model=FlashResponse,
    responses=error_responses(404, 409, 422, 500, 503),
    summary="Build and flash a managed project",
)
async def flash_project(body: FlashRequest, request: Request) -> FlashResponse:
    flash_service = required_state(request, "project_flash_service", "project flash service")
    assert isinstance(flash_service, ProjectFlashService)

    metadata = await resolve_project_from_request(request, body.project_id)
    root = Path(metadata.project_path)
    ini = root / "platformio.ini"
    logger.info(
        "Running flash in workspace root: %s project_id=%s platformio_ini=%s platformio_exists=%s",
        root,
        body.project_id,
        ini,
        ini.is_file(),
    )
    try:
        outcome = await flash_service.build_and_flash(
            project_id=body.project_id,
            project_path=root,
            board_type=body.board_type,
            port=body.port,
            environment=body.environment,
            baudrate=body.baudrate,
            verify=body.verify,
            timeout_s=body.timeout_s,
        )
    except FlashServiceError as exc:
        raise APIError(exc.status_code, exc.code, exc.message, exc.details) from exc

    build_response = BuildResultResponse.model_validate(outcome.build.api_dict())
    if outcome.flash is None:
        return FlashResponse(project_id=body.project_id, build=build_response, flash=None)
    return FlashResponse(
        project_id=body.project_id,
        build=build_response,
        flash=FlashResultResponse.model_validate(outcome.flash.api_dict()),
    )
