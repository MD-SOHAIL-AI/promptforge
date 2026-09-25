"""Durable action-run owner for the chat-first ForgeX product agent."""

from __future__ import annotations
from backend.runtime.process_owner import owned_operation

import asyncio
import json
import logging
import shutil
import threading
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from backend.changes import ChangeSetError, ChangeSetService, IGNORED_DIRS
from backend.observability.diagnostics import emit_diagnostic_event
from backend.tools.board_detector import BoardDetector
from backend.hardware.action_binding import finalize_hardware_binding

from .activity import run_activity_fields
from .approvals import ApprovalService
from .capabilities import ToolPolicyEngine
from .capability_profiles import profile_for_autonomy
from .memory import ProjectMemoryService
from .orchestration_graph import AgentTaskNode
from .orchestration_store import AgentOrchestrationStore, LeaseGrant, utc_now
from .projection import SessionProjectionEngine, PROJECTION_REDUCER_VERSION
from .risk import RiskAssessment, RiskAssessmentService
from .tool_contracts import RuntimeClassification, ToolName
from .tool_policy import product_agent_policy, subagent_policy
from .tool_runtime import ForgeXToolRuntime, ProductRuntimeLimits, ToolRuntimeResult


TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "blocked", "timed_out"})
AUTONOMOUS_AUTONOMIES = frozenset({"build_only", "build_then_confirm_flash"})
logger = logging.getLogger(__name__)


class AgentSessionLeaseLost(RuntimeError):
    """Raised when an active run can no longer renew its fenced session lease."""


Reviewer = Callable[[Mapping[str, object]], Awaitable[Mapping[str, object]]]
RunFactory = Callable[..., Any]


