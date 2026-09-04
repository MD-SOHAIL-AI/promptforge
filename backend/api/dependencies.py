"""Small helpers for accessing application-owned existing services."""

from __future__ import annotations

from fastapi import Request

from ..services.project_import_service import (
    ExternalProjectRecord,
    ProjectImportError,
    ProjectImportService,
)
from ..services.project_service import ProjectMetadata, ProjectService
from .errors import APIError


def required_state(request: Request, name: str, label: str) -> object:
    value = getattr(request.app.state, name, None)
    if value is None:
        raise APIError(
            503,
            "SERVICE_UNAVAILABLE",
            f"{label} is not configured",
            {"service": label},
        )
    return value


async def resolve_project(
    service: ProjectService,
    project_id: str,
) -> ProjectMetadata | ExternalProjectRecord:
    for metadata in await service.list_projects():
        if metadata.project_id == project_id:
            return metadata
    raise APIError(
        404,
        "PROJECT_NOT_FOUND",
        "Project was not found",
        {"project_id": project_id},
    )


async def resolve_project_from_request(
    request: Request,
    project_id: str,
) -> ProjectMetadata | ExternalProjectRecord:
    service = required_state(request, "project_service", "project service")
    assert isinstance(service, ProjectService)
    for metadata in await service.list_projects():
        if metadata.project_id == project_id:
            return metadata
    import_service = getattr(request.app.state, "project_import_service", None)
    if isinstance(import_service, ProjectImportService):
        try:
            return await import_service.get_project(project_id)
        except ProjectImportError as exc:
            status = 404 if exc.code in {"PROJECT_NOT_FOUND", "PROJECT_PATH_MISSING"} else 422
            raise APIError(status, exc.code, str(exc), exc.details) from exc
    raise APIError(
        404,
        "PROJECT_NOT_FOUND",
        "Project was not found",
        {"project_id": project_id},
    )
