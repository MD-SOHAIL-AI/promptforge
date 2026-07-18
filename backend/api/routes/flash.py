"""Build-and-flash route using existing PromptForge tooling."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Request

from ...runtime.result import FlashResult
from ...services.platformio_service import PlatformIOService
from ...services.project_service import ProjectService
from ...tools.board_detector import (
    BoardDependencyError,
    BoardDetectionError,
    BoardDetector,
)
from ...tools.flash_firmware import FlashConfig
from ...validation.board_validator import BoardValidator
from ...workflow.adapters.build_adapter import BuildAdapter, BuildAdapterError
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
    projects = required_state(request, "project_service", "project service")
    platformio = required_state(request, "platformio_service", "PlatformIO")
    manager = required_state(request, "subprocess_manager", "subprocess manager")
    detector = required_state(request, "board_detector", "board detector")
    assert isinstance(projects, ProjectService)
    assert isinstance(platformio, PlatformIOService)
    assert isinstance(detector, BoardDetector)

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
        build = await platformio.build(metadata.project_path, environment=body.environment)
    except (OSError, ValueError) as exc:
        message = _flash_build_error_message(root, exc)
        raise APIError(422, "BUILD_REQUEST_INVALID", message, {"project_id": body.project_id, "rootPath": str(root)}) from exc
    build_response = BuildResultResponse.model_validate(build.api_dict())
    workspace = getattr(request.app.state, "workspace_manager", None)
    if workspace is not None:
        _save_flash_build_workspace(workspace, body.project_id, build)
    if not build.success:
        return FlashResponse(project_id=body.project_id, build=build_response, flash=None)

    try:
        artifact = BuildAdapter.to_artifact(build, environment=body.environment)
    except BuildAdapterError as exc:
        raise APIError(500, "INVALID_BUILD_ARTIFACT", "Build output was invalid") from exc
    try:
        boards = await asyncio.to_thread(detector.detect_boards)
    except BoardDependencyError as exc:
        raise APIError(503, "PYSERIAL_REQUIRED", str(exc)) from exc
    except BoardDetectionError as exc:
        raise APIError(503, "BOARD_DETECTION_FAILED", "Connected boards could not be detected") from exc
    board = next((item for item in boards if item.port.casefold() == body.port.casefold()), None)
    if board is None:
        raise APIError(
            404,
            "BOARD_NOT_FOUND",
            f"No matching {body.board_type.value} device detected. Connect a board and try again.",
            {"port": body.port, "board_type": body.board_type.value},
        )
    if board.board_type is not body.board_type:
        raise APIError(
            409,
            "BOARD_TYPE_MISMATCH",
            "Detected board does not match the requested board type",
            {"requested": body.board_type.value, "detected": board.board_type.value},
        )
    board_validation = BoardValidator().validate(
        {
            "target_board": body.board_type.value,
            "framework": "Arduino",
            "firmware_size_bytes": artifact.size_bytes,
        },
        {"target_board": board.board_type.value},
    )
    if not board_validation.compatible:
        raise APIError(
            409,
            "BOARD_VALIDATION_FAILED",
            "Board validation failed before flashing",
            {
                "issues": [
                    {"rule_id": issue.rule_id, "message": issue.message}
                    for issue in board_validation.errors
                ]
            },
        )

    config = FlashConfig(
        port=board.port,
        baudrate=body.baudrate,
        verify=body.verify,
        timeout_s=body.timeout_s,
    )
    result = await request.app.state.tool_executor(
        "flash_firmware",
        artifact,
        board,
        config,
        manager,
    )
    if not isinstance(result, FlashResult):
        raise APIError(500, "INVALID_FLASH_RESULT", "Flash tooling returned an invalid result")
    if workspace is not None:
        _save_flash_workspace(workspace, body.project_id, result)
    return FlashResponse(
        project_id=body.project_id,
        build=build_response,
        flash=FlashResultResponse.model_validate(result.api_dict()),
    )


def _save_flash_build_workspace(workspace: object, project_id: str, build: object) -> None:
    try:
        execution_id = f"flash-{project_id}"
        api_dict = build.api_dict()
        workspace.save_artifact(execution_id, "build_report.json", api_dict)
        workspace.save_log(execution_id, "build", getattr(build, "message", "Build completed"))
        firmware_path = getattr(build, "firmware_path", None)
        if getattr(build, "success", False) and firmware_path:
            path = Path(firmware_path)
            if path.name in {"firmware.bin", "firmware.elf"}:
                workspace.save_build(
                    execution_id,
                    path.name,
                    path,
                    metadata={
                        "project_id": project_id,
                        "status": "SUCCESS",
                        "target_board": getattr(build, "board", "UNKNOWN") or "UNKNOWN",
                        "framework": "PlatformIO",
                        "duration_ms": api_dict.get("duration_ms"),
                    },
                )
    except Exception:
        pass


def _save_flash_workspace(workspace: object, project_id: str, result: FlashResult) -> None:
    try:
        workspace.save_log(
            f"flash-{project_id}",
            "flash",
            result.message,
        )
    except Exception:
        pass


def _flash_build_error_message(root: Path, exc: Exception) -> str:
    if not (root / "platformio.ini").is_file():
        return f"Flash requires platformio.ini. Generate/build a PlatformIO project first. Workspace root: {root}"
    return str(exc).strip() or "Project could not be built before flashing"
