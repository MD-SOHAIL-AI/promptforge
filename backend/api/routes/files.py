"""Managed workspace file routes."""

from __future__ import annotations

import posixpath
from pathlib import PurePosixPath

from fastapi import APIRouter, Query, Request

from ...contracts.generated_project import GeneratedFile, GeneratedProject
from ...services.project_service import ProjectService, ProjectServiceError
from ..dependencies import required_state, resolve_project
from ..errors import APIError, error_responses
from ..schemas.files import (
    FileContentResponse,
    FileCreateRequest,
    FileDeleteResponse,
    FileUpdateRequest,
    ProjectFilesResponse,
    WorkspaceEntryResponse,
)

router = APIRouter(tags=["files"])

_FOLDER_MARKER = ".promptforge-folder"


@router.get(
    "/projects/{project_id}/files",
    response_model=ProjectFilesResponse,
    responses=error_responses(404, 500, 503),
    summary="List project workspace entries",
)
async def list_project_files(project_id: str, request: Request) -> ProjectFilesResponse:
    service = _project_service(request)
    project = await _load_project(service, project_id)
    return ProjectFilesResponse(
        project_id=project.project_id,
        project_name=project.project_name,
        entries=_entries(project),
    )


@router.get(
    "/files/content",
    response_model=FileContentResponse,
    responses=error_responses(404, 422, 500, 503),
    summary="Read project file content",
)
async def get_file_content(
    request: Request,
    project_id: str = Query(...),
    path: str = Query(...),
) -> FileContentResponse:
    project = await _load_project(_project_service(request), project_id)
    normalized = _workspace_path(path)
    file = _find_file(project, normalized)
    if file is None or _is_folder_marker(file.path):
        raise APIError(404, "FILE_NOT_FOUND", "File was not found", {"path": normalized})
    return FileContentResponse(
        project_id=project.project_id,
        path=file.path,
        content=file.content,
        file_type=file.file_type,
    )


@router.post(
    "/files",
    response_model=ProjectFilesResponse,
    responses=error_responses(404, 409, 422, 500, 503),
    summary="Create a project file or folder",
)
async def create_file(body: FileCreateRequest, request: Request) -> ProjectFilesResponse:
    service = _project_service(request)
    project = await _load_project(service, body.project_id)
    path = _workspace_path(body.path)
    target = _folder_marker(path) if body.kind == "folder" else path
    if _path_exists(project, target):
        raise APIError(409, "FILE_EXISTS", "Workspace entry already exists", {"path": path})
    if body.kind == "folder" and _folder_has_entries(project, path):
        raise APIError(409, "FOLDER_EXISTS", "Workspace folder already exists", {"path": path})
    files = [
        *project.files,
        GeneratedFile(
            path=target,
            content=body.content,
            file_type=body.file_type or _file_type(target),
        ),
    ]
    saved = await _save_project(service, _replace_files(project, files))
    loaded = await service.load_project(saved.project_name)
    return ProjectFilesResponse(
        project_id=loaded.project_id,
        project_name=loaded.project_name,
        entries=_entries(loaded),
    )


@router.put(
    "/files",
    response_model=FileContentResponse | ProjectFilesResponse,
    responses=error_responses(404, 409, 422, 500, 503),
    summary="Update, rename, or move a project file or folder",
)
async def update_file(
    body: FileUpdateRequest,
    request: Request,
) -> FileContentResponse | ProjectFilesResponse:
    service = _project_service(request)
    project = await _load_project(service, body.project_id)
    path = _workspace_path(body.path)
    new_path = _workspace_path(body.new_path) if body.new_path else None
    if body.kind == "folder":
        updated = _rename_folder(project, path, new_path) if new_path else project.files
        saved = await _save_project(service, _replace_files(project, updated))
        loaded = await service.load_project(saved.project_name)
        return ProjectFilesResponse(
            project_id=loaded.project_id,
            project_name=loaded.project_name,
            entries=_entries(loaded),
        )

    current = _find_file(project, path)
    if current is None or _is_folder_marker(current.path):
        raise APIError(404, "FILE_NOT_FOUND", "File was not found", {"path": path})
    target_path = new_path or current.path
    if target_path != current.path and _path_exists(project, target_path):
        raise APIError(
            409,
            "FILE_EXISTS",
            "Workspace entry already exists",
            {"path": target_path},
        )
    updated_file = GeneratedFile(
        path=target_path,
        content=current.content if body.content is None else body.content,
        file_type=body.file_type or current.file_type,
    )
    files = tuple(updated_file if item.path == current.path else item for item in project.files)
    saved = await _save_project(service, _replace_files(project, files))
    loaded = await service.load_project(saved.project_name)
    file = _find_file(loaded, target_path)
    if file is None:
        raise APIError(500, "FILE_SAVE_FAILED", "File save could not be verified")
    return FileContentResponse(
        project_id=loaded.project_id,
        path=file.path,
        content=file.content,
        file_type=file.file_type,
    )


