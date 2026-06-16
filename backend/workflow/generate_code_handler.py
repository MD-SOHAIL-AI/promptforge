"""Production handler for the ``GENERATE_CODE`` execution-plan step.

The handler composes existing planning, generation, persistence, and contract
types. It performs no planning, tool execution, retries, or orchestration.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any

from ..agent.coordinator import StepResult
from ..contracts.execution_context import ExecutionContext
from ..contracts.execution_plan import (
    ExecutionPlan,
    ExecutionStep,
    TaskType,
)
from ..contracts.generated_project import GeneratedProject
from ..services.code_generation_service import (
    CodeGenerationError,
    CodeGenerationRequest,
    CodeGenerationService,
    ProjectValidationError,
)
from ..services.project_service import ProjectMetadata, ProjectService
from ..hardware.constraints.constraint_engine import ConstraintEngine, ConstraintSeverity
from ..validation.firmware_validator import FirmwareValidator
from ..validation.safety_validator import SafetyValidator
from ..utils.serialization import _freeze_json_value, _freeze_mapping
from .adapters.generation_adapter import update_context_after_generation

__all__ = [
    "GenerateCodeHandler",
    "GenerateCodeResult",
    "GenerateCodeStatus",
]

logger = logging.getLogger(__name__)


@unique
class GenerateCodeStatus(str, Enum):
    """Terminal outcomes produced by :class:`GenerateCodeHandler`."""

    SUCCESS = "SUCCESS"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    GENERATION_FAILED = "GENERATION_FAILED"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"


@dataclass(frozen=True, slots=True)
class GenerateCodeResult:
    """Immutable result of one generate-and-persist operation."""

    status: GenerateCodeStatus
    generated_project: GeneratedProject | None
    project_path: str | None
    generation_time_ms: int
    warnings: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        if not isinstance(self.status, GenerateCodeStatus):
            raise ValueError("status must be a GenerateCodeStatus")
        if self.generated_project is not None and not isinstance(
            self.generated_project,
            GeneratedProject,
        ):
            raise ValueError(
                "generated_project must be a GeneratedProject or None"
            )
        if self.project_path is not None:
            _validate_text(self.project_path, "project_path")
        if (
            not isinstance(self.generation_time_ms, int)
            or isinstance(self.generation_time_ms, bool)
            or self.generation_time_ms < 0
        ):
            raise ValueError(
                "generation_time_ms must be a non-negative integer"
            )

        warnings = tuple(self.warnings)
        for warning in warnings:
            _validate_text(warning, "warnings item")
        if len(set(warnings)) != len(warnings):
            raise ValueError("warnings cannot contain duplicates")
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

        if self.status is GenerateCodeStatus.SUCCESS:
            if self.generated_project is None or self.project_path is None:
                raise ValueError(
                    "successful result requires project and project_path"
                )
            if "execution_context" not in self.metadata:
                raise ValueError(
                    "successful result requires execution_context metadata"
                )
        elif self.generated_project is not None or self.project_path is not None:
            raise ValueError(
                "failed result cannot expose a generated project or path"
            )

    @property
    def success(self) -> bool:
        """Coordinator-compatible success indicator."""

        return self.status is GenerateCodeStatus.SUCCESS

    @property
    def message(self) -> str:
        """Coordinator-compatible human-readable result message."""

        if self.success:
            return "Code generation and persistence completed"
        error = self.metadata.get("error")
        if isinstance(error, Mapping):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message
        return self.status.value

    @property
    def execution_context(self) -> ExecutionContext | None:
        """Return the updated immutable context on success."""

        raw_context = self.metadata.get("execution_context")
        if raw_context is None:
            return None
        return ExecutionContext.from_dict(raw_context)


_SUPPORTED_TASK_TYPES = frozenset(
    {
        TaskType.FIRMWARE_GENERATION,
        TaskType.FIRMWARE_MODIFICATION,
        TaskType.SIMULATION,
    }
)


class GenerateCodeHandler:
    """Execute one plan's ``GENERATE_CODE`` step through existing services.

    The handler keeps only immutable dependencies and its input context. The
    updated context is returned through ``GenerateCodeResult`` rather than
    mutating runtime or session state.
    """

    __slots__ = (
        "_clock",
        "_code_generation_service",
        "_context",
        "_project_service",
    )

    def __init__(
        self,
        code_generation_service: CodeGenerationService,
        project_service: ProjectService,
        context: ExecutionContext,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(
            getattr(code_generation_service, "generate_project", None)
        ) or not callable(
            getattr(code_generation_service, "validate_project", None)
        ):
            raise ValueError(
                "code_generation_service must provide generation and validation"
            )
        if not callable(getattr(project_service, "create_project", None)):
            raise ValueError(
                "project_service must provide create_project"
            )
        if not isinstance(context, ExecutionContext):
            raise ValueError("context must be an ExecutionContext")
        if not callable(clock):
            raise ValueError("clock must be callable")

        self._code_generation_service = code_generation_service
        self._project_service = project_service
        self._context = context
        self._clock = clock

    async def execute(
        self,
        plan: ExecutionPlan,
        completed: tuple[StepResult, ...] = (),
    ) -> GenerateCodeResult:
        """Generate, validate, persist, and describe one firmware project."""

        started = self._clock()
        raw_task_id = getattr(plan, "task_id", "unknown")
        task_id = (
            raw_task_id
            if isinstance(raw_task_id, str) and raw_task_id.strip()
            else "unknown"
        )
        try:
            _validate_completed(completed)
            _validate_plan(plan, self._context)
            request = CodeGenerationRequest(plan=plan, context=self._context)
        except Exception as exc:
            return self._failure(
                GenerateCodeStatus.VALIDATION_FAILED,
                started,
                phase="request_validation",
                task_id=task_id,
                exc=exc,
                log_message="validation_failed",
            )

        logger.info(
            "generate_code generation_start task_id=%s task_type=%s "
            "target_board=%s framework=%s requirement_count=%d",
            plan.task_id,
            plan.task_type.value,
            _identifier(plan.target_board),
            _identifier(plan.framework),
            len(plan.requirements),
        )

        try:
            project = await self._code_generation_service.generate_project(
                request
            )
        except asyncio.CancelledError:
            raise
        except ProjectValidationError as exc:
            return self._failure(
                GenerateCodeStatus.VALIDATION_FAILED,
                started,
                phase="generation_validation",
                task_id=plan.task_id,
                exc=exc,
                log_message="validation_failed",
            )
        except Exception as exc:
            return self._failure(
                GenerateCodeStatus.GENERATION_FAILED,
                started,
                phase="generation",
                task_id=plan.task_id,
                exc=exc,
                log_message="generation_failed",
            )

        try:
            _validate_generated_project(project, plan)
            self._code_generation_service.validate_project(project)
            _validate_generated_firmware(project, plan, self._context)
        except Exception as exc:
            return self._failure(
                GenerateCodeStatus.VALIDATION_FAILED,
                started,
                phase="project_validation",
                task_id=plan.task_id,
                exc=exc,
                log_message="validation_failed",
            )

        logger.info(
            "generate_code generation_completion task_id=%s project_id=%s "
            "project_name=%s file_count=%d",
            plan.task_id,
            project.project_id,
            project.project_name,
            len(project.files),
        )
        logger.info(
            "generate_code persistence_start task_id=%s project_id=%s "
            "project_name=%s",
            plan.task_id,
            project.project_id,
            project.project_name,
        )

        try:
            persisted = await self._project_service.create_project(project)
            _validate_persisted_project(persisted, project)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return self._failure(
                GenerateCodeStatus.PERSISTENCE_FAILED,
                started,
                phase="persistence",
                task_id=plan.task_id,
                exc=exc,
                log_message="persistence_failed",
            )

        logger.info(
            "generate_code persistence_completion task_id=%s project_id=%s "
            "project_path=%s file_count=%d",
            plan.task_id,
            project.project_id,
            persisted.project_path,
            persisted.file_count,
        )

        try:
            updated_context = update_context_after_generation(
                self._context,
                project,
                project_path=persisted.project_path,
            )
            metadata = {
                "task_id": plan.task_id,
                "project_id": project.project_id,
                "project_name": project.project_name,
                "file_count": len(project.files),
                "target_board": _identifier(project.target_board),
                "framework": _identifier(project.framework),
                "execution_context": updated_context.to_dict(),
                "persistence": persisted.to_dict(),
            }
            warnings = _project_warnings(project)
            result = GenerateCodeResult(
                status=GenerateCodeStatus.SUCCESS,
                generated_project=project,
                project_path=persisted.project_path,
                generation_time_ms=_elapsed_ms(started, self._clock()),
                warnings=warnings,
                metadata=metadata,
            )
        except Exception as exc:
            return self._failure(
                GenerateCodeStatus.VALIDATION_FAILED,
                started,
                phase="context_update",
                task_id=plan.task_id,
                exc=exc,
                log_message="validation_failed",
            )

        logger.info(
            "generate_code workflow_completion task_id=%s project_id=%s "
            "generation_time_ms=%d warning_count=%d",
            plan.task_id,
            project.project_id,
            result.generation_time_ms,
            len(result.warnings),
        )
        return result

    async def __call__(
        self,
        plan: ExecutionPlan,
        completed: tuple[StepResult, ...] = (),
    ) -> GenerateCodeResult:
        return await self.execute(plan, completed)

    def _failure(
        self,
        status: GenerateCodeStatus,
        started: float,
        *,
        phase: str,
        task_id: object,
        exc: Exception,
        log_message: str,
    ) -> GenerateCodeResult:
        message = str(exc).strip() or type(exc).__name__
        error = {
            "type": type(exc).__name__,
            "message": message,
        }
        if isinstance(exc, CodeGenerationError):
            error["code"] = exc.code
            error["details"] = _json_safe(dict(exc.details))
        logger.warning(
            "generate_code %s task_id=%s phase=%s error_type=%s message=%s",
            log_message,
            task_id,
            phase,
            type(exc).__name__,
            message,
        )
        return GenerateCodeResult(
            status=status,
            generated_project=None,
            project_path=None,
            generation_time_ms=_elapsed_ms(started, self._clock()),
            warnings=(),
            metadata={
                "task_id": str(task_id),
                "phase": phase,
                "error": error,
            },
        )


def _validate_plan(plan: object, context: ExecutionContext) -> None:
    if not isinstance(plan, ExecutionPlan):
        raise TypeError("plan must be an ExecutionPlan")
    _validate_text(plan.task_id, "task_id")
    if plan.task_id != context.task_id:
        raise ValueError("plan.task_id must match context.task_id")
    if plan.task_type not in _SUPPORTED_TASK_TYPES:
        raise ValueError(
            f"task_type {plan.task_type.value} does not support code generation"
        )
    if ExecutionStep.GENERATE_CODE not in plan.execution_steps:
        raise ValueError("execution plan must contain GENERATE_CODE")
    target_board = _identifier(plan.target_board)
    framework = _identifier(plan.framework)
    _validate_text(target_board, "target_board")
    _validate_text(framework, "framework")
    if target_board == "UNKNOWN":
        raise ValueError("target_board must be resolved before generation")
    if framework == "UNKNOWN":
        raise ValueError("framework must be resolved before generation")
    if not plan.requirements:
        raise ValueError("requirements must contain at least one item")
    for requirement in plan.requirements:
        _validate_text(requirement, "requirements item")
    workflow_metadata = context.metadata.get("workflow", {})
    if not isinstance(workflow_metadata, Mapping):
        raise ValueError("context metadata.workflow must be a mapping")


def _validate_completed(completed: object) -> None:
    if not isinstance(completed, tuple):
        raise TypeError("completed must be a tuple of StepResult values")
    if any(not isinstance(item, StepResult) for item in completed):
        raise TypeError("completed must contain only StepResult values")


def _validate_generated_project(
    project: object,
    plan: ExecutionPlan,
) -> None:
    if not isinstance(project, GeneratedProject):
        raise ValueError("generation service must return a GeneratedProject")
    if not project.files:
        raise ValueError("generated project must contain files")
    if _identifier(project.target_board) != _identifier(plan.target_board):
        raise ValueError("generated project target_board must match the plan")
    if _identifier(project.framework) != _identifier(plan.framework):
        raise ValueError("generated project framework must match the plan")
    task_id = project.metadata.get("task_id")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("generated project metadata.task_id is required")
    if task_id != plan.task_id:
        raise ValueError("generated project metadata.task_id must match the plan")
    _project_warnings(project)

    paths = {item.path.casefold() for item in project.files}
    framework = _identifier(project.framework).casefold()
    if framework == "platformio":
        _require_paths(paths, {"platformio.ini", "src/main.cpp"})
    elif framework == "arduino":
        if sum(path.endswith(".ino") for path in paths) != 1:
            raise ValueError(
                "Arduino generated project must contain exactly one .ino file"
            )
    elif framework == "esp-idf":
        _require_paths(paths, {"cmakelists.txt", "main/cmakelists.txt"})
        if not any(
            path.startswith("main/")
            and path.endswith((".c", ".cc", ".cpp"))
            for path in paths
        ):
            raise ValueError(
                "ESP-IDF generated project must contain a main source file"
            )
    elif framework == "stm32cube":
        _require_paths(paths, {"core/src/main.c", "core/inc/main.h"})


def _validate_generated_firmware(
    project: GeneratedProject,
    plan: ExecutionPlan,
    context: ExecutionContext,
) -> None:
    firmware = FirmwareValidator().validate_project(project)
    firmware_errors = tuple(
        issue for issue in firmware.errors if issue.rule_id != "PF-FW-012"
    )
    if firmware_errors:
        raise ValueError(
            _validation_message("firmware validation", firmware_errors)
        )

    board_metadata = {
        "target_board": _identifier(plan.target_board),
        "framework": _identifier(plan.framework),
    }
    firmware_metadata = {
        "target_board": _identifier(project.target_board),
        "framework": _identifier(project.framework),
        "requirements": plan.requirements,
        "execution_steps": tuple(step.value for step in plan.execution_steps),
        "metadata": project.metadata,
    }
    safety = SafetyValidator().validate(
        prompt=context.metadata.get("prompt"),
        planner_output={
            "target_board": _identifier(plan.target_board),
            "framework": _identifier(plan.framework),
            "requirements": plan.requirements,
            "execution_steps": tuple(step.value for step in plan.execution_steps),
            "metadata": plan.metadata,
        },
        firmware_metadata=firmware_metadata,
        hardware_metadata=board_metadata,
    )
    if not safety.safe_to_continue:
        raise ValueError(
            _validation_message(
                "safety validation",
                (*safety.errors, *safety.critical_failures),
            )
        )

    constraint_context = {
        "target_board": _identifier(plan.target_board),
        "framework": _constraint_framework(plan.framework),
        "requirements": plan.requirements,
        "metadata": {**dict(project.metadata), **dict(plan.metadata)},
    }
    violations = ConstraintEngine().validate_constraints(
        _identifier(plan.target_board),
        constraint_context,
        framework=_constraint_framework(plan.framework),
    )
    blocking = tuple(
        violation
        for violation in violations
        if violation.severity is ConstraintSeverity.ERROR
    )
    if blocking:
        raise ValueError(
            _validation_message("constraint validation", blocking)
        )


def _validation_message(label: str, issues: object) -> str:
    details: list[str] = []
    for issue in tuple(issues):  # type: ignore[arg-type]
        rule_id = getattr(issue, "rule_id", "UNKNOWN")
        message = getattr(issue, "message", str(issue))
        details.append(f"{rule_id}: {message}")
    return f"{label} failed" + (": " + "; ".join(details) if details else "")


def _constraint_framework(value: object) -> str:
    framework = _identifier(value)
    return "Arduino" if framework == "PlatformIO" else framework


def _require_paths(paths: set[str], required: set[str]) -> None:
    missing = sorted(required - paths)
    if missing:
        raise ValueError(
            "generated project is missing required files: "
            + ", ".join(missing)
        )


def _validate_persisted_project(
    persisted: object,
    project: GeneratedProject,
) -> None:
    if not isinstance(persisted, ProjectMetadata):
        raise ValueError("ProjectService must return ProjectMetadata")
    if persisted.project_id != project.project_id:
        raise ValueError("persisted project_id must match generated project")
    if persisted.project_name != project.project_name:
        raise ValueError("persisted project_name must match generated project")
    if persisted.target_board != _identifier(project.target_board):
        raise ValueError("persisted target_board must match generated project")
    if persisted.framework != _identifier(project.framework):
        raise ValueError("persisted framework must match generated project")
    if persisted.file_count != len(project.files):
        raise ValueError("persisted file_count must match generated project")
    _validate_text(persisted.project_path, "persisted project_path")


def _project_warnings(project: GeneratedProject) -> tuple[str, ...]:
    raw_warnings = project.metadata.get("warnings", ())
    if not isinstance(raw_warnings, Sequence) or isinstance(
        raw_warnings,
        (str, bytes, bytearray),
    ):
        raise ValueError("generated project metadata.warnings must be a sequence")
    warnings = tuple(raw_warnings)
    for warning in warnings:
        _validate_text(warning, "generated project warning")
    if len(set(warnings)) != len(warnings):
        raise ValueError("generated project warnings cannot contain duplicates")
    return warnings


def _elapsed_ms(started: float, finished: float) -> int:
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        for value in (started, finished)
    ):
        return 0
    return max(0, int((finished - started) * 1000))


def _validate_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if "\x00" in value:
        raise ValueError(f"{field_name} cannot contain NUL characters")


def _identifier(value: str) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return value


def _freeze_metadata(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be a mapping")
    return _freeze_mapping(metadata, path="metadata", active=set())


def _json_safe(value: Any, *, active: set[int] | None = None) -> Any:
    """Return deterministic JSON-compatible diagnostic data."""

    if active is None:
        active = set()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else type(value).__name__
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            return "<cyclic>"
        active.add(identity)
        try:
            return {
                str(key): _json_safe(item, active=active)
                for key, item in value.items()
            }
        finally:
            active.remove(identity)
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        identity = id(value)
        if identity in active:
            return "<cyclic>"
        active.add(identity)
        try:
            return [_json_safe(item, active=active) for item in value]
        finally:
            active.remove(identity)
    return f"<{type(value).__name__}>"
