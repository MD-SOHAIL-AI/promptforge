"""Standalone project build route."""

from fastapi import APIRouter, Request, Header
import asyncio
import re
from pathlib import Path
import logging

from ...services.platformio_service import PlatformIOService
from ...services.project_service import ProjectService
from ..dependencies import required_state, resolve_project_from_request, services
from ..errors import APIError, error_responses
from ..schemas.build import BuildRequest, BuildResponse, BuildResultResponse

router = APIRouter(tags=["build"])
logger = logging.getLogger(__name__)


@router.post("/build/operations", status_code=202)
async def accept_build(body: BuildRequest, request: Request, idempotency_key: str = Header(alias="Idempotency-Key")):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", idempotency_key):
        raise APIError(422, "BUILD_OPERATION_ID_INVALID", "A valid build operation ID is required.")
    metadata = await resolve_project_from_request(request, body.project_id)
    changes = services(request).change_set_service
    snapshot = await asyncio.to_thread(changes.inspect_workspace, metadata.project_path)
    async def execute():
        response = (await build_project(body, request, operation_id=idempotency_key)).model_dump(mode="json")
        revision = (await asyncio.to_thread(changes.inspect_workspace, metadata.project_path)).revision
        response["result"]["metadata"].update(project_id=body.project_id, environment=body.environment,
            source_revision=snapshot.revision, operation_id=idempotency_key)
        return response, revision
    try:
        return request.app.state.build_operations.accept(idempotency_key, project_id=body.project_id,
            environment=body.environment, source_revision=snapshot.revision, execute=execute)
    except ValueError as exc:
        raise APIError(409, "BUILD_OPERATION_CONFLICT", "This operation ID belongs to a different build.") from exc


@router.get("/build/operations/{operation_id}")
async def observe_build(operation_id: str, request: Request):
    try:
        operation = request.app.state.build_operations.get(operation_id)
    except KeyError as exc:
        raise APIError(404, "BUILD_OPERATION_NOT_FOUND", "The saved build operation was not found.") from exc
    if operation["state"] == "passed":
        metadata = await resolve_project_from_request(request, operation["project_id"])
        revision = (await asyncio.to_thread(services(request).change_set_service.inspect_workspace, metadata.project_path)).revision
        if revision != operation["source_revision"]:
            operation.update(state="stale", message="Source changed after this build. Compile the current revision before flashing.")
            request.app.state.build_operations._save(operation)
    return operation


@router.post(
    "/build",
    response_model=BuildResponse,
    responses=error_responses(404, 422, 500, 503),
    summary="Build a managed firmware project",
)
async def build_project(body: BuildRequest, request: Request, operation_id: str | None = None) -> BuildResponse:
    projects = required_state(request, "project_service", "project service")
    platformio = required_state(request, "platformio_service", "PlatformIO")
    assert isinstance(projects, ProjectService)
    assert isinstance(platformio, PlatformIOService)
    metadata = await resolve_project_from_request(request, body.project_id)
    root = Path(metadata.project_path)
    ini = root / "platformio.ini"
    logger.info(
        "Running build in workspace root: %s project_id=%s platformio_ini=%s platformio_exists=%s",
        root,
        body.project_id,
        ini,
        ini.is_file(),
    )
    try:
        result = await platformio.build(
            metadata.project_path,
            environment=body.environment,
        )
    except (OSError, ValueError) as exc:
        message = _build_error_message(root, exc)
        raise APIError(422, "BUILD_REQUEST_INVALID", message, {"project_id": body.project_id, "rootPath": str(root)}) from exc
    workspace = services(request).workspace_manager
    if workspace is not None:
        _save_build_workspace(workspace, body.project_id, result, operation_id=operation_id)
    return BuildResponse(
        project_id=body.project_id,
        result=BuildResultResponse.model_validate(result.api_dict()),
    )


def _save_build_workspace(workspace: object, project_id: str, result: object, *, operation_id: str | None = None) -> None:
    try:
        # Each invocation is an independent, immutable history entry.
        import uuid
        execution_id = f"build-{operation_id or uuid.uuid4().hex}"
        api_dict = result.api_dict()
        workspace.save_artifact(execution_id, "build_report.json", api_dict)
        workspace.save_log(execution_id, "build", getattr(result, "message", "Build completed"))
        firmware_path = getattr(result, "firmware_path", None)
        if getattr(result, "success", False) and firmware_path:
            path = Path(firmware_path)
            if path.name in {"firmware.bin", "firmware.elf"}:
                workspace.save_build(
                    execution_id,
                    path.name,
                    path,
                    metadata={
                        "project_id": project_id,
                        "status": "SUCCESS",
                        "target_board": getattr(result, "board", "UNKNOWN") or "UNKNOWN",
                        "framework": "PlatformIO",
                        "duration_ms": api_dict.get("duration_ms"),
                    },
                )
    except Exception:
        logger.exception("Build evidence persistence failed for project=%s operation=%s", project_id, operation_id)
        raise


def _build_error_message(root: Path, exc: Exception) -> str:
    if not (root / "platformio.ini").is_file():
        return f"Build requires platformio.ini. Generate or initialize a PlatformIO project first. Workspace root: {root}"
    return str(exc).strip() or "Project could not be built"
