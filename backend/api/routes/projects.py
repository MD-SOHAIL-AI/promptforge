"""Managed project routes."""

from fastapi import APIRouter, Request

from ...services.project_import_service import (
    ExternalProjectRecord,
    ProjectImportError,
    ProjectImportService,
)
from ...services.project_service import ProjectService, ProjectServiceError
from ..dependencies import required_state, resolve_project, resolve_project_from_request, services
from ..errors import APIError, error_responses
from ..schemas.project_import import ProjectImportRequest, ProjectImportResponse
from ..schemas.projects import (
    ProjectDeleteResponse,
    ProjectListResponse,
    ProjectResponse,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("/{project_id}/readiness")
async def project_readiness(project_id: str, request: Request, provider_id: str | None = None,
                            model_id: str | None = None, environment: str | None = None):
    """Read local configuration and cached observations without model generation."""
    import asyncio
    from datetime import datetime, timezone
    from ...services.project_profile import inspect_project_profile
    record = await resolve_project_from_request(request, project_id)
    profile = await asyncio.to_thread(inspect_project_profile, record.project_path, environment=environment)
    owner = services(request)
    provider = None
    if provider_id:
        try:
            selected = owner.model_router_service.registry.provider(provider_id)
            checked = selected.last_checked_at
            observed = datetime.fromisoformat(checked.replace("Z", "+00:00")) if checked else None
            if observed and observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            age = max(0, (datetime.now(timezone.utc) - observed).total_seconds()) if observed else None
            provider = {"provider_id": provider_id, "model_id": model_id or selected.default_model,
                "configured": selected.configured, "enabled": selected.enabled, "cached_status": selected.health_status,
                "checked_at": checked, "age_seconds": age, "fresh": age is not None and age < 300,
                "qualified": False}
        except (KeyError, ValueError):
            provider = {"provider_id": provider_id, "configured": False, "enabled": False, "qualified": False}
    toolchain = (await owner.platformio_service.health_status()).to_dict() if owner.platformio_service else None
    orchestrator = owner.agent_orchestrator
    trusted = bool(orchestrator and orchestrator.jobs.is_trusted(project_id, record.project_path))
    environment_ready = not profile.selection_required
    can_generate = bool(provider and provider["configured"] and provider["enabled"])
    can_build = bool(trusted and toolchain and toolchain["available"] and environment_ready and profile.adapter != "unknown")
    return {"project_id": project_id, "observed_at": datetime.now(timezone.utc).isoformat(),
        "workspace": {"available": True, "path": record.project_path}, "provider": provider,
        "toolchain": toolchain, "environment": {"selected": profile.selected_environment, "selection_required": profile.selection_required},
        "permissions": {"local_builds": trusted}, "capabilities": {"generate": can_generate, "build": can_build},
        "qualification": {"provider": "not_qualified", "hardware": "not_qualified"}}


@router.get("/{project_id}/profile")
async def project_profile(project_id: str, request: Request, environment: str | None = None):
    from ...services.project_profile import inspect_project_profile
    import asyncio
    record = await resolve_project_from_request(request, project_id)
    try:
        return await asyncio.to_thread(inspect_project_profile, record.project_path, environment=environment)
    except (OSError, ValueError) as exc:
        raise APIError(422, "PROJECT_PROFILE_INVALID", str(exc)) from exc


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
    import_service = services(request).project_import_service
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


@router.post(
    "/{project_id}/refresh",
    response_model=ProjectResponse,
    responses=error_responses(404, 422, 500, 503),
    summary="Refresh a registered external project",
)
async def refresh_project(project_id: str, request: Request) -> ProjectResponse:
    service = required_state(request, "project_import_service", "project import service")
    assert isinstance(service, ProjectImportService)
    normalized = _identifier(project_id)
    try:
        record = await service.refresh_project(normalized)
    except ProjectImportError as exc:
        status = 404 if exc.code in {"PROJECT_NOT_FOUND", "PROJECT_PATH_MISSING"} else 422
        raise APIError(status, exc.code, str(exc), {"supported": exc.supported, **exc.details}) from exc
    return _project(record.to_dict())


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


@router.delete("/{project_id}/recent", response_model=ProjectDeleteResponse)
async def forget_recent_project(project_id: str, request: Request) -> ProjectDeleteResponse:
    service = required_state(request, "project_import_service", "project import service")
    normalized = _identifier(project_id)
    if not await service.forget_project(normalized):
        raise APIError(404, "PROJECT_NOT_FOUND", "Recent folder was not found")
    return ProjectDeleteResponse(project_id=normalized, deleted=True)


@router.post("/{project_id}/locate", response_model=ProjectResponse)
async def locate_recent_project(project_id: str, body: ProjectImportRequest, request: Request) -> ProjectResponse:
    service = required_state(request, "project_import_service", "project import service")
    try:
        record = await service.relocate_project(_identifier(project_id), body.path)
    except ProjectImportError as exc:
        raise APIError(404 if exc.code == "PROJECT_NOT_FOUND" else 422, exc.code, str(exc), exc.details) from exc
    return _project(record.to_dict())


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
