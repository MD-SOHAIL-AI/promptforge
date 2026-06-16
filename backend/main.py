"""Application composition for the canonical PromptForge workflow."""

from __future__ import annotations
import asyncio
import uuid
import inspect
import logging
from pathlib import Path
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from .agent.coordinator import (
    Coordinator,
    ExecutionOutcome,
    StepResult,
    ToolInvocation,
)
from .agent.planner import Planner
from .contracts.execution_context import ExecutionContext
from .contracts.execution_plan import ExecutionPlan, ExecutionStep
from .contracts.task import Task
from .observability.metrics import MetricsRegistry
from .observability.runtime_logs import RuntimeLogger
from .runtime.result import BuildResult
from .runtime.subprocess_mgr import SubprocessManager
from .services.code_generation_service import CodeGenerationService
from .services.project_service import ProjectService
from .tools.board_detector import BoardInfo, BoardType
from .tools.build_firmware import BuildArtifact
from .tools.tool_registry import execute_tool, register_defaults
from .tools.wokwi_simulator import SimulationConfig
from .validation.board_validator import BoardValidator
from .workflow.adapters.build_adapter import BuildAdapter
from .workflow.adapters.flash_adapter import FlashAdapter
from .workflow.adapters.generation_adapter import GenerationAdapter
from .workflow.adapters.monitor_adapter import MonitorAdapter
from .workflow.generate_code_handler import (
    GenerateCodeHandler,
    GenerateCodeResult,
)
from .workflow.workflow_runner import WorkflowRunner

__all__ = ["execute_prompt", "execute_task"]

ProgressCallback = Callable[
    [str, Mapping[str, object]],
    object | Awaitable[object],
]

logger = logging.getLogger(__name__)


async def execute_prompt(
    prompt: str,
    *,
    code_generation_service: CodeGenerationService,
    project_service: ProjectService,
    subprocess_manager: SubprocessManager,
    serial_runtime: object | None = None,
    task_id: str | None = None,
    tool_executor: Callable[..., Any] = execute_tool,
    progress_callback: ProgressCallback | None = None,
    metrics_registry: MetricsRegistry | None = None,
    runtime_logger: RuntimeLogger | None = None,
) -> ExecutionOutcome:
    """Create a canonical task and execute it through the workflow."""

    task = Task(
        task_id=task_id or f"task-{uuid.uuid4().hex}",
        prompt=prompt,
    )
    await _notify(progress_callback, "TASK_CREATED", {"task_id": task.task_id})
    _runtime_log(
        runtime_logger,
        "workflow",
        "workflow started",
        task_id=task.task_id,
        metadata={"prompt_length": len(prompt)},
    )
    outcome = await execute_task(
        task,
        code_generation_service=code_generation_service,
        project_service=project_service,
        subprocess_manager=subprocess_manager,
        serial_runtime=serial_runtime,
        tool_executor=tool_executor,
        progress_callback=progress_callback,
        metrics_registry=metrics_registry,
        runtime_logger=runtime_logger,
    )
    await _notify(
        progress_callback,
        "WORKFLOW_COMPLETED",
        {
            "task_id": task.task_id,
            "status": outcome.status.value,
            "execution_time_ms": round(outcome.execution_time * 1000),
        },
    )
    _runtime_log(
        runtime_logger,
        "workflow",
        "workflow completed",
        task_id=task.task_id,
        metadata={
            "status": outcome.status.value,
            "execution_time_ms": round(outcome.execution_time * 1000),
        },
    )
    return outcome


