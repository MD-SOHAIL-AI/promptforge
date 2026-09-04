"""Canonical workflow execution route."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response

from ..dependencies import required_state, resolve_project_from_request
from ..errors import APIError, error_responses
from ..observability import (
    execution_id_context,
    new_correlation_id,
    request_id_context,
    workflow_id_context,
)
from ..progress import ExecutionProgressHub
from ..schemas.common import ErrorResponse
from ..schemas.execution import ExecuteRequest, ExecuteResponse
from ..serialization import execution_response, safe_json

router = APIRouter(tags=["execution"])
logger = logging.getLogger("promptforge.api")


@dataclass(slots=True)
class _ActiveExecution:
    task: asyncio.Task[None]
    execution_id: str
    workflow_id: str


_active_executions: dict[str, _ActiveExecution] = {}
_active_lock = asyncio.Lock()


@router.post(
    "/execute",
    response_model=ExecuteResponse,
    responses={**error_responses(409, 422, 500, 503), "default": {"model": ErrorResponse}},
    summary="Execute a PromptForge workflow",
)
async def execute_workflow(
    body: ExecuteRequest,
    request: Request,
    response: Response,
) -> ExecuteResponse:
    execute_prompt = required_state(request, "execute_prompt", "workflow")
    code_generation_service = required_state(
        request,
        "code_generation_service",
        "code generation",
    )
    project_service = required_state(request, "project_service", "project service")
    subprocess_manager = required_state(
        request,
        "subprocess_manager",
        "subprocess manager",
    )
    hub = required_state(request, "progress_hub", "execution progress")
    assert isinstance(hub, ExecutionProgressHub)

    task_id = body.task_id or f"task-{uuid.uuid4().hex}"
    active_workspace = None
    if body.project_id is not None:
        metadata = await resolve_project_from_request(request, body.project_id)
        active_workspace = _active_workspace(metadata)
    elif body.workspace_root is not None:
        active_workspace = _active_workspace_from_root(body.workspace_root)
    if active_workspace is not None:
        _apply_execute_overrides(
            active_workspace,
            selected_board=body.selected_board,
            selected_framework=body.selected_framework,
            generation_mode=body.generation_mode,
            workspace_root=body.workspace_root,
        )
        logger.info(
            "execute_workspace_context",
            extra={
                "operation": "execute",
                "active_project_id": active_workspace.get("id"),
                "active_workspace_root": active_workspace.get("rootPath"),
                "selected_board": active_workspace.get("selected_board"),
                "selected_framework": active_workspace.get("selected_framework"),
                "generation_mode": active_workspace.get("generation_mode"),
                "platformio_ini_exists": active_workspace.get("has_platformio_ini"),
                "resolved_platformio_ini_path": active_workspace.get("platformioIniPath"),
            },
        )

    if not await hub.begin(task_id):
        raise APIError(
            409,
            "TASK_ID_CONFLICT",
            "Task ID is already active or has already been used",
            {"task_id": task_id},
        )

    execution_id = new_correlation_id("execution")
    workflow_id = new_correlation_id("workflow")
    execution_id_context.set(execution_id)
    workflow_id_context.set(workflow_id)
    response.headers["X-Execution-ID"] = execution_id
    response.headers["X-Workflow-Correlation-ID"] = workflow_id
    started = time.monotonic()

    async def progress(event: str, payload: Any) -> None:
        converted = safe_json(payload)
        await hub.emit(
            event=event,
            task_id=task_id,
            execution_id=execution_id,
            workflow_correlation_id=workflow_id,
            payload=converted if isinstance(converted, dict) else {"value": converted},
        )

    async def run_workflow() -> None:
        logger.info(
            "workflow_started",
            extra={
                "request_id": request_id_context.get(),
                "execution_id": execution_id,
                "workflow_correlation_id": workflow_id,
                "task_id": task_id,
            },
        )
        try:
            outcome = await execute_prompt(
                body.prompt,
                code_generation_service=code_generation_service,
                project_service=project_service,
                subprocess_manager=subprocess_manager,
                serial_runtime=getattr(request.app.state, "serial_runtime", None),
                task_id=task_id,
                active_workspace=active_workspace,
                tool_executor=request.app.state.tool_executor,
                progress_callback=progress,
                metrics_registry=getattr(request.app.state, "metrics_registry", None),
                runtime_logger=getattr(request.app.state, "runtime_logger", None),
            )
        except asyncio.CancelledError:
            logger.info(
                "workflow_cancelled",
                extra={
                    "request_id": request_id_context.get(),
                    "execution_id": execution_id,
                    "workflow_correlation_id": workflow_id,
                    "task_id": task_id,
                },
            )
            await hub.emit(
                event="WORKFLOW_CANCELLED",
                task_id=task_id,
                execution_id=execution_id,
                workflow_correlation_id=workflow_id,
                payload={"status": "CANCELLED", "message": "Workflow cancelled by user"},
            )
            raise
        except Exception as exc:
            logger.exception(
                "workflow_failed",
                extra={
                    "request_id": request_id_context.get(),
                    "execution_id": execution_id,
                    "workflow_correlation_id": workflow_id,
                    "task_id": task_id,
                    "exception_type": type(exc).__name__,
                },
            )
            await hub.emit(
                event="WORKFLOW_FAILED",
                task_id=task_id,
                execution_id=execution_id,
                workflow_correlation_id=workflow_id,
                payload={"status": "FAILED", "exception_type": type(exc).__name__},
            )
            return
        finally:
            async with _active_lock:
                current = _active_executions.get(task_id)
                if current is not None and current.task is asyncio.current_task():
                    _active_executions.pop(task_id, None)

        result = execution_response(outcome)
        logger.info(
            "workflow_completed",
            extra={
                "request_id": request_id_context.get(),
                "execution_id": execution_id,
                "workflow_correlation_id": workflow_id,
                "task_id": task_id,
                "status_code": 200,
                "duration_ms": result.execution_time_ms,
            },
        )

    workflow_task = asyncio.create_task(run_workflow(), name=f"forgex-workflow-{task_id}")
    async with _active_lock:
        _active_executions[task_id] = _ActiveExecution(
            task=workflow_task,
            execution_id=execution_id,
            workflow_id=workflow_id,
        )
    return ExecuteResponse(
        status="RUNNING",
        task_id=task_id,
        execution_time_ms=round((time.monotonic() - started) * 1000),
        steps=[],
        failures=[],
    )


@router.post(
    "/execute/{task_id}/cancel",
    responses={**error_responses(404, 500, 503), "default": {"model": ErrorResponse}},
    summary="Cancel an active PromptForge workflow",
)
async def cancel_workflow(task_id: str, request: Request) -> dict[str, str]:
    if not _valid_task_id(task_id):
        raise APIError(404, "TASK_NOT_FOUND", "Active task was not found", {"task_id": task_id})
    subprocess_manager = required_state(
        request,
        "subprocess_manager",
        "subprocess manager",
    )
    async with _active_lock:
        active = _active_executions.get(task_id)
    if active is None or active.task.done():
        raise APIError(404, "TASK_NOT_FOUND", "Active task was not found", {"task_id": task_id})

    active.task.cancel()
    await subprocess_manager.kill_all()
    return {
        "status": "CANCELLED",
        "task_id": task_id,
        "execution_id": active.execution_id,
    }


def _active_workspace(metadata: object) -> dict[str, object]:
    record_metadata = dict(getattr(metadata, "metadata", {}) or {})
    root = Path(str(getattr(metadata, "project_path"))).expanduser().resolve()
    ini_path = root / "platformio.ini"
    has_platformio_ini = ini_path.is_file() and not ini_path.is_symlink()
    target_board = str(
        record_metadata.get("generation_target_board")
        or getattr(metadata, "target_board", "UNKNOWN")
        or "UNKNOWN"
    )
    framework = str(
        record_metadata.get("generation_framework")
        or getattr(metadata, "framework", "UNKNOWN")
        or "UNKNOWN"
    )
    workspace = {
        "id": str(getattr(metadata, "project_id")),
        "name": str(getattr(metadata, "project_name")),
        "rootPath": str(root),
        "root_path": str(root),
        "type": "external" if bool(getattr(metadata, "metadata", {}).get("external", False)) else "generated",
        "board": target_board,
        "framework": framework,
        "platformioIniPath": str(ini_path),
        "platformio_ini_path": str(ini_path),
        "has_platformio_ini": has_platformio_ini,
        "project_type": record_metadata.get("project_type", "generated"),
    }
    logger.info(
        "active_workspace_resolved",
        extra={
            "project_id": workspace["id"],
            "rootPath": workspace["rootPath"],
            "platformioIniPath": workspace["platformioIniPath"],
            "has_platformio_ini": has_platformio_ini,
        },
    )
    return workspace


def _active_workspace_from_root(root_value: str) -> dict[str, object]:
    root = Path(root_value).expanduser().resolve()
    ini_path = root / "platformio.ini"
    return {
        "id": f"workspace-{abs(hash(str(root).casefold()))}",
        "name": root.name,
        "rootPath": str(root),
        "root_path": str(root),
        "type": "external",
        "board": "UNKNOWN",
        "framework": "UNKNOWN",
        "platformioIniPath": str(ini_path),
        "platformio_ini_path": str(ini_path),
        "has_platformio_ini": ini_path.is_file() and not ini_path.is_symlink(),
        "project_type": "platformio" if ini_path.is_file() else "generic",
    }


def _apply_execute_overrides(
    workspace: dict[str, object],
    *,
    selected_board: str | None,
    selected_framework: str | None,
    generation_mode: str | None,
    workspace_root: str | None,
) -> None:
    if workspace_root:
        root = Path(workspace_root).expanduser().resolve()
        ini = root / "platformio.ini"
        workspace["rootPath"] = str(root)
        workspace["root_path"] = str(root)
        workspace["platformioIniPath"] = str(ini)
        workspace["platformio_ini_path"] = str(ini)
        workspace["has_platformio_ini"] = ini.is_file() and not ini.is_symlink()
    if selected_board:
        workspace["selected_board"] = selected_board
        if str(workspace.get("board", "UNKNOWN")) == "UNKNOWN":
            workspace["board"] = _target_family(selected_board)
    if selected_framework:
        workspace["selected_framework"] = selected_framework
        if str(workspace.get("framework", "UNKNOWN")) == "UNKNOWN":
            workspace["framework"] = _generation_framework(selected_framework)
    workspace["generation_mode"] = generation_mode or "generate_into_open_folder"


def _generation_framework(value: str) -> str:
    normalized = value.casefold()
    if normalized in {"platformio", "pio"}:
        return "PlatformIO"
    if normalized in {"arduino", "arduino framework"}:
        return "PlatformIO"
    if "esp" in normalized and "idf" in normalized:
        return "ESP-IDF"
    return value


def _target_family(board: str) -> str:
    normalized = board.casefold().replace("_", "-").replace(" ", "-")
    if "esp32" in normalized and "s3" in normalized:
        return "ESP32-S3"
    if "esp32" in normalized and "c3" in normalized:
        return "ESP32-C3"
    if "esp32" in normalized or "wroom" in normalized or "wrover" in normalized:
        return "ESP32"
    if "uno" in normalized:
        return "Arduino Uno"
    if "stm32" in normalized:
        return "STM32"
    return "UNKNOWN"


def _valid_task_id(value: str) -> bool:
    return (
        value.startswith("task-")
        and len(value) <= 128
        and all(character.isalnum() or character in "._-" for character in value)
    )
