"""Safe conversion from frozen PromptForge contracts to API schemas."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from ..agent.coordinator import ExecutionFailure, ExecutionOutcome, StepResult
from ..tools.board_detector import BoardInfo
from ..workflow.generate_code_handler import GenerateCodeResult
from .schemas.execution import (
    ExecuteResponse,
    ExecutionFailureResponse,
    ExecutionStepResponse,
)


def execution_response(outcome: ExecutionOutcome) -> ExecuteResponse:
    return ExecuteResponse(
        status=outcome.status.value,
        task_id=outcome.plan.task_id,
        execution_time_ms=round(outcome.execution_time * 1000),
        steps=[_step(item) for item in outcome.step_results],
        failures=[_failure(item) for item in outcome.failures],
    )


def safe_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): safe_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [safe_json(item) for item in value]
    api_dict = getattr(value, "api_dict", None)
    if callable(api_dict):
        return safe_json(api_dict())
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return safe_json(to_dict())
    if is_dataclass(value):
        return safe_json(asdict(value))
    return str(value)


def _step(item: StepResult) -> ExecutionStepResponse:
    return ExecutionStepResponse(
        step=item.step.value,
        success=item.success,
        tool_name=item.tool_name,
        execution_time_ms=round(item.execution_time * 1000),
        result=_result(item.result),
        failure=_failure(item.failure) if item.failure else None,
    )


def _failure(item: ExecutionFailure) -> ExecutionFailureResponse:
    return ExecutionFailureResponse(
        step=item.step.value,
        message=item.message,
        category=item.category,
        recoverable=item.recoverable,
        fatal=item.fatal,
        tool_name=item.tool_name,
        exception_type=item.exception_type,
    )


def _result(value: object) -> dict[str, Any] | list[Any] | None:
    if value is None:
        return None
    if isinstance(value, GenerateCodeResult):
        project = value.generated_project
        return {
            "status": value.status.value,
            "project_id": project.project_id if project else None,
            "project_name": project.project_name if project else None,
            "project_path": value.project_path,
            "generation_time_ms": value.generation_time_ms,
            "warnings": list(value.warnings),
        }
    if isinstance(value, (list, tuple)) and all(
        isinstance(item, BoardInfo) for item in value
    ):
        return [safe_json(item) for item in value]
    converted = safe_json(value)
    if isinstance(converted, (dict, list)):
        return converted
    return {"value": converted}