async def execute_task(
    task: Task,
    *,
    code_generation_service: CodeGenerationService,
    project_service: ProjectService,
    subprocess_manager: SubprocessManager,
    serial_runtime: object | None = None,
    planner: Planner | None = None,
    tool_executor: Callable[..., Any] = execute_tool,
    progress_callback: ProgressCallback | None = None,
    metrics_registry: MetricsRegistry | None = None,
    runtime_logger: RuntimeLogger | None = None,
) -> ExecutionOutcome:
    """Execute one task through planning, generation, tools, and coordination."""

    if not isinstance(task, Task):
        raise TypeError("task must be a Task")
    if not callable(tool_executor):
        raise ValueError("tool_executor must be callable")
    if progress_callback is not None and not callable(progress_callback):
        raise ValueError("progress_callback must be callable")

    async def coordinator_progress(
        event: str,
        payload: Mapping[str, object],
    ) -> None:
        step = payload.get("step")
        if not isinstance(step, ExecutionStep):
            return
        names = {
            ExecutionStep.GENERATE_CODE: "CODE_GENERATION",
            ExecutionStep.BUILD_FIRMWARE: "BUILD",
            ExecutionStep.FLASH_FIRMWARE: "FLASH",
            ExecutionStep.START_MONITOR: "MONITOR",
        }
        prefix = names.get(step)
        if prefix is None:
            return
        if event == "STEP_STARTED":
            await _notify(
                progress_callback,
                f"{prefix}_STARTED",
                {"task_id": task.task_id, "step": step.value},
            )
            return
        if event != "STEP_COMPLETED" or step is ExecutionStep.START_MONITOR:
            return
        result = payload.get("result")
        success = bool(getattr(result, "success", False))
        duration = float(getattr(result, "execution_time", 0.0))
        event_payload: dict[str, object] = {
            "task_id": task.task_id,
            "step": step.value,
            "success": success,
            "execution_time_ms": round(duration * 1000),
        }
        failure = getattr(result, "failure", None)
        if failure is not None:
            event_payload["failure"] = {
                "category": getattr(failure, "category", "UNKNOWN"),
                "message": getattr(failure, "message", "Execution failed"),
            }
        await _notify(progress_callback, f"{prefix}_COMPLETED", event_payload)

    context = _context_from_task(task)
    generate_handler = GenerateCodeHandler(
        code_generation_service,
        project_service,
        context,
    )
    coordinator = Coordinator(
        tool_executor=tool_executor,
        step_handlers={ExecutionStep.GENERATE_CODE: generate_handler},
        debug_on_failure=False,
        progress_callback=coordinator_progress,
    )

    def invocation_factory(
        received_task: Task,
        plan: ExecutionPlan,
        execution_context: ExecutionContext,
    ) -> dict[ExecutionStep, object]:
        if received_task is not task or execution_context is not context:
            raise ValueError("workflow dependencies received an unexpected task context")
        return _invocations(
            plan,
            context=execution_context,
            subprocess_manager=subprocess_manager,
            serial_runtime=serial_runtime,
        )

    runner = WorkflowRunner(
        planner or Planner(),
        coordinator,
        context_factory=lambda received_task, plan: context,
        invocation_factory=invocation_factory,
        progress_callback=progress_callback,
    )
    outcome = await runner.run(task, context=context)
    _record_outcome(metrics_registry, runtime_logger, outcome)
    return outcome


async def _notify(
    callback: ProgressCallback | None,
    event: str,
    payload: Mapping[str, object],
) -> None:
    if callback is None:
        return
    try:
        result = callback(event, payload)
        if inspect.isawaitable(result):
            await result
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Workflow progress callback failed", extra={"event": event})


def _invocations(
    plan: ExecutionPlan,
    *,
    context: ExecutionContext,
    subprocess_manager: SubprocessManager,
    serial_runtime: object | None,
) -> dict[ExecutionStep, object]:
    def build_invocation(
        execution_plan: ExecutionPlan,
        step: ExecutionStep,
        completed: tuple[StepResult, ...],
    ) -> ToolInvocation:
        del execution_plan, step
        project, generated_context = _project_state(completed, context)
        config = GenerationAdapter.to_build_config(
            project,
            context=generated_context,
        )
        return ToolInvocation(args=(config, subprocess_manager))

    def flash_invocation(
        execution_plan: ExecutionPlan,
        step: ExecutionStep,
        completed: tuple[StepResult, ...],
    ) -> ToolInvocation:
        del step
        artifact, build_context = _build_state(completed, context)
        board = _selected_board(execution_plan, completed)
        _validate_board_before_flash(execution_plan, artifact, board, build_context)
        config, _ = FlashAdapter.adapt(board, artifact, build_context)
        return ToolInvocation(
            args=(artifact, board, config, subprocess_manager)
        )

    def monitor_invocation(
        execution_plan: ExecutionPlan,
        step: ExecutionStep,
        completed: tuple[StepResult, ...],
    ) -> ToolInvocation:
        del step
        if ExecutionStep.FLASH_FIRMWARE in execution_plan.execution_steps:
            _require_result(completed, ExecutionStep.FLASH_FIRMWARE)
        artifact, build_context = _build_state(completed, context)
        board = _selected_board(execution_plan, completed)
        flash_config, flash_context = FlashAdapter.adapt(
            board,
            artifact,
            build_context,
        )
        del flash_config
        monitor_config, _ = MonitorAdapter.adapt(board, flash_context)
        kwargs = {} if serial_runtime is None else {"runtime": serial_runtime}
        return ToolInvocation(args=(monitor_config,), kwargs=kwargs)

    def simulation_invocation(
        execution_plan: ExecutionPlan,
        step: ExecutionStep,
        completed: tuple[StepResult, ...],
    ) -> ToolInvocation:
        del step
        artifact, _ = _build_state(completed, context)
        return ToolInvocation(
            args=(
                artifact,
                _board_type(execution_plan.target_board),
                SimulationConfig(),
                subprocess_manager,
            )
        )

    invocations: dict[ExecutionStep, object] = {
        ExecutionStep.BUILD_FIRMWARE: build_invocation,
        ExecutionStep.FLASH_FIRMWARE: flash_invocation,
        ExecutionStep.START_MONITOR: monitor_invocation,
        ExecutionStep.START_SIMULATION: simulation_invocation,
    }
    if ExecutionStep.DETECT_BOARD in plan.execution_steps:
        invocations[ExecutionStep.DETECT_BOARD] = ToolInvocation()
    return invocations


