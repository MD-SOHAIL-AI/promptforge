"""Product-facing orchestration for the ForgeX-owned planner/tool runtime."""

from __future__ import annotations
from backend.runtime.process_owner import owned_operation

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from backend.changes import ChangeSetService
from backend.observability.diagnostics import emit_diagnostic_event
from .activity_stream import project_activity
from .run_reporting import (
    ForgeXRunSummary,
    GenerationStatus,
    ProviderErrorCode,
    ProviderRunSummaryStore,
    ProviderType,
    WorkspaceMode,
    actionable_message,
    detect_workspace_mode,
    normalize_provider_error,
    runtime_failure_message,
    model_recovery_message,
)

from .activity import run_activity_fields
from .failure_contract import failure_details
from backend.contracts.diagnostics import run_provider_diagnostics
from .capability_profiles import profile_for_autonomy
from .product_provider_registry import ProductProviderRegistry
from .tool_contracts import RuntimeClassification
from .tool_policy import product_agent_policy
from .tool_runtime import ForgeXToolRuntime, ProductRuntimeLimits, ToolRuntimeResult

TERMINAL_AGENT_STATUSES = frozenset(
    {"completed", "completed_with_warning", "failed", "cancelled", "blocked", "timed_out"}
)
AUTONOMY_READ_ONLY = "read_only"
AUTONOMY_STAGED_CHANGES = "staged_changes"
AUTONOMY_BUILD_ONLY_STRICT = "build_only_strict"
AUTONOMY_PLAN_ONLY = "plan_only"
AUTONOMY_BUILD_ONLY = "build_only"
AUTONOMY_SIMULATE = "simulate"
AUTONOMY_BUILD_THEN_CONFIRM_FLASH = "build_then_confirm_flash"
AUTONOMOUS_RUN_AUTONOMIES = frozenset(
    {
        AUTONOMY_BUILD_ONLY_STRICT,
        AUTONOMY_BUILD_ONLY,
        AUTONOMY_SIMULATE,
        AUTONOMY_BUILD_THEN_CONFIRM_FLASH,
    }
)
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


