"""Product-facing orchestration for the ForgeX-owned planner/tool runtime."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from backend.changes import ChangeSetService
from .run_reporting import ForgeXRunSummary, GenerationStatus, ProviderRunSummaryStore, ProviderType, WorkspaceMode, actionable_message, detect_workspace_mode, normalize_provider_error

from .activity import run_activity_fields
from .product_provider_registry import ProductProviderRegistry
from .tool_contracts import RuntimeClassification
from .tool_policy import product_agent_policy
from .tool_runtime import ForgeXToolRuntime, ProductRuntimeLimits, ToolRuntimeResult


TERMINAL_AGENT_STATUSES = frozenset({"completed", "failed", "cancelled", "blocked", "timed_out"})
AUTONOMY_STAGED_CHANGES = "staged_changes"
AUTONOMY_PLAN_ONLY = "plan_only"
AUTONOMY_BUILD_ONLY = "build_only"
AUTONOMY_BUILD_THEN_CONFIRM_FLASH = "build_then_confirm_flash"
AUTONOMOUS_RUN_AUTONOMIES = frozenset({AUTONOMY_BUILD_ONLY, AUTONOMY_BUILD_THEN_CONFIRM_FLASH})
AUTONOMOUS_STAGE_NAMES = (
    "planning",
    "generation",
    "changes",
    "build",
    "repair",
    "flash_confirmation",
    "flash",
    "monitor",
)


ProgressCallback = Callable[[str, Mapping[str, object]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AutonomousWorkflowRequest:
    run_id: str
    task_id: str
    project_id: str
    active_workspace_root: Path
    active_workspace: Mapping[str, object]
    instruction: str
    provider_id: str
    model_id: str | None
    fallback_provider_id: str | None
    progress_callback: ProgressCallback


@dataclass(frozen=True, slots=True)
class FlashConfirmationRequest:
    run_id: str
    project_id: str
    port: str | None
    board_type: str | None
    environment: str | None
    start_monitor_after_flash: bool
    progress_callback: ProgressCallback


AutonomousWorkflowExecutor = Callable[[AutonomousWorkflowRequest], Awaitable[Mapping[str, object]]]
FlashConfirmationExecutor = Callable[[FlashConfirmationRequest], Awaitable[Mapping[str, object]]]
CancelExecutor = Callable[[Any], Awaitable[None]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _provider_failure_message(
    classification: str | None,
    *,
    provider_id: str,
    request_reached_provider: bool,
    http_status: int | None,
) -> str | None:
    if classification == "API_RATE_LIMITED" and http_status == 429:
        return f"{provider_id} returned HTTP 429 for this request. Wait briefly or choose another model/provider."
    if classification == "API_QUOTA_EXCEEDED":
        return f"{provider_id} rejected the request because quota or credit is unavailable. Check account credits and model access."
    if classification == "API_AUTH_INVALID":
        return f"{provider_id} rejected the saved API key. Replace the key in Models and test it again."
    if classification == "API_NETWORK_ERROR" and not request_reached_provider:
        return f"ForgeX could not reach {provider_id}, so no provider request was recorded. Check DNS, proxy, firewall, and connectivity."
    if classification == "API_MODEL_UNAVAILABLE":
        return f"{provider_id} could not route the selected model. Choose an available model and retry."
    if classification in {"API_TOOLPLAN_INVALID", "API_RESPONSE_INVALID"}:
        return (
            f"{provider_id} could not produce a valid safe file plan after ForgeX attempted schema repair. "
            "Retry once or choose a model with reliable structured-output support."
        )
    return None


def _local_conversation_response(instruction: str) -> str | None:
    normalized = " ".join(instruction.casefold().strip().rstrip(".!?").split())
    if normalized in {"hi", "hii", "hilo", "hello", "hey", "hi there", "hello there", "hey there"}:
        return "Hello. Describe the ESP32 or Arduino project you want to create, build, or inspect."
    if normalized in {"how are you", "how are you doing"}:
        return "Ready. Describe the embedded project or firmware task you want help with."
    if normalized in {"thanks", "thank you", "thank you very much"}:
        return "You’re welcome."
    return None


@dataclass(slots=True)
class ProductAgentRun:
    run_id: str
    project_id: str
    provider_id: str
    status: str = "queued"
    classification: str | None = None
    change_set_id: str | None = None
    created_file_count: int = 0
    modified_file_count: int = 0
    deleted_file_count: int = 0
    tool_execution_count: int = 0
    active_workspace_unchanged: bool = False
    workspace_mode: str = "generate_into_open_folder"
    created_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    actual_provider_id: str | None = None
    fallback_reason: str | None = None
    model_id: str | None = None
    outbound_request_count: int = 0
    request_reached_provider: bool = False
    http_status: int | None = None
    provider_request_id: str | None = None
    assistant_message_override: str | None = None
    autonomy: str = AUTONOMY_STAGED_CHANGES
    stage_statuses: dict[str, str] = field(default_factory=lambda: {name: "pending" for name in AUTONOMOUS_STAGE_NAMES})
    stage_messages: dict[str, str] = field(default_factory=dict)
    active_workflow_task_id: str | None = None
    build_result: dict[str, object] | None = None
    flash_result: dict[str, object] | None = None
    monitor_result: dict[str, object] | None = None
    validation_report: dict[str, object] | None = None
    repair_attempt_count: int = 0
    agent_plan: list[dict[str, str]] = field(default_factory=list)
    pending_decision: bool = False
    suggested_alternatives: list[str] = field(default_factory=list)
    flash_port: str | None = None
    flash_board_type: str | None = None
    flash_environment: str | None = None
    start_monitor_after_flash: bool = False
    approval_id: str | None = None
    approval_expires_at: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    events: list[dict[str, object]] = field(default_factory=list, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_safe_dict(self) -> dict[str, object]:
        error_code = normalize_provider_error(self.classification) if self.status in TERMINAL_AGENT_STATUSES and self.status != "completed" else None
        actual_provider = self.actual_provider_id or self.provider_id
        provider_type = ProviderType.TEMPLATE if actual_provider in {"verified_template", "forgex_local", "forgex_builtin"} else ProviderType.API
        source = "ForgeX local" if actual_provider in {"forgex_local", "forgex_builtin"} else "Verified template" if actual_provider == "verified_template" else actual_provider
        summary = ForgeXRunSummary(
            run_id=self.run_id,
            requested_provider=self.provider_id,
            actual_provider=actual_provider,
            provider_type=provider_type,
            selected_model=self.model_id,
            generation_source=source,
            workspace_mode=detect_workspace_mode_value(self.workspace_mode),
            generation_status=_summary_generation_status(self),
            content_verification_status="verified" if self.stage_statuses.get("generation") == "completed" or self.status == "completed" else "pending",
            build_status=self.stage_statuses.get("build", "not_started"),
            flash_status=self.stage_statuses.get("flash", "not_started"),
            monitor_status=self.stage_statuses.get("monitor", "not_started"),
            no_op_status=self.status == "completed" and not (self.created_file_count + self.modified_file_count + self.deleted_file_count),
            created_files=list(self.created_files),
            modified_files=list(self.modified_files),
            deleted_files=list(self.deleted_files),
            changed_files=[*self.created_files, *self.modified_files, *self.deleted_files],
            next_suggested_action=(
                "Confirm flash to upload the built firmware to the selected board."
                if self.status == "awaiting_flash_confirmation"
                else
                "Review generated changes before applying them."
                if self.status == "completed" and (self.created_file_count + self.modified_file_count + self.deleted_file_count)
                else None
            ),
            errors=([{"code": error_code.value, "message": actionable_message(error_code, self.provider_id)}] if error_code else []),
            fallback_used=(actual_provider != self.provider_id and actual_provider != "forgex_local") or bool(self.fallback_reason),
            fallback_reason=self.fallback_reason,
        ).to_safe_dict()
        return {
            "run_id": self.run_id, "project_id": self.project_id,
            "provider_id": self.provider_id, "status": self.status,
            "classification": self.classification, "change_set_id": self.change_set_id,
            "model_id": self.model_id,
            "autonomy": self.autonomy,
            "stage_statuses": dict(self.stage_statuses),
            "stage_messages": dict(self.stage_messages),
            "active_workflow_task_id": self.active_workflow_task_id,
            "build_result": dict(self.build_result) if isinstance(self.build_result, Mapping) else None,
            "flash_result": dict(self.flash_result) if isinstance(self.flash_result, Mapping) else None,
            "monitor_result": dict(self.monitor_result) if isinstance(self.monitor_result, Mapping) else None,
            "validation_report": dict(self.validation_report) if isinstance(self.validation_report, Mapping) else None,
            "repair_attempt_count": self.repair_attempt_count,
            "agent_plan": [dict(item) for item in self.agent_plan],
            "pending_decision": self.pending_decision,
            "suggested_alternatives": list(self.suggested_alternatives),
            "flash_confirmation_required": self.status == "awaiting_flash_confirmation",
            "flash_port": self.flash_port,
            "flash_board_type": self.flash_board_type,
            "flash_environment": self.flash_environment,
            "start_monitor_after_flash": self.start_monitor_after_flash,
            "approval_id": self.approval_id,
            "approval_expires_at": self.approval_expires_at,
            "created_file_count": self.created_file_count,
            "modified_file_count": self.modified_file_count,
            "deleted_file_count": self.deleted_file_count,
            "tool_execution_count": self.tool_execution_count,
            "active_workspace_unchanged": self.active_workspace_unchanged,
            "cancellable": self.status not in TERMINAL_AGENT_STATUSES,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "execution_mode": "tool_planner",
            "assistant_message": self._assistant_message(),
            "provider_diagnostics": {
                "actual_provider_id": actual_provider,
                "model_id": self.model_id,
                "fallback_reason": self.fallback_reason,
                "outbound_request_count": self.outbound_request_count,
                "request_reached_provider": self.request_reached_provider,
                "http_status": self.http_status,
                "provider_request_id": self.provider_request_id,
            },
            "summary": summary,
        }

    def _assistant_message(self) -> str | None:
        if self.assistant_message_override:
            return self.assistant_message_override
        if self.classification in {"CHANGESET_REVIEW_REQUIRED", "CHANGESET_APPROVAL_REQUIRED"}:
            return "Firmware generated, validated, and built. The files are staged and require your approval before they appear in the workspace."
        if self.classification == "CHANGESET_APPLIED":
            return "The validated generated files were applied to the workspace."
        if self.classification == "PLAN_READY":
            if self.agent_plan:
                return "Plan ready. Review the implementation steps, then switch to Auto or Ask mode when you want Forge to execute them."
            return "Plan mode completed without modifying the workspace."
        if self.status == "awaiting_flash_confirmation":
            return "Firmware generated and built successfully. Confirm flash when the target board is connected."
        if self.autonomy == AUTONOMY_BUILD_ONLY and self.status == "completed":
            return "Firmware workflow completed without flashing. Say flash it when you are ready to upload to hardware."
        if self.autonomy == AUTONOMY_BUILD_THEN_CONFIRM_FLASH and self.status == "completed":
            return "Autonomous run completed."
        if self.status == "completed":
            changed = self.created_file_count + self.modified_file_count + self.deleted_file_count
            return f"Prepared {changed} staged file change{'s' if changed != 1 else ''}. Review the ChangeSet before applying it."
        if self.status in TERMINAL_AGENT_STATUSES:
            detail = _provider_failure_message(
                self.classification,
                provider_id=self.actual_provider_id or self.provider_id,
                request_reached_provider=self.request_reached_provider,
                http_status=self.http_status,
            )
            return detail or f"The agent task ended with {self.status}: {self.classification or self.status}."
        return None


class ProductAgentService:
    def __init__(
        self,
        *,
        change_service: ChangeSetService,
        provider_registry: ProductProviderRegistry,
        enabled: bool = False,
        limits: ProductRuntimeLimits | None = None,
        summary_store: ProviderRunSummaryStore | None = None,
        autonomous_workflow_executor: AutonomousWorkflowExecutor | None = None,
        flash_confirmation_executor: FlashConfirmationExecutor | None = None,
        cancel_executor: CancelExecutor | None = None,
    ) -> None:
        self.change_service = change_service
        self.provider_registry = provider_registry
        self.enabled = bool(enabled)
        self.limits = limits or ProductRuntimeLimits()
        self.summary_store = summary_store
        self.autonomous_workflow_executor = autonomous_workflow_executor
        self.flash_confirmation_executor = flash_confirmation_executor
        self.cancel_executor = cancel_executor
        self._runs: dict[str, ProductAgentRun] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._provider_cooldowns: dict[str, float] = {}
        self._lock = threading.RLock()
        self.orchestrator: Any | None = None

    def attach_orchestrator(self, orchestrator: Any) -> None:
        """Attach the v2 action owner; chat/session ownership remains here."""
        required = ("start_run", "get_run", "events", "cancel", "confirm_flash", "close")
        if not all(callable(getattr(orchestrator, name, None)) for name in required):
            raise ValueError("AGENT_ORCHESTRATOR_INVALID")
        self.orchestrator = orchestrator

    async def start_run(
        self,
        *,
        project_id: str,
        active_workspace_root: str | Path,
        instruction: str,
        provider_id: str = "fake_planner",
        model_id: str | None = None,
        fallback_provider_id: str | None = None,
        autonomy: str = AUTONOMY_STAGED_CHANGES,
        active_workspace: Mapping[str, object] | None = None,
        board_port: str | None = None,
        board_type: str | None = None,
        environment: str | None = None,
        start_monitor_after_flash: bool = False,
        session_id: str | None = None,
        explicit_edit_authorized: bool = False,
    ) -> ProductAgentRun:
        if not self.enabled:
            raise PermissionError("AGENT_RUNTIME_DISABLED")
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 16_384:
            raise ValueError("AGENT_RUNTIME_INSTRUCTION_INVALID")
        if autonomy not in {AUTONOMY_STAGED_CHANGES, AUTONOMY_PLAN_ONLY, AUTONOMY_BUILD_ONLY, AUTONOMY_BUILD_THEN_CONFIRM_FLASH}:
            raise ValueError("AGENT_RUNTIME_AUTONOMY_INVALID")
        workspace = Path(active_workspace_root).resolve(strict=True)
        local_response = _local_conversation_response(instruction)
        if local_response is not None:
            run = ProductAgentRun(
                f"agent-run-{uuid.uuid4().hex}",
                project_id,
                provider_id,
                status="completed",
                classification="LOCAL_RESPONSE",
                active_workspace_unchanged=True,
                workspace_mode=detect_workspace_mode(workspace).value,
                actual_provider_id="forgex_local",
                model_id=model_id,
                assistant_message_override=local_response,
                autonomy=autonomy,
            )
            self._append_event(run, {"event_type": "runtime.completed", "status": "completed", "classification": "LOCAL_RESPONSE"})
            with self._lock:
                self._runs[run.run_id] = run
            self._persist_summary(run)
            return run
        if self.orchestrator is not None:
            return await self.orchestrator.start_run(
                project_id=project_id,
                active_workspace_root=workspace,
                instruction=instruction,
                provider_id=provider_id,
                model_id=model_id,
                fallback_provider_id=fallback_provider_id,
                autonomy=autonomy,
                active_workspace=active_workspace or {},
                board_port=board_port,
                board_type=board_type,
                environment=environment,
                start_monitor_after_flash=start_monitor_after_flash,
                session_id=session_id,
                explicit_edit_authorized=explicit_edit_authorized,
            )
        if autonomy in AUTONOMOUS_RUN_AUTONOMIES:
            if self.autonomous_workflow_executor is None:
                raise ValueError("AGENT_RUNTIME_AUTONOMOUS_WORKFLOW_UNAVAILABLE")
            return await self._start_autonomous_workflow_run(
                project_id=project_id,
                active_workspace_root=workspace,
                active_workspace=active_workspace or {},
                instruction=instruction,
                provider_id=provider_id,
                model_id=model_id,
                fallback_provider_id=fallback_provider_id,
                autonomy=autonomy,
                board_port=board_port,
                board_type=board_type,
                environment=environment,
                start_monitor_after_flash=start_monitor_after_flash,
            )
        initial_provider_id = provider_id
        initial_fallback_reason: str | None = None
        if self._provider_cooldowns.get(provider_id, 0) > time.monotonic():
            if fallback_provider_id:
                initial_provider_id = fallback_provider_id
                initial_fallback_reason = "API_RATE_LIMITED_COOLDOWN"
            else:
                run = ProductAgentRun(
                    f"agent-run-{uuid.uuid4().hex}",
                    project_id,
                    provider_id,
                    status="failed",
                    classification="API_RATE_LIMITED_COOLDOWN",
                    active_workspace_unchanged=True,
                    workspace_mode=detect_workspace_mode(workspace).value,
                    model_id=model_id,
                    assistant_message_override=f"{provider_id} is temporarily paused after a recent HTTP 429. Choose another provider or retry after five minutes.",
                )
                self._append_event(run, {"event_type": "runtime.failed", "status": "failed", "classification": run.classification})
                with self._lock:
                    self._runs[run.run_id] = run
                self._persist_summary(run)
                return run
        try:
            initial_planner = self.provider_registry.resolve(initial_provider_id)
        except ValueError as exc:
            if not fallback_provider_id:
                raise
            initial_planner = self.provider_registry.resolve(fallback_provider_id)
            initial_provider_id = fallback_provider_id
            initial_fallback_reason = str(exc)
        run = ProductAgentRun(
            f"agent-run-{uuid.uuid4().hex}",
            project_id,
            provider_id,
            workspace_mode=detect_workspace_mode(workspace).value,
            actual_provider_id=initial_provider_id,
            fallback_reason=initial_fallback_reason,
            model_id=model_id,
        )
        self._append_event(run, {"event_type": "runtime.queued", "status": "queued"})
        with self._lock:
            self._runs[run.run_id] = run
        self._persist_summary(run)

        async def execute() -> None:
            run.status = "running"
            run.updated_at = _now()
            self._append_event(run, {"event_type": "runtime.started", "status": "running"})
            runtime = ForgeXToolRuntime(
                active_workspace_root=workspace,
                change_service=self.change_service,
            )
            planner = initial_planner
            if initial_fallback_reason:
                self._append_event(run, {"event_type": "provider.fallback", "status": "running", "classification": initial_fallback_reason})
            result = await asyncio.to_thread(
                runtime.run_product,
                task=instruction,
                planner=planner,
                policy=product_agent_policy(max_bytes_per_file=self.limits.max_bytes_per_file),
                limits=self.limits,
                cancel_event=run.cancel_event,
                event_callback=lambda event: self._append_event(run, event),
            )
            if result.provider_classification in {"API_RATE_LIMITED", "API_QUOTA_EXCEEDED"}:
                self._provider_cooldowns[provider_id] = time.monotonic() + 300
            primary_diagnostics = result.provider_diagnostics
            if (
                result.classification is RuntimeClassification.PROVIDER_INVALID
                and fallback_provider_id
                and run.actual_provider_id == provider_id
            ):
                try:
                    fallback = self.provider_registry.resolve(fallback_provider_id)
                except ValueError:
                    pass
                else:
                    run.actual_provider_id = fallback_provider_id
                    run.fallback_reason = result.provider_classification or result.classification.value
                    self._append_event(run, {"event_type": "provider.fallback", "status": "running", "classification": run.fallback_reason})
                    result = await asyncio.to_thread(
                        runtime.run_product,
                        task=instruction,
                        planner=fallback,
                        policy=product_agent_policy(max_bytes_per_file=self.limits.max_bytes_per_file),
                        limits=self.limits,
                        cancel_event=run.cancel_event,
                        event_callback=lambda event: self._append_event(run, event),
                    )
                    if primary_diagnostics and not result.provider_diagnostics:
                        result = replace(result, provider_diagnostics=primary_diagnostics)
            self._complete(run, result)

        self._tasks[run.run_id] = asyncio.create_task(execute(), name=run.run_id)
        return run

    async def _start_autonomous_workflow_run(
        self,
        *,
        project_id: str,
        active_workspace_root: Path,
        active_workspace: Mapping[str, object],
        instruction: str,
        provider_id: str,
        model_id: str | None,
        fallback_provider_id: str | None,
        autonomy: str,
        board_port: str | None,
        board_type: str | None,
        environment: str | None,
        start_monitor_after_flash: bool,
    ) -> ProductAgentRun:
        run = ProductAgentRun(
            f"agent-run-{uuid.uuid4().hex}",
            project_id,
            provider_id,
            workspace_mode=detect_workspace_mode(active_workspace_root).value,
            actual_provider_id=provider_id,
            fallback_reason=fallback_provider_id,
            model_id=model_id,
            autonomy=autonomy,
            flash_port=board_port,
            flash_board_type=board_type,
            flash_environment=environment,
            start_monitor_after_flash=bool(start_monitor_after_flash),
        )
        run.active_workflow_task_id = f"task-{run.run_id}"
        _set_stage(run, "planning", "pending")
        _set_stage(run, "generation", "pending")
        _set_stage(run, "build", "pending")
        self._append_event(run, {"event_type": "runtime.queued", "status": "queued", "stage": "planning", "message": "Queued autonomous Forge run"})
        with self._lock:
            self._runs[run.run_id] = run
        self._persist_summary(run)

        async def progress(event: str, payload: Mapping[str, object]) -> None:
            self._record_workflow_progress(run, event, payload)

        async def execute() -> None:
            run.status = "running"
            run.updated_at = _now()
            _set_stage(run, "planning", "running", "Planning project workflow")
            self._append_event(run, {"event_type": "runtime.started", "status": "running", "stage": "planning", "message": "Autonomous Forge run started"})
            try:
                assert self.autonomous_workflow_executor is not None
                result = await self.autonomous_workflow_executor(
                    AutonomousWorkflowRequest(
                        run_id=run.run_id,
                        task_id=run.active_workflow_task_id or f"task-{run.run_id}",
                        project_id=project_id,
                        active_workspace_root=active_workspace_root,
                        active_workspace=dict(active_workspace),
                        instruction=instruction,
                        provider_id=provider_id,
                        model_id=model_id,
                        fallback_provider_id=fallback_provider_id,
                        progress_callback=progress,
                    )
                )
            except asyncio.CancelledError:
                run.status = "cancelled"
                run.classification = "AGENT_RUNTIME_CANCELLED"
                _set_active_stages_cancelled(run)
                self._append_event(run, {"event_type": "runtime.cancelled", "status": "cancelled", "stage": _active_stage(run), "message": "Autonomous Forge run cancelled"})
                self._persist_summary(run)
                raise
            except Exception as exc:
                run.status = "failed"
                run.classification = type(exc).__name__
                _set_stage(run, _active_stage(run), "failed", str(exc) or type(exc).__name__)
                self._append_event(run, {"event_type": "runtime.failed", "status": "failed", "stage": _active_stage(run), "classification": run.classification, "message": "Autonomous Forge run failed"})
                self._persist_summary(run)
                return

            if run.cancel_event.is_set() or run.status == "cancelling":
                run.status = "cancelled"
                run.classification = "AGENT_RUNTIME_CANCELLED"
                _set_active_stages_cancelled(run)
                self._append_event(run, {"event_type": "runtime.cancelled", "status": "cancelled", "stage": _active_stage(run), "message": "Autonomous Forge run cancelled"})
                self._persist_summary(run)
                return

            run.build_result = _safe_mapping(result.get("build_result"))
            success = result.get("success") is True
            if success:
                _set_stage(run, "planning", "completed")
                _set_stage(run, "generation", "completed")
                _set_stage(run, "build", "completed", _string_or_none(result.get("message")) or "Build completed")
                if run.autonomy == AUTONOMY_BUILD_THEN_CONFIRM_FLASH:
                    _set_stage(run, "flash_confirmation", "waiting", "Confirm flash when the target board is connected")
                    run.status = "awaiting_flash_confirmation"
                    run.classification = "BUILD_READY_FOR_FLASH"
                    event_type = "runtime.awaiting_flash_confirmation"
                    message = "Build completed; waiting for flash confirmation"
                else:
                    run.status = "completed"
                    run.classification = "AUTONOMOUS_WORKFLOW_COMPLETED"
                    event_type = "runtime.completed"
                    message = "Build completed without hardware action"
                run.active_workspace_unchanged = False
                run.updated_at = _now()
                self._append_event(run, {"event_type": event_type, "status": run.status, "stage": "build", "classification": run.classification, "message": message})
            else:
                _set_stage(run, "build", "failed", _string_or_none(result.get("message")) or "Build failed")
                run.status = "failed"
                run.classification = _string_or_none(result.get("classification")) or "AUTONOMOUS_WORKFLOW_FAILED"
                run.updated_at = _now()
                self._append_event(run, {"event_type": "runtime.failed", "status": "failed", "stage": "build", "classification": run.classification, "message": "Autonomous workflow failed"})
            self._persist_summary(run)

        self._tasks[run.run_id] = asyncio.create_task(execute(), name=run.run_id)
        return run

    def get_run(self, run_id: str) -> ProductAgentRun:
        if self.orchestrator is not None and self.orchestrator.owns(run_id):
            return self.orchestrator.get_run(run_id)
        with self._lock:
            try:
                return self._runs[run_id]
            except KeyError as exc:
                raise KeyError("AGENT_RUNTIME_RUN_NOT_FOUND") from exc

    def steer(self, run_id: str, message: str) -> ProductAgentRun:
        """Inject user guidance into an active V3 turn at the next safe tool boundary."""
        if self.orchestrator is not None and self.orchestrator.owns(run_id):
            return self.orchestrator.steer(run_id, message)
        raise ValueError("AGENT_RUNTIME_STEERING_UNAVAILABLE")

    def prepare_flash(
        self,
        run_id: str,
        *,
        port: str | None = None,
        board_type: str | None = None,
        environment: str | None = None,
        start_monitor_after_flash: bool | None = None,
    ) -> ProductAgentRun:
        if self.orchestrator is not None and self.orchestrator.owns(run_id):
            return self.orchestrator.prepare_flash(
                run_id,
                port=port,
                board_type=board_type,
                environment=environment,
                start_monitor_after_flash=start_monitor_after_flash,
            )
        run = self.get_run(run_id)
        if run.status == "awaiting_flash_confirmation":
            return run
        if run.status != "completed" or not isinstance(run.build_result, Mapping):
            raise ValueError("AGENT_RUNTIME_VERIFIED_BUILD_NOT_FOUND")
        digest = run.build_result.get("firmware_hash") or run.build_result.get("artifact_hash") or run.build_result.get("checksum")
        if not isinstance(digest, str) or not digest:
            raise ValueError("AGENT_RUNTIME_VERIFIED_BUILD_NOT_FOUND")
        # Legacy non-orchestrator mode has no durable approval service; callers
        # must use the V2/V3 orchestrator for safe continuation.
        raise ValueError("AGENT_RUNTIME_FLASH_APPROVAL_UNAVAILABLE")

    async def confirm_flash(
        self,
        run_id: str,
        *,
        port: str | None = None,
        board_type: str | None = None,
        environment: str | None = None,
        start_monitor_after_flash: bool | None = None,
    ) -> ProductAgentRun:
        if self.orchestrator is not None and self.orchestrator.owns(run_id):
            return await self.orchestrator.confirm_flash(
                run_id,
                port=port,
                board_type=board_type,
                environment=environment,
                start_monitor_after_flash=start_monitor_after_flash,
            )
        run = self.get_run(run_id)
        if run.autonomy != AUTONOMY_BUILD_THEN_CONFIRM_FLASH:
            raise ValueError("AGENT_RUNTIME_FLASH_CONFIRMATION_UNAVAILABLE")
        if run.status != "awaiting_flash_confirmation":
            raise ValueError("AGENT_RUNTIME_FLASH_CONFIRMATION_NOT_READY")
        if self.flash_confirmation_executor is None:
            raise ValueError("AGENT_RUNTIME_FLASH_EXECUTOR_UNAVAILABLE")
        run.status = "running"
        run.updated_at = _now()
        if port is not None:
            run.flash_port = port
        if board_type is not None:
            run.flash_board_type = board_type
        if environment is not None:
            run.flash_environment = environment
        if start_monitor_after_flash is not None:
            run.start_monitor_after_flash = bool(start_monitor_after_flash)
        _set_stage(run, "flash_confirmation", "completed", "Flash confirmed")
        _set_stage(run, "flash", "running", "Flashing firmware")
        self._append_event(run, {"event_type": "flash.confirmed", "status": "running", "stage": "flash", "message": "Flash confirmed"})

        async def progress(event: str, payload: Mapping[str, object]) -> None:
            self._record_flash_progress(run, event, payload)

        async def execute_flash() -> None:
            try:
                assert self.flash_confirmation_executor is not None
                self._append_event(run, {"event_type": "flash.started", "status": "running", "stage": "flash", "message": "Flashing firmware"})
                result = await self.flash_confirmation_executor(
                    FlashConfirmationRequest(
                        run_id=run.run_id,
                        project_id=run.project_id,
                        port=run.flash_port,
                        board_type=run.flash_board_type,
                        environment=run.flash_environment,
                        start_monitor_after_flash=run.start_monitor_after_flash,
                        progress_callback=progress,
                    )
                )
            except asyncio.CancelledError:
                run.status = "cancelled"
                run.classification = "AGENT_RUNTIME_CANCELLED"
                _set_stage(run, "flash", "cancelled", "Flash cancelled")
                self._append_event(run, {"event_type": "runtime.cancelled", "status": "cancelled", "stage": "flash", "message": "Flash cancelled"})
                self._persist_summary(run)
                raise
            except Exception as exc:
                run.status = "failed"
                run.classification = type(exc).__name__
                _set_stage(run, "flash", "failed", str(exc) or type(exc).__name__)
                self._append_event(run, {"event_type": "runtime.failed", "status": "failed", "stage": "flash", "classification": run.classification, "message": "Flash failed"})
                self._persist_summary(run)
                return

            run.flash_result = _safe_mapping(result.get("flash_result"))
            run.monitor_result = _safe_mapping(result.get("monitor_result"))
            if result.get("success") is True:
                _set_stage(run, "flash", "completed", _string_or_none(result.get("message")) or "Flash completed")
                if run.start_monitor_after_flash:
                    _set_stage(run, "monitor", "completed" if run.monitor_result else "failed")
                run.status = "completed"
                run.classification = "AUTONOMOUS_WORKFLOW_COMPLETED"
                self._append_event(run, {"event_type": "runtime.completed", "status": "completed", "stage": "flash", "classification": run.classification, "message": "Autonomous Forge run completed"})
            else:
                _set_stage(run, "flash", "failed", _string_or_none(result.get("message")) or "Flash failed")
                run.status = "failed"
                run.classification = _string_or_none(result.get("classification")) or "FLASH_FAILED"
                self._append_event(run, {"event_type": "runtime.failed", "status": "failed", "stage": "flash", "classification": run.classification, "message": "Flash failed"})
            run.updated_at = _now()
            self._persist_summary(run)

        self._tasks[f"{run.run_id}:flash"] = asyncio.create_task(execute_flash(), name=f"{run.run_id}:flash")
        self._persist_summary(run)
        return run

    def apply_pending_changes(self, run_id: str) -> ProductAgentRun:
        if self.orchestrator is not None and self.orchestrator.owns(run_id):
            return self.orchestrator.apply_pending_changes(run_id)
        run = self.get_run(run_id)
        if not run.change_set_id:
            raise ValueError("AGENT_RUNTIME_CHANGESET_NOT_READY")
        if run.classification not in {"CHANGESET_REVIEW_REQUIRED", "CHANGESET_APPROVAL_REQUIRED"}:
            raise ValueError("AGENT_RUNTIME_CHANGESET_APPROVAL_NOT_READY")
        applied = self.change_service.apply(run.change_set_id)
        run.active_workspace_unchanged = False
        run.classification = "CHANGESET_APPLIED"
        run.assistant_message_override = f"Applied {len(applied.changed_files)} validated file changes to the workspace."
        _set_stage(run, "changes", "completed", "Validated ChangeSet applied")
        self._persist_summary(run)
        return run

    def events(self, run_id: str, *, after_sequence: int = 0) -> tuple[dict[str, object], ...]:
        if self.orchestrator is not None and self.orchestrator.owns(run_id):
            return self.orchestrator.events(run_id, after_sequence=after_sequence)
        run = self.get_run(run_id)
        with self._lock:
            return tuple(dict(item) for item in run.events if int(item["sequence"]) > after_sequence)

    async def cancel(self, run_id: str) -> ProductAgentRun:
        if self.orchestrator is not None and self.orchestrator.owns(run_id):
            return await self.orchestrator.cancel(run_id)
        run = self.get_run(run_id)
        if run.status == "awaiting_flash_confirmation":
            run.cancel_event.set()
            run.status = "cancelled"
            run.classification = "FLASH_SKIPPED"
            run.updated_at = _now()
            _set_stage(run, "flash_confirmation", "cancelled", "Flash skipped")
            self._append_event(run, {"event_type": "runtime.cancelled", "status": "cancelled", "stage": "flash_confirmation", "classification": run.classification, "message": "Flash skipped"})
            self._persist_summary(run)
            return run
        if run.status not in TERMINAL_AGENT_STATUSES:
            run.cancel_event.set()
            run.status = "cancelling"
            run.updated_at = _now()
            task = self._tasks.get(run_id)
            if run.autonomy in AUTONOMOUS_RUN_AUTONOMIES and task is not None:
                task.cancel()
            flash_task = self._tasks.get(f"{run_id}:flash")
            if flash_task is not None:
                flash_task.cancel()
            self._append_event(run, {"event_type": "runtime.cancelling", "status": "cancelling"})
            if run.autonomy in AUTONOMOUS_RUN_AUTONOMIES and self.cancel_executor is not None:
                try:
                    await self.cancel_executor(run)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._append_event(
                        run,
                        {
                            "event_type": "runtime.cancel_cleanup_failed",
                            "status": "cancelling",
                            "stage": _active_stage(run),
                            "message": str(exc) or type(exc).__name__,
                        },
                    )
        return run

    def _record_workflow_progress(self, run: ProductAgentRun, event: str, payload: Mapping[str, object]) -> None:
        if event.casefold() == "message.delta":
            self._append_event(run, {
                "event_type": "message.delta",
                "status": run.status,
                "turn": payload.get("turn"),
                "index": payload.get("index"),
                "delta": payload.get("delta"),
            })
            return
        normalized = event.upper()
        failure = payload.get("failure")
        failure_message = _string_or_none(failure.get("message")) if isinstance(failure, Mapping) else None
        message = _string_or_none(payload.get("message")) or failure_message or _workflow_event_message(normalized)
        if normalized == "GENERATION_COMPLETED":
            message = "Files generated; validating the complete firmware project"
        stage = _workflow_event_stage(normalized)
        provider_id = _string_or_none(payload.get("provider_id"))
        model_id = _string_or_none(payload.get("model_id"))
        if provider_id:
            run.actual_provider_id = provider_id
        if model_id:
            run.model_id = model_id
        if normalized == "PLAN_GENERATED":
            _set_stage(run, "planning", "completed", message)
            _set_stage(run, "generation", "running", "Generating project files")
        elif normalized.startswith("GENERATION_"):
            status = "running" if normalized == "GENERATION_COMPLETED" else _stage_status_for_event(normalized)
            _set_stage(run, "generation", status, message)
        elif normalized.startswith("CODE_GENERATION_"):
            _set_stage(run, "generation", _stage_status_for_event(normalized), message)
        elif normalized == "PROJECT_VALIDATION_STARTED":
            _set_stage(run, "generation", "running", message)
        elif normalized == "PROJECT_VALIDATION_FAILED":
            _set_stage(run, "generation", "running", message)
        elif normalized == "PROJECT_REPAIR_STARTED":
            _set_stage(run, "repair", "running", message)
        elif normalized == "PROJECT_REPAIR_COMPLETED":
            _set_stage(run, "repair", "completed", message)
            _set_stage(run, "generation", "running", "Revalidating the repaired project")
        elif normalized == "PROJECT_REPAIR_FAILED":
            _set_stage(run, "repair", "running", message)
            _set_stage(run, "generation", "running", "Retrying bounded project repair")
        elif normalized == "PROJECT_VALIDATION_COMPLETED":
            _set_stage(run, "generation", "completed", message)
        elif normalized == "BUILD_STARTED":
            _set_stage(run, "generation", "completed")
            _set_stage(run, "build", "running", message)
        elif normalized == "BUILD_COMPLETED":
            _set_stage(run, "build", "completed", message)
            run.build_result = _safe_mapping(payload)
        elif normalized == "BUILD_FAILED":
            _set_stage(run, "build", "failed", message)
            run.build_result = _safe_mapping(payload)
        elif normalized == "BUILD_REPAIR_STARTED":
            _set_stage(run, "repair", "running", message)
        elif normalized == "BUILD_REPAIR_COMPLETED":
            _set_stage(run, "repair", "completed", message)
            _set_stage(run, "build", "completed", message)
            run.build_result = _safe_mapping(payload)
        elif normalized == "BUILD_REPAIR_FAILED":
            _set_stage(run, "repair", "failed", message)
        elif normalized == "WORKFLOW_FAILED":
            _set_stage(run, stage, "failed", message)
        run.updated_at = _now()
        self._append_event(
            run,
            {
                "event_type": event.lower(),
                "status": run.status,
                "stage": stage,
                "message": message,
                "classification": (
                    _string_or_none(payload.get("status"))
                    or _string_or_none(payload.get("category"))
                    or (_string_or_none(failure.get("category")) if isinstance(failure, Mapping) else None)
                ),
            },
        )

    def _record_flash_progress(self, run: ProductAgentRun, event: str, payload: Mapping[str, object]) -> None:
        normalized = event.upper()
        stage = "monitor" if normalized.startswith("MONITOR") else "flash"
        message = _string_or_none(payload.get("message")) or _workflow_event_message(normalized)
        status = _stage_status_for_event(normalized)
        _set_stage(run, stage, status, message)
        run.updated_at = _now()
        self._append_event(run, {"event_type": event.lower(), "status": run.status, "stage": stage, "message": message})

    async def close(self) -> None:
        if self.orchestrator is not None:
            await self.orchestrator.close()
        for run in tuple(self._runs.values()):
            if run.status not in TERMINAL_AGENT_STATUSES:
                run.cancel_event.set()
        for task in tuple(self._tasks.values()):
            if not task.done():
                task.cancel()
        tasks = tuple(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _append_event(self, run: ProductAgentRun, event: Mapping[str, object]) -> None:
        allowed = {
            "event_type",
            "status",
            "classification",
            "turn",
            "tool",
            "tool_count",
            "tool_execution_count",
            "changed_file_count",
            "stage",
            "message",
            "safe_message",
            "progress",
            "attempt_number",
            "model_id",
            "delta",
            "index",
            "activity",
            "activity_label",
            "activity_phase",
        }
        safe = {key: value for key, value in event.items() if key in allowed and isinstance(value, (str, int, bool))}
        safe.update(run_activity_fields(safe))
        safe["run_id"] = run.run_id
        safe["created_at"] = _now()
        safe["timestamp"] = safe["created_at"]
        safe["stage"] = safe.get("stage") if isinstance(safe.get("stage"), str) else "generation"
        safe["provider_id"] = run.provider_id
        safe["provider_type"] = (ProviderType.TEMPLATE if run.provider_id in {"verified_template", "forgex_builtin"} else ProviderType.API).value
        actual_provider = run.actual_provider_id or run.provider_id
        safe["generation_source"] = "ForgeX local" if actual_provider == "forgex_builtin" else "Verified template" if actual_provider == "verified_template" else actual_provider
        safe["message"] = _truncate_message(str(safe.get("message") or safe.get("safe_message") or safe.get("status") or safe.get("event_type") or "generation update"))
        safe["safe_summary"] = {"classification": safe["classification"]} if "classification" in safe else {}
        with self._lock:
            safe["sequence"] = len(run.events) + 1
            run.events.append(safe)

    def _complete(self, run: ProductAgentRun, result: ToolRuntimeResult) -> None:
        run.classification = result.provider_classification or result.classification.value
        run.change_set_id = result.change_set_id
        run.created_file_count = result.created_file_count
        run.modified_file_count = result.modified_file_count
        run.deleted_file_count = result.deleted_file_count
        run.tool_execution_count = result.tool_execution_count
        run.active_workspace_unchanged = result.active_workspace_unchanged
        diagnostics = result.provider_diagnostics
        actual_provider = diagnostics.get("provider_id")
        if isinstance(actual_provider, str) and actual_provider:
            run.actual_provider_id = actual_provider
        actual_model = diagnostics.get("model_id")
        if isinstance(actual_model, str) and actual_model:
            run.model_id = actual_model
        fallback_reason = diagnostics.get("fallback_reason")
        if isinstance(fallback_reason, str) and fallback_reason:
            run.fallback_reason = fallback_reason
        run.outbound_request_count = int(diagnostics.get("outbound_request_count", 0) or 0)
        run.request_reached_provider = diagnostics.get("request_reached_provider") is True
        status = diagnostics.get("http_status")
        run.http_status = status if isinstance(status, int) and not isinstance(status, bool) else None
        request_id = diagnostics.get("provider_request_id")
        run.provider_request_id = request_id if isinstance(request_id, str) else None
        if result.change_set_id:
            try:
                change_set = self.change_service.get(result.change_set_id)
                run.created_files = [item.path for item in change_set.changed_files if item.change_type == "created"]
                run.modified_files = [item.path for item in change_set.changed_files if item.change_type == "modified"]
                run.deleted_files = [item.path for item in change_set.changed_files if item.change_type == "deleted"]
            except Exception:
                # Summary path enrichment must not replace the validated run result.
                pass
        if result.classification is RuntimeClassification.PASS:
            run.status = "completed"
        elif result.classification is RuntimeClassification.CANCELLED:
            run.status = "cancelled"
        elif result.classification is RuntimeClassification.LIMIT_EXCEEDED:
            run.status = "timed_out"
        elif result.classification is RuntimeClassification.POLICY_DENIED:
            run.status = "blocked"
        else:
            run.status = "failed"
        run.updated_at = _now()
        self._append_event(run, {"event_type": "runtime.completed" if run.status == "completed" else "runtime.failed", "status": run.status, "classification": run.classification})
        self._persist_summary(run)

    def _persist_summary(self, run: ProductAgentRun) -> None:
        if self.summary_store is None:
            return
        try:
            self.summary_store.save(run.to_safe_dict()["summary"])  # type: ignore[arg-type]
        except (OSError, ValueError):
            # Summary persistence is secondary to generation and never changes its result.
            return


def _set_stage(run: ProductAgentRun, stage: str, status: str, message: str | None = None) -> None:
    if stage not in AUTONOMOUS_STAGE_NAMES:
        return
    run.stage_statuses[stage] = status
    if message:
        run.stage_messages[stage] = _truncate_message(message)


def _set_active_stages_cancelled(run: ProductAgentRun) -> None:
    for stage, status in list(run.stage_statuses.items()):
        if status in {"running", "waiting"}:
            _set_stage(run, stage, "cancelled", "Cancelled")


def _active_stage(run: ProductAgentRun) -> str:
    for stage, status in run.stage_statuses.items():
        if status in {"running", "waiting"}:
            return stage
    return "generation"


def _safe_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    allowed_value_types = (str, int, bool, float, type(None))
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > 128:
            continue
        if isinstance(item, allowed_value_types):
            result[key] = item
        elif isinstance(item, list):
            result[key] = [
                element
                for element in item[:20]
                if isinstance(element, allowed_value_types)
            ]
    return result


def _string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value else None


def _truncate_message(value: str) -> str:
    text = " ".join(value.split())
    return text[:1000]


def _workflow_event_stage(event: str) -> str:
    if event.startswith("BUILD_REPAIR"):
        return "repair"
    if event.startswith("GENERATION"):
        return "generation"
    if event.startswith("BUILD"):
        return "build"
    if event.startswith("FLASH"):
        return "flash"
    if event.startswith("MONITOR"):
        return "monitor"
    if event.startswith("PLAN") or event == "TASK_CREATED":
        return "planning"
    return "generation"


def _stage_status_for_event(event: str) -> str:
    if event.endswith("_STARTED"):
        return "running"
    if event.endswith("_COMPLETED"):
        return "completed"
    if event.endswith("_FAILED"):
        return "failed"
    if event.endswith("_CANCELLED"):
        return "cancelled"
    return "running"


def _workflow_event_message(event: str) -> str:
    return event.lower().replace("_", " ")


def _summary_generation_status(run: ProductAgentRun) -> str:
    if run.stage_statuses.get("generation") == "completed":
        return GenerationStatus.CONTENT_VERIFIED.value
    if run.status in TERMINAL_AGENT_STATUSES and run.status != "completed":
        return GenerationStatus.PROVIDER_FAILED.value
    return GenerationStatus.STARTED.value


def detect_workspace_mode_value(value: str) -> WorkspaceMode:
    try:
        return WorkspaceMode(value)
    except ValueError:
        return WorkspaceMode.GENERATE_INTO_FOLDER