@router.delete(
    "/files",
    response_model=FileDeleteResponse,
    responses=error_responses(404, 422, 500, 503),
    summary="Delete a project file or folder",
)
async def delete_file(
    request: Request,
    project_id: str = Query(...),
    path: str = Query(...),
) -> FileDeleteResponse:
    service = _project_service(request)
    project = await _load_project(service, project_id)
    normalized = _workspace_path(path)
    files = [
        item
        for item in project.files
        if item.path != normalized and not item.path.startswith(f"{normalized}/")
    ]
    deleted_count = len(project.files) - len(files)
    if deleted_count == 0:
        raise APIError(404, "FILE_NOT_FOUND", "Workspace entry was not found", {"path": normalized})
    if not files:
        raise APIError(422, "PROJECT_EMPTY", "Project must contain at least one file")
    await _save_project(service, _replace_files(project, files))
    return FileDeleteResponse(
        project_id=project.project_id,
        path=normalized,
        deleted=True,
        deleted_count=deleted_count,
    )


def _project_service(request: Request) -> ProjectService:
    service = required_state(request, "project_service", "project service")
    assert isinstance(service, ProjectService)
    return service


async def _load_project(service: ProjectService, project_id: str) -> GeneratedProject:
    metadata = await resolve_project(service, _identifier(project_id))
    try:
        return await service.load_project(metadata.project_name)
    except ProjectServiceError as exc:
        raise APIError(500, "PROJECT_SERVICE_ERROR", "Project could not be loaded") from exc


async def _save_project(service: ProjectService, project: GeneratedProject):
    try:
        return await service.save_project(project)
    except ProjectServiceError as exc:
        raise APIError(500, "PROJECT_SERVICE_ERROR", "Project could not be saved") from exc


def _replace_files(
    project: GeneratedProject,
    files: list[GeneratedFile] | tuple[GeneratedFile, ...],
) -> GeneratedProject:
    return GeneratedProject(
        project_id=project.project_id,
        project_name=project.project_name,
        target_board=project.target_board,
        framework=project.framework,
        files=tuple(files),
        created_at=project.created_at,
        metadata=project.metadata,
    )


def _entries(project: GeneratedProject) -> list[WorkspaceEntryResponse]:
    folders: set[str] = set()
    files: list[WorkspaceEntryResponse] = []
    for generated_file in project.files:
        parts = PurePosixPath(generated_file.path).parts
        for index in range(1, len(parts)):
            folders.add("/".join(parts[:index]))
        if _is_folder_marker(generated_file.path):
            folders.add(generated_file.path.rsplit("/", 1)[0])
            continue
        files.append(
            WorkspaceEntryResponse(
                path=generated_file.path,
                kind="file",
                file_type=generated_file.file_type,
            )
        )
    folder_entries = [
        WorkspaceEntryResponse(path=path, kind="folder", file_type=None)
        for path in folders
    ]
    return sorted(
        [*folder_entries, *files],
        key=lambda item: (item.path.casefold(), item.kind),
    )


def _rename_folder(
    project: GeneratedProject,
    path: str,
    new_path: str | None,
) -> tuple[GeneratedFile, ...]:
    if new_path is None:
        return project.files
    if new_path == path:
        return project.files
    if _path_exists(project, new_path) or _folder_has_entries(project, new_path):
        raise APIError(409, "FOLDER_EXISTS", "Workspace folder already exists", {"path": new_path})
    prefix = f"{path}/"
    updated: list[GeneratedFile] = []
    renamed = 0
    for item in project.files:
        if item.path == path or item.path.startswith(prefix):
            suffix = item.path[len(path) :].lstrip("/")
            updated.append(
                GeneratedFile(
                    path=posixpath.join(new_path, suffix) if suffix else new_path,
                    content=item.content,
                    file_type=item.file_type,
                )
            )
            renamed += 1
        else:
            updated.append(item)
    if renamed == 0:
        raise APIError(404, "FOLDER_NOT_FOUND", "Workspace folder was not found", {"path": path})
    return tuple(updated)


def _find_file(project: GeneratedProject, path: str) -> GeneratedFile | None:
    return next((item for item in project.files if item.path == path), None)


def _path_exists(project: GeneratedProject, path: str) -> bool:
    normalized = path.casefold()
    return any(item.path.casefold() == normalized for item in project.files)


def _folder_has_entries(project: GeneratedProject, path: str) -> bool:
    prefix = f"{path.casefold()}/"
    return any(item.path.casefold().startswith(prefix) for item in project.files)


def _folder_marker(path: str) -> str:
    return f"{path}/{_FOLDER_MARKER}"


def _is_folder_marker(path: str) -> bool:
    return path.endswith(f"/{_FOLDER_MARKER}")


def _workspace_path(value: str) -> str:
    try:
        probe = GeneratedFile(path=value, content="", file_type="text")
    except ValueError as exc:
        raise APIError(422, "INVALID_WORKSPACE_PATH", "Workspace path is invalid") from exc
    return probe.path


def _file_type(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower().lstrip(".")
    if suffix:
        return suffix
    name = PurePosixPath(path).name.lower()
    if name == "platformio.ini":
        return "ini"
    return "text"


def _identifier(value: str) -> str:
    if not value or len(value) > 128 or not all(
        character.isalnum() or character in "._-" for character in value
    ):
        raise APIError(422, "INVALID_PROJECT_ID", "Project ID is invalid")
    return value
