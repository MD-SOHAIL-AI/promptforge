"""Small helpers for accessing application-owned existing services."""

from __future__ import annotations

from fastapi import Request

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
) -> ProjectMetadata:
    for metadata in await service.list_projects():
        if metadata.project_id == project_id:
            return metadata
    raise APIError(
        404,
        "PROJECT_NOT_FOUND",
        "Project was not found",
        {"project_id": project_id},
    )