AutonomousWorkflowExecutor = Callable[
    [AutonomousWorkflowRequest], Awaitable[Mapping[str, object]]
]
FlashConfirmationExecutor = Callable[
    [FlashConfirmationRequest], Awaitable[Mapping[str, object]]
]
CancelExecutor = Callable[[Any], Awaitable[None]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _provider_failure_message(
    classification: str | None,
    *,
    provider_id: str,
    request_reached_provider: bool,
    http_status: int | None,
    request_dispatched: bool = False,
) -> str | None:
    if classification in {"API_RATE_LIMITED", "PROVIDER_RATE_LIMITED"} and http_status == 429:
        return f"{provider_id} returned HTTP 429 for this request. Wait until the provider reset window, then retry the same model, or explicitly choose another model."
    if classification == "API_QUOTA_EXCEEDED":
        return f"{provider_id} rejected the request because quota or credit is unavailable. Check account credits and model access."
    if classification in {"API_AUTH_INVALID", "PROVIDER_AUTH_ERROR"}:
        return f"{provider_id} rejected the saved API key. Replace the key in Models and test it again."
    if http_status in {502, 503, 504} and classification in {
        "API_NETWORK_ERROR",
        "API_MODEL_UNAVAILABLE",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_TIMEOUT",
        "PROVIDER_HTTP_ERROR",
    }:
        return (
            f"{provider_id} returned HTTP {http_status} because the selected model endpoint is temporarily unavailable. "
            "ForgeX kept your run recoverable and did not switch providers."
        )
    if classification == "PROVIDER_BUSY":
        return (
            "Another ForgeX task is using this model endpoint. "
            "This task paused locally before sending a provider request. Stop the other task or retry when it finishes."
        )
    if classification in {"API_NETWORK_ERROR", "PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT"} and not request_reached_provider:
        if request_dispatched:
            return f"ForgeX dispatched a request to {provider_id} but received no response before the transport deadline. The provider may have received it; saved tool work is preserved."
        return f"ForgeX could not reach {provider_id}, so no provider request was recorded. Check DNS, proxy, firewall, and connectivity."
    if classification in {"API_MODEL_UNAVAILABLE", "PROVIDER_UNAVAILABLE"}:
        return f"{provider_id} could not route the selected model. Choose an available model and retry."
    if classification in {
        "API_TOOLPLAN_INVALID",
        "API_RESPONSE_INVALID",
        "MODEL_OUTPUT_NOT_JSON",
        "TOOLPLAN_SCHEMA_INVALID",
        "TOOLPLAN_VERSION_INVALID",
        "TOOLPLAN_TOOL_UNKNOWN",
        "TOOL_ARGUMENTS_INVALID",
        "FORGEX_NORMALIZATION_ERROR",
    }:
        return (
            f"{provider_id} could not produce a valid safe file plan after ForgeX attempted schema repair. "
            "Retry once or choose a model with reliable structured-output support."
        )
    return None


def _local_conversation_response(instruction: str) -> str | None:
    normalized = " ".join(instruction.casefold().strip().rstrip(".!?").split())
    if normalized in {
        "hi",
        "hii",
        "hilo",
        "hello",
        "hey",
        "hi there",
        "hello there",
        "hey there",
    }:
        return "Hello. Describe the ESP32 or Arduino project you want to create, build, or inspect."
    if normalized in {"how are you", "how are you doing"}:
        return (
            "Ready. Describe the embedded project or firmware task you want help with."
        )
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
    physical_request_count: int = 0
    request_reached_provider: bool = False
    request_dispatched: bool = False
    http_status: int | None = None
    provider_request_id: str | None = None
    retry_after_ms: int | None = None
    last_turn: int | None = None
    response_mode: str | None = None
    last_failure_code: str | None = None
    last_repair_outcome: str | None = None
    last_parse_reason: str | None = None
    last_argument_issue: dict[str, str] | None = None
    last_failure_origin: str | None = None
    budget_persistence_cause: str | None = None
    failure_shape: str | None = None
    budget_failure: dict[str, object] | None = None
    last_response_shape: str | None = None
    last_response_tool_count: int | None = None
    stream_fallback_used: bool = False
    initial_failure_code: str | None = None
    recovery_failure_code: str | None = None
    physical_attempt_id: str | None = None
    response_headers_received: bool = False
    first_event_ms: int | None = None
    last_event_ms: int | None = None
    stream_event_count: int = 0
    completion_received: bool = False
    timeout_stage: str | None = None
    duration_ms: int | None = None
    requested_output_tokens: int | None = None
    effective_output_tokens: int | None = None
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None
    text_event_count: int = 0
    reasoning_event_count: int = 0
    tool_event_count: int = 0
    worker_exit_code: int | None = None
    worker_cleanup_incomplete: bool = False
    worker_phase: str | None = None
    worker_failure_kind: str | None = None
    repair_skip_reason: str | None = None
    initial_attempt_diagnostics: dict[str, object] | None = None
    repair_attempt_diagnostics: dict[str, object] | None = None
    assistant_message_override: str | None = None
    autonomy: str = AUTONOMY_STAGED_CHANGES
    stage_statuses: dict[str, str] = field(
        default_factory=lambda: {name: "pending" for name in AUTONOMOUS_STAGE_NAMES}
    )
    stage_messages: dict[str, str] = field(default_factory=dict)
    active_workflow_task_id: str | None = None
    build_result: dict[str, object] | None = None
    build_verified: bool = False
    simulation_result: dict[str, object] | None = None
    flash_result: dict[str, object] | None = None
    monitor_result: dict[str, object] | None = None
    validation_report: dict[str, object] | None = None
    repair_attempt_count: int = 0
    repair_history: list[dict[str, object]] = field(default_factory=list)
    agent_plan: list[dict[str, str]] = field(default_factory=list)
    pending_decision: bool = False
    task_contract: dict[str, object] | None = None
    task_state: dict[str, object] | None = None
    requirement_report: dict[str, object] | None = None
    suggested_alternatives: list[str] = field(default_factory=list)
    flash_port: str | None = None
    flash_board_type: str | None = None
    flash_environment: str | None = None
    start_monitor_after_flash: bool = False
    approval_id: str | None = None
    approval_expires_at: str | None = None
    retry_of_run_id: str | None = None
    recovered_by_run_id: str | None = None
    interrupted_at: str | None = None
    recoverable: bool = False
    recovery_actions: list[str] = field(default_factory=list)
    recovery: dict[str, object] | None = None
    job_id: str | None = None
    job: dict[str, object] | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    events: list[dict[str, object]] = field(default_factory=list, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_safe_dict(self) -> dict[str, object]:
        error_code = (
            normalize_provider_error(self.classification)
            if self.status in TERMINAL_AGENT_STATUSES and self.status != "completed"
            else None
        )
        actual_provider = self.actual_provider_id or self.provider_id
        provider_type = (
            ProviderType.TEMPLATE
            if actual_provider
            in {"verified_template", "forgex_local", "forgex_builtin"}
            else ProviderType.API
        )
        source = (
            "ForgeX local"
            if actual_provider in {"forgex_local", "forgex_builtin"}
            else (
                "Verified template"
                if actual_provider == "verified_template"
                else actual_provider
            )
        )
        summary = ForgeXRunSummary(
            run_id=self.run_id,
            requested_provider=self.provider_id,
            selected_model=self.model_id,
            actual_provider=actual_provider,
            provider_type=provider_type,
            generation_source=source,
            workspace_mode=detect_workspace_mode_value(self.workspace_mode),
            generation_status=_summary_generation_status(self),
            content_verification_status=(
                "verified"
                if self.requirement_report and self.requirement_report.get("valid") is True
                else "pending"
            ),
            build_status=self.stage_statuses.get("build", "not_started"),
            flash_status=self.stage_statuses.get("flash", "not_started"),
            monitor_status=self.stage_statuses.get("monitor", "not_started"),
            no_op_status=self.status == "completed"
            and not (
                self.created_file_count
                + self.modified_file_count
                + self.deleted_file_count
            ),
            created_files=list(self.created_files),
            modified_files=list(self.modified_files),
            deleted_files=list(self.deleted_files),
            changed_files=[
                *self.created_files,
                *self.modified_files,
                *self.deleted_files,
            ],
            next_suggested_action=(
                "Allow local builds for this project, then verify the saved draft."
                if self.classification == "AGENT_PROJECT_TRUST_REQUIRED"
                else
                "Confirm flash to upload the built firmware to the selected board."
                if self.status == "awaiting_flash_confirmation"
                else (
                    "Review generated changes before applying them."
                    if self.status in {"completed", "completed_with_warning"}
                    and (
                        self.created_file_count
                        + self.modified_file_count
                        + self.deleted_file_count
                    )
                    else None
                )
            ),
            errors=(
                [
                    {
                        "code": (
                            self.classification or error_code.value
                            if error_code is ProviderErrorCode.FORGEX_RUNTIME_ERROR
                            else error_code.value
                        ),
                        "message": (
                            str((self.build_result or {}).get("message"))[:1000]
                            if str(self.classification or "").startswith("DEPENDENCY_")
                            and (self.build_result or {}).get("message") else
                            runtime_failure_message(self.classification)
                            if error_code is ProviderErrorCode.FORGEX_RUNTIME_ERROR
                            else actionable_message(error_code, self.provider_id)
                        ),
                    }
                ]
                if error_code
                else []
            ),
            fallback_used=actual_provider != self.provider_id
            and actual_provider != "forgex_local",
            fallback_reason=self.fallback_reason,
        ).to_safe_dict()
        return {
            "schema_version": 1,
            "task_revision": self.task_contract.get("revision") if self.task_contract else None,
            "task_state": self.task_state,
            "requirement_report": self.requirement_report,
            "failure": failure_details(
                self.classification, status=self.status, actions=self.recovery_actions,
                evidence={"run_id": self.run_id, "build_id": (self.build_result or {}).get("build_id"),
                          "initial_failure_code": self.initial_failure_code, "recovery_failure_code": self.recovery_failure_code,
                          "physical_request_count": self.physical_request_count, "http_status": self.http_status,
                          "job_id": self.job_id, "diagnostics": run_provider_diagnostics(self),
                          "references_valid": bool((self.requirement_report or {}).get("references_valid")),
                          "remaining_allowances": (self.job or {}).get("budget", {}).get("remaining", {}),
                          **({"budget": self.budget_failure} if self.budget_failure else {})}),
            "recovery": dict(self.recovery) if self.recovery else None,
            "job_id": self.job_id,
            "job": self.job,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "provider_id": self.provider_id,
            "status": self.status,
            "classification": self.classification,
            "change_set_id": self.change_set_id,
            "model_id": self.model_id,
            "autonomy": self.autonomy,
            "stage_statuses": dict(self.stage_statuses),
            "stage_messages": dict(self.stage_messages),
            "active_workflow_task_id": self.active_workflow_task_id,
            "build_verified": self.build_verified,
            "build_result": (
                dict(self.build_result)
                if isinstance(self.build_result, Mapping)
                else None
            ),
            "simulation_result": (
                dict(self.simulation_result)
                if isinstance(self.simulation_result, Mapping)
                else None
            ),
            "flash_result": (
                dict(self.flash_result)
                if isinstance(self.flash_result, Mapping)
                else None
            ),
            "monitor_result": (
                dict(self.monitor_result)
                if isinstance(self.monitor_result, Mapping)
                else None
            ),
            "validation_report": (
                dict(self.validation_report)
                if isinstance(self.validation_report, Mapping)
                else None
            ),
            "repair_attempt_count": self.repair_attempt_count,
            "repair_history": [dict(item) for item in self.repair_history],
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
            "retry_of_run_id": self.retry_of_run_id,
            "recovered_by_run_id": self.recovered_by_run_id,
            "interrupted_at": self.interrupted_at,
            "recoverable": self.recoverable,
            "recovery_actions": list(self.recovery_actions),
            "created_file_count": self.created_file_count,
            "modified_file_count": self.modified_file_count,
            "deleted_file_count": self.deleted_file_count,
            "tool_execution_count": self.tool_execution_count,
            "active_workspace_unchanged": self.active_workspace_unchanged,
            "cancellable": self.status not in TERMINAL_AGENT_STATUSES
            and self.status not in {"awaiting_flash_confirmation", "paused_model_unavailable"},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "execution_mode": "tool_planner",
            "assistant_message": self._assistant_message(),
            "provider_diagnostics": run_provider_diagnostics(self),
            "summary": summary,
        }

    def _assistant_message(self) -> str | None:
        if self.status in {"failed", "timed_out"}:
            detail = model_recovery_message(self.initial_failure_code, self.classification,
                self.recovery_failure_code, self.repair_skip_reason)
            if detail:
                return detail
        if self.assistant_message_override:
            return self.assistant_message_override
        if self.classification == "FORGEX_REQUEST_INVALID":
            return "ForgeX could not prepare the model request locally. No request was sent for this attempt. Your saved work is preserved."
        if self.classification in {
            "CHANGESET_REVIEW_REQUIRED",
            "CHANGESET_APPROVAL_REQUIRED",
        }:
            return "Firmware generated, validated, and built. The files are staged and require your approval before they appear in the workspace."
        if self.classification == "CHANGESET_APPLIED":
            return "The validated generated files were applied to the workspace."
        if self.classification == "PLAN_READY":
            if self.agent_plan:
                return "Plan ready. Review the implementation steps, then switch to Auto or Ask mode when you want Forge to execute them."
            return "Plan mode completed without modifying the workspace."
        if self.status == "awaiting_flash_confirmation":
            return "Firmware generated and built successfully. Confirm flash when the target board is connected."
        if self.status == "completed_with_warning":
            return "Firmware build verified. Review the saved draft or retry for a complete model response."
        if self.status == "paused_model_unavailable":
            detail = _provider_failure_message(
                self.classification,
                provider_id=self.actual_provider_id or self.provider_id,
                request_reached_provider=self.request_reached_provider,
                request_dispatched=self.request_dispatched,
                http_status=self.http_status,
            )
            if detail:
                return detail
            return (
                f"{self.actual_provider_id or self.provider_id}'s selected model is temporarily unavailable. "
                "Retry the same model when the endpoint recovers, or explicitly choose another model."
            )
        if self.autonomy == AUTONOMY_BUILD_ONLY and self.status == "completed":
            return "Firmware workflow completed without flashing. Say flash it when you are ready to upload to hardware."
        if self.autonomy == AUTONOMY_SIMULATE and self.status == "completed":
            return (
                "Firmware built and virtual hardware simulation completed successfully."
            )
        if (
            self.autonomy == AUTONOMY_BUILD_THEN_CONFIRM_FLASH
            and self.status == "completed"
        ):
            return "Autonomous run completed."
        if self.status == "completed":
            changed = (
                self.created_file_count
                + self.modified_file_count
                + self.deleted_file_count
            )
            return f"Prepared {changed} staged file change{'s' if changed != 1 else ''}. Review the ChangeSet before applying it."
        if self.status in TERMINAL_AGENT_STATUSES:
            if normalize_provider_error(self.classification) is ProviderErrorCode.FORGEX_RUNTIME_ERROR:
                return runtime_failure_message(self.classification)
            detail = _provider_failure_message(
                self.classification,
                provider_id=self.actual_provider_id or self.provider_id,
                request_reached_provider=self.request_reached_provider,
                request_dispatched=self.request_dispatched,
                http_status=self.http_status,
            )
            return (
                detail
                or f"The agent task ended with {self.status}: {self.classification or self.status}."
            )
        return None


def _summary_generation_status(run: ProductAgentRun) -> str:
    if run.stage_statuses.get("generation") == "completed":
        return GenerationStatus.CONTENT_VERIFIED.value
    if run.status in TERMINAL_AGENT_STATUSES and run.status not in {"completed", "completed_with_warning"}:
        error = normalize_provider_error(run.classification)
        if error in {ProviderErrorCode.FORGEX_RUNTIME_ERROR, ProviderErrorCode.FILE_NOT_FOUND, ProviderErrorCode.PATH_UNSAFE, ProviderErrorCode.POLICY_DENIED}:
            return GenerationStatus.RUNTIME_FAILED.value
        return GenerationStatus.PROVIDER_FAILED.value
    return GenerationStatus.STARTED.value


def detect_workspace_mode_value(value: str) -> WorkspaceMode:
    try:
        return WorkspaceMode(value)
    except ValueError:
        return WorkspaceMode.GENERATE_INTO_FOLDER