def _generation_state(
    completed: tuple[StepResult, ...],
) -> tuple[GenerateCodeResult, ExecutionContext]:
    result = _require_result(completed, ExecutionStep.GENERATE_CODE)
    if not isinstance(result, GenerateCodeResult) or not result.success:
        raise ValueError("GENERATE_CODE did not produce a successful result")
    context = result.execution_context
    if context is None or result.generated_project is None:
        raise ValueError("GENERATE_CODE did not propagate project context")
    return result, context


def _build_state(
    completed: tuple[StepResult, ...],
    context: ExecutionContext,
) -> tuple[object, ExecutionContext]:
    generated_context = context
    if any(item.step is ExecutionStep.GENERATE_CODE for item in completed):
        _, generated_context = _generation_state(completed)
    if not any(item.step is ExecutionStep.BUILD_FIRMWARE for item in completed):
        return _artifact_from_context(generated_context), generated_context
    result = _require_result(completed, ExecutionStep.BUILD_FIRMWARE)
    if not isinstance(result, BuildResult):
        raise ValueError("BUILD_FIRMWARE did not return BuildResult")
    return BuildAdapter.adapt(result, generated_context)


def _artifact_from_context(context: ExecutionContext) -> BuildArtifact:
    data = context.build_artifact
    if data is None:
        if context.firmware_path is None:
            raise ValueError("build artifact context is missing")
        data = {}
    raw_path = data.get("path", context.firmware_path) if isinstance(data, Mapping) else context.firmware_path
    raw_environment = data.get("environment", "default") if isinstance(data, Mapping) else "default"
    raw_type = data.get("artifact_type") if isinstance(data, Mapping) else None
    raw_size = data.get("size_bytes", 1) if isinstance(data, Mapping) else 1
    if raw_type is None and isinstance(raw_path, str):
        raw_type = Path(raw_path).suffix.lstrip(".") or "bin"
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("build artifact path is missing")
    if not isinstance(raw_environment, str) or not raw_environment.strip():
        raise ValueError("build artifact environment is missing")
    if not isinstance(raw_type, str) or not raw_type.strip():
        raise ValueError("build artifact type is missing")
    if not isinstance(raw_size, int) or isinstance(raw_size, bool) or raw_size <= 0:
        raise ValueError("build artifact size_bytes must be positive")
    return BuildArtifact(
        path=Path(raw_path),
        environment=raw_environment,
        artifact_type=raw_type,
        size_bytes=raw_size,
    )


def _project_state(
    completed: tuple[StepResult, ...],
    context: ExecutionContext,
) -> tuple[object, ExecutionContext]:
    if any(item.step is ExecutionStep.GENERATE_CODE for item in completed):
        generation, generated_context = _generation_state(completed)
        project = generation.generated_project
        if project is None:
            raise ValueError("GENERATE_CODE returned no project")
        return project, generated_context
    if context.project_path is None:
        raise ValueError("BUILD_FIRMWARE requires a project_path for this task")
    return context.project_path, context


def _validate_board_before_flash(
    plan: ExecutionPlan,
    artifact: BuildArtifact,
    board: BoardInfo,
    context: ExecutionContext,
) -> None:
    project_context = {
        "target_board": _string_value(plan.target_board),
        "framework": _board_validation_framework(plan.framework),
        "firmware_size_bytes": artifact.size_bytes,
        "metadata": dict(context.metadata),
    }
    selected_board = {
        "target_board": board.board_type.value,
        "firmware_size_bytes": artifact.size_bytes,
    }
    result = BoardValidator().validate(project_context, selected_board)
    if not result.compatible:
        details = "; ".join(
            f"{issue.rule_id}: {issue.message}" for issue in result.errors
        )
        raise ValueError(f"board validation failed: {details}")


def _board_validation_framework(value: object) -> str:
    framework = _string_value(value)
    return "Arduino" if framework == "PlatformIO" else framework


