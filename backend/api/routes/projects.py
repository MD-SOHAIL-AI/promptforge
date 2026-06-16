"""Managed project routes."""

from fastapi import APIRouter, Request

from ...services.project_service import ProjectService, ProjectServiceError
from ..dependencies import required_state, resolve_project
from ..errors import APIError, error_responses
from ..schemas.projects import (
    ProjectDeleteResponse,
    ProjectListResponse,
    ProjectResponse,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get(
    "",
    response_model=ProjectListResponse,
    responses=error_responses(500, 503),
    summary="List managed projects",
)
async def list_projects(request: Request) -> ProjectListResponse:
    service = required_state(request, "project_service", "project service")
    assert isinstance(service, ProjectService)
    try:
        projects = [
            _project(item.to_dict(), await service.load_project(item.project_name))
            for item in await service.list_projects()
        ]
    except ProjectServiceError as exc:
        raise APIError(500, "PROJECT_SERVICE_ERROR", "Projects could not be listed") from exc
    return ProjectListResponse(projects=projects, count=len(projects))


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    responses=error_responses(404, 500, 503),
    summary="Get a managed project",
)
async def get_project(project_id: str, request: Request) -> ProjectResponse:
    service = required_state(request, "project_service", "project service")
    assert isinstance(service, ProjectService)
    metadata = await resolve_project(service, _identifier(project_id))
    try:
        loaded = await service.load_project(metadata.project_name)
    except ProjectServiceError as exc:
        raise APIError(500, "PROJECT_SERVICE_ERROR", "Project could not be loaded") from exc
    return _project(metadata.to_dict(), loaded)


@router.delete(
    "/{project_id}",
    response_model=ProjectDeleteResponse,
    responses=error_responses(404, 500, 503),
    summary="Delete a managed project",
)
async def delete_project(project_id: str, request: Request) -> ProjectDeleteResponse:
    service = required_state(request, "project_service", "project service")
    assert isinstance(service, ProjectService)
    normalized = _identifier(project_id)
    metadata = await resolve_project(service, normalized)
    try:
        deleted = await service.delete_project(metadata.project_name)
    except ProjectServiceError as exc:
        raise APIError(500, "PROJECT_SERVICE_ERROR", "Project could not be deleted") from exc
    if not deleted:
        raise APIError(404, "PROJECT_NOT_FOUND", "Project was not found", {"project_id": normalized})
    return ProjectDeleteResponse(project_id=normalized, deleted=True)


def _project(data: dict[str, object], project: object | None = None) -> ProjectResponse:
    if project is not None:
        data = {
            **data,
            "files": [
                {
                    "path": item.path,
                    "content": item.content,
                    "file_type": item.file_type,
                }
                for item in getattr(project, "files", ())
            ],
        }
    return ProjectResponse.model_validate(data)


def _identifier(value: str) -> str:
    if not value or len(value) > 128 or not all(
        character.isalnum() or character in "._-" for character in value
    ):
        raise APIError(422, "INVALID_PROJECT_ID", "Project ID is invalid")
    return value
