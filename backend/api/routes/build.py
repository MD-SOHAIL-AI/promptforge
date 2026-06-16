"""Standalone project build route."""

from fastapi import APIRouter, Request
from pathlib import Path

from ...services.platformio_service import PlatformIOService
from ...services.project_service import ProjectService
from ..dependencies import required_state, resolve_project
from ..errors import APIError, error_responses
from ..schemas.build import BuildRequest, BuildResponse, BuildResultResponse

router = APIRouter(tags=["build"])


@router.post(
    "/build",
    response_model=BuildResponse,
    responses=error_responses(404, 422, 500, 503),
    summary="Build a managed firmware project",
)
async def build_project(body: BuildRequest, request: Request) -> BuildResponse:
    projects = required_state(request, "project_service", "project service")
    platformio = required_state(request, "platformio_service", "PlatformIO")
    assert isinstance(projects, ProjectService)
    assert isinstance(platformio, PlatformIOService)
    metadata = await resolve_project(projects, body.project_id)
    try:
        result = await platformio.build(
            metadata.project_path,
            environment=body.environment,
        )
    except (OSError, ValueError) as exc:
        raise APIError(422, "BUILD_REQUEST_INVALID", "Project could not be built") from exc
    workspace = getattr(request.app.state, "workspace_manager", None)
    if workspace is not None:
        _save_build_workspace(workspace, body.project_id, result)
    return BuildResponse(
        project_id=body.project_id,
        result=BuildResultResponse.model_validate(result.api_dict()),
    )


def _save_build_workspace(workspace: object, project_id: str, result: object) -> None:
    try:
        execution_id = f"build-{project_id}"
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
        pass
