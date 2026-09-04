"""ForgeX non-secret settings API routes."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ...settings.models import (
    SettingsExportResponse,
    SettingsPatchRequest,
    SettingsResponse,
    SettingsSchemaResponse,
)
from ...settings.service import SettingsService, SettingsValidationError
from ..dependencies import required_state
from ..errors import APIError


router = APIRouter(tags=["settings"])


def _service(request: Request) -> SettingsService:
    service = required_state(request, "settings_service", "settings service")
    assert isinstance(service, SettingsService)
    return service


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(request: Request) -> SettingsResponse:
    return SettingsResponse(settings=_service(request).get_settings())


@router.get("/settings/schema", response_model=SettingsSchemaResponse)
async def get_settings_schema(request: Request) -> SettingsSchemaResponse:
    return SettingsSchemaResponse(schema=_service(request).get_schema())


@router.patch("/settings", response_model=SettingsResponse)
async def patch_settings(request: Request, payload: SettingsPatchRequest) -> SettingsResponse:
    try:
        settings = _service(request).patch_settings(payload.settings)
    except SettingsValidationError as exc:
        raise APIError(422, "INVALID_SETTING", str(exc), {}) from exc
    except OSError as exc:
        raise APIError(
            500,
            "SETTINGS_WRITE_FAILED",
            "Settings could not be saved. Check that the ForgeX settings file is writable.",
            {},
        ) from exc
    return SettingsResponse(settings=settings)


@router.post("/settings/reset", response_model=SettingsResponse)
async def reset_settings(request: Request) -> SettingsResponse:
    try:
        settings = _service(request).reset()
    except OSError as exc:
        raise APIError(
            500,
            "SETTINGS_WRITE_FAILED",
            "Settings could not be saved. Check that the ForgeX settings file is writable.",
            {},
        ) from exc
    return SettingsResponse(settings=settings)


@router.post("/settings/export", response_model=SettingsExportResponse)
async def export_settings(request: Request) -> SettingsExportResponse:
    return SettingsExportResponse(settings=_service(request).export_settings(), secrets_included=False)