def _selected_board(
    plan: ExecutionPlan,
    completed: tuple[StepResult, ...],
) -> BoardInfo:
    result = _require_result(completed, ExecutionStep.DETECT_BOARD)
    if not isinstance(result, (list, tuple)) or any(
        not isinstance(board, BoardInfo) for board in result
    ):
        raise ValueError("DETECT_BOARD did not return BoardInfo values")

    expected = _board_type(plan.target_board)
    matches = [board for board in result if board.board_type is expected]
    if not matches:
        raise ValueError(f"no detected board matches {expected.value}")
    if len(matches) > 1:
        ports = ", ".join(sorted(board.port for board in matches))
        raise ValueError(
            f"multiple detected boards match {expected.value}: {ports}"
        )
    return matches[0]


def _board_type(value: object) -> BoardType:
    raw = str(getattr(value, "value", value))
    normalized = raw.strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "ESP32": BoardType.ESP32,
        "ESP32_S3": BoardType.ESP32_S3,
        "ESP32_C3": BoardType.ESP32_C3,
        "STM32": BoardType.STM32,
        "ARDUINO_UNO": BoardType.ARDUINO_UNO,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported target board for hardware execution: {raw}") from exc


def _string_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _require_result(
    completed: tuple[StepResult, ...],
    step: ExecutionStep,
) -> object:
    for item in reversed(completed):
        if item.step is step:
            if not item.success:
                raise ValueError(f"{step.value} did not complete successfully")
            return item.result
    raise ValueError(f"{step.value} result is missing")


def _context_from_task(task: Task) -> ExecutionContext:
    metadata = dict(task.metadata)
    context_data = metadata.get("execution_context")
    if isinstance(context_data, Mapping):
        data = dict(context_data)
        data.setdefault("task_id", task.task_id)
        return ExecutionContext.from_dict(data)
    return ExecutionContext(
        task_id=task.task_id,
        project_path=_optional_text(metadata.get("project_path")),
        target_board=_optional_text(metadata.get("target_board")) or "UNKNOWN",
        framework=_optional_text(metadata.get("framework")) or "UNKNOWN",
        firmware_path=_optional_text(metadata.get("firmware_path")),
        build_artifact=(
            metadata.get("build_artifact")
            if isinstance(metadata.get("build_artifact"), Mapping)
            else None
        ),
        board_info=(
            metadata.get("board_info")
            if isinstance(metadata.get("board_info"), Mapping)
            else None
        ),
        metadata={
            "prompt": task.prompt,
            "task": task.to_dict(),
            **metadata,
        },
    )


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _record_outcome(
    metrics: MetricsRegistry | None,
    runtime_logger: RuntimeLogger | None,
    outcome: ExecutionOutcome,
) -> None:
    duration_ms = outcome.execution_time * 1000
    if metrics is not None:
        try:
            metrics.record_execution(
                outcome.status.value == "COMPLETED",
                duration_ms,
            )
            for item in outcome.step_results:
                step_duration_ms = item.execution_time * 1000
                if item.step is ExecutionStep.GENERATE_CODE:
                    metrics.record_generation(item.success, step_duration_ms)
                elif item.step is ExecutionStep.BUILD_FIRMWARE:
                    metrics.record_build(item.success, step_duration_ms)
                elif item.step is ExecutionStep.FLASH_FIRMWARE:
                    metrics.record_flash(item.success, step_duration_ms)
                elif item.step is ExecutionStep.START_MONITOR:
                    metrics.record_monitor_session(item.success, step_duration_ms)
        except Exception:
            logger.exception("Metrics recording failed")
    if runtime_logger is None:
        return
    for item in outcome.step_results:
        log_type = {
            ExecutionStep.GENERATE_CODE: "generation",
            ExecutionStep.BUILD_FIRMWARE: "build",
            ExecutionStep.FLASH_FIRMWARE: "flash",
            ExecutionStep.START_MONITOR: "monitor",
            ExecutionStep.START_SIMULATION: "workflow",
        }.get(item.step, "workflow")
        _runtime_log(
            runtime_logger,
            log_type,
            f"{item.step.value.lower()} {'completed' if item.success else 'failed'}",
            task_id=outcome.plan.task_id,
            metadata={
                "step": item.step.value,
                "success": item.success,
                "execution_time_ms": round(item.execution_time * 1000),
            },
        )


def _runtime_log(
    runtime_logger: RuntimeLogger | None,
    log_type: str,
    message: str,
    *,
    task_id: str,
    metadata: Mapping[str, object],
) -> None:
    if runtime_logger is None:
        return
    try:
        methods = {
            "workflow": runtime_logger.log_workflow,
            "generation": runtime_logger.log_generation,
            "build": runtime_logger.log_build,
            "flash": runtime_logger.log_flash,
            "monitor": runtime_logger.log_monitor,
        }
        methods.get(log_type, runtime_logger.log_workflow)(
            message,
            task_id=task_id,
            metadata=metadata,
        )
    except Exception:
        logger.exception("Runtime logging failed")


# fix: register built-in tools once from the application composition root.
register_defaults()
