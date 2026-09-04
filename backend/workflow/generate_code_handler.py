"""Production handler for the ``GENERATE_CODE`` execution-plan step.

The handler composes existing planning, generation, persistence, and contract
types. It performs no planning, tool execution, retries, or orchestration.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import math
import time
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum, unique
from pathlib import Path
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
from ..validation.generated_project_validation import (
    GeneratedProjectValidationError,
    GeneratedProjectValidationService,
)
from ..services.chunked_generation_writer import WorkspaceGenerationTransaction
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
        "_generation_event_callback",
        "_project_service",
    )

    def __init__(
        self,
        code_generation_service: CodeGenerationService,
        project_service: ProjectService,
        context: ExecutionContext,
        *,
        clock: Callable[[], float] = time.monotonic,
        generation_event_callback: Callable[[Mapping[str, Any]], object] | None = None,
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
        if generation_event_callback is not None and not callable(generation_event_callback):
            raise ValueError("generation_event_callback must be callable")

        self._code_generation_service = code_generation_service
        self._project_service = project_service
        self._context = context
        self._clock = clock
        self._generation_event_callback = generation_event_callback

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
            _validate_active_workspace(plan, self._context)
            workspace_root = _active_workspace_root(self._context)
            workspace_transaction = (
                WorkspaceGenerationTransaction(workspace_root)
                if workspace_root is not None
                else None
            )
            request = CodeGenerationRequest(
                plan=plan,
                context=self._context,
                generation_event_callback=self._generation_event_callback,
                workspace_transaction=workspace_transaction,
            )
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
            "target_board=%s framework=%s requirement_count=%d workspace_root=%s",
            plan.task_id,
            plan.task_type.value,
            _identifier(plan.target_board),
            _identifier(plan.framework),
            len(plan.requirements),
            _active_workspace_root(self._context),
        )

        try:
            project = await self._code_generation_service.generate_project(
                request
            )
        except asyncio.CancelledError:
            if workspace_transaction is not None:
                workspace_transaction.rollback()
            raise
        except ProjectValidationError as exc:
            if workspace_transaction is not None:
                workspace_transaction.rollback()
            return self._failure(
                GenerateCodeStatus.VALIDATION_FAILED,
                started,
                phase="generation_validation",
                task_id=plan.task_id,
                exc=exc,
                log_message="validation_failed",
            )
        except Exception as exc:
            if workspace_transaction is not None:
                workspace_transaction.rollback()
            return self._failure(
                GenerateCodeStatus.GENERATION_FAILED,
                started,
                phase="generation",
                task_id=plan.task_id,
                exc=exc,
                log_message="generation_failed",
            )

        repair_attempt_count = 0
        try:
            await _emit_handler_event(
                self._generation_event_callback,
                "project_validation_started",
                message="Validating the complete generated firmware project",
            )
            while True:
                _validate_generated_project(project, plan)
                self._code_generation_service.validate_project(project)
                report = GeneratedProjectValidationService().validate(project, plan, self._context)
                if report.valid:
                    break
                await _emit_handler_event(
                    self._generation_event_callback,
                    "project_validation_failed",
                    message=f"Project validation failed: {report.message()}",
                    validation_report=report.to_dict(),
                    repair_attempt_count=repair_attempt_count,
                )
                repairer = getattr(self._code_generation_service, "repair_generated_project", None)
                max_repairs = int(getattr(self._code_generation_service, "max_project_repair_attempts", 0))
                if (
                    report.requires_user_decision
                    or not report.repairable
                    or not callable(repairer)
                    or repair_attempt_count >= max_repairs
                ):
                    raise GeneratedProjectValidationError(
                        report,
                        repair_attempt_count=repair_attempt_count,
                    )
                repair_attempt_count += 1
                await _emit_handler_event(
                    self._generation_event_callback,
                    "project_repair_started",
                    message=f"Repairing the complete project ({repair_attempt_count}/{max_repairs})",
                    validation_report=report.to_dict(),
                    repair_attempt_count=repair_attempt_count,
                )
                try:
                    project = await repairer(
                        request,
                        project,
                        report.to_dict(),
                        attempt_number=repair_attempt_count,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as repair_exc:
                    await _emit_handler_event(
                        self._generation_event_callback,
                        "project_repair_failed",
                        message=(
                            f"Project repair {repair_attempt_count} failed: "
                            f"{repair_exc}"
                        ),
                        validation_report=report.to_dict(),
                        repair_attempt_count=repair_attempt_count,
                    )
                    if repair_attempt_count >= max_repairs:
                        raise GeneratedProjectValidationError(
                            report,
                            repair_attempt_count=repair_attempt_count,
                        ) from repair_exc
                    continue
                await _emit_handler_event(
                    self._generation_event_callback,
                    "project_repair_completed",
                    message=f"Project repair {repair_attempt_count} completed; revalidating",
                    repair_attempt_count=repair_attempt_count,
                )
            await _emit_handler_event(
                self._generation_event_callback,
                "project_validation_completed",
                message="Complete firmware project validated",
                validation_report=report.to_dict(),
                repair_attempt_count=repair_attempt_count,
            )
        except asyncio.CancelledError:
            if workspace_transaction is not None:
                workspace_transaction.rollback()
            raise
        except Exception as exc:
            if workspace_transaction is not None:
                workspace_transaction.rollback()
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
        if workspace_root is not None:
            logger.info(
                "Running generation in workspace root: %s project_id=%s platformio_ini=%s platformio_exists=%s",
                workspace_root,
                _active_workspace_id(self._context),
                workspace_root / "platformio.ini",
                (workspace_root / "platformio.ini").is_file(),
            )
            try:
                if workspace_transaction is not None:
                    for generated_file in project.files:
                        workspace_transaction.remember(
                            _contained_workspace_file(workspace_root, generated_file.path)
                        )
                persisted = _write_project_to_workspace(
                    project,
                    workspace_root,
                    plan=plan,
                    context=self._context,
                )
            except Exception as exc:
                if workspace_transaction is not None:
                    workspace_transaction.rollback()
                return self._failure(
                    GenerateCodeStatus.PERSISTENCE_FAILED,
                    started,
                    phase="workspace_persistence",
                    task_id=plan.task_id,
                    exc=exc,
                    log_message="persistence_failed",
                )
        else:
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
                persisted = _persisted_with_artifact_summary(
                    persisted,
                    project,
                    plan=plan,
                    context=self._context,
                    before={},
                )
            except asyncio.CancelledError:
                if workspace_transaction is not None:
                    workspace_transaction.rollback()
                raise
            except Exception as exc:
                if workspace_transaction is not None:
                    workspace_transaction.rollback()
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
                _persisted_project_path(persisted),
                _persisted_file_count(persisted),
            )

        try:
            persisted_path = _persisted_project_path(persisted)
            artifact_summary = _artifact_summary_from_persisted(persisted)
            _validate_generation_artifact_summary(artifact_summary)
            updated_context = update_context_after_generation(
                self._context,
                project,
                project_path=persisted_path,
            )
            metadata = {
                "task_id": plan.task_id,
                "project_id": project.project_id,
                "project_name": project.project_name,
                "file_count": len(project.files),
                "target_board": _identifier(project.target_board),
                "framework": _identifier(project.framework),
                "artifact_summary": artifact_summary,
                "execution_context": updated_context.to_dict(),
                "persistence": persisted.to_dict() if hasattr(persisted, "to_dict") else dict(persisted),
            }
            generation_report = project.metadata.get("generation_report")
            if isinstance(generation_report, Mapping):
                metadata["generation_report"] = generation_report
            warnings = _project_warnings(project)
            warnings = tuple(
                dict.fromkeys((*warnings, *_artifact_warnings(artifact_summary)))
            )
            result = GenerateCodeResult(
                status=GenerateCodeStatus.SUCCESS,
                generated_project=project,
                project_path=persisted_path,
                generation_time_ms=_elapsed_ms(started, self._clock()),
                warnings=warnings,
                metadata=metadata,
            )
        except Exception as exc:
            if workspace_transaction is not None:
                workspace_transaction.rollback()
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
        if workspace_transaction is not None:
            workspace_transaction.commit()
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
        elif isinstance(exc, GeneratedProjectValidationError):
            report = exc.report.to_dict()
            error["code"] = (
                "HARDWARE_CONSTRAINT_DECISION_REQUIRED"
                if exc.report.requires_user_decision
                else "PROJECT_VALIDATION_FAILED"
            )
            error["details"] = {
                "validation_report": report,
                "repair_attempt_count": exc.repair_attempt_count,
                "pending_decision": exc.report.requires_user_decision,
                "suggested_alternatives": list(exc.report.suggested_alternatives),
            }
        failure_metadata: dict[str, Any] = {
            "task_id": str(task_id),
            "phase": phase,
            "error": error,
        }
        details = error.get("details")
        if isinstance(details, Mapping):
            validation_report = details.get("validation_report")
            if isinstance(validation_report, Mapping):
                failure_metadata["validation_report"] = dict(validation_report)
                failure_metadata["repair_attempt_count"] = int(details.get("repair_attempt_count", 0))
                failure_metadata["pending_decision"] = bool(details.get("pending_decision", False))
                failure_metadata["suggested_alternatives"] = list(details.get("suggested_alternatives", ()))
            attempts = details.get("generation_attempts")
            if isinstance(attempts, Sequence) and not isinstance(attempts, (str, bytes, bytearray)):
                failure_metadata["generation_report"] = {
                    "attempt_count": len(attempts),
                    "repair_used": any(
                        isinstance(item, Mapping) and item.get("repair_used") is True
                        for item in attempts
                    ),
                    "fallback_used": any(
                        isinstance(item, Mapping) and item.get("fallback_used") is True
                        for item in attempts
                    ),
                    "attempts": attempts,
                    "warnings": (),
                }
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
            metadata=failure_metadata,
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


def _validate_active_workspace(plan: ExecutionPlan, context: ExecutionContext) -> None:
    root = _active_workspace_root(context)
    if root is None:
        return
    logger.info(
        "Running generation in workspace root: %s project_id=%s platformio_ini=%s platformio_exists=%s",
        root,
        _active_workspace_id(context),
        root / "platformio.ini",
        (root / "platformio.ini").is_file(),
    )
    if not root.exists():
        raise ValueError(f"Workspace root does not exist: {root}")
    if not root.is_dir():
        raise ValueError(f"Workspace root is not a directory: {root}")
    generation_mode = _generation_mode(context)
    if (
        _identifier(plan.framework).casefold() == "platformio"
        and generation_mode == "modify_existing_project"
    ):
        ini = root / "platformio.ini"
        if not ini.is_file() or ini.is_symlink():
            raise ValueError(
                "Cannot generate PlatformIO firmware because platformio.ini "
                f"is missing from workspace root: {root}"
            )


def _active_workspace(context: ExecutionContext) -> Mapping[str, Any]:
    workspace = context.metadata.get("active_workspace")
    return workspace if isinstance(workspace, Mapping) else {}


def _generation_mode(context: ExecutionContext) -> str:
    value = _active_workspace(context).get("generation_mode")
    if isinstance(value, str) and value in {
        "new_project",
        "modify_existing_project",
        "generate_into_open_folder",
    }:
        return value
    return "generate_into_open_folder" if _active_workspace_root(context) is not None else "new_project"


def _active_workspace_id(context: ExecutionContext) -> str:
    workspace = _active_workspace(context)
    value = workspace.get("id")
    return value if isinstance(value, str) and value.strip() else "none"


def _active_workspace_root(context: ExecutionContext) -> Path | None:
    workspace = _active_workspace(context)
    value = workspace.get("rootPath") or workspace.get("root_path")
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value).expanduser().resolve()


def _write_project_to_workspace(
    project: GeneratedProject,
    root: Path,
    *,
    plan: ExecutionPlan,
    context: ExecutionContext,
) -> Mapping[str, Any]:
    root = root.expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Workspace root is not available: {root}")
    tracked_paths = _tracked_artifact_paths(project, plan, context)
    before = _snapshot_files(root, tracked_paths)
    chunked = project.metadata.get("chunked_generation")
    incremental_writes = isinstance(chunked, Mapping) and chunked.get("incremental_writes") is True
    for generated_file in project.files:
        target = _contained_workspace_file(root, generated_file.path)
        if target.exists() and target.is_symlink():
            raise ValueError(f"Workspace file cannot be a symlink: {generated_file.path}")
        if incremental_writes:
            if not target.is_file():
                raise ValueError(f"Generation failed: {generated_file.path} was not written to the active workspace.")
            continue
        if (
            generated_file.path.casefold() == "platformio.ini"
            and target.is_file()
            and _generation_mode(context) == "modify_existing_project"
            and not isinstance(context.metadata.get("build_repair"), Mapping)
        ):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generated_file.content, encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "project_id": project.project_id,
        "project_name": project.project_name,
        "target_board": _identifier(project.target_board),
        "framework": _identifier(project.framework),
        "created_at": project.created_at.isoformat().replace("+00:00", "Z"),
        "metadata": _json_safe(dict(project.metadata)),
        "files": [
            {"path": item.path, "file_type": item.file_type}
            for item in project.files
        ],
    }
    (root / ".promptforge-project.json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = _build_generation_artifact_summary(
        project,
        root,
        plan=plan,
        context=context,
        before=before,
    )
    _validate_generation_artifact_summary(summary)
    return {
        "project_id": project.project_id,
        "project_name": project.project_name,
        "target_board": _identifier(project.target_board),
        "framework": _identifier(project.framework),
        "project_path": str(root),
        "file_count": len(project.files),
        "external": True,
        "artifact_summary": summary,
    }


def _persisted_with_artifact_summary(
    persisted: object,
    project: GeneratedProject,
    *,
    plan: ExecutionPlan,
    context: ExecutionContext,
    before: Mapping[str, str | None],
) -> Mapping[str, Any]:
    data = persisted.to_dict() if hasattr(persisted, "to_dict") else dict(persisted)  # type: ignore[arg-type]
    root = Path(_persisted_project_path(data)).expanduser().resolve()
    summary = _build_generation_artifact_summary(
        project,
        root,
        plan=plan,
        context=context,
        before=before,
    )
    _validate_generation_artifact_summary(summary)
    data["artifact_summary"] = summary
    return data


def _persisted_project_path(persisted: object) -> str:
    value = getattr(persisted, "project_path", None)
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(persisted, Mapping):
        value = persisted.get("project_path")
        if isinstance(value, str) and value.strip():
            return value
    raise ValueError("persisted project path is missing")


def _persisted_file_count(persisted: object) -> int:
    value = getattr(persisted, "file_count", None)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(persisted, Mapping):
        value = persisted.get("file_count")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    raise ValueError("persisted project file_count is missing")


def _artifact_summary_from_persisted(persisted: object) -> Mapping[str, Any]:
    value = None
    if isinstance(persisted, Mapping):
        value = persisted.get("artifact_summary")
    if isinstance(value, Mapping):
        return value
    raise ValueError("generation artifact summary is missing")


def _tracked_artifact_paths(
    project: GeneratedProject,
    plan: ExecutionPlan,
    context: ExecutionContext,
) -> tuple[str, ...]:
    paths = {_normalize_generated_path(item.path) for item in project.files}
    paths.update(_required_artifact_paths(project, plan, context))
    paths.add(".promptforge-project.json")
    return tuple(sorted(paths))


def _required_artifact_paths(
    project: GeneratedProject,
    plan: ExecutionPlan,
    context: ExecutionContext,
) -> tuple[str, ...]:
    framework = _identifier(project.framework).casefold()
    if framework != "platformio":
        return ()
    required = {"platformio.ini", "src/main.cpp"}
    if _prompt_requests_readme(plan, context):
        required.add("README.md")
    return tuple(sorted(required, key=str.casefold))


def _build_generation_artifact_summary(
    project: GeneratedProject,
    root: Path,
    *,
    plan: ExecutionPlan,
    context: ExecutionContext,
    before: Mapping[str, str | None],
) -> Mapping[str, Any]:
    root = root.expanduser().resolve()
    tracked = _tracked_artifact_paths(project, plan, context)
    required = _required_artifact_paths(project, plan, context)
    generated = {
        _normalize_generated_path(item.path): item
        for item in project.files
    }
    generated_hashes = {
        path: _content_hash(item.content)
        for path, item in generated.items()
    }
    if root.exists() and root.is_dir():
        after = _snapshot_files(root, tracked)
    else:
        after = {
            path: generated_hashes.get(path)
            for path in tracked
        }
    created: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    for path in tracked:
        previous = before.get(path)
        current = after.get(path)
        if current is None:
            continue
        if previous is None:
            created.append(path)
        elif previous != current:
            updated.append(path)
        else:
            unchanged.append(path)

    required_present = all(after.get(path) is not None for path in required)
    generated_required = [path for path in required if path in generated_hashes]
    matching_required = [
        path
        for path in required
        if after.get(path) is not None and after.get(path) == generated_hashes.get(path)
    ]
    generated_changed = sorted(
        (set(created) | set(updated)) & set(generated_hashes),
        key=str.casefold,
    )
    mode = _generation_mode(context)
    content_verified = (
        all(after.get(path) == digest for path, digest in generated_hashes.items())
        and (not required or len(generated_required) == len(required))
        and (not required or len(matching_required) == len(required))
    )
    satisfied = content_verified
    no_op = content_verified and not generated_changed

    summary = {
        "task_id": plan.task_id,
        "workspace_root": str(root),
        "framework": _identifier(project.framework),
        "advanced_prompt": _prompt_requests_readme(plan, context),
        "generation_mode": mode,
        "created_files": created,
        "updated_files": updated,
        "unchanged_files": unchanged,
        "required_files": list(required),
        "required_files_present": required_present,
        "required_files_generated": generated_required,
        "required_files_matching": matching_required,
        "platformio_ini_present": after.get("platformio.ini") is not None,
        "main_cpp_present": after.get("src/main.cpp") is not None,
        "file_hashes": {
            path: digest for path, digest in after.items() if digest is not None
        },
        "raw_generated_file_count": len(project.files),
        "parsed_file_count": len(project.files),
        "content_verified": content_verified,
        "no_op": no_op,
        "changed_files": generated_changed,
        "generation_satisfied_prompt": satisfied,
    }
    chunked = project.metadata.get("chunked_generation")
    if isinstance(chunked, Mapping):
        summary["generation_mode"] = "chunked"
        summary["file_statuses"] = chunked.get("file_statuses", [])
        summary["failed_files"] = chunked.get("failed_files", [])
        summary["pending_files"] = chunked.get("pending_files", [])
        summary["incremental_writes"] = chunked.get("incremental_writes") is True
        file_statuses = chunked.get("file_statuses")
        if chunked.get("incremental_writes") is True and isinstance(file_statuses, Sequence):
            touched_from_chunked = {
                item.get("path")
                for item in file_statuses
                if isinstance(item, Mapping)
                and item.get("status") == "written"
                and (item.get("created") is True or item.get("updated") is True)
            }
            for item in file_statuses:
                if not isinstance(item, Mapping):
                    continue
                path = item.get("path")
                if not isinstance(path, str):
                    continue
                if item.get("created") is True and path not in created:
                    created.append(path)
                elif item.get("updated") is True and path not in updated:
                    updated.append(path)
            summary["created_files"] = sorted(set(created), key=str.casefold)
            summary["updated_files"] = sorted(set(updated), key=str.casefold)
            summary["chunked_touched_required_files"] = sorted(
                {path for path in touched_from_chunked if isinstance(path, str)} & set(required),
                key=str.casefold,
            )
    else:
        summary["generation_mode"] = "one_shot"
    logger.info(
        "generate_code artifact_summary task_id=%s workspace_root=%s "
        "generation_mode=%s raw_generated_file_count=%d parsed_file_count=%d "
        "created_files=%s updated_files=%s required_files_present=%s",
        plan.task_id,
        root,
        mode,
        len(project.files),
        len(project.files),
        created,
        updated,
        required_present,
    )
    return summary


def _validate_generation_artifact_summary(summary: Mapping[str, Any]) -> None:
    workspace = summary.get("workspace_root")
    if not isinstance(workspace, str) or not workspace.strip():
        raise ValueError("Generation failed: active workspace root is missing.")
    required_files = summary.get("required_files")
    required = tuple(required_files) if isinstance(required_files, Sequence) and not isinstance(required_files, (str, bytes)) else ()
    platformio_required = "platformio.ini" in required or "src/main.cpp" in required
    if summary.get("required_files_present") is not True:
        hashes = summary.get("file_hashes")
        present = set(hashes.keys()) if isinstance(hashes, Mapping) else set()
        missing = [path for path in required if isinstance(path, str) and path not in present]
        detail = ", ".join(missing)
        raise ValueError(
            "Generation failed: required project files are missing"
            + (f": {detail}." if detail else ".")
        )
    if platformio_required and summary.get("platformio_ini_present") is not True:
        raise ValueError("Generation failed: platformio.ini was not written to the active workspace.")
    if platformio_required and summary.get("main_cpp_present") is not True:
        raise ValueError("Generation failed: src/main.cpp was not written to the active workspace.")
    if summary.get("generation_satisfied_prompt") is not True:
        raise ValueError(
            "Generation failed: workspace files do not match the generated project content."
        )


def _artifact_warnings(summary: Mapping[str, Any]) -> tuple[str, ...]:
    if summary.get("advanced_prompt") is not True:
        return ()
    hashes = summary.get("file_hashes")
    if not isinstance(hashes, Mapping):
        return ()
    root_value = summary.get("workspace_root")
    if not isinstance(root_value, str) or not root_value.strip():
        return ()
    main_hash = hashes.get("src/main.cpp")
    if not isinstance(main_hash, str):
        return ()
    try:
        source = (Path(root_value) / "src" / "main.cpp").read_text(encoding="utf-8")
    except OSError:
        return ()
    normalized = source.casefold()
    if (
        len(source) < 1800
        and "webserver" not in normalized
        and "freertos" not in normalized
        and "preferences" not in normalized
    ):
        return ("Generated project is minimal compared to prompt. Consider retrying with a stronger model.",)
    return ()


def _snapshot_files(root: Path, paths: Sequence[str]) -> dict[str, str | None]:
    snapshot: dict[str, str | None] = {}
    for relative in paths:
        target = _contained_workspace_file(root, relative)
        if target.is_file() and not target.is_symlink():
            snapshot[relative] = _file_hash(target)
        else:
            snapshot[relative] = None
    return snapshot


def _file_hash(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    return _content_hash(content)


def _content_hash(content: str) -> str:
    canonical = content.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_generated_path(path: str) -> str:
    return "/".join(Path(*path.split("/")).parts).replace("\\", "/")


def _prompt_requests_readme(plan: ExecutionPlan, context: ExecutionContext) -> bool:
    prompt = context.metadata.get("prompt")
    text = " ".join(
        str(item)
        for item in (
            prompt,
            plan.metadata.get("normalized_prompt"),
            " ".join(plan.requirements),
        )
        if isinstance(item, str)
    ).casefold()
    return "readme" in text or "documentation" in text or "document the project" in text


def _contained_workspace_file(root: Path, relative_path: str) -> Path:
    destination = root / Path(*relative_path.split("/"))
    try:
        destination.resolve(strict=False).relative_to(root.resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("generated file escapes workspace root") from exc
    return destination


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
    report = GeneratedProjectValidationService().validate(project, plan, context)
    if not report.valid:
        raise GeneratedProjectValidationError(report)


def _validation_message(label: str, issues: object) -> str:
    details: list[str] = []
    for issue in tuple(issues):  # type: ignore[arg-type]
        rule_id = getattr(issue, "rule_id", "UNKNOWN")
        message = getattr(issue, "message", str(issue))
        details.append(f"{rule_id}: {message}")
    return f"{label} failed" + (": " + "; ".join(details) if details else "")


async def _emit_handler_event(
    callback: Callable[[Mapping[str, Any]], object] | None,
    event_type: str,
    **payload: object,
) -> None:
    if callback is None:
        return
    result = callback({"event_type": event_type, **payload})
    if inspect.isawaitable(result):
        await result


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
