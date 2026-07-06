"""Managed project routes."""

from fastapi import APIRouter, Request

from ...services.project_import_service import (
    ExternalProjectRecord,
    ProjectImportError,
    ProjectImportService,
)
from ...services.project_service import ProjectService, ProjectServiceError
from ..dependencies import required_state, resolve_project, resolve_project_from_request
from ..errors import APIError, error_responses
from ..schemas.project_import import ProjectImportRequest, ProjectImportResponse
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
    import_service = getattr(request.app.state, "project_import_service", None)
    if isinstance(import_service, ProjectImportService):
        try:
            projects.extend(_project(item.to_dict()) for item in await import_service.list_projects())
        except ProjectImportError as exc:
            raise APIError(500, exc.code, "External projects could not be listed", exc.details) from exc
    return ProjectListResponse(projects=projects, count=len(projects))


@router.post(
    "/import",
    response_model=ProjectImportResponse,
    responses=error_responses(422, 500, 503),
    summary="Open or register an external project folder",
)
async def import_project(body: ProjectImportRequest, request: Request) -> ProjectImportResponse:
    service = required_state(request, "project_import_service", "project import service")
    assert isinstance(service, ProjectImportService)
    try:
        record = await service.import_project(body.path)
    except ProjectImportError as exc:
        raise APIError(422, exc.code, str(exc), {"supported": exc.supported, **exc.details}) from exc
    return _import_response(record)


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    responses=error_responses(404, 500, 503),
    summary="Get a managed project",
)
async def get_project(project_id: str, request: Request) -> ProjectResponse:
    metadata = await resolve_project_from_request(request, _identifier(project_id))
    if isinstance(metadata, ExternalProjectRecord):
        return _project(metadata.to_dict())
    service = required_state(request, "project_service", "project service")
    assert isinstance(service, ProjectService)
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


def _import_response(record: ExternalProjectRecord) -> ProjectImportResponse:
    metadata = dict(record.metadata)
    platformio = metadata.get("platformio", {})
    environments = []
    if isinstance(platformio, dict):
        raw_environments = platformio.get("environments", [])
        if isinstance(raw_environments, list):
            environments = [item for item in raw_environments if isinstance(item, dict)]
    active_name = metadata.get("active_environment")
    active = next(
        (item for item in environments if item.get("name") == active_name),
        environments[0] if environments else {},
    )
    return ProjectImportResponse.model_validate(
        {
            "project_id": record.project_id,
            "name": record.project_name,
            "path": record.project_path,
            "external": True,
            "project_type": metadata.get("project_type", "platformio"),
            "board": record.target_board,
            "framework": record.framework,
            "platform": active.get("platform", ""),
            "environment": active.get("name"),
            "monitor_speed": metadata.get("monitor_speed"),
            "upload_speed": metadata.get("upload_speed"),
            "has_platformio_ini": metadata.get("has_platformio_ini", True),
            "files_loaded": metadata.get("files_loaded", True),
            "platformio": {"environments": environments},
            "metadata": metadata,
        }
    )


def _identifier(value: str) -> str:
    if not value or len(value) > 128 or not all(
        character.isalnum() or character in "._-" for character in value
    ):
        raise APIError(422, "INVALID_PROJECT_ID", "Project ID is invalid")
    return value
