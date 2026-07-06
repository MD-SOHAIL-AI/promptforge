"""Application composition for the canonical PromptForge workflow."""

from __future__ import annotations
import asyncio
import uuid
import inspect
import logging


import re
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from .agent.coordinator import (
    Coordinator,
    ExecutionFailure,
    ExecutionOutcome,
    ExecutionStatus,
    StepResult,
    ToolInvocation,
)
from .agent.planner import Planner
from .contracts.execution_context import ExecutionContext
from .contracts.execution_plan import ExecutionPlan, ExecutionStep, TaskType
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


@dataclass(frozen=True, slots=True)
class HardwarePendingResult:
    success: bool
    status: str
    message: str
    category: str
    stage: str
    expected_board: str

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "status": self.status,
            "message": self.message,
            "category": self.category,
            "stage": self.stage,
            "expected_board": self.expected_board,
        }


async def execute_prompt(
    prompt: str,
    *,
    code_generation_service: CodeGenerationService,
    project_service: ProjectService,
    subprocess_manager: SubprocessManager,
    serial_runtime: object | None = None,
    task_id: str | None = None,
    active_workspace: Mapping[str, object] | None = None,
    tool_executor: Callable[..., Any] = execute_tool,
    progress_callback: ProgressCallback | None = None,
    metrics_registry: MetricsRegistry | None = None,
    runtime_logger: RuntimeLogger | None = None,
) -> ExecutionOutcome:
    """Create a canonical task and execute it through the workflow."""

    task = Task(
        task_id=task_id or f"task-{uuid.uuid4().hex}",
        prompt=prompt,
        metadata={"active_workspace": dict(active_workspace)} if active_workspace else {},
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
            "message": _workflow_completion_message(outcome),
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
        execution_plan = payload.get("plan")
        pending_hardware = (
            isinstance(execution_plan, ExecutionPlan)
            and _is_optional_hardware_step_result(execution_plan, step, result)
        )
        success = True if pending_hardware else bool(getattr(result, "success", False))
        duration = float(getattr(result, "execution_time", 0.0))
        event_payload: dict[str, object] = {
            "task_id": task.task_id,
            "step": step.value,
            "success": success,
            "execution_time_ms": round(duration * 1000),
        }
        failure = getattr(result, "failure", None)
        if pending_hardware:
            event_payload.update(
                {
                    "status": "WAITING_FOR_DEVICE",
                    "category": "NO_DEVICE",
                    "message": _hardware_pending_message(execution_plan),
                }
            )
        elif failure is not None:
            event_payload["failure"] = {
                "category": getattr(failure, "category", "UNKNOWN"),
                "message": getattr(failure, "message", "Execution failed"),
            }
        step_value = getattr(result, "result", None)
        if step is ExecutionStep.GENERATE_CODE and isinstance(step_value, GenerateCodeResult):
            report = step_value.metadata.get("generation_report")
            if isinstance(report, Mapping):
                event_payload["generation_report"] = dict(report)
                event_payload["provider_id"] = report.get("provider_id", "")
                event_payload["model_id"] = report.get("model_id", "")
                event_payload["attempt_count"] = report.get("attempt_count", 0)
                event_payload["repair_used"] = report.get("repair_used", False)
                event_payload["fallback_used"] = report.get("fallback_used", False)
                event_payload["warnings"] = report.get("warnings", [])
            summary = step_value.metadata.get("artifact_summary")
            if isinstance(summary, Mapping):
                event_payload.update(
                    {
                        "artifact_validated": summary.get("generation_satisfied_prompt") is True,
                        "artifact_summary": dict(summary),
                        "workspace_root": summary.get("workspace_root", step_value.project_path or ""),
                        "message": _generation_summary_message(summary, report if isinstance(report, Mapping) else None),
                    }
                )
        elif step is ExecutionStep.BUILD_FIRMWARE and isinstance(step_value, BuildResult):
            metadata = step_value.metadata if isinstance(step_value.metadata, Mapping) else {}
            command = metadata.get("command")
            build_root = metadata.get("build_root")
            event_payload.update(
                {
                    "workspace_root": build_root if isinstance(build_root, str) else "",
                    "command": command if isinstance(command, list) else [],
                    "artifact": step_value.firmware_path or "",
                    "message": step_value.message,
                }
            )
        event_name = f"{prefix}_COMPLETED" if success else f"{prefix}_FAILED"
        await _notify(progress_callback, event_name, event_payload)

    async def generation_progress(payload: Mapping[str, Any]) -> None:
        event_type = payload.get("event_type")
        if not isinstance(event_type, str) or not event_type.strip():
            return
        event_name = _generation_event_name(event_type)
        await _notify(progress_callback, event_name, dict(payload))

    context = _context_from_task(task)
    generate_handler = GenerateCodeHandler(
        code_generation_service,
        project_service,
        context,
        generation_event_callback=generation_progress,
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
        _WorkspaceAwarePlanner(planner or Planner(), context),
        coordinator,
        context_factory=lambda received_task, plan: context,
        invocation_factory=invocation_factory,
        progress_callback=progress_callback,
    )
    outcome = await runner.run(task, context=context)
    outcome = await _repair_build_failures(
        outcome,
        task=task,
        context=context,
        code_generation_service=code_generation_service,
        project_service=project_service,
        subprocess_manager=subprocess_manager,
        tool_executor=tool_executor,
        generation_progress=generation_progress,
        progress_callback=progress_callback,
    )
    outcome = _normalize_optional_hardware_outcome(outcome)
    _record_outcome(metrics_registry, runtime_logger, outcome)
    return outcome


async def _repair_build_failures(
    outcome: ExecutionOutcome,
    *,
    task: Task,
    context: ExecutionContext,
    code_generation_service: CodeGenerationService,
    project_service: ProjectService,
    subprocess_manager: SubprocessManager,
    tool_executor: Callable[..., Any],
    generation_progress: Callable[[Mapping[str, Any]], Awaitable[None]],
    progress_callback: ProgressCallback | None,
) -> ExecutionOutcome:
    if outcome.status is not ExecutionStatus.FAILED:
        return outcome
    if outcome.plan.task_type is not TaskType.FIRMWARE_GENERATION:
        return outcome
    generation = next(
        (item.result for item in outcome.step_results if item.step is ExecutionStep.GENERATE_CODE and item.success),
        None,
    )
    build = next(
        (item.result for item in reversed(outcome.step_results) if item.step is ExecutionStep.BUILD_FIRMWARE),
        None,
    )
    if not isinstance(generation, GenerateCodeResult) or not isinstance(build, BuildResult):
        return outcome
    if build.success:
        return outcome
    if generation.generated_project is None or not generation.project_path:
        return outcome

    started = time.monotonic()
    diagnostics = _sanitized_build_diagnostics(build)
    previous_project = generation.generated_project
    primary_provider = _generation_provider(generation)
    last_generation: GenerateCodeResult = generation
    last_build = build

    for attempt in range(1, 4):
        fallback = attempt == 3
        await _notify(
            progress_callback,
            "BUILD_REPAIR_STARTED",
            {
                "task_id": task.task_id,
                "attempt_number": attempt,
                "max_attempts": 3,
                "fallback": fallback,
                "provider_id": primary_provider,
                "message": "Trying a fallback provider" if fallback else f"Repairing compiler errors ({attempt}/2)",
                "diagnostics": diagnostics,
            },
        )
        context_data = context.to_dict()
        metadata = dict(context_data["metadata"])
        metadata.update(
            {
                "generation_strategy": "one_shot",
                "fallback_enabled": fallback,
                "build_repair": {
                    "attempt_number": attempt,
                    "compiler_diagnostics": diagnostics,
                    "previous_files": [
                        {"path": item.path, "content": item.content[:16_000]}
                        for item in previous_project.files
                    ],
                    "instruction": "Correct the project so PlatformIO compiles. Preserve all requested behavior.",
                },
            }
        )
        metadata["active_workspace"] = {
            **(dict(metadata.get("active_workspace", {})) if isinstance(metadata.get("active_workspace"), Mapping) else {}),
            "id": str(last_generation.metadata.get("project_id", task.task_id)),
            "rootPath": last_generation.project_path,
            "root_path": last_generation.project_path,
            "generation_mode": "modify_existing_project",
            "has_platformio_ini": True,
            "project_type": "platformio",
        }
        if fallback and primary_provider:
            metadata["skip_provider_id"] = primary_provider
        context_data["metadata"] = metadata
        repair_context = ExecutionContext.from_dict(context_data)
        handler = GenerateCodeHandler(
            code_generation_service,
            project_service,
            repair_context,
            generation_event_callback=generation_progress,
        )
        last_generation = await handler.execute(outcome.plan)
        if not last_generation.success or last_generation.generated_project is None or not last_generation.project_path:
            await _notify(
                progress_callback,
                "BUILD_REPAIR_FAILED",
                {
                    "task_id": task.task_id,
                    "attempt_number": attempt,
                    "fallback": fallback,
                    "message": last_generation.message,
                },
            )
            if attempt < 3:
                continue
            return _repair_generation_failure_outcome(outcome, last_generation, started)

        previous_project = last_generation.generated_project
        config = GenerationAdapter.resolve_build_config(
            previous_project,
            project_path=last_generation.project_path,
        )
        produced = tool_executor("build_firmware", config, subprocess_manager)
        last_build = await produced if inspect.isawaitable(produced) else produced
        if not isinstance(last_build, BuildResult):
            raise TypeError("build_firmware must return BuildResult during repair")
        if last_build.success:
            await _notify(
                progress_callback,
                "BUILD_REPAIR_COMPLETED",
                {
                    "task_id": task.task_id,
                    "attempt_number": attempt,
                    "fallback": fallback,
                    "provider_id": _generation_provider(last_generation),
                    "model_id": _generation_model(last_generation),
                    "workspace_root": last_generation.project_path,
                    "message": f"Build succeeded after repair attempt {attempt}",
                    "execution_time_ms": round((time.monotonic() - started) * 1000),
                },
            )
            return ExecutionOutcome(
                plan=outcome.plan,
                status=ExecutionStatus.COMPLETED,
                step_results=(
                    StepResult(step=ExecutionStep.GENERATE_CODE, success=True, result=last_generation),
                    StepResult(step=ExecutionStep.BUILD_FIRMWARE, success=True, result=last_build, tool_name="build_firmware"),
                ),
                failures=(),
                execution_time=outcome.execution_time + (time.monotonic() - started),
            )
        diagnostics = _sanitized_build_diagnostics(last_build)
        await _notify(
            progress_callback,
            "BUILD_REPAIR_FAILED",
            {
                "task_id": task.task_id,
                "attempt_number": attempt,
                "fallback": fallback,
                "provider_id": _generation_provider(last_generation),
                "model_id": _generation_model(last_generation),
                "message": last_build.message,
                "diagnostics": diagnostics,
            },
        )

    failure = _build_execution_failure(last_build)
    failed_step = StepResult(
        step=ExecutionStep.BUILD_FIRMWARE,
        success=False,
        result=last_build,
        failure=failure,
        tool_name="build_firmware",
    )
    return ExecutionOutcome(
        plan=outcome.plan,
        status=ExecutionStatus.FAILED,
        step_results=(failed_step,),
        failures=(failure,),
        execution_time=outcome.execution_time + (time.monotonic() - started),
    )


def _repair_generation_failure_outcome(
    original: ExecutionOutcome,
    result: GenerateCodeResult,
    started: float,
) -> ExecutionOutcome:
    failure = ExecutionFailure(
        step=ExecutionStep.GENERATE_CODE,
        message=result.message,
        category="GENERATION_REPAIR_FAILED",
        recoverable=False,
        fatal=True,
    )
    step = StepResult(step=ExecutionStep.GENERATE_CODE, success=False, result=result, failure=failure)
    return ExecutionOutcome(
        plan=original.plan,
        status=ExecutionStatus.FAILED,
        step_results=(step,),
        failures=(failure,),
        execution_time=original.execution_time + (time.monotonic() - started),
    )


def _build_execution_failure(result: BuildResult) -> ExecutionFailure:
    source = result.failure
    category = _string_value(getattr(source, "category", "BUILD_FAILED"))
    return ExecutionFailure(
        step=ExecutionStep.BUILD_FIRMWARE,
        message=result.message or "PlatformIO build failed after automatic repair",
        category=category or "BUILD_FAILED",
        recoverable=False,
        fatal=True,
        tool_name="build_firmware",
        exception_type=getattr(source, "exception_type", None),
        raw_failure=source,
    )


def _sanitized_build_diagnostics(result: BuildResult) -> str:
    raw = getattr(result.failure, "raw_output", None) if result.failure is not None else None
    value = raw if isinstance(raw, str) and raw.strip() else result.message
    value = value[-6_000:]
    value = re.sub(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*\S+", r"\1=[REDACTED]", value)
    return value


def _generation_provider(result: GenerateCodeResult) -> str:
    report = result.metadata.get("generation_report")
    value = report.get("provider_id") if isinstance(report, Mapping) else None
    return value if isinstance(value, str) else ""


def _generation_model(result: GenerateCodeResult) -> str:
    report = result.metadata.get("generation_report")
    value = report.get("model_id") if isinstance(report, Mapping) else None
    return value if isinstance(value, str) else ""


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


_GENERATION_EVENT_NAMES = {
    "generation_strategy_selected": "GENERATION_STRATEGY_SELECTED",
    "generation_started": "GENERATION_STARTED",
    "generation_failed": "GENERATION_FAILED",
    "requirements_extraction_started": "REQUIREMENTS_EXTRACTION_STARTED",
    "requirements_extracted": "REQUIREMENTS_EXTRACTED",
    "manifest_generation_started": "MANIFEST_GENERATION_STARTED",
    "manifest_created": "MANIFEST_CREATED",
    "file_generation_started": "FILE_GENERATION_STARTED",
    "file_generation_repair_started": "FILE_GENERATION_REPAIR_STARTED",
    "file_generation_fallback_started": "FILE_GENERATION_FALLBACK_STARTED",
    "file_generation_validated": "FILE_GENERATION_VALIDATED",
    "file_written": "FILE_WRITTEN",
    "file_failed": "FILE_FAILED",
    "generation_incomplete": "GENERATION_INCOMPLETE",
    "generation_completed": "GENERATION_COMPLETED",
    "build_blocked": "BUILD_BLOCKED",
}


def _generation_event_name(event_type: str) -> str:
    return _GENERATION_EVENT_NAMES.get(event_type, event_type.upper())


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
        del step
        generation: GenerateCodeResult | None = None
        generated_context = context
        if ExecutionStep.GENERATE_CODE in execution_plan.execution_steps:
            generation, generated_context = _generation_state(completed)
            _validate_verified_generation_for_build(generation, generated_context)
        project, generated_context = _project_state(completed, context)
        config = GenerationAdapter.resolve_build_config(
            project,
            context=generated_context,
        )
        if generation is not None:
            _validate_build_workspace_matches_generation(config.project_dir, generation)
        logger.info(
            "build firmware_start task_id=%s build_workspace_root=%s build_command=%s",
            generated_context.task_id,
            config.project_dir,
            "platformio run -d " + str(config.project_dir),
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


def _normalize_optional_hardware_outcome(outcome: ExecutionOutcome) -> ExecutionOutcome:
    if outcome.status is not ExecutionStatus.FAILED:
        return outcome
    if not outcome.failures:
        return outcome
    failure = outcome.failures[0]
    if not _is_optional_hardware_failure(outcome.plan, failure.step, failure.message):
        return outcome
    if not _software_steps_succeeded(outcome.step_results):
        return outcome

    converted: list[StepResult] = []
    for item in outcome.step_results:
        if item.failure is not None and item.step is failure.step:
            converted.append(
                StepResult(
                    step=item.step,
                    success=True,
                    result=HardwarePendingResult(
                        success=True,
                        status="WAITING_FOR_DEVICE",
                        message=_hardware_pending_message(outcome.plan),
                        category="NO_DEVICE",
                        stage=_stage_name(item.step),
                        expected_board=_string_value(outcome.plan.target_board),
                    ),
                    failure=None,
                    tool_name=item.tool_name,
                    execution_time=item.execution_time,
                )
            )
        else:
            converted.append(item)

    return ExecutionOutcome(
        plan=outcome.plan,
        status=ExecutionStatus.COMPLETED_WITH_PENDING_HARDWARE,
        step_results=tuple(converted),
        failures=(),
        execution_time=outcome.execution_time,
    )


def _is_optional_hardware_step_result(
    plan: ExecutionPlan,
    step: ExecutionStep,
    result: object,
) -> bool:
    failure = getattr(result, "failure", None)
    message = str(getattr(failure, "message", "") or getattr(result, "message", ""))
    return _is_optional_hardware_failure(plan, step, message)


def _is_optional_hardware_failure(
    plan: ExecutionPlan,
    step: ExecutionStep,
    message: str,
) -> bool:
    if plan.task_type not in {
        TaskType.FIRMWARE_GENERATION,
        TaskType.FIRMWARE_MODIFICATION,
    }:
        return False
    if _hardware_requested(plan):
        return False
    if step not in {
        ExecutionStep.DETECT_BOARD,
        ExecutionStep.FLASH_FIRMWARE,
        ExecutionStep.START_MONITOR,
    }:
        return False
    normalized = message.casefold()
    return (
        "no detected board matches" in normalized
        or "no matching" in normalized and "device" in normalized
        or "device" in normalized and "not found" in normalized
        or "board" in normalized and "not found" in normalized
    )


def _hardware_requested(plan: ExecutionPlan) -> bool:
    metadata_requested = plan.metadata.get("hardware_requested")
    if isinstance(metadata_requested, bool):
        return metadata_requested
    prompt = plan.metadata.get("normalized_prompt")
    if not isinstance(prompt, str):
        return False
    normalized = prompt.casefold()
    return any(word in normalized for word in ("flash", "upload", "burn", "serial monitor", "monitor serial"))


def _software_steps_succeeded(step_results: tuple[StepResult, ...]) -> bool:
    required = {
        ExecutionStep.GENERATE_CODE,
        ExecutionStep.BUILD_FIRMWARE,
    }
    seen: set[ExecutionStep] = set()
    for item in step_results:
        if item.step in required:
            if not item.success:
                return False
            seen.add(item.step)
    return ExecutionStep.BUILD_FIRMWARE in seen


def _hardware_pending_message(plan: ExecutionPlan) -> str:
    board = _string_value(plan.target_board)
    display = board if board and board != "UNKNOWN" else "matching"
    article = "an" if display[:1].lower() in {"a", "e", "i", "o", "u"} else "a"
    return f"Firmware generated and built successfully. Connect {article} {display} board to flash."


def _workflow_completion_message(outcome: ExecutionOutcome) -> str:
    if outcome.status is ExecutionStatus.COMPLETED_WITH_PENDING_HARDWARE:
        return _hardware_pending_message(outcome.plan)
    if outcome.status is ExecutionStatus.COMPLETED:
        if any(item.step is ExecutionStep.BUILD_FIRMWARE and item.success for item in outcome.step_results):
            return "Firmware generated and built successfully."
        return "Workflow completed successfully."
    if outcome.status is ExecutionStatus.CANCELLED:
        return "Workflow cancelled by user."
    if outcome.failures:
        return outcome.failures[0].message
    return outcome.status.value


def _stage_name(step: ExecutionStep) -> str:
    if step is ExecutionStep.DETECT_BOARD:
        return "detect_board"
    if step is ExecutionStep.FLASH_FIRMWARE:
        return "flash"
    if step is ExecutionStep.START_MONITOR:
        return "monitor"
    return step.value.casefold()


def _generation_state(
    completed: tuple[StepResult, ...],
) -> tuple[GenerateCodeResult, ExecutionContext]:
    result = _require_result(completed, ExecutionStep.GENERATE_CODE)
    if not isinstance(result, GenerateCodeResult) or not result.success:
        raise ValueError("GENERATE_CODE did not produce a successful result")
    context = result.execution_context
    if context is None:
        raise ValueError("GENERATE_CODE did not propagate project context")
    return result, context


def _validate_verified_generation_for_build(
    generation: GenerateCodeResult,
    context: ExecutionContext,
) -> None:
    summary = generation.metadata.get("artifact_summary")
    if not isinstance(summary, Mapping):
        raise ValueError("Build blocked: no verified generation artifact for current execution.")
    if summary.get("task_id") != context.task_id:
        raise ValueError("Build blocked: generation artifact belongs to a different execution.")
    workspace_root = summary.get("workspace_root")
    if not isinstance(workspace_root, str) or not workspace_root.strip():
        raise ValueError("Build blocked: generation artifact workspace is missing.")
    if summary.get("generation_satisfied_prompt") is not True:
        raise ValueError("Build blocked: generation did not produce required files for this execution.")
    if summary.get("platformio_ini_present") is not True or summary.get("main_cpp_present") is not True:
        raise ValueError("Build blocked: generation did not produce required PlatformIO files for this execution.")
    root = Path(workspace_root).expanduser().resolve()
    if not (root / "platformio.ini").is_file() or not (root / "src" / "main.cpp").is_file():
        raise ValueError("Build blocked: verified generation files are missing from the active workspace.")
    if context.project_path:
        context_root = Path(context.project_path).expanduser().resolve()
        if context_root != root:
            raise ValueError("Build blocked: generation workspace does not match the active project.")


def _validate_build_workspace_matches_generation(
    project_dir: str,
    generation: GenerateCodeResult,
) -> None:
    summary = generation.metadata.get("artifact_summary")
    if not isinstance(summary, Mapping):
        raise ValueError("Build blocked: no verified generation artifact for current execution.")
    workspace_root = summary.get("workspace_root")
    if not isinstance(workspace_root, str) or not workspace_root.strip():
        raise ValueError("Build blocked: generation artifact workspace is missing.")
    if Path(project_dir).expanduser().resolve() != Path(workspace_root).expanduser().resolve():
        raise ValueError("Build blocked: build workspace does not match generated workspace.")


def _generation_summary_message(
    summary: Mapping[str, object],
    report: Mapping[str, object] | None = None,
) -> str:
    created = summary.get("created_files")
    updated = summary.get("updated_files")
    unchanged = summary.get("unchanged_files")
    created_count = len(created) if isinstance(created, Sequence) else 0
    updated_count = len(updated) if isinstance(updated, Sequence) else 0
    unchanged_count = len(unchanged) if isinstance(unchanged, Sequence) else 0
    workspace = summary.get("workspace_root")
    workspace_text = workspace if isinstance(workspace, str) and workspace else "active workspace"
    message = (
        "Generation success: "
        f"created: {created_count} files, updated: {updated_count} files, "
        f"unchanged: {unchanged_count} files, workspace: {workspace_text}"
    )
    if report is None:
        return message
    provider = report.get("provider_id")
    model = report.get("model_id")
    attempts = report.get("attempt_count")
    repair = "yes" if report.get("repair_used") is True else "no"
    fallback = "yes" if report.get("fallback_used") is True else "no"
    if isinstance(provider, str) and isinstance(model, str):
        message += f"; provider: {provider}; model: {model}"
    if isinstance(attempts, int):
        message += f"; attempts: {attempts}; repair: {repair}; fallback: {fallback}"
    return message


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
) -> tuple[object | None, ExecutionContext]:
    if any(item.step is ExecutionStep.GENERATE_CODE for item in completed):
        generation, generated_context = _generation_state(completed)
        project = generation.generated_project
        if project is None:
            raise ValueError("GENERATE_CODE returned no project")
        return project, generated_context
    return None, context


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
    active_workspace = metadata.get("active_workspace")
    workspace_metadata = active_workspace if isinstance(active_workspace, Mapping) else {}
    workspace_root = _optional_text(workspace_metadata.get("rootPath")) or _optional_text(workspace_metadata.get("root_path"))
    workspace_board = _optional_text(workspace_metadata.get("board"))
    workspace_framework = _optional_text(workspace_metadata.get("framework"))
    selected_board = _optional_text(workspace_metadata.get("selected_board"))
    selected_framework = _optional_text(workspace_metadata.get("selected_framework"))
    target_board = (
        _optional_text(metadata.get("target_board"))
        or _target_family(workspace_board or "")
        or _target_family(selected_board or "")
        or "UNKNOWN"
    )
    framework = (
        _optional_text(metadata.get("framework"))
        or _workflow_framework(workspace_framework)
        or _workflow_framework(selected_framework)
        or ("PlatformIO" if workspace_root else "UNKNOWN")
    )
    return ExecutionContext(
        task_id=task.task_id,
        project_path=_optional_text(metadata.get("project_path")) or workspace_root,
        target_board=target_board,
        framework=framework,
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
            "active_workspace": dict(workspace_metadata),
            **metadata,
        },
    )


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _workflow_framework(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.casefold()
    if normalized in {"unknown", ""}:
        return None
    if normalized in {"platformio", "pio", "arduino"}:
        return "PlatformIO"
    if "esp" in normalized and "idf" in normalized:
        return "ESP-IDF"
    if "stm32" in normalized:
        return "STM32Cube"
    return value


def _target_family(board: str) -> str | None:
    if not board or board == "UNKNOWN":
        return None
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
    return board


class _WorkspaceAwarePlanner:
    def __init__(self, planner: Planner, context: ExecutionContext) -> None:
        self._planner = planner
        self._context = context

    def plan(self, prompt: str) -> ExecutionPlan:
        plan = self._planner.plan(prompt)
        target_board = _string_value(plan.target_board)
        framework = _string_value(plan.framework)
        context_board = self._context.target_board
        context_framework = self._context.framework
        next_board = context_board if target_board == "UNKNOWN" and context_board != "UNKNOWN" else target_board
        next_framework = context_framework if framework == "UNKNOWN" and context_framework != "UNKNOWN" else framework
        platformio_arduino_override = (
            context_framework == "PlatformIO"
            and framework == "Arduino"
            and next_board in {"ESP32", "ESP32-S3", "ESP32-C3", "Arduino Uno"}
        )
        if platformio_arduino_override:
            next_framework = "PlatformIO"
        if next_board == target_board and next_framework == framework:
            return plan
        metadata = {
            **dict(plan.metadata),
            "workspace_defaults_applied": True,
            "workspace_target_board": context_board,
            "workspace_framework": context_framework,
            "requested_firmware_framework": framework if platformio_arduino_override else None,
            "build_system_override": "PlatformIO" if platformio_arduino_override else None,
        }
        return ExecutionPlan(
            task_id=plan.task_id,
            task_type=plan.task_type,
            target_board=next_board,
            framework=next_framework,
            requirements=plan.requirements,
            execution_steps=plan.execution_steps,
            confidence=plan.confidence,
            metadata=metadata,
        )


def _record_outcome(
    metrics: MetricsRegistry | None,
    runtime_logger: RuntimeLogger | None,
    outcome: ExecutionOutcome,
) -> None:
    duration_ms = outcome.execution_time * 1000
    if metrics is not None:
        try:
            metrics.record_execution(
                outcome.status in {
                    ExecutionStatus.COMPLETED,
                    ExecutionStatus.COMPLETED_WITH_PENDING_HARDWARE,
                },
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
