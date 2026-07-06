"""Product-facing orchestration for the ForgeX-owned planner/tool runtime."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from backend.bridges.diff_service import BridgeDiffService
from backend.provider_runtime import ForgeXRunSummary, GenerationStatus, ProviderRunSummaryStore, ProviderType, actionable_message, detect_workspace_mode, normalize_provider_error

from .product_provider_registry import ProductProviderRegistry
from .tool_contracts import RuntimeClassification
from .tool_policy import product_agent_policy
from .tool_runtime import ForgeXToolRuntime, ProductRuntimeLimits, ToolRuntimeResult


TERMINAL_AGENT_STATUSES = frozenset({"completed", "failed", "cancelled", "blocked", "timed_out"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _provider_failure_message(
    classification: str | None,
    *,
    request_reached_provider: bool,
    http_status: int | None,
) -> str | None:
    if classification == "API_RATE_LIMITED" and http_status == 429:
        return "OpenRouter returned HTTP 429 for this request. Wait briefly or choose another model/provider."
    if classification == "API_QUOTA_EXCEEDED":
        return "OpenRouter rejected the request because quota or credit is unavailable. Check account credits and model access."
    if classification == "API_AUTH_INVALID":
        return "OpenRouter rejected the saved API key. Replace the key in Models & Agents and test it again."
    if classification == "API_NETWORK_ERROR" and not request_reached_provider:
        return "ForgeX could not reach OpenRouter, so no provider request was recorded. Check DNS, proxy, firewall, and connectivity."
    if classification == "API_MODEL_UNAVAILABLE":
        return "OpenRouter could not route the selected model. Choose an available model and retry."
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
    review_id: str | None = None
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
    outbound_request_count: int = 0
    request_reached_provider: bool = False
    http_status: int | None = None
    provider_request_id: str | None = None
    assistant_message_override: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    events: list[dict[str, object]] = field(default_factory=list, repr=False)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def to_safe_dict(self) -> dict[str, object]:
        error_code = normalize_provider_error(self.classification) if self.status in TERMINAL_AGENT_STATUSES and self.status != "completed" else None
        actual_provider = self.actual_provider_id or self.provider_id
        provider_type = ProviderType.TEMPLATE if actual_provider in {"verified_template", "forgex_local"} else ProviderType.API
        source = "ForgeX local" if actual_provider == "forgex_local" else "Verified template" if actual_provider == "verified_template" else actual_provider
        summary = ForgeXRunSummary(
            run_id=self.run_id,
            requested_provider=self.provider_id,
            actual_provider=actual_provider,
            provider_type=provider_type,
            generation_source=source,
            workspace_mode=detect_workspace_mode_value(self.workspace_mode),
            generation_status=(GenerationStatus.CONTENT_VERIFIED.value if self.status == "completed" else GenerationStatus.PROVIDER_FAILED.value if self.status in TERMINAL_AGENT_STATUSES else GenerationStatus.STARTED.value),
            content_verification_status="verified" if self.status == "completed" else "pending",
            no_op_status=self.status == "completed" and not (self.created_file_count + self.modified_file_count + self.deleted_file_count),
            created_files=list(self.created_files),
            modified_files=list(self.modified_files),
            deleted_files=list(self.deleted_files),
            changed_files=[*self.created_files, *self.modified_files, *self.deleted_files],
            next_suggested_action=(
                "Review generated changes before applying them."
                if self.status == "completed" and (self.created_file_count + self.modified_file_count + self.deleted_file_count)
                else None
            ),
            errors=([{"code": error_code.value, "message": actionable_message(error_code, self.provider_id)}] if error_code else []),
            fallback_used=actual_provider != self.provider_id and actual_provider != "forgex_local",
            fallback_reason=self.fallback_reason,
        ).to_safe_dict()
        return {
            "run_id": self.run_id, "project_id": self.project_id,
            "provider_id": self.provider_id, "status": self.status,
            "classification": self.classification, "review_id": self.review_id,
            "created_file_count": self.created_file_count,
            "modified_file_count": self.modified_file_count,
            "deleted_file_count": self.deleted_file_count,
            "tool_execution_count": self.tool_execution_count,
            "active_workspace_unchanged": self.active_workspace_unchanged,
            "raw_prompt_persisted": False, "raw_response_persisted": False,
            "apply_run": False, "build_run": False, "flash_run": False,
            "cancellable": self.status not in TERMINAL_AGENT_STATUSES,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "execution_mode": "tool_planner", "pending_approvals": [],
            "assistant_message": self._assistant_message(),
            "provider_diagnostics": {
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
        if self.status == "completed":
            changed = self.created_file_count + self.modified_file_count + self.deleted_file_count
            return f"Completed the sandbox task with {changed} changed file{'s' if changed != 1 else ''}. Review the changes before applying them."
        if self.status in TERMINAL_AGENT_STATUSES:
            detail = _provider_failure_message(
                self.classification,
                request_reached_provider=self.request_reached_provider,
                http_status=self.http_status,
            )
            return detail or f"The sandbox task ended with {self.status}: {self.classification or self.status}."
        return None


class ProductAgentService:
    def __init__(
        self,
        *,
        managed_sandbox_root: str | Path,
        review_service: BridgeDiffService,
        provider_registry: ProductProviderRegistry,
        enabled: bool = False,
        limits: ProductRuntimeLimits | None = None,
        summary_store: ProviderRunSummaryStore | None = None,
    ) -> None:
        if enabled and review_service.store is None:
            raise ValueError("product_agent_requires_persistent_review_store")
        self.managed_sandbox_root = Path(managed_sandbox_root).resolve()
        self.review_service = review_service
        self.provider_registry = provider_registry
        self.enabled = bool(enabled)
        self.limits = limits or ProductRuntimeLimits()
        self.summary_store = summary_store
        self._runs: dict[str, ProductAgentRun] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._provider_cooldowns: dict[str, float] = {}
        self._lock = threading.RLock()

    async def start_run(
        self,
        *,
        project_id: str,
        active_workspace_root: str | Path,
        instruction: str,
        provider_id: str = "fake_planner",
        fallback_provider_id: str | None = None,
    ) -> ProductAgentRun:
        if not self.enabled:
            raise PermissionError("AGENT_RUNTIME_DISABLED")
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 16_384:
            raise ValueError("AGENT_RUNTIME_INSTRUCTION_INVALID")
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
                assistant_message_override=local_response,
            )
            self._append_event(run, {"event_type": "runtime.completed", "status": "completed", "classification": "LOCAL_RESPONSE"})
            with self._lock:
                self._runs[run.run_id] = run
            self._persist_summary(run)
            return run
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
                    assistant_message_override="OpenRouter is temporarily paused after a recent HTTP 429. Choose another provider or retry after five minutes.",
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
                managed_sandbox_root=self.managed_sandbox_root,
                active_workspace_root=workspace,
                review_service=self.review_service,
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

    def get_run(self, run_id: str) -> ProductAgentRun:
        with self._lock:
            try:
                return self._runs[run_id]
            except KeyError as exc:
                raise KeyError("AGENT_RUNTIME_RUN_NOT_FOUND") from exc

    def events(self, run_id: str, *, after_sequence: int = 0) -> tuple[dict[str, object], ...]:
        run = self.get_run(run_id)
        with self._lock:
            return tuple(dict(item) for item in run.events if int(item["sequence"]) > after_sequence)

    def cancel(self, run_id: str) -> ProductAgentRun:
        run = self.get_run(run_id)
        if run.status not in TERMINAL_AGENT_STATUSES:
            run.cancel_event.set()
            run.status = "cancelling"
            run.updated_at = _now()
            self._append_event(run, {"event_type": "runtime.cancelling", "status": "cancelling"})
        return run

    async def close(self) -> None:
        for run in tuple(self._runs.values()):
            if run.status not in TERMINAL_AGENT_STATUSES:
                run.cancel_event.set()
        tasks = tuple(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _append_event(self, run: ProductAgentRun, event: Mapping[str, object]) -> None:
        allowed = {"event_type", "status", "classification", "turn", "tool", "tool_count", "tool_execution_count", "changed_file_count"}
        safe = {key: value for key, value in event.items() if key in allowed and isinstance(value, (str, int, bool))}
        safe["run_id"] = run.run_id
        safe["created_at"] = _now()
        safe["timestamp"] = safe["created_at"]
        safe["stage"] = "generation"
        safe["provider_id"] = run.provider_id
        safe["provider_type"] = (ProviderType.TEMPLATE if run.provider_id == "verified_template" else ProviderType.API).value
        actual_provider = run.actual_provider_id or run.provider_id
        safe["generation_source"] = "Verified template" if actual_provider == "verified_template" else actual_provider
        safe["message"] = str(safe.get("status") or safe.get("event_type") or "generation update")
        safe["safe_summary"] = {"classification": safe["classification"]} if "classification" in safe else {}
        with self._lock:
            safe["sequence"] = len(run.events) + 1
            run.events.append(safe)

    def _complete(self, run: ProductAgentRun, result: ToolRuntimeResult) -> None:
        run.classification = result.provider_classification or result.classification.value
        run.review_id = result.review_id
        run.created_file_count = result.created_file_count
        run.modified_file_count = result.modified_file_count
        run.deleted_file_count = result.deleted_file_count
        run.tool_execution_count = result.tool_execution_count
        run.active_workspace_unchanged = result.active_workspace_unchanged
        diagnostics = result.provider_diagnostics
        run.outbound_request_count = int(diagnostics.get("outbound_request_count", 0) or 0)
        run.request_reached_provider = diagnostics.get("request_reached_provider") is True
        status = diagnostics.get("http_status")
        run.http_status = status if isinstance(status, int) and not isinstance(status, bool) else None
        request_id = diagnostics.get("provider_request_id")
        run.provider_request_id = request_id if isinstance(request_id, str) else None
        if result.review_id:
            try:
                review = self.review_service.get_review(result.review_id)
                run.created_files = [item.path for item in review.changed_files if item.change_type == "created"]
                run.modified_files = [item.path for item in review.changed_files if item.change_type == "modified"]
                run.deleted_files = [item.path for item in review.changed_files if item.change_type == "deleted"]
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


def detect_workspace_mode_value(value: str):
    from backend.provider_runtime import WorkspaceMode

    try:
        return WorkspaceMode(value)
    except ValueError:
        return WorkspaceMode.GENERATE_INTO_FOLDER