class AgentOrchestrator:
    """Own action run state while ProductAgentService remains the chat facade."""

    def __init__(
        self,
        *,
        change_service: ChangeSetService,
        provider_registry: object,
        store: AgentOrchestrationStore,
        run_factory: RunFactory,
        limits: ProductRuntimeLimits | None = None,
        autonomous_executor: Callable[[Any], Awaitable[Mapping[str, object]]] | None = None,
        flash_executor: Callable[[Any], Awaitable[Mapping[str, object]]] | None = None,
        cancel_executor: Callable[[Any], Awaitable[None]] | None = None,
        reviewer: Reviewer | None = None,
        lease_ttl_seconds: int = 20,
        lease_heartbeat_interval_seconds: float | None = None,
    ) -> None:
        self.change_service = change_service
        self.provider_registry = provider_registry
        self.store = store
        self.run_factory = run_factory
        self.limits = limits or ProductRuntimeLimits()
        self.autonomous_executor = autonomous_executor
        self.flash_executor = flash_executor
        self.cancel_executor = cancel_executor
        self.reviewer = reviewer
        self._lease_ttl_seconds = max(5, min(int(lease_ttl_seconds), 300))
        default_heartbeat = min(5.0, self._lease_ttl_seconds / 3)
        requested_heartbeat = (
            default_heartbeat
            if lease_heartbeat_interval_seconds is None
            else float(lease_heartbeat_interval_seconds)
        )
        self._lease_heartbeat_interval_seconds = max(
            0.01,
            min(requested_heartbeat, self._lease_ttl_seconds / 2),
        )
        self.policy_engine = ToolPolicyEngine()
        self.risk_service = RiskAssessmentService()
        self.memory = ProjectMemoryService(store)
        self.approvals = ApprovalService(store)
        self.projections = SessionProjectionEngine()
        self._runs: dict[str, Any] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._nodes: dict[str, dict[str, AgentTaskNode]] = {}
        self._session_for_run: dict[str, str] = {}
        self._steering: dict[str, list[str]] = {}  # legacy in-memory compatibility
        self._leases: dict[str, LeaseGrant] = {}
        self._transient_events: dict[str, list[dict[str, object]]] = {}
        self._transient_sequence: dict[str, int] = {}
        self._recovery_context: dict[str, dict[str, object]] = {}
        self._lock = threading.RLock()
        self._run_listeners: list[object] = []
        self._restore_runs()

    def add_run_listener(self, listener: object) -> None:
        """Register a synchronous projection listener for persisted run updates."""
        self._run_listeners.append(listener)

    async def start_run(
        self,
        *,
        project_id: str,
        active_workspace_root: str | Path,
        instruction: str,
        provider_id: str,
        model_id: str | None = None,
        fallback_provider_id: str | None = None,
        autonomy: str = "staged_changes",
        active_workspace: Mapping[str, object] | None = None,
        board_port: str | None = None,
        board_type: str | None = None,
        environment: str | None = None,
        start_monitor_after_flash: bool = False,
        session_id: str | None = None,
        explicit_edit_authorized: bool = False,
        retry_of_run_id: str | None = None,
    ) -> Any:
        workspace = Path(active_workspace_root).resolve(strict=True)
        run_id = f"agent-run-{uuid.uuid4().hex}"
        session_key = session_id or f"run:{run_id}"
        run = self.run_factory(
            run_id,
            project_id,
            provider_id,
            model_id=model_id,
            autonomy=autonomy,
            actual_provider_id=provider_id,
            flash_port=board_port,
            flash_board_type=board_type,
            flash_environment=environment,
            start_monitor_after_flash=bool(start_monitor_after_flash),
            retry_of_run_id=retry_of_run_id,
        )
        run.active_workflow_task_id = f"task-{run_id}"
        nodes = self._initial_graph(autonomy)
        lease = self.store.acquire_lease(
            session_key,
            run_id,
            ttl_seconds=self._lease_ttl_seconds,
        )
        with self._lock:
            self._runs[run_id] = run
            self._nodes[run_id] = {node.node_id: node for node in nodes}
            self._session_for_run[run_id] = session_key
            self._steering[run_id] = []
            self._leases[run_id] = lease
            self._recovery_context[run_id] = {
                "workspace_root": str(workspace),
                "instruction": instruction,
                "explicit_edit_authorized": bool(explicit_edit_authorized),
                "active_workspace": _json_safe_mapping(active_workspace or {}),
            }
        self._persist_graph(run)
        self._event(run, "runtime.queued", stage="planning", message="Action run queued")

        async def execute_workflow() -> None:
            if autonomy in AUTONOMOUS_AUTONOMIES:
                await self._execute_autonomous(
                    run,
                    workspace=workspace,
                    instruction=instruction,
                    active_workspace=active_workspace or {},
                    fallback_provider_id=None,
                    explicit_edit_authorized=explicit_edit_authorized,
                )
            else:
                await self._execute_staged(
                    run,
                    workspace=workspace,
                    instruction=instruction,
                    fallback_provider_id=None,
                    explicit_edit_authorized=explicit_edit_authorized,
                    active_workspace=active_workspace or {},
                )

        async def execute() -> None:
            try:
                await self._run_with_lease_heartbeat(run.run_id, execute_workflow())
            except AgentSessionLeaseLost as exc:
                # A fenced/expired owner cannot safely append another journal
                # event. Persist the run projection so polling clients still see
                # a terminal failure, while the journal remains single-writer.
                logger.error("agent session lease lost run_id=%s: %s", run.run_id, exc)
                run.status = "failed"
                run.classification = "AGENT_SESSION_LEASE_LOST"
                run.assistant_message_override = (
                    "The action stopped because this backend lost ownership of its session. "
                    "Retry the action; no further tools were allowed to run."
                )
                self._fail_active_node(run, str(exc))
                self._persist_run(run)
            finally:
                self.policy_engine.revoke_run(run.run_id)
                grant = self._leases.pop(run.run_id, None)
                if grant is not None:
                    try:
                        self.store.release_lease(grant)
                    except ValueError:
                        pass

        async def execute_owned() -> None:
            # Create the inner execution coroutine only after the scheduled task
            # actually starts. If the outer task is cancelled before its first
            # timeslice, no orphan coroutine is left un-awaited.
            await owned_operation(run_id, execute())

        self._tasks[run_id] = asyncio.create_task(execute_owned(), name=f"orchestrator:{run_id}")
        return run

    async def _run_with_lease_heartbeat(self, run_id: str, operation: Awaitable[None]) -> None:
        """Keep a session lease live and stop work immediately if fencing is lost."""

        stop = asyncio.Event()
        operation_task = asyncio.create_task(operation, name=f"orchestrator:{run_id}:operation")
        heartbeat_task = asyncio.create_task(
            self._lease_heartbeat(run_id, stop),
            name=f"orchestrator:{run_id}:lease-heartbeat",
        )
        try:
            done, _ = await asyncio.wait(
                {operation_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done:
                heartbeat_error = heartbeat_task.exception()
                if heartbeat_error is not None:
                    operation_task.cancel()
                    await asyncio.gather(operation_task, return_exceptions=True)
                    if isinstance(heartbeat_error, AgentSessionLeaseLost):
                        raise heartbeat_error
                    raise AgentSessionLeaseLost(str(heartbeat_error)) from heartbeat_error
                if operation_task not in done:
                    operation_task.cancel()
                    await asyncio.gather(operation_task, return_exceptions=True)
                    raise AgentSessionLeaseLost("AGENT_SESSION_LEASE_HEARTBEAT_STOPPED")
            await operation_task
        finally:
            stop.set()
            if not operation_task.done():
                operation_task.cancel()
            if not heartbeat_task.done():
                await asyncio.gather(heartbeat_task, return_exceptions=True)
            await asyncio.gather(operation_task, return_exceptions=True)

    async def _lease_heartbeat(self, run_id: str, stop: asyncio.Event) -> None:
        while not stop.is_set():
            with self._lock:
                grant = self._leases.get(run_id)
            if grant is None:
                raise AgentSessionLeaseLost("AGENT_SESSION_LEASE_MISSING")
            try:
                renewed = await asyncio.to_thread(
                    self.store.renew_lease,
                    grant,
                    ttl_seconds=self._lease_ttl_seconds,
                )
            except Exception as exc:
                raise AgentSessionLeaseLost(str(exc) or type(exc).__name__) from exc
            with self._lock:
                current = self._leases.get(run_id)
                if (
                    current is None
                    or current.owner_id != renewed.owner_id
                    or current.lease_epoch != renewed.lease_epoch
                ):
                    raise AgentSessionLeaseLost("AGENT_SESSION_LEASE_FENCED")
                self._leases[run_id] = renewed
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self._lease_heartbeat_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def retry(self, run_id: str) -> Any:
        """Create one explicit replacement run for an interrupted action."""
        previous_task = self._tasks.get(run_id)
        if previous_task is not None and not previous_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(previous_task), timeout=5)
            except asyncio.TimeoutError as exc:
                raise ValueError("AGENT_SESSION_LEASE_HELD") from exc
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            replacement_id = getattr(run, "recovered_by_run_id", None)
            if replacement_id and replacement_id in self._runs:
                return self._runs[replacement_id]
            recoverable_classification = (
                run.classification == "AGENT_RUNTIME_INTERRUPTED"
                or run.status == "paused_model_unavailable"
                or (run.status in {"failed", "blocked", "timed_out"} and bool(run.change_set_id)
                    and self.change_service.get(run.change_set_id).status == "draft")
            )
            if not bool(getattr(run, "recoverable", False)) or not recoverable_classification:
                raise ValueError("AGENT_RUNTIME_RUN_NOT_RECOVERABLE")
            context = dict(self._recovery_context.get(run_id, {}))
            if not context:
                raise ValueError("AGENT_RUNTIME_RECOVERY_CONTEXT_MISSING")
            run.recoverable = False
            run.recovery_actions = []
            self._persist_run(run)

        try:
            replacement = await self.start_run(
                project_id=run.project_id,
                active_workspace_root=str(context["workspace_root"]),
                instruction=str(context["instruction"]),
                provider_id=run.provider_id,
                model_id=run.model_id,
                fallback_provider_id=(
                    str(context["fallback_provider_id"])
                    if context.get("fallback_provider_id")
                    else None
                ),
                autonomy=run.autonomy,
                active_workspace=(
                    context["active_workspace"]
                    if isinstance(context.get("active_workspace"), Mapping)
                    else {}
                ),
                board_port=run.flash_port,
                board_type=run.flash_board_type,
                environment=run.flash_environment,
                start_monitor_after_flash=run.start_monitor_after_flash,
                session_id=self._session_for_run[run_id],
                explicit_edit_authorized=bool(context.get("explicit_edit_authorized")),
                retry_of_run_id=run_id,
            )
        except Exception:
            with self._lock:
                run.recoverable = True
                run.recovery_actions = ["retry"]
                self._persist_run(run)
            raise

        with self._lock:
            run.recovered_by_run_id = replacement.run_id
            self._persist_run(run)
        self._event(run, "runtime.retried", status=run.status, replacement_run_id=replacement.run_id)
        return replacement

    def get_run(self, run_id: str) -> Any:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(run_id)
            return self._runs[run_id]

    def owns(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._runs

    def graph(self, run_id: str) -> dict[str, object]:
        run = self.get_run(run_id)
        return {
            "run_id": run_id,
            "status": run.status,
            "nodes": [node.to_dict() for node in self._nodes[run_id].values()],
        }

    def context_artifact(self, run_id: str, artifact_id: str) -> dict[str, object]:
        self.get_run(run_id)
        return self.store.context_artifact(run_id, artifact_id)

    def events(self, run_id: str, *, after_sequence: int = 0) -> tuple[dict[str, object], ...]:
        self.get_run(run_id)
        session_id = self._session_for_run[run_id]
        return self.store.events(session_id, after_sequence=after_sequence, run_id=run_id)

    def session_events(self, session_id: str, *, after_sequence: int = 0) -> tuple[dict[str, object], ...]:
        return self.store.events(session_id, after_sequence=after_sequence)

    def session_projection(self, session_id: str) -> dict[str, object]:
        events = self.store.events(session_id, after_sequence=0)
        state = self.projections.rebuild(session_id, events)
        with self._lock:
            run_ids = [
                run_id for run_id, owner_session in self._session_for_run.items()
                if owner_session == session_id
            ]
            for run_id in run_ids:
                for frame in self._transient_events.get(run_id, ()):
                    self.projections.apply_transient(state, frame)
        payload = state.to_dict()
        self.store.save_projection_checkpoint(
            session_id,
            "default",
            session_seq=state.session_seq,
            reducer_version=PROJECTION_REDUCER_VERSION,
            payload=payload,
        )
        return payload

    def transient_events(self, run_id: str, *, after_transient_sequence: int = 0) -> tuple[dict[str, object], ...]:
        """Return live-only frames. These are never historical truth or reconnect cursors."""
        self.get_run(run_id)
        with self._lock:
            return tuple(
                dict(item)
                for item in self._transient_events.get(run_id, ())
                if int(item.get("transient_sequence") or 0) > after_transient_sequence
            )

    def steer(self, run_id: str, message: str) -> Any:
        """Persist user guidance for the next safe Step boundary."""
        run = self.get_run(run_id)
        text = str(message).strip()
        if not text or len(text) > 8_000:
            raise ValueError("AGENT_RUNTIME_STEERING_INVALID")
        if run.status in TERMINAL_STATUSES or run.status == "awaiting_flash_confirmation":
            raise ValueError("AGENT_RUNTIME_STEERING_NOT_ACTIVE")
        session_id = self._session_for_run[run_id]
        self.store.enqueue_input(session_id, run_id, mode="STEER", content=text)
        self._persist_run(run)
        return run

    def queued_guidance(self, run_id: str) -> tuple[dict[str, object], ...]:
        self.get_run(run_id)
        return self.store.queue_items(self._session_for_run[run_id], run_id=run_id)

    def edit_guidance(self, run_id: str, item_id: str, message: str) -> dict[str, object]:
        self.get_run(run_id)
        items = {str(item.get("item_id")): item for item in self.queued_guidance(run_id)}
        if item_id not in items:
            raise KeyError(item_id)
        return self.store.edit_queue_item(item_id, message)

    def remove_guidance(self, run_id: str, item_id: str) -> dict[str, object]:
        self.get_run(run_id)
        items = {str(item.get("item_id")): item for item in self.queued_guidance(run_id)}
        if item_id not in items:
            raise KeyError(item_id)
        return self.store.remove_queue_item(item_id)

    def _consume_steering(self, run_id: str) -> tuple[str, ...]:
        session_id = self._session_for_run[run_id]
        claimed = self.store.claim_queue_items(session_id, run_id=run_id, modes=("STEER",), limit=16)
        return tuple(str(item.get("content") or "") for item in claimed if str(item.get("content") or ""))

    def append_chat_event(self, session_id: str, payload: Mapping[str, object]) -> dict[str, object]:
        return self.store.append_event(session_id, payload)

    async def cancel(self, run_id: str) -> Any:
        run = self.get_run(run_id)
        if run.status in TERMINAL_STATUSES:
            return run
        run.status = "cancelling"
        run.cancel_event.set()
        self._event(run, "cancellation.requested", message="Cancellation requested")
        # Fence future host capabilities before touching the active task.
        self.policy_engine.revoke_run(run_id)
        if self.cancel_executor is not None:
            try:
                await self.cancel_executor(run)
            except Exception:
                logger.exception("ForgeX cancellation executor failed for %s", run_id)
        task = self._tasks.get(run_id)
        if task and not task.done() and task is not asyncio.current_task():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=1.5)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
            except (asyncio.CancelledError, Exception):
                pass
        run.status = "cancelled"
        run.classification = "AGENT_RUNTIME_CANCELLED"
        self._cancel_active_nodes(run_id)
        self._event(run, "runtime.cancelled", message="Action run cancelled after safe drain")
        self._persist_run(run)
        return run

    def apply_pending_changes(self, run_id: str) -> Any:
        """Apply a reviewed/staged ChangeSet after explicit local user approval."""

        run = self.get_run(run_id)
        if run.status != "completed" or run.classification not in {
            "CHANGESET_REVIEW_REQUIRED",
            "CHANGESET_APPROVAL_REQUIRED",
        }:
            raise ValueError("AGENT_RUNTIME_CHANGESET_APPROVAL_NOT_READY")
        if not run.change_set_id:
            raise ValueError("AGENT_RUNTIME_CHANGESET_NOT_READY")
        change_set = self.change_service.get(run.change_set_id)
        if change_set.status != "pending":
            raise ValueError("AGENT_RUNTIME_CHANGESET_NOT_PENDING")
        workspace = Path(change_set.workspace_root).resolve(strict=True)
        grant = self.policy_engine.issue_grant(
            run_id=run.run_id,
            node_id="apply",
            workspace_root=workspace,
            allowed_services=("workspace_stage",),
            authorization_source="explicit_review",
            session_id=self._session_for_run[run.run_id],
            max_calls=1,
            allow_workspace_write=True,
        )
        try:
            self.policy_engine.validate_service(grant, "workspace_stage", workspace)
            self._start_node(run, "apply")
            applied = self.change_service.apply(change_set.change_set_id)
            fresh = self.change_service.inspect_workspace(workspace)
            changed_paths = [item.path for item in applied.changed_files]
            self.memory.invalidate_for_change(
                run.project_id,
                changed_paths=changed_paths,
                new_workspace_revision=fresh.revision,
                board_id=run.flash_board_type,
            )
            self.memory.add_verified(
                project_id=run.project_id,
                kind="applied_change",
                value=applied.summary,
                source_type="changeset",
                source_id=applied.change_set_id,
                source_payload=json.dumps(applied.to_dict(), ensure_ascii=True, sort_keys=True),
                workspace_revision=fresh.revision,
                affected_paths=changed_paths,
                board_id=run.flash_board_type,
            )
            run.active_workspace_unchanged = False
            run.classification = "CHANGESET_APPLIED"
            run.assistant_message_override = (
                f"Applied {len(changed_paths)} validated file change"
                f"{'s' if len(changed_paths) != 1 else ''} to the workspace."
            )
            _set_run_stage(run, "generation", "completed", "Generated files applied to the workspace")
            _set_run_stage(run, "changes", "completed", "Validated ChangeSet applied")
            self._complete_node(
                run,
                "apply",
                "Explicit local approval applied the validated ChangeSet",
                artifact_id=applied.change_set_id,
            )
            self._event(
                run,
                "changes.applied",
                node_id="apply",
                status="completed",
                message="Validated files applied to the active workspace",
                change_set_id=applied.change_set_id,
            )
            self._persist_run(run)
            return run
        except Exception as exc:
            run.classification = getattr(exc, "code", None) or type(exc).__name__
            run.assistant_message_override = str(exc) or "The staged changes could not be applied safely."
            self._fail_node(run, "apply", run.assistant_message_override)
            self._event(
                run,
                "changes.apply_failed",
                node_id="apply",
                status="failed",
                message=run.assistant_message_override,
                classification=run.classification,
            )
            self._persist_run(run)
            raise
        finally:
            self.policy_engine.revoke(grant.grant_id)

    def prepare_flash(
        self,
        run_id: str,
        *,
        port: str | None = None,
        board_type: str | None = None,
        environment: str | None = None,
        start_monitor_after_flash: bool | None = None,
    ) -> Any:
        """Turn a completed verified build into a one-time flash approval.

        This fixes the V2 dead-end where a build_only run forgot how to become
        flashable on a later user turn. It never flashes by itself.
        """
        run = self.get_run(run_id)
        if run.status == "awaiting_flash_confirmation":
            return run
        if run.status != "completed":
            raise ValueError("AGENT_RUNTIME_FLASH_PREPARATION_NOT_READY")
        if run.classification in {"CHANGESET_REVIEW_REQUIRED", "CHANGESET_APPROVAL_REQUIRED"}:
            raise ValueError("AGENT_RUNTIME_FLASH_CHANGES_NOT_APPLIED")
        if _approved_artifact_hash(run) is None:
            raise ValueError("AGENT_RUNTIME_VERIFIED_BUILD_NOT_FOUND")
        build = run.build_result if isinstance(run.build_result, Mapping) else {}
        expected_revision = build.get("workspace_revision")
        if isinstance(expected_revision, str) and expected_revision:
            current_revision = self.change_service.inspect_workspace(self._capability_root(run)).revision
            if current_revision != expected_revision:
                raise ValueError("AGENT_RUNTIME_BUILD_WORKSPACE_CHANGED")
        run.flash_port = port or run.flash_port
        run.flash_board_type = board_type or run.flash_board_type
        run.flash_environment = environment or run.flash_environment
        if start_monitor_after_flash is not None:
            run.start_monitor_after_flash = bool(start_monitor_after_flash)
        flash_node = self._nodes.get(run.run_id, {}).get("flash")
        if flash_node is not None and flash_node.status in {"skipped", "failed", "cancelled"}:
            flash_node.status = "pending"
            flash_node.message = None
            self.store.save_node(run.run_id, flash_node.node_id, flash_node.status, flash_node.to_dict())
        capability_root = self._capability_root(run)
        workspace_revision = self.change_service.inspect_workspace(capability_root).revision
        approval = self.approvals.issue(
            run_id=run.run_id,
            action_type="flash",
            binding=_flash_binding(run, workspace_revision=workspace_revision),
            summary="Flash the latest verified firmware artifact to the selected device once.",
        )
        run.approval_id = approval.approval_id
        run.approval_expires_at = approval.expires_at
        run.status = "awaiting_flash_confirmation"
        run.classification = "BUILD_READY_FOR_FLASH"
        _set_run_stage(run, "flash_confirmation", "waiting", "Confirm flash to upload the verified artifact")
        self._event(run, "runtime.awaiting_flash_confirmation", status=run.status, stage="flash_confirmation", message="Verified build is ready; waiting for one-time flash confirmation")
        self._persist_run(run)
        return run

    async def confirm_flash(
        self,
        run_id: str,
        *,
        port: str | None = None,
        board_type: str | None = None,
        environment: str | None = None,
        start_monitor_after_flash: bool | None = None,
    ) -> Any:
        run = self.get_run(run_id)
        if run.status != "awaiting_flash_confirmation":
            raise ValueError("AGENT_RUNTIME_FLASH_CONFIRMATION_NOT_READY")
        if self.flash_executor is None:
            raise ValueError("AGENT_RUNTIME_FLASH_CONFIRMATION_UNAVAILABLE")
        if not run.approval_id:
            raise ValueError("AGENT_RUNTIME_FLASH_APPROVAL_REQUIRED")
        proposed_port = port or run.flash_port
        proposed_board_type = board_type or run.flash_board_type
        proposed_environment = environment or run.flash_environment
        proposed_monitor = (
            bool(start_monitor_after_flash)
            if start_monitor_after_flash is not None
            else bool(run.start_monitor_after_flash)
        )
        capability_root = self._capability_root(run)
        flash_services = ["build", "hardware"]
        if proposed_monitor:
            flash_services.append("serial_monitor")
        flash_grant = self.policy_engine.issue_grant(
            run_id=run.run_id,
            node_id="flash",
            workspace_root=capability_root,
            allowed_services=flash_services,
            authorization_source="one_time_hardware_approval",
            max_calls=len(flash_services),
            allow_build=True,
            allow_hardware=True,
        )
        for service in flash_services:
            self.policy_engine.validate_service(flash_grant, service, capability_root)
        current_revision = self.change_service.inspect_workspace(capability_root).revision
        self.approvals.consume(
            run.approval_id,
            action_type="flash",
            binding=_flash_binding(
                run,
                port=proposed_port,
                board_type=proposed_board_type,
                environment=proposed_environment,
                start_monitor_after_flash=proposed_monitor,
                workspace_revision=current_revision,
            ),
        )
        run.flash_port = proposed_port
        run.flash_board_type = proposed_board_type
        run.flash_environment = proposed_environment
        run.start_monitor_after_flash = proposed_monitor
        run.status = "running"
        _set_run_stage(run, "flash_confirmation", "completed", "One-time flash confirmation accepted")
        _set_run_stage(run, "flash", "running", "Flashing verified firmware")
        self._start_node(run, "flash")
        self._event(run, "flash.confirmed", node_id="flash", status="running", message="One-time flash confirmation accepted")
        self._event(run, "flash.started", node_id="flash", stage="flash", status="running", message="Flashing verified firmware")

        async def progress(event: str, payload: Mapping[str, object]) -> None:
            self._runtime_event(run, {"event_type": event.casefold(), **dict(payload)})

        async def execute() -> None:
            try:
                result = await self.flash_executor(_FlashRequest(
                    run_id=run.run_id,
                    project_id=run.project_id,
                    port=run.flash_port,
                    board_type=run.flash_board_type,
                    environment=run.flash_environment,
                    start_monitor_after_flash=run.start_monitor_after_flash,
                    expected_artifact_hash=_approved_artifact_hash(run),
                    progress_callback=progress,
                ))
                run.flash_result = _safe_mapping(result.get("flash_result"))
                run.monitor_result = _safe_mapping(result.get("monitor_result"))
                run.build_result = _safe_mapping(result.get("build_result")) or run.build_result
                if result.get("success") is True:
                    run.status = "completed"
                    run.classification = str(result.get("classification") or "FLASH_COMPLETED")
                    _set_run_stage(run, "flash", "completed", str(result.get("message") or "Flash completed"))
                    self._complete_node(run, "flash", str(result.get("message") or "Flash completed"))
                    self._event(run, "runtime.completed", status="completed", message="Hardware workflow completed")
                else:
                    run.status = "failed"
                    run.classification = str(result.get("classification") or "FLASH_FAILED")
                    _set_run_stage(run, "flash", "failed", str(result.get("message") or "Flash failed"))
                    self._fail_node(run, "flash", str(result.get("message") or "Flash failed"))
                    self._event(run, "runtime.failed", status="failed", message="Flash failed")
                self._persist_run(run)
            except asyncio.CancelledError:
                run.status = "cancelled"
                run.classification = "AGENT_RUNTIME_CANCELLED"
                _set_run_stage(run, "flash", "cancelled", "Flash cancelled")
                self._cancel_active_nodes(run.run_id)
                self._event(run, "runtime.cancelled", message="Flash cancelled")
                raise
            except Exception as exc:
                run.status = "failed"
                run.classification = type(exc).__name__
                _set_run_stage(run, "flash", "failed", str(exc) or type(exc).__name__)
                self._fail_node(run, "flash", str(exc) or type(exc).__name__)
                self._event(run, "runtime.failed", status="failed", message="Flash failed")
                self._persist_run(run)
            finally:
                self.policy_engine.revoke_run(run.run_id)

        self._tasks[run_id] = asyncio.create_task(owned_operation(run_id, execute()), name=f"orchestrator:{run_id}:flash")
        return run

    async def close(self) -> None:
        tasks = tuple(task for task in self._tasks.values() if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute_staged(
        self,
        run: Any,
        *,
        workspace: Path,
        instruction: str,
        fallback_provider_id: str | None,
        explicit_edit_authorized: bool,
        active_workspace: Mapping[str, object],
    ) -> None:
        try:
            self._start_node(run, "planning")
            planner = self.provider_registry.resolve(
                run.provider_id, model_id=run.model_id,
                session_id=self._session_for_run[run.run_id],
            )
            self._complete_node(run, "planning", "Bounded tool plan selected")
            self._start_node(run, "implementation")
            run.status = "running"
            async def run_planner(selected_planner: object) -> ToolRuntimeResult:
                runtime = ForgeXToolRuntime(active_workspace_root=workspace, change_service=self.change_service)
                revision = self.change_service.inspect_workspace(workspace).revision
                memories = self.memory.active(run.project_id, current_workspace_revision=revision, board_id=run.flash_board_type)

                def memory_search(args: Mapping[str, object]) -> object:
                    query = str(args.get("query") or "").casefold().split()
                    limit = int(args.get("limit", 5))
                    matches = []
                    for item in memories:
                        if item.get("status") != "active" or item.get("verified") is not True:
                            continue
                        text = (str(item.get("kind") or "") + " " + str(item.get("value") or "")).casefold()
                        score = sum(term in text for term in query) if query else 1
                        if score:
                            matches.append((score, {"kind": item.get("kind"), "value": str(item.get("value") or "")[:1200], "source_id": item.get("source_id")}))
                    matches.sort(key=lambda pair: pair[0], reverse=True)
                    return [value for _, value in matches[:max(1, min(limit, 20))]]

                session_id = self._session_for_run[run.run_id]

                def public_session_event(event: Mapping[str, object]) -> dict[str, object]:
                    keys = (
                        "sequence", "session_seq", "event_id", "event_type", "status",
                        "message", "turn", "step", "attempt_id", "tool", "tool_call_id",
                        "call_id", "path", "duration_ms", "classification", "build_id",
                        "artifact_id", "stage",
                    )
                    return {key: event[key] for key in keys if key in event and event[key] is not None}

                def session_event_read(args: Mapping[str, object]) -> object:
                    after = max(0, int(args.get("after_sequence", 0) or 0))
                    limit = max(1, min(int(args.get("limit", 50) or 50), 100))
                    return [
                        public_session_event(item)
                        for item in self.store.events(
                            session_id, after_sequence=after, run_id=run.run_id, limit=limit
                        )
                    ]

                def session_event_search(args: Mapping[str, object]) -> object:
                    query = str(args.get("query") or "").casefold().strip()
                    limit = max(1, min(int(args.get("limit", 20) or 20), 50))
                    matches: list[dict[str, object]] = []
                    for item in self.store.events(session_id, run_id=run.run_id, limit=1000):
                        public = public_session_event(item)
                        haystack = " ".join(str(value) for value in public.values()).casefold()
                        if query and query in haystack:
                            matches.append(public)
                    return matches[-limit:]

                def session_trace(args: Mapping[str, object]) -> object:
                    limit = max(1, min(int(args.get("limit", 50) or 50), 100))
                    events = self.store.events(session_id, run_id=run.run_id, limit=1000)
                    trace = []
                    for item in events:
                        event_type = str(item.get("event_type") or "")
                        if event_type.startswith((
                            "turn.", "step.", "model.attempt.", "tool.invocation.",
                            "tool.execution.", "tool.result.", "build.", "changeset.",
                            "approval.", "runtime.",
                        )):
                            trace.append(public_session_event(item))
                    return trace[-limit:]

                requires_write = active_workspace.get("agent_requires_write") is True or explicit_edit_authorized
                requires_build = active_workspace.get("agent_requires_build") is True
                profile = profile_for_autonomy(
                    run.autonomy,
                    requires_write=requires_write,
                    requires_build=requires_build,
                )
                return await asyncio.to_thread(
                    runtime.run_product,
                    task=instruction,
                    planner=selected_planner,
                    policy=profile.policy(max_bytes_per_file=self.limits.max_bytes_per_file),
                    limits=self.limits,
                    cancel_event=run.cancel_event,
                    event_callback=lambda event: self._runtime_event(run, event),
                    policy_engine=self.policy_engine,
                    capability_authorization_source=("explicit_edit_request" if explicit_edit_authorized else "explicit_review"),
                    capability_run_id=run.run_id,
                    capability_node_id="implementation",
                    capability_session_id=self._session_for_run[run.run_id],
                    service_handlers={
                        ToolName.MEMORY_SEARCH: memory_search,
                        ToolName.SESSION_EVENT_READ: session_event_read,
                        ToolName.SESSION_EVENT_SEARCH: session_event_search,
                        ToolName.SESSION_TRACE: session_trace,
                    },
                    memory_entries=memories,
                    steering_callback=lambda: self._consume_steering(run.run_id),
                    allow_no_changes_success=profile.allow_no_changes,
                    retry_change_set_id=(
                        self.get_run(run.retry_of_run_id).change_set_id
                        if run.retry_of_run_id else None
                    ),
                    retry_context=(
                        {"classification": str(self.get_run(run.retry_of_run_id).classification or "")[:100],
                         "message": str(self.get_run(run.retry_of_run_id).assistant_message_override or "")[:1000]}
                        if run.retry_of_run_id else None
                    ),
                )

            result = await run_planner(planner)
            self._apply_runtime_result(run, result)
            if run.autonomy == "read_only" and result.classification is RuntimeClassification.PASS:
                self._complete_node(run, "implementation", "Read-only Forge analysis completed")
                self._skip_node(run, "validation", "Read-only mode does not build or mutate the project")
                self._skip_node(run, "review", "Read-only mode does not create a ChangeSet")
                self._skip_node(run, "apply", "Read-only mode does not mutate the workspace")
                self._skip_node(run, "flash", "Read-only mode cannot perform hardware actions")
                run.status = "completed"
                run.classification = "READ_ONLY_COMPLETED"
                run.active_workspace_unchanged = True
                if result.assistant_message:
                    run.assistant_message_override = result.assistant_message[:4000]
                self._event(run, "runtime.completed", status="completed", message="Read-only Forge response ready")
                self._persist_run(run)
                return
            if run.autonomy == "plan_only" and result.classification is RuntimeClassification.PASS:
                self._complete_node(run, "implementation", "Read-only implementation plan prepared")
                self._skip_node(run, "validation", "Plan mode does not build or mutate the project")
                self._skip_node(run, "review", "Plan mode is read-only")
                self._skip_node(run, "apply", "Plan mode is read-only")
                self._skip_node(run, "flash", "Plan mode cannot perform hardware actions")
                run.status = "completed"
                run.classification = "PLAN_READY"
                run.active_workspace_unchanged = True
                if result.assistant_message:
                    run.assistant_message_override = result.assistant_message[:4000]
                self._event(run, "plan.ready", status="completed", message="Read-only Forge plan is ready", plan=run.agent_plan)
                self._persist_run(run)
                return
            if result.assistant_message and not run.assistant_message_override:
                run.assistant_message_override = result.assistant_message[:4000]
            if run.status == "paused_model_unavailable":
                node = self._nodes[run.run_id]["implementation"]
                node.status = "waiting"
                node.message = "Waiting for the selected model endpoint; no provider was substituted"
                self.store.save_node(run.run_id, node.node_id, node.status, node.to_dict())
                self._event(
                    run,
                    "runtime.paused_model_unavailable",
                    status=run.status,
                    message="Selected model temporarily unavailable; retry can resume as a replacement run",
                    classification=run.classification,
                )
                self._persist_run(run)
                return
            if result.classification is not RuntimeClassification.PASS or not result.change_set_id:
                self._fail_node(run, "implementation", str(run.classification or result.classification.value))
                return
            self._complete_node(run, "implementation", "Staged ChangeSet created", artifact_id=result.change_set_id)
            self._start_node(run, "validation")
            change_set = self.change_service.get(result.change_set_id)
            assessment = self.risk_service.assess(
                change_set,
                explicit_edit_authorized=explicit_edit_authorized,
            )
            change_set = self.change_service.annotate_authorization(
                change_set.change_set_id,
                authorization_id=(run.run_id if explicit_edit_authorized else None),
                authorization_source=("explicit_edit_request" if explicit_edit_authorized else "explicit_review"),
                risk_level=assessment.level,
                review_required=assessment.reviewer_required,
            )
            self._annotate_risk(run, assessment)
            self._complete_node(run, "validation", f"Risk classified {assessment.level}")
            reviewer_approved = False
            if assessment.reviewer_required:
                verdict = await self._review(run, change_set.to_dict(), assessment)
                if verdict != "approve":
                    run.status = "blocked" if verdict == "block" else "completed"
                    run.classification = "REVIEW_BLOCKED" if verdict == "block" else "REVIEW_REQUIRED"
                    self._event(run, "review.completed", node_id="review", status=run.status, message=f"Reviewer verdict: {verdict}")
                    self._persist_run(run)
                    return
                reviewer_approved = True
            else:
                self._skip_node(run, "review", "Low-risk validated edit")
            scoped_apply_allowed = assessment.scoped_auto_apply_allowed or (
                explicit_edit_authorized and assessment.level == "medium" and reviewer_approved
            )
            if scoped_apply_allowed:
                self._start_node(run, "apply")
                applied = self.change_service.apply(result.change_set_id)
                run.active_workspace_unchanged = False
                self._complete_node(run, "apply", "Scoped edit authorization applied the ChangeSet", artifact_id=applied.change_set_id)
                fresh = self.change_service.inspect_workspace(workspace)
                self.memory.invalidate_for_change(
                    run.project_id,
                    changed_paths=[item.path for item in applied.changed_files],
                    new_workspace_revision=fresh.revision,
                )
                self.memory.add_verified(
                    project_id=run.project_id,
                    kind="applied_change",
                    value=applied.summary,
                    source_type="changeset",
                    source_id=applied.change_set_id,
                    source_payload=json.dumps(applied.to_dict(), ensure_ascii=True, sort_keys=True),
                    workspace_revision=fresh.revision,
                    affected_paths=[item.path for item in applied.changed_files],
                )
                run.assistant_message_override = "Applied the requested low-risk edit after validation. The change remains undoable."
            else:
                self._skip_node(run, "apply", "ChangeSet is staged for explicit approval")
            self._skip_node(run, "flash", "No hardware action was requested")
            run.status = "completed"
            run.classification = "TOOL_RUNTIME_PASS"
            self._event(run, "runtime.completed", status="completed", message="Action run completed")
            self._persist_run(run)
        except asyncio.CancelledError:
            if run.status != "cancelled":
                run.status = "cancelled"
                run.classification = "AGENT_RUNTIME_CANCELLED"
                self._cancel_active_nodes(run.run_id)
                self._event(run, "runtime.cancelled", message="Action run cancelled")
            raise
        except Exception as exc:
            run.status = "failed"
            run.classification = type(exc).__name__
            self._fail_active_node(run, str(exc) or type(exc).__name__)
            self._event(run, "runtime.failed", status="failed", message="Action run failed", classification=run.classification)
            self._persist_run(run)

    async def _execute_autonomous(
        self,
        run: Any,
        *,
        workspace: Path,
        instruction: str,
        active_workspace: Mapping[str, object],
        fallback_provider_id: str | None,
        explicit_edit_authorized: bool,
    ) -> None:
        if self.autonomous_executor is None:
            run.status = "failed"
            run.classification = "AGENT_RUNTIME_AUTONOMOUS_WORKFLOW_UNAVAILABLE"
            self._persist_run(run)
            return
        stage: Path | None = None
        baseline = None
        try:
            baseline = self.change_service.inspect_workspace(workspace)
            stage = self.change_service.create_stage(f"workflow-{run.run_id}")
            for relative in sorted(baseline.files):
                source = workspace.joinpath(*relative.split("/"))
                target = stage.joinpath(*relative.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            if run.retry_of_run_id:
                previous = self.get_run(run.retry_of_run_id)
                if previous.change_set_id:
                    previous_change_set = self.change_service.get(previous.change_set_id)
                    if previous_change_set.status == "draft":
                        self.change_service.materialize_review_draft(
                            previous.change_set_id, workspace_root=workspace,
                            target_stage=stage,
                        )
                previous_build = previous.build_result if isinstance(previous.build_result, Mapping) else {}
                sandbox_retry_context = {
                    "classification": str(previous.classification or "")[:100],
                    "message": str(previous_build.get("message") or previous.assistant_message_override or "")[:1000],
                    "diagnostics": str(previous_build.get("diagnostics") or "")[-4000:],
                    "dependency_candidates": list(previous_build.get("dependency_candidates") or [])[:10],
                    "changed_paths": (previous.created_files + previous.modified_files + previous.deleted_files)[:100],
                }
            sandbox_workspace = dict(active_workspace)
            if run.retry_of_run_id:
                sandbox_workspace["agent_retry_context"] = sandbox_retry_context
            sandbox_workspace["agent_skill_root"] = str(workspace)
            sandbox_workspace["rootPath"] = str(stage)
            sandbox_workspace["root_path"] = str(stage)
            sandbox_workspace["platformioIniPath"] = str(stage / "platformio.ini")
            sandbox_workspace["platformio_ini_path"] = str(stage / "platformio.ini")
            sandbox_workspace["has_platformio_ini"] = (stage / "platformio.ini").is_file()
            sandbox_workspace["environment"] = run.flash_environment
            sandbox_workspace["agent_verified_memories"] = list(
                self.memory.active(
                    run.project_id,
                    current_workspace_revision=baseline.revision,
                    board_id=run.flash_board_type,
                )
            )
            self._start_node(run, "planning")
            self._complete_node(run, "planning", "Embedded workflow selected")
            self._start_node(run, "implementation")

            async def progress(event: str, payload: Mapping[str, object]) -> None:
                self._runtime_event(run, {"event_type": event.casefold(), **dict(payload)})

            request = _WorkflowRequest(
                run_id=run.run_id,
                task_id=run.active_workflow_task_id,
                project_id=run.project_id,
                active_workspace_root=stage,
                active_workspace=sandbox_workspace,
                instruction=instruction,
                provider_id=run.provider_id,
                model_id=run.model_id,
                fallback_provider_id=None,
                progress_callback=progress,
                steering_callback=lambda: self._consume_steering(run.run_id),
                session_id=self._session_for_run[run.run_id],
            )
            workflow_services = ["workspace_stage", "build"]
            requires_network = run.provider_id not in {"verified_template", "fake_planner"}
            if requires_network:
                workflow_services.append("network")
            workflow_grant = self.policy_engine.issue_grant(
                run_id=run.run_id,
                node_id="implementation",
                workspace_root=stage,
                allowed_services=workflow_services,
                authorization_source=("explicit_edit_request" if explicit_edit_authorized else "explicit_action_request"),
                session_id=self._session_for_run[run.run_id],
                max_calls=len(workflow_services),
                allow_workspace_write=True,
                allow_build=True,
                allow_network=requires_network,
            )
            for service in workflow_services:
                self.policy_engine.validate_service(workflow_grant, service, stage)
            run.status = "running"
            result = await self.autonomous_executor(request)
            diagnostics = result.get("provider_diagnostics")
            if isinstance(diagnostics, Mapping):
                for name in (
                    "outbound_request_count", "physical_request_count",
                    "request_reached_provider", "http_status", "provider_request_id",
                    "retry_after_ms", "last_turn", "response_mode",
                    "last_failure_code", "last_repair_outcome",
                    "last_parse_reason", "last_failure_origin", "last_response_shape", "last_response_tool_count", "stream_fallback_used",
                ):
                    value = diagnostics.get(name)
                    if isinstance(value, (str, int, bool, type(None))) and hasattr(run, name):
                        setattr(run, name, value)
            run.build_result = _safe_mapping(result.get("build_result"))
            run.simulation_result = _safe_mapping(result.get("simulation_result"))
            if run.build_result:
                _set_run_stage(run, "build", "completed" if run.build_result.get("success") is True else "failed", str(run.build_result.get("message") or "Build completed"))
            raw_plan = result.get("plan")
            run.agent_plan = [
                {"text": str(item.get("text") or ""), "status": str(item.get("status") or "pending")}
                for item in raw_plan
                if isinstance(item, Mapping) and str(item.get("text") or "").strip()
            ][:32] if isinstance(raw_plan, list) else []
            run.validation_report = _safe_mapping(result.get("validation_report"))
            repair_count = result.get("repair_attempt_count")
            run.repair_attempt_count = repair_count if isinstance(repair_count, int) and not isinstance(repair_count, bool) else 0
            run.pending_decision = result.get("pending_decision") is True
            alternatives = result.get("suggested_alternatives")
            run.suggested_alternatives = [str(item) for item in alternatives if isinstance(item, str)][:10] if isinstance(alternatives, list) else []
            run.active_workspace_unchanged = True
            if result.get("success") is not True:
                failure_message = str(result.get("message") or "Workflow failed")
                failure_code = str(result.get("classification") or "")
                if _recovery_draft_allowed(failure_code):
                    try:
                        draft = self.change_service.create_review_draft(
                            provider_id=run.provider_id, workspace_root=workspace,
                            stage_root=stage, baseline=baseline,
                        )
                    except ChangeSetError:
                        draft = None
                    if draft is not None:
                        for previous_run in tuple(self._runs.values()):
                            if (
                                previous_run.run_id == run.run_id
                                or not previous_run.change_set_id
                                or not previous_run.recoverable
                            ):
                                continue
                            try:
                                previous_status = self.change_service.get(previous_run.change_set_id).status
                            except ChangeSetError:
                                previous_status = "discarded"
                            if previous_status == "discarded":
                                previous_run.recoverable = False
                                previous_run.recovery_actions = []
                                self._persist_run(previous_run)
                        run.change_set_id = draft.change_set_id
                        run.created_files = [item.path for item in draft.changed_files if item.change_type == "created"]
                        run.modified_files = [item.path for item in draft.changed_files if item.change_type == "modified"]
                        run.deleted_files = [item.path for item in draft.changed_files if item.change_type == "deleted"]
                        run.created_file_count = len(run.created_files)
                        run.modified_file_count = len(run.modified_files)
                        run.deleted_file_count = len(run.deleted_files)
                        run.recoverable = True
                        run.recovery_actions = ["retry"]
                        failure_message += " Generated files were saved as an unverified recovery draft. Retry to repair and validate them."
                active_failure_node = "validation" if self._nodes[run.run_id]["validation"].status == "running" else "implementation"
                self._fail_node(run, active_failure_node, failure_message)
                run.status = "blocked" if result.get("blocked") is True else "failed"
                run.classification = str(result.get("classification") or "AUTONOMOUS_WORKFLOW_FAILED")
                if run.pending_decision and run.suggested_alternatives:
                    failure_message += " Compatible options: " + ", ".join(run.suggested_alternatives) + "."
                run.assistant_message_override = failure_message[:1000]
                self._skip_pending_nodes(run, "Not reached after project validation stopped the workflow")
                self._event(
                    run,
                    "runtime.blocked" if run.status == "blocked" else "runtime.failed",
                    status=run.status,
                    message=failure_message,
                    classification=run.classification,
                )
                self._persist_run(run)
                return
            authorized_paths = {
                path.relative_to(stage).as_posix()
                for path in stage.rglob("*")
                if path.is_file()
                and not path.is_symlink()
                and not any(part.casefold() in IGNORED_DIRS for part in path.relative_to(stage).parts)
            }
            change_set = None
            # This workflow uses a complete stage copy, not an overlay. Missing
            # baseline files therefore represent staged deletions.
            deleted_paths = set(baseline.files) - authorized_paths
            authorized_paths.update(deleted_paths)
            try:
                change_set = self.change_service.create_from_stage(
                    provider_id=run.provider_id,
                    workspace_root=workspace,
                    stage_root=stage,
                    baseline=baseline,
                    authorized_paths=authorized_paths,
                    deleted_paths=deleted_paths,
                )
            except ChangeSetError as exc:
                if exc.code != "CHANGE_NO_CHANGES":
                    raise
            if change_set is not None:
                run.change_set_id = change_set.change_set_id
                run.created_files = [item.path for item in change_set.changed_files if item.change_type == "created"]
                run.modified_files = [item.path for item in change_set.changed_files if item.change_type == "modified"]
                run.deleted_files = [item.path for item in change_set.changed_files if item.change_type == "deleted"]
                run.created_file_count = len(run.created_files)
                run.modified_file_count = len(run.modified_files)
                run.deleted_file_count = len(run.deleted_files)
            if self._nodes[run.run_id]["implementation"].status != "completed":
                self._complete_node(
                    run,
                    "implementation",
                    "Firmware workflow completed in a managed stage",
                    artifact_id=(change_set.change_set_id if change_set is not None else None),
                )
            if self._nodes[run.run_id]["validation"].status == "pending":
                self._start_node(run, "validation")
            if self._nodes[run.run_id]["validation"].status == "running":
                self._complete_node(run, "validation", "Build validation completed")
            if change_set is not None:
                assessment = self.risk_service.assess(
                    change_set,
                    explicit_edit_authorized=explicit_edit_authorized,
                )
                change_set = self.change_service.annotate_authorization(
                    change_set.change_set_id,
                    authorization_id=(run.run_id if explicit_edit_authorized else None),
                    authorization_source=("explicit_edit_request" if explicit_edit_authorized else "explicit_review"),
                    risk_level=assessment.level,
                    review_required=assessment.reviewer_required,
                )
                self._annotate_risk(run, assessment)
                reviewer_approved = False
                if assessment.reviewer_required:
                    reviewer_approved = await self._review(run, change_set.to_dict(), assessment) == "approve"
                    if not reviewer_approved:
                        run.status = "completed"
                        run.classification = "CHANGESET_REVIEW_REQUIRED"
                        self._skip_node(run, "apply", "Staged changes require explicit review")
                        self._skip_node(run, "flash", "Hardware action blocked until changes are approved")
                        self._event(run, "runtime.completed", status="completed", message="Build passed; staged changes require review")
                        self._persist_run(run)
                        return
                else:
                    self._skip_node(run, "review", "Low-risk validated edit")
                scoped_apply = assessment.scoped_auto_apply_allowed or (
                    explicit_edit_authorized and assessment.level == "medium" and reviewer_approved
                )
                if not scoped_apply:
                    run.status = "completed"
                    run.classification = "CHANGESET_APPROVAL_REQUIRED"
                    self._skip_node(run, "apply", "Staged changes require explicit approval")
                    self._skip_node(run, "flash", "Hardware action blocked until changes are approved")
                    self._event(run, "runtime.completed", status="completed", message="Build passed; staged changes require approval")
                    self._persist_run(run)
                    return
                self._start_node(run, "apply")
                applied = self.change_service.apply(change_set.change_set_id)
                run.active_workspace_unchanged = False
                self._complete_node(run, "apply", "Scoped edit authorization applied the built ChangeSet", artifact_id=applied.change_set_id)
                fresh = self.change_service.inspect_workspace(workspace)
                changed_paths = [item.path for item in applied.changed_files]
                self.memory.invalidate_for_change(
                    run.project_id,
                    changed_paths=changed_paths,
                    new_workspace_revision=fresh.revision,
                    board_id=run.flash_board_type,
                )
                self.memory.add_verified(
                    project_id=run.project_id,
                    kind="applied_change",
                    value=applied.summary,
                    source_type="changeset",
                    source_id=applied.change_set_id,
                    source_payload=json.dumps(applied.to_dict(), ensure_ascii=True, sort_keys=True),
                    workspace_revision=fresh.revision,
                    affected_paths=changed_paths,
                    board_id=run.flash_board_type,
                )
            else:
                self._skip_node(run, "review", "No workspace diff required review")
                self._skip_node(run, "apply", "No workspace diff to apply")
            if run.build_result:
                self.memory.add_verified(
                    project_id=run.project_id,
                    kind="verified_build",
                    value=str(result.get("message") or "Build completed"),
                    source_type="build_result",
                    source_id=run.run_id,
                    source_payload=json.dumps(run.build_result, ensure_ascii=True, sort_keys=True),
                    workspace_revision=self.change_service.inspect_workspace(workspace).revision,
                    affected_paths=(
                        [item.path for item in change_set.changed_files]
                        if change_set is not None
                        else ()
                    ),
                    board_id=run.flash_board_type,
                )
            if run.autonomy == "build_then_confirm_flash":
                if _approved_artifact_hash(run) is None:
                    run.status = "failed"
                    run.classification = "BUILD_ARTIFACT_DIGEST_UNAVAILABLE"
                    self._fail_node(run, "flash", "A verified firmware digest is required before flash approval")
                    self._event(
                        run,
                        "runtime.failed",
                        status="failed",
                        message="Build completed but its firmware artifact could not be integrity-bound",
                    )
                    self._persist_run(run)
                    return
                capability_root = self._capability_root(run)
                workspace_revision = self.change_service.inspect_workspace(capability_root).revision
                approval = self.approvals.issue(
                    run_id=run.run_id,
                    action_type="flash",
                    binding=_flash_binding(run, workspace_revision=workspace_revision),
                    summary="Flash the verified build artifact to the selected device once.",
                )
                run.approval_id = approval.approval_id
                run.approval_expires_at = approval.expires_at
                run.status = "awaiting_flash_confirmation"
                run.classification = "BUILD_READY_FOR_FLASH"
                _set_run_stage(run, "flash_confirmation", "waiting", "Confirm flash to upload the verified artifact")
                self._event(run, "runtime.awaiting_flash_confirmation", status=run.status, stage="flash_confirmation", message="Build completed; waiting for flash confirmation")
            else:
                self._skip_node(run, "flash", "Build/simulation autonomy does not touch physical hardware")
                run.status = "completed"
                run.classification = "AUTONOMOUS_WORKFLOW_COMPLETED"
                if change_set is None and str(result.get("message") or "").strip():
                    run.assistant_message_override = str(result.get("message"))[:4000]
                self._event(run, "runtime.completed", status="completed", message=("Simulation completed" if run.autonomy == "simulate" else "Build completed"))
            self._persist_run(run)
        except asyncio.CancelledError:
            run.status = "cancelled"
            run.classification = "AGENT_RUNTIME_CANCELLED"
            self._cancel_active_nodes(run.run_id)
            self._event(run, "runtime.cancelled", message="Action run cancelled")
            raise
        except Exception as exc:
            logger.exception(
                "agent autonomous workflow finalization failed run_id=%s",
                run.run_id,
            )
            if stage is not None and baseline is not None and not run.change_set_id:
                self._retain_draft_after_unexpected_failure(
                    run, workspace=workspace, stage=stage, baseline=baseline,
                )
            run.status = "failed"
            run.classification = "AGENT_RUNTIME_FINALIZATION_FAILED"
            detail = str(exc) or type(exc).__name__
            active = next(
                (node for node in self._nodes[run.run_id].values() if node.status == "running"),
                None,
            )
            if active is not None:
                self._fail_node(run, active.node_id, detail)
            elif self._nodes[run.run_id]["apply"].status == "pending":
                self._fail_node(run, "apply", detail)
            else:
                self._fail_active_node(run, detail)
            self._skip_pending_nodes(run, "Not reached after workflow finalization failed")
            run.assistant_message_override = (
                "Firmware workflow finalization failed after generation/build: " + detail
            )[:1000]
            if run.change_set_id and run.recoverable:
                run.assistant_message_override = (
                    run.assistant_message_override
                    + " Generated files were saved as an unverified recovery draft. Retry to repair and validate them."
                )[:1000]
            self._event(
                run,
                "runtime.failed",
                status="failed",
                message="Embedded workflow finalization failed",
                classification=run.classification,
            )
            self._persist_run(run)
        finally:
            # ChangeSets retain their managed stage until review/apply/undo completes.
            # Failed/no-diff workflows must not leak full workspace copies.
            if stage is not None:
                retained_stage = None
                if run.change_set_id:
                    retained_stage = Path(self.change_service.get(run.change_set_id).staging_root)
                if retained_stage is None or retained_stage.resolve() != stage.resolve():
                    shutil.rmtree(stage, ignore_errors=True)

    def _retain_draft_after_unexpected_failure(
        self, run: Any, *, workspace: Path, stage: Path, baseline: Any,
    ) -> None:
        """Retain bounded staged changes when finalization fails before apply."""
        try:
            if self.change_service.inspect_workspace(workspace).tree_hash != baseline.tree_hash:
                return
            draft = self.change_service.create_review_draft(
                provider_id=run.provider_id, workspace_root=workspace,
                stage_root=stage, baseline=baseline,
            )
        except (ChangeSetError, OSError, ValueError):
            return
        if draft is None:
            return
        run.change_set_id = draft.change_set_id
        run.created_files = [item.path for item in draft.changed_files if item.change_type == "created"]
        run.modified_files = [item.path for item in draft.changed_files if item.change_type == "modified"]
        run.deleted_files = [item.path for item in draft.changed_files if item.change_type == "deleted"]
        run.created_file_count = len(run.created_files)
        run.modified_file_count = len(run.modified_files)
        run.deleted_file_count = len(run.deleted_files)
        run.recoverable = True
        run.recovery_actions = ["retry"]

    async def _review(self, run: Any, change_set: Mapping[str, object], assessment: RiskAssessment) -> str:
        self._start_node(run, "review")
        if self.reviewer is None:
            self._complete_node(run, "review", "Reviewer unavailable; explicit approval required")
            return "request_changes"
        capability_root = self._capability_root(run)
        review_grant = self.policy_engine.issue_grant(
            run_id=run.run_id,
            node_id="review",
            workspace_root=capability_root,
            allowed_services=("network",),
            authorization_source="risk_based_review",
            session_id=self._session_for_run[run.run_id],
            max_calls=1,
            allow_network=True,
        )
        self.policy_engine.validate_service(review_grant, "network", capability_root)
        response = await self.reviewer({
            "change_set": dict(change_set), "risk": assessment.to_dict(),
            "routing": {
                "provider_id": run.provider_id, "model_id": run.model_id,
                "session_id": self._session_for_run[run.run_id],
            },
        })
        verdict = str(response.get("verdict") or "block").casefold()
        if verdict not in {"approve", "request_changes", "block"}:
            verdict = "block"
        self._complete_node(run, "review", str(response.get("summary") or f"Reviewer verdict: {verdict}"))
        return verdict

    def _capability_root(self, run: Any) -> Path:
        # Hardware authorization is anchored to the durable project workspace,
        # never to the managed workflow stage (which is deleted after finalization).
        context = self._recovery_context.get(run.run_id, {})
        workspace_root = context.get("workspace_root")
        if isinstance(workspace_root, str) and workspace_root:
            return Path(workspace_root).resolve(strict=True)
        if run.change_set_id:
            return Path(self.change_service.get(run.change_set_id).workspace_root).resolve(strict=True)
        raise ValueError("AGENT_CAPABILITY_WORKSPACE_UNAVAILABLE")

    def _apply_runtime_result(self, run: Any, result: ToolRuntimeResult) -> None:
        run.classification = result.provider_classification or result.classification.value
        run.change_set_id = result.change_set_id
        run.created_file_count = result.created_file_count
        run.modified_file_count = result.modified_file_count
        run.deleted_file_count = result.deleted_file_count
        run.tool_execution_count = result.tool_execution_count
        run.agent_plan = [dict(item) for item in result.plan]
        run.active_workspace_unchanged = result.active_workspace_unchanged
        diagnostics = result.provider_diagnostics
        run.outbound_request_count = int(diagnostics.get("outbound_request_count", 0) or 0)
        run.physical_request_count = int(diagnostics.get("physical_request_count", 0) or 0)
        run.request_reached_provider = diagnostics.get("request_reached_provider") is True
        http_status = diagnostics.get("http_status")
        run.http_status = http_status if isinstance(http_status, int) and not isinstance(http_status, bool) else None
        request_id = diagnostics.get("provider_request_id")
        run.provider_request_id = request_id if isinstance(request_id, str) else None
        retry_after = diagnostics.get("retry_after_ms")
        run.retry_after_ms = retry_after if isinstance(retry_after, int) and retry_after >= 0 else None
        if run.classification in {
            "API_RATE_LIMITED",
            "PROVIDER_RATE_LIMITED",
            "API_QUOTA_EXCEEDED",
            "QUOTA_EXCEEDED",
            "API_NETWORK_ERROR",
            "API_MODEL_UNAVAILABLE",
            "PROVIDER_UNAVAILABLE",
            "PROVIDER_TIMEOUT",
            "PROVIDER_HTTP_ERROR",
            "PROVIDER_BUSY",
        }:
            run.status = "paused_model_unavailable"
            run.recoverable = True
            run.recovery_actions = ["retry", "change_model"]
        elif result.classification is RuntimeClassification.CANCELLED:
            run.status = "cancelled"
        elif result.classification is RuntimeClassification.LIMIT_EXCEEDED:
            run.status = "timed_out"
        elif result.classification is RuntimeClassification.POLICY_DENIED:
            run.status = "blocked"
        elif result.classification is not RuntimeClassification.PASS:
            run.status = "failed"
        if result.change_set_id:
            change_set = self.change_service.get(result.change_set_id)
            run.created_files = [item.path for item in change_set.changed_files if item.change_type == "created"]
            run.modified_files = [item.path for item in change_set.changed_files if item.change_type == "modified"]
            run.deleted_files = [item.path for item in change_set.changed_files if item.change_type == "deleted"]
            if change_set.status == "draft":
                run.recoverable = True
                run.recovery_actions = ["retry"]
                run.assistant_message_override = (
                    "Generated files were saved as an unverified recovery draft. Retry to repair and validate them."
                )

    def _annotate_risk(self, run: Any, assessment: RiskAssessment) -> None:
        for node_id in ("validation", "review", "apply"):
            node = self._nodes[run.run_id][node_id]
            node.risk_level = assessment.level
            node.reviewer_required = assessment.reviewer_required
            self.store.save_node(run.run_id, node.node_id, node.status, node.to_dict())
        self._event(run, "risk.assessed", node_id="validation", message=f"Risk: {assessment.level}", risk=assessment.to_dict())

    def _initial_graph(self, autonomy: str) -> tuple[AgentTaskNode, ...]:
        return (
            AgentTaskNode("planning", "supervisor_planner", "Plan the requested embedded action"),
            AgentTaskNode("implementation", "firmware_implementer", "Produce the requested result", ("planning",)),
            AgentTaskNode("validation", "validation_services", "Validate artifacts and risk", ("implementation",)),
            AgentTaskNode("review", "reviewer", "Independently review risk-selected changes", ("validation",)),
            AgentTaskNode("apply", "changeset_service", "Apply an authorized ChangeSet", ("review",)),
            AgentTaskNode("flash", "flash_monitor_services", "Execute an approved hardware action", ("validation",)),
        )

    def _start_node(self, run: Any, node_id: str) -> None:
        node = self._nodes[run.run_id][node_id]
        node.status = "running"
        node.attempt += 1
        self.store.save_node(run.run_id, node_id, node.status, node.to_dict())
        self._event(run, "node.started", node_id=node_id, status="running", message=node.goal)

    def _complete_node(self, run: Any, node_id: str, message: str, artifact_id: str | None = None) -> None:
        node = self._nodes[run.run_id][node_id]
        node.status = "completed"
        node.message = message[:512]
        if artifact_id:
            node.artifact_ids.append(artifact_id)
        self.store.save_node(run.run_id, node_id, node.status, node.to_dict())
        self._event(run, "node.completed", node_id=node_id, status="completed", message=node.message)

    def _skip_node(self, run: Any, node_id: str, message: str) -> None:
        node = self._nodes[run.run_id][node_id]
        node.status = "skipped"
        node.message = message[:512]
        self.store.save_node(run.run_id, node_id, node.status, node.to_dict())
        self._event(run, "node.skipped", node_id=node_id, status="skipped", message=node.message)

    def _fail_node(self, run: Any, node_id: str, message: str) -> None:
        node = self._nodes[run.run_id][node_id]
        node.status = "failed"
        node.message = message[:512]
        self.store.save_node(run.run_id, node_id, node.status, node.to_dict())
        self._persist_run(run)

    def _fail_active_node(self, run: Any, message: str) -> None:
        for node in self._nodes[run.run_id].values():
            if node.status == "running":
                self._fail_node(run, node.node_id, message)
                return

    def _cancel_active_nodes(self, run_id: str) -> None:
        run = self._runs[run_id]
        for node in self._nodes[run_id].values():
            if node.status == "running":
                node.status = "cancelled"
                node.message = "Cancelled"
                self.store.save_node(run_id, node.node_id, node.status, node.to_dict())
        self._persist_run(run)

    def _skip_pending_nodes(self, run: Any, message: str) -> None:
        for node in self._nodes[run.run_id].values():
            if node.status == "pending":
                self._skip_node(run, node.node_id, message)

    def _runtime_event(self, run: Any, event: Mapping[str, object]) -> None:
        payload = dict(event)
        payload.setdefault("event_type", "tool.updated")
        event_type = str(payload.pop("event_type"))
        normalized = event_type.upper()
        artifact_delta = payload.pop("_context_artifact_delta", None)
        if normalized == "CONTEXT_CHECKPOINT_CREATED":
            checkpoint_value = payload.get("checkpoint")
            if isinstance(checkpoint_value, Mapping):
                checkpoint = dict(checkpoint_value)
                if (
                    isinstance(artifact_delta, list)
                    and artifact_delta
                    and all(isinstance(item, Mapping) for item in artifact_delta)
                ):
                    try:
                        artifact = self.store.save_context_artifact(
                            run.run_id,
                            self._session_for_run[run.run_id],
                            observation_count=int(checkpoint.get("compacted_observations") or 0),
                            content_sha256=str(checkpoint.get("observation_digest") or ""),
                            observation_delta=artifact_delta,
                        )
                        checkpoint.update({
                            "artifact_status": "ready",
                            "artifact_id": artifact["artifact_id"],
                            "artifact_parent_id": artifact["parent_artifact_id"],
                            "retrieval": (
                                f"/agent-runtime/runs/{run.run_id}/context-artifacts/"
                                f"{artifact['artifact_id']}"
                            ),
                        })
                    except Exception:
                        checkpoint["artifact_status"] = "unavailable"
                        logger.exception(
                            "context artifact persistence failed run_id=%s",
                            run.run_id,
                        )
                else:
                    checkpoint["artifact_status"] = "unavailable"
                payload["checkpoint"] = checkpoint
        message_value = payload.get("message")
        message = str(message_value).strip()[:1000] if isinstance(message_value, str) and message_value.strip() else None
        failure_value = payload.get("failure")
        if message is None and isinstance(failure_value, Mapping):
            nested_message = failure_value.get("message")
            if isinstance(nested_message, str) and nested_message.strip():
                message = nested_message.strip()[:1000]
                payload["message"] = message
        if normalized == "GENERATION_COMPLETED":
            message = "Files generated; validating the complete firmware project"
            payload["message"] = message
        provider_id = payload.get("provider_id")
        model_id = payload.get("model_id")
        if isinstance(provider_id, str) and provider_id.strip():
            run.actual_provider_id = provider_id.strip()
        if isinstance(model_id, str) and model_id.strip():
            run.model_id = model_id.strip()
        stage = _workflow_stage(normalized)
        status = _workflow_stage_status(normalized)
        if normalized == "TASK_CREATED":
            _set_run_stage(run, "planning", "running", message)
        elif normalized == "PLAN_GENERATED":
            _set_run_stage(run, "planning", "completed", message or "Execution plan generated")
        elif normalized == "PROJECT_VALIDATION_STARTED":
            if self._nodes[run.run_id]["implementation"].status == "running":
                self._complete_node(run, "implementation", "Generated project is ready for validation")
            if self._nodes[run.run_id]["validation"].status == "pending":
                self._start_node(run, "validation")
        elif normalized == "PROJECT_VALIDATION_COMPLETED":
            if self._nodes[run.run_id]["validation"].status == "running":
                self._complete_node(run, "validation", message or "Complete project validated")
        elif stage is not None and status is not None:
            _set_run_stage(run, stage, status, message)
            if stage == "build" and normalized in {"BUILD_COMPLETED", "BUILD_FAILED"}:
                run.build_result = _safe_mapping(payload) or run.build_result
            if normalized == "BUILD_REPAIR_COMPLETED":
                _set_run_stage(run, "build", "completed", message)
        self._persist_run(run)
        if stage is not None:
            payload.setdefault("stage", stage)
        self._event(run, event_type, **payload)

    def _event(self, run: Any, event_type: str, **payload: object) -> dict[str, object]:
        safe = {key: value for key, value in payload.items() if value is not None}
        safe["event_type"] = event_type
        safe["run_id"] = run.run_id
        if "flash" in event_type or event_type.startswith("monitor"):
            safe.setdefault("port", run.flash_port)
            safe.setdefault("board", run.flash_board_type)
            safe.setdefault("environment", run.flash_environment)
        if event_type in {"runtime.completed", "runtime.failed", "runtime.cancelled"}:
            safe["changed_files"] = sum(int(getattr(run, key, 0) or 0) for key in ("created_file_count", "modified_file_count", "deleted_file_count"))
            if isinstance(run.build_result, dict):
                safe["build_passed"] = run.build_result.get("success")
                safe["warnings"] = run.build_result.get("warnings_count")
        safe.update(run_activity_fields(safe))
        if bool(safe.pop("transient", False)):
            # Transient attempt frames intentionally do not consume session_seq and
            # are not replayed after process restart. Durable model settlement is
            # the reconnect/reconciliation source of truth.
            with self._lock:
                next_seq = self._transient_sequence.get(run.run_id, 0) + 1
                self._transient_sequence[run.run_id] = next_seq
                durable_base = int(run.events[-1].get("sequence") or 0) if run.events else 0
                event = {
                    **safe,
                    "transient": True,
                    "transient_sequence": next_seq,
                    # Keep a sortable UI sequence without advancing the durable cursor.
                    "sequence": durable_base + min(next_seq, 999_999) / 1_000_000,
                }
                frames = self._transient_events.setdefault(run.run_id, [])
                frames.append(event)
                if len(frames) > 2048:
                    del frames[:-2048]
            _emit_orchestrator_diagnostic(run, event, session_id=self._session_for_run[run.run_id])
            return event
        event = self.store.append_event(
            self._session_for_run[run.run_id],
            safe,
            grant=self._leases.get(run.run_id),
        )
        run.events.append(dict(event))
        if event_type == "turn.settled":
            try:
                self.store.save_run_checkpoint(
                    run.run_id,
                    self._session_for_run[run.run_id],
                    turn=int(safe.get("turn") or 1),
                    session_seq=int(event["sequence"]),
                    payload={
                        "step": int(safe.get("step") or safe.get("turn") or 1),
                        "attempt_id": str(safe.get("attempt_id") or ""),
                        "outcome": str(safe.get("outcome") or "unknown"),
                        "tool_count": int(safe.get("tool_count") or 0),
                        "run_status": str(getattr(run, "status", "running")),
                        "stage_statuses": dict(getattr(run, "stage_statuses", {}) or {}),
                    },
                )
            except Exception:
                # The journal event remains authoritative and can rebuild this
                # derived checkpoint; never fail a settled turn for projection IO.
                logger.exception("agent checkpoint projection failed run_id=%s", run.run_id)
        # Settlement atomically supersedes live frames for the same attempt.
        if event_type in {"model.attempt.settled", "model.attempt.abandoned", "MODEL_ATTEMPT_SETTLED"}:
            attempt_id = str(safe.get("attempt_id") or "")
            if attempt_id:
                with self._lock:
                    self._transient_events[run.run_id] = [
                        frame for frame in self._transient_events.get(run.run_id, [])
                        if str(frame.get("attempt_id") or "") != attempt_id
                    ]
        _emit_orchestrator_diagnostic(run, event, session_id=self._session_for_run[run.run_id])
        return event

    def _persist_graph(self, run: Any) -> None:
        for node in self._nodes[run.run_id].values():
            self.store.save_node(run.run_id, node.node_id, node.status, node.to_dict())
        self._persist_run(run)

    def _persist_run(self, run: Any) -> None:
        run.updated_at = utc_now()
        payload = run.to_safe_dict()
        payload["session_id"] = self._session_for_run[run.run_id]
        recovery = self._recovery_context.get(run.run_id)
        if recovery:
            payload["_recovery_context"] = recovery
        self.store.save_run(
            run.run_id,
            self._session_for_run[run.run_id],
            run.project_id,
            run.status,
            payload,
        )
        for listener in tuple(self._run_listeners):
            try:
                project = getattr(listener, "project", None)
                if callable(project):
                    project(run)
                elif callable(listener):
                    listener(run)
            except Exception:
                logger.exception("agent run projection failed run_id=%s", run.run_id)

    def _restore_runs(self) -> None:
        for payload in self.store.list_runs():
            try:
                run_id = str(payload["run_id"])
                session_id = str(payload.get("session_id") or f"run:{run_id}")
                run = self.run_factory(
                    run_id,
                    str(payload["project_id"]),
                    str(payload["provider_id"]),
                    status=str(payload.get("status") or "failed"),
                    classification=(str(payload["classification"]) if payload.get("classification") else None),
                    model_id=(str(payload["model_id"]) if payload.get("model_id") else None),
                    autonomy=str(payload.get("autonomy") or "staged_changes"),
                )
                for name in (
                    "change_set_id", "created_file_count", "modified_file_count", "deleted_file_count",
                    "tool_execution_count", "active_workspace_unchanged", "active_workflow_task_id",
                    "build_result", "simulation_result", "flash_result", "monitor_result", "flash_port", "flash_board_type",
                    "flash_environment", "start_monitor_after_flash", "approval_id", "approval_expires_at",
                    "assistant_message_override", "stage_statuses", "stage_messages",
                    "validation_report", "repair_attempt_count", "pending_decision", "suggested_alternatives",
                    "agent_plan", "created_files", "modified_files", "deleted_files",
                    "actual_provider_id", "fallback_reason", "outbound_request_count",
                    "physical_request_count", "request_reached_provider", "http_status",
                    "provider_request_id", "retry_after_ms",
                    "last_turn", "response_mode", "last_failure_code", "last_repair_outcome",
                    "last_parse_reason", "last_failure_origin", "last_response_shape", "last_response_tool_count", "stream_fallback_used",
                    "retry_of_run_id", "recovered_by_run_id", "interrupted_at",
                    "recoverable", "recovery_actions",
                ):
                    if name in payload and hasattr(run, name):
                        setattr(run, name, payload[name])
                provider_diagnostics = payload.get("provider_diagnostics")
                if isinstance(provider_diagnostics, Mapping):
                    for name in (
                        "outbound_request_count",
                        "physical_request_count",
                        "request_reached_provider",
                        "http_status",
                        "provider_request_id",
                        "retry_after_ms",
                        "last_turn", "response_mode", "last_failure_code", "last_repair_outcome",
                        "last_parse_reason", "last_failure_origin", "last_response_shape", "last_response_tool_count", "stream_fallback_used",
                    ):
                        if name in provider_diagnostics and hasattr(run, name):
                            setattr(run, name, provider_diagnostics[name])
                recovery_context = payload.get("_recovery_context")
                if isinstance(recovery_context, Mapping):
                    self._recovery_context[run_id] = _json_safe_mapping(recovery_context)
                if run.status not in TERMINAL_STATUSES and run.status != "paused_model_unavailable":
                    previous_status = run.status
                    run.status = "failed"
                    run.classification = "AGENT_RUNTIME_INTERRUPTED"
                    run.interrupted_at = utc_now()
                    run.recoverable = bool(self._recovery_context.get(run_id))
                    run.recovery_actions = ["retry"] if run.recoverable else []
                    if previous_status == "awaiting_flash_confirmation" or run.approval_id:
                        self.store.invalidate_pending_approvals(run_id, "backend_restarted")
                        run.approval_id = None
                        run.approval_expires_at = None
                nodes: dict[str, AgentTaskNode] = {}
                for raw in self.store.graph(run_id):
                    node = AgentTaskNode(
                        node_id=str(raw["node_id"]),
                        role=str(raw["role"]),
                        goal=str(raw["goal"]),
                        dependencies=tuple(str(item) for item in raw.get("dependencies", [])),
                        status=("failed" if raw.get("status") == "running" else str(raw.get("status") or "pending")),
                        attempt=int(raw.get("attempt", 0)),
                        risk_level=str(raw.get("risk_level") or "unassessed"),
                        reviewer_required=bool(raw.get("reviewer_required", False)),
                        message=(str(raw["message"]) if raw.get("message") else None),
                        artifact_ids=[str(item) for item in raw.get("artifact_ids", [])],
                    )
                    nodes[node.node_id] = node
            except (KeyError, TypeError, ValueError):
                continue
            self._runs[run_id] = run
            self._nodes[run_id] = nodes or {node.node_id: node for node in self._initial_graph(run.autonomy)}
            self._session_for_run[run_id] = session_id
            self._steering[run_id] = []
            if run.classification == "AGENT_RUNTIME_INTERRUPTED":
                checkpoint = self.store.latest_run_checkpoint(run_id)
                if checkpoint and run_id in self._recovery_context:
                    self._recovery_context[run_id]["last_safe_checkpoint"] = checkpoint
                self._event(
                    run,
                    "runtime.interrupted",
                    status="failed",
                    message="The backend restarted during this action run",
                    checkpoint_turn=(checkpoint.get("turn") if checkpoint else None),
                    checkpoint_sequence=(checkpoint.get("session_seq") if checkpoint else None),
                    recovery_mode="replacement_run_only",
                    effect_replay_allowed=False,
                )
                self._persist_run(run)


@dataclass(frozen=True, slots=True)
class _WorkflowRequest:
    run_id: str
    task_id: str
    project_id: str
    active_workspace_root: Path
    active_workspace: Mapping[str, object]
    instruction: str
    provider_id: str
    model_id: str | None
    fallback_provider_id: str | None
    progress_callback: Callable[[str, Mapping[str, object]], Awaitable[None]]
    steering_callback: Callable[[], tuple[str, ...]] | None = None
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class _FlashRequest:
    run_id: str
    project_id: str
    port: str | None
    board_type: str | None
    environment: str | None
    start_monitor_after_flash: bool
    expected_artifact_hash: str | None
    progress_callback: Callable[[str, Mapping[str, object]], Awaitable[None]]


def _workflow_stage(event: str) -> str | None:
    if event.startswith("BUILD_REPAIR"):
        return "repair"
    if event.startswith(("GENERATION", "CODE_GENERATION", "MANIFEST_GENERATION", "FILE_GENERATION")):
        return "generation"
    if event.startswith("PROJECT_REPAIR"):
        return "repair"
    if event.startswith("PROJECT_VALIDATION"):
        return "generation"
    if event.startswith("BUILD"):
        return "build"
    if event.startswith("FLASH"):
        return "flash"
    if event.startswith("MONITOR"):
        return "monitor"
    if event.startswith("PLAN") or event == "TASK_CREATED":
        return "planning"
    return None


def _workflow_stage_status(event: str) -> str | None:
    if event.endswith(("_FAILED", "_INCOMPLETE", "_BLOCKED")):
        return "failed"
    if event == "GENERATION_COMPLETED":
        # Files are written, but GenerateCodeHandler still performs whole-
        # project firmware, safety, and board validation.  The canonical
        # CODE_GENERATION_COMPLETED event closes this stage.
        return "running"
    if event == "PROJECT_VALIDATION_FAILED":
        return "running"
    if event == "PROJECT_REPAIR_COMPLETED":
        return "running"
    if event == "PROJECT_REPAIR_FAILED":
        return "running"
    if event.endswith(("_COMPLETED", "_VALIDATED")):
        return "completed"
    if event.endswith("_CANCELLED"):
        return "cancelled"
    if event.endswith("_STARTED") or event.endswith("_SELECTED"):
        return "running"
    return None


def _set_run_stage(run: Any, stage: str, status: str, message: str | None = None) -> None:
    if hasattr(run, "stage_statuses"):
        run.stage_statuses[stage] = status
    if message and hasattr(run, "stage_messages"):
        run.stage_messages[stage] = message


def _recovery_draft_allowed(classification: str) -> bool:
    normalized = classification.upper()
    if not normalized:
        return True
    return not any(token in normalized for token in (
        "CANCELLED", "POLICY_DENIED", "PATH_UNSAFE", "UNSAFE_ABORTED",
        "STAGE_TAMPERED", "WORKSPACE_TAMPERED", "CHANGE_CONFLICT",
    ))


def _safe_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items() if isinstance(item, (str, int, bool, float, type(None), dict, list))}


def _json_safe_mapping(value: Mapping[str, object]) -> dict[str, object]:
    """Return bounded, JSON-safe recovery metadata without failing a run."""
    try:
        encoded = json.dumps(dict(value), ensure_ascii=True, sort_keys=True)
        if len(encoded) > 64_000:
            return {}
        decoded = json.loads(encoded)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _flash_binding(
    run: Any,
    *,
    port: str | None = None,
    board_type: str | None = None,
    environment: str | None = None,
    start_monitor_after_flash: bool | None = None,
    workspace_revision: str | None = None,
) -> dict[str, object]:
    build = run.build_result if isinstance(run.build_result, Mapping) else {}
    artifact_identity = {
        key: build.get(key)
        for key in (
            "build_id",
            "artifact_hash",
            "firmware_hash",
            "checksum",
            "artifact_path",
            "firmware_path",
            "environment",
            "project_id",
            "workspace_revision",
            "source_manifest_hash",
        )
        if build.get(key) is not None
    }
    selected_port = port if port is not None else run.flash_port
    selected_board = board_type if board_type is not None else run.flash_board_type
    device_identity = _device_identity(selected_port, selected_board)
    binding = {
        "run_id": run.run_id,
        "project_id": run.project_id,
        "port": selected_port,
        "board_type": selected_board,
        "device_identity": device_identity,
        "environment": environment if environment is not None else run.flash_environment,
        "workspace_revision": workspace_revision or build.get("workspace_revision"),
        "artifact": artifact_identity,
        "start_monitor_after_flash": (
            bool(start_monitor_after_flash)
            if start_monitor_after_flash is not None
            else bool(run.start_monitor_after_flash)
        ),
    }
    return finalize_hardware_binding("flash", binding)


def _device_identity(port: str | None, board_type: str | None) -> dict[str, object]:
    """Resolve the strongest stable identity available for an approved device.

    COM/tty names are transport locations, not durable device identities. Prefer
    USB serial + VID/PID when the host exposes them; fall back conservatively and
    make the confidence explicit so weak matches can require confirmation.
    """
    selected_port = str(port or "").strip()
    selected_board = str(board_type or "").strip()
    if not selected_port:
        return {
            "stable_id": "unbound",
            "port": None,
            "board_type": selected_board or None,
            "confidence": "unbound",
        }

    try:
        detected = BoardDetector().detect_boards()
    except Exception:
        detected = []
    match = next(
        (board for board in detected if board.port.casefold() == selected_port.casefold()),
        None,
    )
    if match is not None:
        vid = match.vid
        pid = match.pid
        serial_number = str(match.serial_number or "").strip() or None
        if vid is not None and pid is not None and serial_number:
            return {
                "stable_id": f"usb:{vid:04x}:{pid:04x}:{serial_number.casefold()}",
                "port": match.port,
                "board_type": selected_board or match.board_type.value,
                "vid": vid,
                "pid": pid,
                "serial_number": serial_number,
                "confidence": "strong",
            }
        if vid is not None and pid is not None:
            return {
                "stable_id": f"usb:{vid:04x}:{pid:04x}|port:{match.port.casefold()}",
                "port": match.port,
                "board_type": selected_board or match.board_type.value,
                "vid": vid,
                "pid": pid,
                "serial_number": None,
                "confidence": "medium",
            }

    return {
        "stable_id": f"port:{selected_port.casefold()}|board:{selected_board.casefold()}",
        "port": selected_port,
        "board_type": selected_board or None,
        "confidence": "weak",
    }


def _approved_artifact_hash(run: Any) -> str | None:
    build = run.build_result if isinstance(run.build_result, Mapping) else {}
    value = build.get("firmware_hash") or build.get("artifact_hash") or build.get("checksum")
    return str(value) if isinstance(value, str) and value else None


def _emit_orchestrator_diagnostic(run: Any, event: Mapping[str, object], *, session_id: str) -> None:
    event_type = str(event.get("event_type") or "agent.event").replace("_", ".")
    mapped = _diagnostic_event_name(event_type)
    status = str(event.get("status") or getattr(run, "status", ""))
    level = "ERROR" if status == "failed" or mapped.endswith(".failed") else "INFO"
    common = dict(
        session_id=session_id,
        run_id=getattr(run, "run_id", None),
        project_id=getattr(run, "project_id", None),
        provider=getattr(run, "actual_provider_id", None) or getattr(run, "provider_id", None),
        model=getattr(run, "model_id", None),
        turn_id=event.get("turn_id") or (f"{getattr(run, 'run_id', '')}:turn-{event.get('turn')}" if event.get("turn") else None),
        tool_call_id=event.get("tool_call_id"),
        build_id=event.get("build_id"),
        status=status or None,
        tool=event.get("tool"),
        duration_ms=event.get("duration_ms"),
        classification=event.get("classification"),
        arguments=event.get("arguments"),
        result=event.get("result"),
        error_code=event.get("error_code"),
        context_files=event.get("context_files"),
        skill_count=event.get("skill_count"),
    )
    emit_diagnostic_event(
        component="agent",
        event=mapped,
        level=level,
        message=str(event.get("message") or ""),
        **common,
    )
    if event_type.casefold().startswith("tool."):
        emit_diagnostic_event(
            component="tool",
            event=event_type.casefold(),
            level=level,
            message=str(event.get("message") or ""),
            **common,
        )


def _diagnostic_event_name(event_type: str) -> str:
    normalized = event_type.casefold().replace("_", ".")
    mapping = {
        "runtime.queued": "agent.run.started",
        "runtime.completed": "agent.run.completed",
        "runtime.failed": "agent.run.failed",
        "agent.context.prepared": "agent.context.prepared",
        "agent.turn.started": "agent.turn.started",
        "agent.turn.completed": "agent.turn.completed",
        "tool.pending": "agent.tool.requested",
        "tool.started": "agent.tool.started",
        "tool.completed": "agent.tool.completed",
        "tool.failed": "agent.tool.failed",
        "build.started": "agent.build.started",
        "build.completed": "agent.build.completed",
        "build.failed": "agent.build.completed",
        "build.repair.started": "agent.repair.started",
        "build.repair.completed": "agent.repair.completed",
        "build.repair.failed": "agent.repair.completed",
        "project.repair.started": "agent.repair.started",
        "project.repair.completed": "agent.repair.completed",
        "project.repair.failed": "agent.repair.completed",
        "simulation.started": "agent.simulation.started",
        "simulation.completed": "agent.simulation.completed",
        "simulation.failed": "agent.simulation.completed",
        "plan.generated": "agent.plan.updated",
        "runtime.awaiting.flash.confirmation": "agent.approval.required",
    }
    return mapping.get(normalized, normalized if normalized.startswith("agent.") else f"agent.{normalized}")
