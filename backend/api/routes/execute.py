"""Canonical workflow execution route."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request, Response

from ..dependencies import required_state
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


@router.post(
    "/execute",
    response_model=ExecuteResponse,
    responses={**error_responses(409, 422, 500, 503), "default": {"model": ErrorResponse}},
    summary="Execute a PromptForge workflow",
)
async def execute_workflow(
    body: ExecuteRequest,
    background_tasks: BackgroundTasks,
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
                tool_executor=request.app.state.tool_executor,
                progress_callback=progress,
                metrics_registry=getattr(request.app.state, "metrics_registry", None),
                runtime_logger=getattr(request.app.state, "runtime_logger", None),
            )
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

    background_tasks.add_task(run_workflow)
    return ExecuteResponse(
        status="RUNNING",
        task_id=task_id,
        execution_time_ms=round((time.monotonic() - started) * 1000),
        steps=[],
        failures=[],
    )
