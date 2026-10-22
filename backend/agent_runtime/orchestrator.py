"""Durable action-run owner for the chat-first ForgeX product agent."""

from __future__ import annotations
from backend.runtime.process_owner import owned_operation

import asyncio
import copy
import hashlib
from .checkpoint_files import copy_checkpoint_tree
from .workspace_io import workspace_io
import json
import logging
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from backend.changes import ChangeSetError, ChangeSetService, IGNORED_DIRS
from backend.observability.diagnostics import emit_diagnostic_event
from backend.tools.board_detector import BoardDetector
from backend.hardware.action_binding import finalize_hardware_binding

from backend.contracts.diagnostics import apply_provider_diagnostics, safe_provider_diagnostics
from .activity import run_activity_fields
from .approvals import ApprovalService
from .capabilities import ToolPolicyEngine
from .capability_profiles import profile_for_autonomy
from .memory import ProjectMemoryService
from .jobs import AgentJobStore, TRANSIENT_PROVIDER_ERRORS, recovery_time_reserve
from .orchestration_graph import AgentTaskNode
from .orchestration_store import AgentOrchestrationStore, LeaseGrant, utc_now
from .projection import SessionProjection, SessionProjectionEngine, PROJECTION_REDUCER_VERSION
from .risk import RiskAssessment, RiskAssessmentService
from .tool_contracts import RuntimeClassification, ToolName
from .tool_policy import product_agent_policy, subagent_policy
from .tool_runtime import ForgeXToolRuntime, ProductRuntimeLimits, ToolRuntimeResult


TERMINAL_STATUSES = frozenset({"completed", "completed_with_warning", "failed", "cancelled", "blocked", "timed_out"})
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
        self.jobs = AgentJobStore(store)
        from .intake import AgentIntakeStore
        self.intakes = AgentIntakeStore(store)
        self.auto_recovery_enabled = os.getenv("FORGEX_ENABLE_SAFE_AUTO_RECOVERY", "1") == "1"
        self._closing = False
        self._attempt_clocks = {}
        self._retry_tasks = {}
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
        self._checkpoint_payloads: dict[str, dict[str, object]] = {}
        self._cancel_locks: dict[str, asyncio.Lock] = {}
        self._cancelling_runs: set[str] = set()
        self._recovery_locks: dict[str, asyncio.Lock] = {}
        self._lock = threading.RLock()
        self._run_listeners: list[object] = []
        self._persistence_gate = asyncio.Lock()
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
        reserved_run_id: str | None = None,
        resume_checkpoint: Mapping[str, object] | None = None,
        job_id: str | None = None,
        execution_profile: str = "standard",
    ) -> Any:
        workspace = Path(active_workspace_root).resolve(strict=True)
        run_id = reserved_run_id or f"agent-run-{uuid.uuid4().hex}"
        if run_id in self._runs:
            return self._runs[run_id]
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
        contract = (active_workspace or {}).get("task_contract")
        if isinstance(contract, Mapping):
            if contract.get("project_id") != project_id or contract.get("workspace_root") != str(workspace) or contract.get("session_id") != session_id:
                raise ValueError("TASK_WORKSPACE_BINDING_MISMATCH")
            if session_id is None:
                from .task_contract import bind_task_session
                contract = bind_task_session(contract, session_key)
                active_workspace = {**(active_workspace or {}), "task_contract": contract}
            run.task_contract = dict(contract)
        nodes = self._initial_graph(autonomy)
        lease = self.store.acquire_lease(
            session_key,
            run_id,
            ttl_seconds=self._lease_ttl_seconds,
        )
        job = self.jobs.attach(job_id, run_id) if job_id else self.jobs.create(
            project_id=project_id, session_id=session_key, workspace_root=workspace,
            run_id=run_id, execution_profile=execution_profile)
        run.job_id = job["job_id"]
        intake_id = (active_workspace or {}).get("agent_intake_id")
        if intake_id and not retry_of_run_id:
            job = self.intakes.attach(intake_id, job["job_id"])
        run.job = self.jobs.public(job)
        if run.task_contract and session_id:
            self.memory.remember_choices(project_id=project_id,session_id=session_id,
                contract=run.task_contract,board_id=board_type)
        import time
        self._attempt_clocks[run_id] = (time.monotonic(),job["consumed"]["elapsed_seconds"])
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
                "resume_checkpoint": dict(resume_checkpoint) if resume_checkpoint else None,
            }
        self._persist_graph(run)
        self._event(run, "runtime.queued", stage="planning", message="Action run queued")

        async def execute_workflow() -> None:
            # Existing firmware edits use the same asynchronous loop as generation
            # and repair. Read-only and legacy overlay callers keep their seam.
            contract = (active_workspace or {}).get("task_contract") or {}
            shared_edit = (autonomy in {"staged_changes", "review_changes"}
                and (bool(contract) or (active_workspace or {}).get("agent_requires_build") is True) and ((workspace / "platformio.ini").is_file()
                    or (active_workspace or {}).get("agent_requires_build") is True)
                and not {"build", "execute"}.intersection(contract.get("prohibited_actions", ()))
                and ((active_workspace or {}).get("agent_requires_build") is True
                     or os.getenv("FORGEX_ENABLE_SHARED_EDIT_LOOP", "1") == "1"))
            if autonomy in AUTONOMOUS_AUTONOMIES or shared_edit:
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
                if not self._closing:
                    self._schedule_retry(run.job_id)

        async def execute_owned() -> None:
            # Create the inner execution coroutine only after the scheduled task
            # actually starts. If the outer task is cancelled before its first
            # timeslice, no orphan coroutine is left un-awaited.
            await owned_operation(run_id, execute())

        self._tasks[run_id] = asyncio.create_task(execute_owned(), name=f"orchestrator:{run_id}")
        return run

    async def _run_with_lease_heartbeat(self, run_id: str, operation: Awaitable[None]) -> None:
        """Keep a session lease live and stop work immediately if fencing is lost."""

        stop = threading.Event()
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
            if operation_task in done and heartbeat_task not in done:
                # A worker may fence the run just before the operation returns.
                # Join it before deciding completion so that failure is retained.
                stop.set()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
                done.add(heartbeat_task)
            if heartbeat_task in done:
                heartbeat_error = heartbeat_task.exception()
                if heartbeat_error is not None:
                    self._runs[run_id].cancel_event.set()
                    self.policy_engine.fence_run(run_id)
                    if self.cancel_executor:
                        try:
                            await asyncio.wait_for(self.cancel_executor(self._runs[run_id]), timeout=5.0)
                        except Exception:
                            logger.exception("Lease-loss cleanup could not be confirmed for %s", run_id)
                    self.store.interrupt_operations(run_id)
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

    async def _lease_heartbeat(self, run_id: str, stop: threading.Event) -> None:
        # The timer and renewal both live in the worker. A slow synchronous
        # call on the application loop must not expire an otherwise live owner.
        worker = asyncio.create_task(asyncio.to_thread(self._renew_lease_until_stopped, run_id, stop))
        observed_loop = asyncio.get_running_loop()
        try:
            while not worker.done():
                started = observed_loop.time()
                await asyncio.wait({worker}, timeout=self._lease_heartbeat_interval_seconds)
                lag_ms = round(max(0, observed_loop.time() - started - self._lease_heartbeat_interval_seconds) * 1000)
                if lag_ms >= 100:
                    _emit_orchestrator_diagnostic(self._runs[run_id], {"event_type": "runtime.event_loop.slow",
                        "loop_delay_ms": lag_ms}, session_id=self._session_for_run[run_id])
            await worker
        finally:
            stop.set()
            await asyncio.gather(worker, return_exceptions=True)

    def _renew_lease_until_stopped(self, run_id: str, stop: threading.Event) -> None:
        try:
            self._renew_session_lease(run_id, stop)
        except Exception as exc:
            # Fence capabilities immediately, even if the application loop is
            # still blocked. These two operations are thread-safe.
            self._runs[run_id].cancel_event.set()
            self.policy_engine.fence_run(run_id)
            raise AgentSessionLeaseLost(str(exc) or type(exc).__name__) from exc

    def _renew_session_lease(self, run_id: str, stop: threading.Event) -> None:
        while not stop.is_set():
            with self._lock:
                grant = self._leases.get(run_id)
            if grant is None:
                raise AgentSessionLeaseLost("AGENT_SESSION_LEASE_MISSING")
            try:
                renewed = self.store.renew_lease(
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
            stop.wait(self._lease_heartbeat_interval_seconds)

    async def retry(self, run_id: str) -> Any:
        async with self._recovery_locks.setdefault(run_id, asyncio.Lock()):
            return await self._retry_locked(run_id)

    async def _retry_locked(self, run_id: str, *, request_key: str = "retry", checkpoint: Mapping[str, object] | None = None,
                            selection: Mapping[str, str] | None = None, verify_only: bool = False) -> Any:
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
            replacement_id = self.store.recovery_replacement(run_id) or getattr(run, "recovered_by_run_id", None)
            if replacement_id and replacement_id in self._runs:
                return self._runs[replacement_id]
            self.store.require_healthy_journal(self._session_for_run[run_id])
            if run.change_set_id and self.change_service.get(run.change_set_id).status == "discarded":
                raise ValueError("AGENT_RECOVERY_DRAFT_DISCARDED")
            if any(item["status"] in {"started", "unknown"} for item in self.store.operations(run_id)):
                raise ValueError("AGENT_RECOVERY_OUTCOME_UNKNOWN")
            recoverable_classification = (
                run.classification == "AGENT_RUNTIME_INTERRUPTED"
                or run.classification in {"provider_smoke_toolplan_unsafe", "provider_smoke_call_count_invalid"}
                or run.status == "paused_model_unavailable"
                or run.classification == "MODEL_RETIRED"
                or (checkpoint is not None and run.classification in {"AGENT_PROVIDER_BUDGET_EXCEEDED","AGENT_JOB_BUDGET_EXHAUSTED","AGENT_RUNTIME_TOOL_BUDGET_EXCEEDED","AGENT_RUNTIME_LIMIT_EXCEEDED"})
                or (run.status in {"failed", "blocked", "timed_out", "completed_with_warning", "cancelled"} and bool(run.change_set_id)
                    and self.change_service.get(run.change_set_id).status == "draft")
            )
            if not bool(getattr(run, "recoverable", False)) or not recoverable_classification:
                raise ValueError("AGENT_RUNTIME_RUN_NOT_RECOVERABLE")
            if run.classification == "MODEL_RETIRED" and not verify_only and selection is None:
                raise ValueError("MODEL_RETIRED")
            context = dict(self._recovery_context.get(run_id, {}))
            workspace_error = self._recovery_workspace_error(run_id)
            if workspace_error:
                if workspace_error == "AGENT_RESUME_WORKSPACE_UNAVAILABLE":
                    self._record_unavailable_workspace(run)
                raise ValueError(workspace_error)
            job = self.jobs.get(run.job_id)
            if not verify_only and job.get("blocking_reason") == "AGENT_JOB_TIME_RESERVE_REQUIRED":
                raise ValueError("AGENT_JOB_TIME_RESERVE_REQUIRED")
            if verify_only and job["limits"]["elapsed_seconds"] - job["consumed"]["elapsed_seconds"] <= 5:
                raise ValueError("AGENT_JOB_TIME_RESERVE_REQUIRED")
            budget_dimensions = {"tool_calls_used", "elapsed_seconds"} if verify_only else set(job["limits"])
            if any(job["consumed"][key] >= job["limits"][key] for key in budget_dimensions):
                raise ValueError("AGENT_JOB_BUDGET_EXHAUSTED")
            if not context:
                raise ValueError("AGENT_RUNTIME_RECOVERY_CONTEXT_MISSING")
            from .task_contract import repair_task_contract
            active = dict(context.get("active_workspace") or {})
            if active.get("task_contract"):
                # Older direct API runs had an anonymous contract even though
                # their durable session was generated from this exact run ID.
                # Bind only that legacy case; named-session mismatches still fail.
                if (active["task_contract"].get("session_id") is None
                        and self._session_for_run[run_id] == f"run:{run_id}"):
                    from .task_contract import bind_task_session
                    active["task_contract"] = bind_task_session(active["task_contract"], self._session_for_run[run_id])
                active["task_contract"] = repair_task_contract(active["task_contract"],
                    workspace=Path(str(context["workspace_root"])))
                context["active_workspace"] = active
            if run.classification in {"provider_smoke_toolplan_unsafe", "provider_smoke_call_count_invalid"}:
                # Upgrade an explicit retry of the old keyword-routing incident
                # to today's requirement checks without changing its journal.
                from .task_contract import create_task_contract
                active = dict(context.get("active_workspace") or {})
                if "task_contract" not in active and run.provider_id not in {"fake_planner", "verified_template"} and run.autonomy not in {"read_only", "plan_only"}:
                    active["task_contract"] = create_task_contract(str(context["instruction"]),
                        project_id=run.project_id, session_id=self._session_for_run[run_id],
                        workspace=Path(str(context["workspace_root"])),
                        intent=str(active.get("agent_turn_intent") or "generate"),
                        prohibited=active.get("agent_prohibited_actions", ()))
                context["active_workspace"] = active
            fingerprint = hashlib.sha256(json.dumps({"run_id": run_id, "checkpoint": checkpoint}, sort_keys=True).encode()).hexdigest()
            if selection or verify_only:
                fingerprint = hashlib.sha256(json.dumps({"run_id": run_id, "checkpoint": checkpoint,
                    "selection": selection, "verify_only": verify_only}, sort_keys=True).encode()).hexdigest()
            replacement_id = self.store.reserve_recovery(run_id, request_key, fingerprint)
            self.store.transfer_unacknowledged_guidance(run_id, replacement_id)
            replacement_workspace = dict(context.get("active_workspace") or {})
            output_recovery = context.get("model_output_recovery")
            if not output_recovery and getattr(run, "initial_failure_code", None) == "MODEL_OUTPUT_TRUNCATED":
                output_recovery = {"output_floor": max(8192, int(getattr(run, "requested_output_tokens", None) or 8192)),
                                   "prefer_small_actions": True}
            if output_recovery and not selection:
                replacement_workspace["agent_output_recovery"] = dict(output_recovery)
            elif selection:
                replacement_workspace["agent_output_recovery"] = {"output_floor": 8192, "prefer_small_actions": False}
            replacement_workspace.pop("agent_verify_saved_draft", None)
            continuation_autonomy = str(replacement_workspace.pop("agent_resume_autonomy", None) or run.autonomy)
            if verify_only:
                replacement_workspace["agent_verify_saved_draft"] = True
                replacement_workspace["agent_resume_autonomy"] = continuation_autonomy

        try:
            replacement = await self.start_run(
                project_id=run.project_id,
                active_workspace_root=str(context["workspace_root"]),
                instruction=str(context["instruction"]),
                provider_id=selection["provider_id"] if selection else run.provider_id,
                model_id=selection["model_id"] if selection else run.model_id,
                fallback_provider_id=(
                    str(context["fallback_provider_id"])
                    if context.get("fallback_provider_id")
                    else None
                ),
                autonomy="build_only" if verify_only else continuation_autonomy,
                active_workspace=replacement_workspace,
                board_port=run.flash_port,
                board_type=run.flash_board_type,
                environment=run.flash_environment,
                start_monitor_after_flash=run.start_monitor_after_flash,
                session_id=self._session_for_run[run_id],
                explicit_edit_authorized=bool(context.get("explicit_edit_authorized")),
                retry_of_run_id=run_id,
                reserved_run_id=replacement_id,
                resume_checkpoint=checkpoint,
                job_id=run.job_id,
            )
        except Exception:
            with self._lock:
                run.recoverable = True
                run.recovery_actions = ["retry"]
                self._persist_run(run)
            raise

        with self._lock:
            run.recovered_by_run_id = replacement.run_id
            run.recoverable = False
            run.recovery_actions = []
            self._persist_run(run)
        self.store.complete_recovery(run_id, replacement.run_id)
        self._event(run, "runtime.retried", status=run.status, replacement_run_id=replacement.run_id)
        return replacement

    def _recovery_checkpoint(self, run_id: str):
        checkpoint = self.store.latest_run_checkpoint(run_id)
        # The event and projection commit atomically. Rebuild a derived checkpoint
        # if the process stopped between that transaction and checkpoint storage.
        sealed = self._recovery_context.get(run_id, {}).get("sealed_resume_boundary")
        if isinstance(sealed, dict):
            boundaries = [event for event in self.store.snapshot_events(self._session_for_run[run_id], run_id=run_id)
                          if event.get("event_type") in {"runtime.admitted", "turn.settled", "tool.checkpoint"}]
            event = boundaries[-1] if boundaries else None
            if event and event.get("event_type") == sealed.get("event_type") and int(event.get("turn") or 0) == int(sealed.get("turn") or 0):
                if checkpoint is None or int(checkpoint["session_seq"]) < int(event["sequence"]):
                    self.store.require_healthy_journal(self._session_for_run[run_id])
                    checkpoint = self.store.save_run_checkpoint(run_id, self._session_for_run[run_id],
                        turn=0 if event["event_type"] == "runtime.admitted" else int(event["turn"]),
                        session_seq=int(event["sequence"]), payload={"resume": sealed["resume"]})
        if checkpoint:
            return checkpoint
        # Older continuations did not seal admission. Use only their verified
        # parent seal, and only when this attempt executed no tools/effects.
        run = self._runs[run_id]
        parent_id = getattr(run, "retry_of_run_id", None)
        inherited = self._recovery_context.get(run_id, {}).get("resume_checkpoint")
        parent = self._runs.get(parent_id)
        if not parent or not inherited or parent.job_id != run.job_id or self._session_for_run[parent_id] != self._session_for_run[run_id]:
            return None
        if self.store.operations(run_id) or any(
            str(event.get("event_type", "")).startswith(("tool.", "build.", "turn.settled", "changeset."))
            for event in self.store.snapshot_events(self._session_for_run[run_id], run_id=run_id)
        ):
            return None
        parent_checkpoint = self.store.latest_run_checkpoint(parent_id)
        if parent_checkpoint and parent_checkpoint.get("checkpoint_id") == inherited.get("checkpoint_id"):
            return parent_checkpoint
        return None

    def _recovery_workspace_error(self, run_id: str) -> str | None:
        workspace = self._recovery_context.get(run_id, {}).get("workspace_root")
        if not isinstance(workspace, (str, Path)) or not workspace:
            return "AGENT_RUNTIME_RECOVERY_CONTEXT_MISSING"
        try:
            if not Path(workspace).resolve(strict=True).is_dir():
                return "AGENT_RESUME_WORKSPACE_UNAVAILABLE"
        except (OSError, ValueError):
            return "AGENT_RESUME_WORKSPACE_UNAVAILABLE"
        return None

    def _record_unavailable_workspace(self, run: Any) -> None:
        """Pause the current job without altering its history, seal or budget."""
        from .jobs import _finish_wait
        changed = False
        def pause(value):
            nonlocal changed
            if value["current_run_id"] != run.run_id:
                return
            changed = value.get("blocking_reason") != "AGENT_RESUME_WORKSPACE_UNAVAILABLE"
            _finish_wait(value)
            value.update(blocking_reason="AGENT_RESUME_WORKSPACE_UNAVAILABLE", retry_due_at=None)
            if value["status"] not in {"completed", "completed_with_warning", "cancelled"}:
                value["status"] = "blocked"
        if run.job_id:
            self.jobs.mutate(run.job_id, pause)
        if changed:
            logger.info("Recovery paused: project folder unavailable run_id=%s; sealed checkpoint preserved", run.run_id)

    def recovery_info(self, run_id: str, *, run=None) -> dict[str, object]:
        run = self._runs[run_id] if run is None else run
        try:
            checkpoint = self._recovery_checkpoint(run_id)
        except ValueError:
            return self._recovery_action_info(run, {"eligible": False, "blocking_reason": "AGENT_CHECKPOINT_CORRUPT", "checkpoint_id": None, "uncertain_operations": []}, None)
        unknown = [item for item in self.store.operations(run_id) if item["status"] in {"started", "unknown"}]
        stage_paths = ()
        reason = None
        if not self.store.verify_journal(self._session_for_run[run_id])["healthy"]:
            reason = "AGENT_JOURNAL_QUARANTINED"
        elif unknown or run.classification == "AGENT_RECOVERY_OUTCOME_UNKNOWN":
            reason = "AGENT_RECOVERY_OUTCOME_UNKNOWN"
        elif self._recovery_context.get(run_id, {}).get("retention_conflict"):
            reason = str(self._recovery_context[run_id]["retention_conflict"]["code"])
        elif not run.recoverable:
            reason = "AGENT_RUNTIME_RUN_NOT_RECOVERABLE"
        elif run.change_set_id and self.change_service.get(run.change_set_id).status == "discarded":
            reason = "AGENT_RECOVERY_DRAFT_DISCARDED"
        elif not checkpoint or not isinstance(checkpoint.get("resume"), dict):
            reason = "AGENT_RESUME_CHECKPOINT_UNAVAILABLE"
        else:
            saved = checkpoint["resume"]
            reason = self._recovery_workspace_error(run_id)
            from .generation_controller import controller_state_supported
            if reason is None and not controller_state_supported(saved.get("generation_controller")):
                reason = "AGENT_CONTROLLER_VERSION_UNSUPPORTED"
            if reason is None:
                try:
                    workspace = self._recovery_context[run_id]["workspace_root"]
                    if self.change_service.inspect_workspace(workspace).revision != saved["workspace_revision"]:
                        reason = "AGENT_RESUME_WORKSPACE_CHANGED"
                    stage = Path(saved["stage_path"]).absolute()
                    if reason is None:
                        if stage.parent != self.change_service.staging_root:
                            reason = "AGENT_RESUME_STAGE_CHANGED"
                        else:
                            stage_snapshot = self.change_service.inspect_workspace(stage)
                            stage_paths = tuple(stage_snapshot.files)
                            if stage_snapshot.revision != saved["stage_revision"]:
                                reason = "AGENT_RESUME_STAGE_CHANGED"
                except (OSError, ValueError, KeyError):
                    reason = self._recovery_workspace_error(run_id) or "AGENT_RESUME_STAGE_UNAVAILABLE"
        return self._recovery_action_info(run, {"eligible": reason is None, "blocking_reason": reason,
                "checkpoint_id": checkpoint.get("checkpoint_id") if checkpoint else None,
                "uncertain_operations": [{key: item[key] for key in ("operation_id", "kind", "status")} for item in unknown]}, checkpoint, stage_paths=stage_paths)

    def _recovery_action_info(self, run, info, checkpoint, *, stage_paths=()):
        """One permission/budget projection used by the API and its controls."""
        from backend.contracts.recovery import RecoveryActions
        job = self.jobs.get(run.job_id) if run.job_id else None
        context = self._recovery_context.get(run.run_id, {})
        draft = self.change_service.get(run.change_set_id) if run.change_set_id else None
        continuation_reason = info["blocking_reason"]
        verification_reason = info["blocking_reason"]
        if job and continuation_reason is None:
            if any(job["consumed"][key] >= limit for key, limit in job["limits"].items()):
                continuation_reason = "AGENT_JOB_BUDGET_EXHAUSTED"
            elif job.get("blocking_reason") == "AGENT_JOB_TIME_RESERVE_REQUIRED":
                continuation_reason = "AGENT_JOB_TIME_RESERVE_REQUIRED"
        if verification_reason is None:
            if not draft or draft.status != "draft":
                verification_reason = "AGENT_RECOVERY_DRAFT_UNAVAILABLE"
            elif (run.build_result or {}).get("error_code") in {"BUILD_CONFIGURATION_MISSING", "BUILD_CONFIGURATION_INVALID"} or (
                    (run.build_result or {}).get("error_code") == "DEPENDENCY_VALIDATION_FAILED"
                    and "missing platformio.ini" in str((run.build_result or {}).get("message", ""))):
                verification_reason = "AGENT_RECOVERY_DRAFT_INCOMPLETE"
            elif not self.jobs.is_trusted(run.project_id, context.get("workspace_root")):
                verification_reason = "AGENT_PROJECT_TRUST_REQUIRED"
            elif job and (job["limits"]["elapsed_seconds"] - job["consumed"]["elapsed_seconds"] <= 5
                          or job["consumed"]["tool_calls_used"] >= job["limits"]["tool_calls_used"]):
                verification_reason = "AGENT_JOB_BUDGET_EXHAUSTED"
            else:
                from .deliverables import has_firmware_source
                if "platformio.ini" in stage_paths and not has_firmware_source(stage_paths):
                    verification_reason = "AGENT_RECOVERY_DRAFT_INCOMPLETE"
        inspect_reason = None if draft and draft.status != "discarded" else "AGENT_RECOVERY_DRAFT_UNAVAILABLE"
        action = lambda reason: {"allowed": reason is None, "blocking_reason": reason}
        actions = RecoveryActions.model_validate({"inspect": action(inspect_reason),
            "continue_generation": action(continuation_reason), "verify_locally": action(verification_reason)})
        return {**info, "actions": actions.model_dump(mode="json")}

    async def resume(self, run_id: str, *, checkpoint_id: str, idempotency_key: str,
                     provider_id: str | None = None, model_id: str | None = None, automatic: bool = False) -> Any:
        async with self._recovery_locks.setdefault(run_id, asyncio.Lock()):
            previous = self.store.recovery_replacement(run_id)
            checkpoint = self._recovery_checkpoint(run_id)
            if not checkpoint or checkpoint.get("checkpoint_id") != checkpoint_id:
                raise ValueError("AGENT_RESUME_CHECKPOINT_CHANGED")
            fingerprint = hashlib.sha256(json.dumps({"run_id": run_id, "checkpoint": checkpoint}, sort_keys=True).encode()).hexdigest()
            selection = None
            if provider_id is not None or model_id is not None:
                if not provider_id or not model_id:
                    raise ValueError("AGENT_RECOVERY_SELECTION_INVALID")
                selected = self.provider_registry.resolve(provider_id, model_id=model_id,
                    session_id=self._session_for_run[run_id])
                router = getattr(selected, "router", None)
                if router is not None:
                    from backend.model_router.models import ModelRequest
                    from backend.services.llm_service import LLMError
                    try:
                        router.resolver.resolve(ModelRequest(prompt="Continue saved work", task_type="planning",
                            provider_id=provider_id, model_id=model_id, allow_fallback=False))
                    except LLMError as exc:
                        raise ValueError(str(exc.code)) from exc
                selection = {"provider_id": provider_id, "model_id": model_id}
                fingerprint = hashlib.sha256(json.dumps({"run_id": run_id, "checkpoint": checkpoint,
                    "selection": selection, "verify_only": False}, sort_keys=True).encode()).hexdigest()
            if previous and previous in self._runs:
                self.store.reserve_recovery(run_id, idempotency_key, fingerprint)
                return self._runs[previous]
            info = self.recovery_info(run_id)
            action = info["actions"]["continue_generation"]
            if not action["allowed"]:
                raise ValueError(str(action["blocking_reason"]))
            self.store.invalidate_pending_approvals(run_id, "explicit_resume")
            if not automatic and not previous:
                self.jobs.begin_manual_recovery(self.get_run(run_id).job_id)
            replacement = await self._retry_locked(run_id, request_key=idempotency_key, checkpoint=checkpoint, selection=selection)
            self._event(replacement, "runtime.resumed", source_run_id=run_id, checkpoint_id=checkpoint_id)
            if selection:
                self._event(replacement, "recovery.model_selected", source_run_id=run_id,
                    provider_id=provider_id, model_id=model_id, authorization="explicit_selection")
            return replacement

    async def verify_saved_draft(self, run_id: str, *, idempotency_key: str) -> Any:
        """Compile the sealed draft locally without requesting another model turn."""
        async with self._recovery_locks.setdefault(run_id, asyncio.Lock()):
            run = self.get_run(run_id)
            checkpoint = self._recovery_checkpoint(run_id)
            fingerprint = hashlib.sha256(json.dumps({"run_id": run_id, "checkpoint": checkpoint,
                "selection": None, "verify_only": True}, sort_keys=True).encode()).hexdigest()
            previous = self.store.recovery_replacement(run_id)
            if previous and previous in self._runs:
                self.store.reserve_recovery(run_id, idempotency_key, fingerprint)
                return self._runs[previous]
            info = self.recovery_info(run_id)
            action = info["actions"]["verify_locally"]
            if not action["allowed"]:
                raise ValueError(str(action["blocking_reason"]))
            self.store.invalidate_pending_approvals(run_id, "verify_saved_draft")
            replacement = await self._retry_locked(run_id, request_key=idempotency_key,
                checkpoint=checkpoint, verify_only=True)
            self._event(replacement, "runtime.resumed", source_run_id=run_id,
                checkpoint_id=checkpoint["checkpoint_id"], verification_only=True)
            return replacement

    async def discard_draft(self, run_id: str) -> Any:
        async with self._recovery_locks.setdefault(run_id, asyncio.Lock()):
            run = self.get_run(run_id)
            if run.status not in TERMINAL_STATUSES and run.status != "paused_model_unavailable":
                raise ValueError("AGENT_RUNTIME_RUN_NOT_SETTLED")
            if not run.change_set_id:
                raise ValueError("AGENT_RECOVERY_DRAFT_UNAVAILABLE")
            draft = self.change_service.get(run.change_set_id)
            if draft.status not in {"draft", "discarded"}:
                raise ValueError("AGENT_RECOVERY_DRAFT_UNAVAILABLE")
            lease = self.store.acquire_lease(self._session_for_run[run_id], run_id, ttl_seconds=self._lease_ttl_seconds)
            self._leases[run_id] = lease
            try:
                if draft.status == "draft":
                    self.change_service.discard(draft.change_set_id)
                run.recoverable = False
                run.recovery_actions = []
                self._event(run, "recovery.draft.discarded", message="Recovery draft explicitly discarded")
                self._persist_run(run)
                return run
            finally:
                self._leases.pop(run_id, None)
                self.store.release_lease(lease)

    async def recover_jobs(self, job_id: str | None = None) -> None:
        """Startup-only recovery, never a new authorization or budget reset."""
        if not self.auto_recovery_enabled or self._closing:
            return
        for job in (self.jobs.get(job_id),) if job_id else self.jobs.list():
            if job["recovery_policy"] != "safe_auto" or job.get("paused_by") in {"user", "budget"}:
                continue
            run_id = job["current_run_id"]
            run = self._runs.get(run_id)
            if not run or not (run.classification == "AGENT_RUNTIME_INTERRUPTED" or
                (run.status == "paused_model_unavailable" and run.classification in TRANSIENT_PROVIDER_ERRORS)):
                continue
            if self._recovery_workspace_error(run_id) == "AGENT_RESUME_WORKSPACE_UNAVAILABLE":
                self._record_unavailable_workspace(run)
                continue
            if job.get("retry_due_at"):
                from datetime import datetime, timezone
                due = datetime.fromisoformat(job["retry_due_at"].replace("Z","+00:00"))
                if due > datetime.now(timezone.utc):
                    self._schedule_retry(job["job_id"])
                    continue
            if any(job["consumed"][key] >= limit for key, limit in job["limits"].items()):
                job.update(status="paused", paused_by="budget", blocking_reason="AGENT_JOB_BUDGET_EXHAUSTED")
                self.jobs.save(job)
                continue
            from .jobs import _outage_block
            outage_reason = _outage_block(job)
            if outage_reason:
                self.jobs.mutate(job["job_id"], lambda value: value.update(blocking_reason=outage_reason))
                continue
            if run.status == "paused_model_unavailable" and (
                job["limits"]["elapsed_seconds"] - job["consumed"]["elapsed_seconds"]
                < recovery_time_reserve(job["limits"])
            ):
                job.update(status="paused", paused_by="budget",
                    blocking_reason="AGENT_JOB_TIME_RESERVE_REQUIRED", retry_due_at=None)
                self.jobs.save(job)
                continue
            if job.get("auto_recovery_count", 0) >= 3:
                job.update(blocking_reason="AGENT_AUTO_RECOVERY_LIMIT", status="blocked")
                self.jobs.save(job)
                continue
            if run.autonomy == "build_then_confirm_flash":
                job.update(blocking_reason="AGENT_HARDWARE_REQUIRES_MANUAL_RESUME", status="blocked")
                self.jobs.save(job)
                continue
            # Generation has no local build authority. Each execution boundary
            # checks project trust; permission changes do not dispatch work.
            saved_checkpoint = self._recovery_checkpoint(run_id)
            next_operation = (saved_checkpoint or {}).get("resume", {}).get("next_operation")
            context = self._recovery_context.get(run_id, {})
            if next_operation != "generation" and not self.jobs.is_trusted(run.project_id, context.get("workspace_root")):
                self.jobs.mutate(job["job_id"], lambda value: value.update(status="blocked", blocking_reason="AGENT_PROJECT_TRUST_REQUIRED"))
                continue  # Older checkpoints have no operation-specific authority.
            info = self.recovery_info(run_id)
            if not info["eligible"]:
                job.update(blocking_reason=info["blocking_reason"], status="blocked")
                self.jobs.save(job)
                continue
            try:
                if not self.jobs.claim_recovery(job["job_id"], uuid.uuid4().hex):
                    continue
                replacement = await self.resume(run_id, checkpoint_id=info["checkpoint_id"],
                    idempotency_key="auto:" + info["checkpoint_id"], automatic=True)
                self._event(replacement, "recovery.started", source_run_id=run_id, automatic=True)
            except (ValueError, OSError):
                self.jobs.mutate(job["job_id"], lambda value: value.update(status="blocked", blocking_reason="AGENT_AUTO_RECOVERY_BLOCKED"))

    def project_trust_changed(self, project_id: str) -> None:
        """Refresh eligibility without starting work or resetting an outage."""
        for job in self.jobs.list(project_id):
            run = self._runs.get(job["current_run_id"])
            if run is None:
                continue
            if job.get("blocking_reason") == "AGENT_PROJECT_TRUST_REQUIRED" and self.jobs.is_trusted(project_id, job["workspace_root"]):
                self.jobs.mutate(job["job_id"], lambda value: value.update(blocking_reason=None,
                    status="paused" if value.get("paused_by") else run.status))
            self._event(run, "job.permissions.changed", message="Project build permission updated; saved work is preserved.")

    def _schedule_retry(self, job_id):
        if not self.auto_recovery_enabled or self._closing:
            return
        job = self.jobs.get(job_id)
        from .jobs import _outage_block
        if job["recovery_policy"] != "safe_auto" or job.get("paused_by") in {"user","budget"} or not job.get("retry_due_at") or job.get("auto_recovery_count",0) >= 3 or _outage_block(job):
            return
        prior = self._retry_tasks.get(job_id)
        if prior and not prior.done():
            return
        async def wake():
            from datetime import datetime, timezone
            due = datetime.fromisoformat(job["retry_due_at"].replace("Z","+00:00"))
            await asyncio.sleep(max(0,(due-datetime.now(timezone.utc)).total_seconds()))
            # A replacement may itself need a later wake; do not let this
            # currently executing wake suppress its successor.
            if self._retry_tasks.get(job_id) is asyncio.current_task():
                self._retry_tasks.pop(job_id, None)
            await self.recover_jobs(job_id)
        self._retry_tasks[job_id] = asyncio.create_task(wake(),name="agent-retry:"+job_id)

    async def pause_job(self, job_id: str, *, reason="user") -> dict:
        job = self.jobs.get(job_id)
        run = self.get_run(job["current_run_id"])
        if run.status in TERMINAL_STATUSES and run.classification != "AGENT_RUNTIME_INTERRUPTED":
            raise ValueError("AGENT_JOB_ALREADY_SETTLED")
        job.update(paused_by=reason, status="paused")
        self.jobs.save(job)
        if run.status not in TERMINAL_STATUSES:
            await self.cancel(run.run_id, pause_reason=reason)
        if run.classification != "AGENT_RECOVERY_OUTCOME_UNKNOWN":
            run.status = "failed"
            run.classification = "AGENT_RUNTIME_INTERRUPTED"
            run.recoverable = bool(self._recovery_context.get(run.run_id))
            run.recovery_actions = ["resume"]
            self._event(run, "job.paused", reason=reason)
            self._event(run, "runtime.interrupted", status="failed", message="Task paused; settled work was saved", effect_replay_allowed=False)
            self._persist_run(run)
        return self.jobs.public(self.jobs.get(job_id))

    async def resume_job(self, job_id: str, *, idempotency_key: str, provider_id: str | None = None,
                         model_id: str | None = None, checkpoint_id: str | None = None) -> Any:
        job = self.jobs.get(job_id)
        if bool(provider_id) != bool(model_id):
            raise ValueError("AGENT_RECOVERY_SELECTION_INVALID")
        selection = {"provider_id": provider_id, "model_id": model_id} if provider_id else None
        run_id = self.jobs.reserve_resume(job_id,idempotency_key,selection,checkpoint_id)
        replacement = self.store.recovery_replacement(run_id)
        if replacement and replacement in self._runs:
            return self._runs[replacement]
        info = self.recovery_info(run_id)
        if checkpoint_id and checkpoint_id != info["checkpoint_id"]:
            raise ValueError("AGENT_RESUME_CHECKPOINT_CHANGED")
        if any(job["consumed"][key] >= limit for key, limit in job["limits"].items()):
            raise ValueError("AGENT_JOB_BUDGET_EXHAUSTED")
        if job.get("blocking_reason") == "AGENT_JOB_TIME_RESERVE_REQUIRED":
            raise ValueError("AGENT_JOB_TIME_RESERVE_REQUIRED")
        if not info["eligible"]:
            raise ValueError(str(info["blocking_reason"]))
        job.update(paused_by=None, blocking_reason=None)
        self.jobs.save(job)
        return await self.resume(run_id, checkpoint_id=info["checkpoint_id"], idempotency_key=idempotency_key,
                                 provider_id=provider_id, model_id=model_id)

    def get_run(self, run_id: str) -> Any:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(run_id)
            run = self._runs[run_id]
            if getattr(run, "job_id", None):
                run.job = self.jobs.public(self.jobs.get(run.job_id))
            if hasattr(run, "recovery"):
                conflict = self._recovery_context.get(run_id, {}).get("retention_conflict")
                run.recovery = self.recovery_info(run_id) if run.recoverable or conflict else None
            return run

    def run_snapshot(self, run_id: str) -> dict[str, object]:
        """Public reads never serialize an in-flight mutable run."""
        payload, job = self.store.run_snapshot(run_id)
        public = {key: value for key, value in payload.items() if not key.startswith("_")}
        if job is not None:
            public["job"] = self.jobs.public(job)
        live = self._runs.get(run_id)
        if live is not None:
            view = copy.copy(live)
            for name in ("status", "classification", "recoverable", "change_set_id", "job_id", "build_result"):
                if name in payload:
                    setattr(view, name, copy.deepcopy(payload[name]))
            conflict = self._recovery_context.get(run_id, {}).get("retention_conflict")
            public["recovery"] = self.recovery_info(run_id, run=view) if view.recoverable or conflict else None
        return public

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
        checkpoint = self.store.projection_checkpoint(session_id, "default")
        state = SessionProjection.from_dict(checkpoint["payload"]) if checkpoint and checkpoint["reducer_version"] == PROJECTION_REDUCER_VERSION else SessionProjection(session_id=session_id)
        previous_sequence = state.session_seq
        for event in self.store.snapshot_events(session_id, after_sequence=state.session_seq, observation=True):
            self.projections.apply_event(state, event)
        if state.session_seq != previous_sequence or checkpoint is None:
            self.store.save_projection_checkpoint(session_id, "default", session_seq=state.session_seq,
                reducer_version=PROJECTION_REDUCER_VERSION, payload=state.to_dict())
        # Transient frames are applied to this response, never to a durable cursor.
        with self._lock:
            run_ids = [
                run_id for run_id, owner_session in self._session_for_run.items()
                if owner_session == session_id
            ]
            for run_id in run_ids:
                for frame in self._transient_events.get(run_id, ()):
                    self.projections.apply_transient(state, frame)
        payload = state.to_dict()
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

    async def cancel(self, run_id: str, *, pause_reason: str | None = None) -> Any:
        async with self._cancel_locks.setdefault(run_id, asyncio.Lock()):
            return await self._cancel_locked(run_id, pause_reason=pause_reason)

    async def _cancel_locked(self, run_id: str, *, pause_reason: str | None = None) -> Any:
        run = self.get_run(run_id)
        if run.status in TERMINAL_STATUSES:
            return run
        run.status = "cancelling"
        self._cancelling_runs.add(run_id)
        run.cancel_event.set()
        self._event(run, "cancellation.requested", message="Cancellation requested")
        # Fence future host capabilities before touching the active task.
        self.policy_engine.fence_run(run_id)
        cleanup_failed = False
        if self.cancel_executor is not None:
            try:
                cleanup = asyncio.create_task(self.cancel_executor(run))
                done, _ = await asyncio.wait({cleanup}, timeout=5.0)
                if done:
                    await cleanup
                else:
                    cleanup.cancel()
                    cleanup_failed = True
            except Exception:
                logger.exception("ForgeX cancellation executor failed for %s", run_id)
                cleanup_failed = True
        task = self._tasks.get(run_id)
        if task and not task.done() and task is not asyncio.current_task():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=1.5)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    done, _ = await asyncio.wait({task}, timeout=2.0)
                    if not done:
                        cleanup_failed = True
                except (asyncio.CancelledError, Exception):
                    pass
            except (asyncio.CancelledError, Exception):
                pass
        self.store.interrupt_operations(run_id)
        uncertain = cleanup_failed or any(item["status"] == "unknown" for item in self.store.operations(run_id))
        run.status = "blocked" if uncertain else ("failed" if pause_reason else "cancelled")
        run.classification = "AGENT_RECOVERY_OUTCOME_UNKNOWN" if uncertain else ("AGENT_RUNTIME_INTERRUPTED" if pause_reason else "AGENT_RUNTIME_CANCELLED")
        self._retain_sealed_checkpoint(run)
        if uncertain:
            run.recoverable = False
            run.recovery_actions = []
        if pause_reason and not uncertain:
            run.recoverable = bool(self._recovery_context.get(run_id))
            run.recovery_actions = ["resume"]
        self._cancel_active_nodes(run_id)
        self._cancelling_runs.discard(run_id)
        self._event(run, "runtime.blocked" if uncertain else ("runtime.interrupted" if pause_reason else "runtime.cancelled"), message="Cancellation needs outcome review" if uncertain else ("Task paused after safe drain" if pause_reason else "Action run cancelled after safe drain"))
        self._persist_run(run)
        return run

    def _apply_changes(self, run: Any, change_set_id: str) -> Any:
        operation_id = f"{run.run_id}:apply:{change_set_id}"
        payload = {"change_set_id": change_set_id}
        self.store.operation(operation_id, run.run_id, "workspace.apply", status="prepared", payload=payload)
        if run.cancel_event.is_set():
            raise ValueError("AGENT_RUNTIME_CANCELLED")
        self.store.operation(operation_id, run.run_id, "workspace.apply", status="started", payload=payload)
        try:
            result = self.change_service.apply(change_set_id)
        except BaseException:
            self.store.operation(operation_id, run.run_id, "workspace.apply", status="unknown", payload=payload)
            raise
        self.store.operation(operation_id, run.run_id, "workspace.apply", status="completed", payload=payload)
        return result

    def apply_pending_changes(self, run_id: str) -> Any:
        grant = self.store.acquire_lease(self._session_for_run[run_id], run_id, ttl_seconds=self._lease_ttl_seconds)
        self._leases[run_id] = grant
        try:
            return self._apply_pending_changes_impl(run_id)
        finally:
            self._leases.pop(run_id, None)
            self.store.release_lease(grant)

    def _apply_pending_changes_impl(self, run_id: str) -> Any:
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
            applied = self._apply_changes(run, change_set.change_set_id)
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
                workspace_root=workspace,
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
        if not isinstance(build.get("artifact_manifest"), Mapping):
            raise ValueError("SEALED_UPLOAD_REQUIRED")
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

    async def confirm_flash(self, run_id: str, **kwargs: object) -> Any:
        async with self._recovery_locks.setdefault(run_id, asyncio.Lock()):
            run = self.get_run(run_id)
            if run.status != "awaiting_flash_confirmation":
                raise ValueError("AGENT_RUNTIME_FLASH_CONFIRMATION_NOT_READY")
            prior = self._tasks.get(run_id)
            if prior and not prior.done():
                await asyncio.wait_for(asyncio.shield(prior), timeout=5)
            return await self._acquire_flash(run_id, **kwargs)

    async def _acquire_flash(self, run_id: str, **kwargs: object) -> Any:
        run = self.get_run(run_id)
        if run.status != "awaiting_flash_confirmation":
            raise ValueError("AGENT_RUNTIME_FLASH_CONFIRMATION_NOT_READY")
        grant = self.store.acquire_lease(self._session_for_run[run_id], run_id, ttl_seconds=self._lease_ttl_seconds)
        self._leases[run_id] = grant
        try:
            return await self._confirm_flash_impl(run_id, **kwargs)
        except BaseException:
            self.policy_engine.revoke_run(run_id)
            self._leases.pop(run_id, None)
            self.store.release_lease(grant)
            raise

    async def _confirm_flash_impl(
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
        flash_operation = f"{run_id}:flash:{run.approval_id}"
        flash_payload = _flash_binding(run, port=proposed_port, board_type=proposed_board_type,
            environment=proposed_environment, start_monitor_after_flash=proposed_monitor,
            workspace_revision=current_revision)
        self.store.operation(flash_operation, run_id, "hardware.flash", status="prepared", payload=flash_payload)
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
            await self._runtime_event_async(run, {"event_type": event.casefold(), **dict(payload)})

        async def execute() -> None:
            try:
                if run.cancel_event.is_set():
                    raise asyncio.CancelledError()
                self.store.operation(flash_operation, run_id, "hardware.flash", status="started", payload=flash_payload)
                result = await self.flash_executor(_FlashRequest(
                    run_id=run.run_id,
                    project_id=run.project_id,
                    port=run.flash_port,
                    board_type=run.flash_board_type,
                    environment=run.flash_environment,
                    start_monitor_after_flash=run.start_monitor_after_flash,
                    expected_artifact_hash=_approved_artifact_hash(run),
                    progress_callback=progress,
                    artifact_manifest=run.build_result.get("artifact_manifest") if run.build_result else None,
                ))
                run.flash_result = _safe_mapping(result.get("flash_result"))
                run.monitor_result = _safe_mapping(result.get("monitor_result"))
                run.build_result = {**(run.build_result or {}), **(_safe_mapping(result.get("build_result")) or {})}
                self.store.operation(flash_operation, run_id, "hardware.flash",
                    status="completed" if result.get("success") is True else "unknown", payload=flash_payload)
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
                self.store.interrupt_operations(run_id)
                run.status = "cancelled"
                run.classification = "AGENT_RUNTIME_CANCELLED"
                _set_run_stage(run, "flash", "cancelled", "Flash cancelled")
                self._cancel_active_nodes(run.run_id)
                self._event(run, "runtime.cancelled", message="Flash cancelled")
                raise
            except Exception as exc:
                self.store.interrupt_operations(run_id)
                run.status = "failed"
                run.classification = type(exc).__name__
                _set_run_stage(run, "flash", "failed", str(exc) or type(exc).__name__)
                self._fail_node(run, "flash", str(exc) or type(exc).__name__)
                self._event(run, "runtime.failed", status="failed", message="Flash failed")
                self._persist_run(run)
            finally:
                self.policy_engine.revoke_run(run.run_id)

        async def execute_owned_flash():
            try:
                await self._run_with_lease_heartbeat(run_id, execute())
            except AgentSessionLeaseLost:
                run.status = "blocked"
                run.classification = "AGENT_RECOVERY_OUTCOME_UNKNOWN"
                self.store.interrupt_operations(run_id)
                self._persist_run(run)
            finally:
                grant = self._leases.pop(run_id, None)
                if grant:
                    try:
                        self.store.release_lease(grant)
                    except ValueError:
                        pass
        self._tasks[run_id] = asyncio.create_task(owned_operation(run_id, execute_owned_flash()), name=f"orchestrator:{run_id}:flash")
        return run

    async def close(self) -> None:
        self._closing = True
        retry_tasks = tuple(self._retry_tasks.values())
        for retry_task in retry_tasks:
            retry_task.cancel()
        if retry_tasks:
            await asyncio.gather(*retry_tasks,return_exceptions=True)
        for run_id, task in tuple(self._tasks.items()):
            if not task.done():
                run = self._runs[run_id]
                if run.status in TERMINAL_STATUSES:
                    continue  # A terminal projection can precede lease cleanup.
                if run.job_id:
                    await self.pause_job(run.job_id, reason="shutdown")
                else:
                    await self.cancel(run_id)
        tasks = tuple(task for task in self._tasks.values() if not task.done())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _read_only_draft_root(self, run: Any, workspace: Path) -> tuple[Path, str | None]:
        """Inspect a same-session saved draft when the bound original folder is empty."""
        if run.autonomy not in {"read_only", "plan_only"} or self.change_service.inspect_workspace(workspace).files:
            return workspace, None
        session = self._session_for_run[run.run_id]
        candidates = sorted((item for item in self._runs.values()
            if item.run_id != run.run_id and item.project_id == run.project_id
            and self._session_for_run.get(item.run_id) == session and item.change_set_id),
            key=lambda item: item.created_at, reverse=True)
        from backend.changes.service import hash_file, is_safe_relative_path
        for prior in candidates:
            try:
                draft = self.change_service.get(prior.change_set_id)
                if draft.status not in {"draft", "pending"} or Path(draft.workspace_root).resolve() != workspace.resolve():
                    continue
                stage = Path(draft.staging_root).resolve(strict=True)
                if stage.parent != self.change_service.staging_root or not draft.changed_files:
                    continue
                expected_paths = {item.path for item in draft.changed_files if item.change_type != "deleted"}
                actual_paths = set(self.change_service.inspect_workspace(stage).files)
                if actual_paths - {".promptforge-project.json"} != expected_paths:
                    raise ValueError("Unexpected draft files")
                for item in draft.changed_files:
                    if not is_safe_relative_path(item.path):
                        raise ValueError("Unsafe draft path")
                    if item.change_type == "deleted":
                        continue
                    path = (stage / item.path).resolve(strict=True)
                    if not path.is_relative_to(stage) or hash_file(path) != item.new_hash:
                        raise ValueError("Draft integrity mismatch")
                return stage, draft.change_set_id
            except (ChangeSetError, OSError, ValueError):
                # Never silently substitute an older draft after an integrity failure.
                break
        return workspace, None

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
            inspection_root, inspection_draft_id = self._read_only_draft_root(run, workspace)
            if inspection_draft_id:
                instruction += ("\nRead-only source context: the original bound project folder is empty. "
                    "Inspect its saved draft " + inspection_draft_id + ". Report findings from these files; "
                    "this mode permits inspection only. Editing and building are unavailable.")
                self._recovery_context[run.run_id]["inspection_change_set_id"] = inspection_draft_id
                self._event(run, "context.draft.selected", status="completed",
                    message="Inspecting the saved project draft", change_set_id=inspection_draft_id)
            async def run_planner(selected_planner: object) -> ToolRuntimeResult:
                runtime = ForgeXToolRuntime(active_workspace_root=inspection_root, change_service=self.change_service)
                revision = self.change_service.inspect_workspace(workspace).revision
                memories = self.memory.active(run.project_id, current_workspace_revision=revision, board_id=run.flash_board_type, workspace_root=workspace)

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
                job = self.jobs.get(run.job_id)
                from .lifecycle import bind_job_resources
                bind_job_resources(selected_planner, self.jobs, job, run.run_id)
                recovery_binder = getattr(selected_planner, "bind_recovery_events", None)
                if callable(recovery_binder):
                    recovery_binder(lambda event: self._runtime_event(run, event))
                saved = (self._recovery_context[run.run_id].get("resume_checkpoint") or {}).get("resume",{})
                job_limits = replace(self.limits,
                    max_total_tool_calls=int(job["limits"]["tool_calls_used"]),
                    max_runtime_seconds=float(job["limits"]["elapsed_seconds"]),
                    max_turns=int(saved.get("turn",0))+self.limits.max_turns)
                return await runtime.run_product_async(
                    task=instruction,
                    environment=run.flash_environment,
                    planner=selected_planner,
                    policy=profile.policy(max_bytes_per_file=self.limits.max_bytes_per_file),
                    limits=job_limits,
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
                    task_contract=run.task_contract if run.autonomy not in {"read_only", "plan_only"} else None,
                    budget_consumed=job["consumed"],
                    retry_change_set_id=(
                        self.get_run(run.retry_of_run_id).change_set_id
                        if run.retry_of_run_id and not self._recovery_context[run.run_id].get("resume_checkpoint") else None
                    ),
                    resume_checkpoint=(self._recovery_context[run.run_id].get("resume_checkpoint") or {}).get("resume"),
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
                if run.status not in TERMINAL_STATUSES:
                    run.status = "failed"
                self._skip_pending_nodes(run, "Not reached after planner failure")
                self._event(run, "runtime.blocked" if run.status == "blocked" else "runtime.failed",
                            status=run.status, classification=run.classification,
                            message=run._assistant_message() or "Planner could not complete the request")
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
            if scoped_apply_allowed and run.autonomy != "review_changes":
                self._start_node(run, "apply")
                applied = self._apply_changes(run, result.change_set_id)
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
                    workspace_root=workspace,
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
            if run.status not in {"cancelled", "cancelling"}:
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
            self._event(run, "runtime.failed", status="failed", classification=run.classification,
                        message="Firmware workflow is unavailable")
            return
        stage: Path | None = None
        baseline = None
        try:
            baseline = await workspace_io(self.change_service.inspect_workspace, workspace)
            # Keep the full random ID, without a redundant prefix. Some Windows
            # embedded toolchains still enforce MAX_PATH for dependency files.
            stage = self.change_service.create_stage(f"workflow-{run.run_id.removeprefix('agent-run-')}")
            def copy_baseline():
                for relative in sorted(baseline.files):
                    source = workspace.joinpath(*relative.split("/"))
                    target = stage.joinpath(*relative.split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
            await workspace_io(copy_baseline)
            saved_checkpoint = (self._recovery_context[run.run_id].get("resume_checkpoint") or {}).get("resume")
            verify_only = active_workspace.get("agent_verify_saved_draft") is True
            if saved_checkpoint and not verify_only:
                await workspace_io(copy_checkpoint_tree, saved_checkpoint["stage_path"], stage)
                for relative in saved_checkpoint.get("deleted_paths", ()):
                    target = stage.joinpath(*str(relative).split("/"))
                    target.relative_to(stage)
                    if target.is_file():
                        target.unlink()
            if run.retry_of_run_id:
                previous = self.get_run(run.retry_of_run_id)
                if previous.change_set_id and (not saved_checkpoint or verify_only):
                    previous_change_set = self.change_service.get(previous.change_set_id)
                    if previous_change_set.status == "draft":
                        await workspace_io(self.change_service.materialize_review_draft,
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
            if saved_checkpoint:
                sandbox_workspace["agent_resume_checkpoint"] = saved_checkpoint
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
                    workspace_root=workspace,
                )
            )
            self._start_node(run, "planning")
            self._complete_node(run, "planning", "Embedded workflow selected")
            self._start_node(run, "implementation")

            async def progress(event: str, payload: Mapping[str, object]) -> None:
                if event in {"turn.settled", "runtime.admitted", "tool.checkpoint"} and isinstance(payload.get("_resume_checkpoint"), dict):
                    from .checkpoint_io import seal_checkpoint
                    checkpoint = await seal_checkpoint(self.change_service, stage,
                        dict(payload["_resume_checkpoint"]), baseline.revision)
                    payload = {**payload, "_resume_checkpoint": checkpoint}
                    _emit_orchestrator_diagnostic(run, {"event_type": "checkpoint.io.completed",
                        **checkpoint["io_timings"]}, session_id=self._session_for_run[run.run_id])
                import time
                started = time.monotonic()
                await self._runtime_event_async(run, {"event_type": event.casefold(), **dict(payload)})
                persistence_ms = round((time.monotonic() - started) * 1000)
                if persistence_ms >= 100:
                    _emit_orchestrator_diagnostic(run, {"event_type": "runtime.persistence.slow",
                        "duration_ms": persistence_ms, "source_event": event},
                        session_id=self._session_for_run[run.run_id])

            # Every attempt owns a sealed starting point, even if its first
            # provider request fails before another turn can settle.
            admission = dict(saved_checkpoint or {"turn": 0, "observations": [], "plan": [],
                "tool_calls_used": 0, "elapsed_seconds": 0,
                "deleted_paths": [], "authorized_paths": []})
            job = self.jobs.get(run.job_id)
            for counter in ("tool_calls_used", "elapsed_seconds"):
                admission[counter] = max(admission.get(counter, 0), job["consumed"][counter])
            admission["provider_budget"] = {key: job["consumed"][key]
                for key in ("requests", "output_tokens", "input_bytes")}
            await progress("runtime.admitted", {"turn": admission["turn"],
                "tool_count": admission["tool_calls_used"], "_resume_checkpoint": admission})

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
            requires_network = (sandbox_workspace.get("agent_verify_saved_draft") is not True
                and run.provider_id not in {"verified_template", "fake_planner"})
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
            if run.cancel_event.is_set():
                # The cancellation owner seals the final projection. A worker
                # returning late must not publish another outcome or live draft.
                return
            apply_provider_diagnostics(run, result.get("provider_diagnostics"))
            if isinstance(result.get("model_output_recovery"), Mapping):
                self._recovery_context[run.run_id]["model_output_recovery"] = dict(result["model_output_recovery"])
            if isinstance(result.get("budget_failure"), Mapping) and result["budget_failure"]:
                run.budget_failure = dict(result["budget_failure"])
            run.build_result = _safe_mapping(result.get("build_result"))
            run.build_verified = result.get("build_verified") is True and bool(run.build_result and run.build_result.get("success") is True)
            run.tool_execution_count = max(run.tool_execution_count, int(result.get("tool_execution_count") or 0))
            run.requirement_report = _safe_mapping(result.get("requirement_report"))
            run.task_state = _safe_mapping(result.get("task_state"))
            run.simulation_result = _safe_mapping(result.get("simulation_result"))
            if run.build_result:
                build_status = (
                    "blocked" if run.build_result.get("error_code") == "AGENT_PROJECT_TRUST_REQUIRED"
                    else "completed" if run.build_result.get("success") is True else "failed"
                )
                _set_run_stage(run, "build", build_status, str(run.build_result.get("message") or "Build completed"))
            raw_plan = result.get("plan")
            run.agent_plan = [
                {"text": str(item.get("text") or ""), "status": str(item.get("status") or "pending")}
                for item in raw_plan
                if isinstance(item, Mapping) and str(item.get("text") or "").strip()
            ][:32] if isinstance(raw_plan, list) else []
            run.validation_report = _safe_mapping(result.get("validation_report"))
            repair_count = result.get("repair_attempt_count")
            run.repair_attempt_count = repair_count if isinstance(repair_count, int) and not isinstance(repair_count, bool) else 0
            history = result.get("repair_history")
            run.repair_history = [dict(item) for item in history[-32:] if isinstance(item, Mapping)] if isinstance(history, list) else []
            run.pending_decision = result.get("pending_decision") is True
            alternatives = result.get("suggested_alternatives")
            run.suggested_alternatives = [str(item) for item in alternatives if isinstance(item, str)][:10] if isinstance(alternatives, list) else []
            run.active_workspace_unchanged = True
            if result.get("success") is not True:
                failure_message = str(result.get("message") or "Workflow failed")
                failure_code = str(result.get("classification") or "")
                draft = None
                if _recovery_draft_allowed(failure_code):
                    try:
                        draft = await workspace_io(self.change_service.create_review_draft,
                            provider_id=run.provider_id, workspace_root=workspace,
                            stage_root=stage, baseline=baseline,
                            on_cancel=lambda saved: self.change_service.discard(saved.change_set_id),
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
                verified_partial = (
                    draft is not None
                    and result.get("build_verified") is True
                    and run.build_result is not None
                    and run.build_result.get("success") is True
                    and run.autonomy != "simulate"
                    and failure_code in {
                        "MODEL_OUTPUT_NOT_JSON", "TOOLPLAN_SCHEMA_INVALID",
                        "TOOLPLAN_VERSION_INVALID", "TOOLPLAN_TOOL_UNKNOWN",
                        "TOOL_ARGUMENTS_INVALID",
                        "MODEL_OUTPUT_TRUNCATED", "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "PROVIDER_BUSY",
                        "DRAFT_BUILD_VERIFIED",
                    }
                )
                if draft is not None:
                    failure_message += (
                        " The compiled draft was saved. Continue to finish the remaining checks and response."
                        if run.build_verified
                        else " Generated files were saved as an unverified recovery draft. Allow local builds, then build the saved draft."
                        if failure_code == "AGENT_PROJECT_TRUST_REQUIRED"
                        else " Generated files were saved as an unverified recovery draft. Retry to repair and validate them."
                    )
                active_failure_node = "validation" if self._nodes[run.run_id]["validation"].status == "running" else "implementation"
                if failure_code == "AGENT_PROJECT_TRUST_REQUIRED":
                    node = self._nodes[run.run_id][active_failure_node]
                    node.status = "waiting"
                    node.message = failure_message[:512]
                    self.store.save_node(run.run_id, node.node_id, node.status, node.to_dict())
                else:
                    self._fail_node(run, active_failure_node, failure_message)
                # Terminal publication promises a sealed draft and completed
                # cleanup. Do not expose a settled run while its live stage is
                # still being removed by a worker.
                await workspace_io(shutil.rmtree, stage, ignore_errors=True)
                stage = None
                run.status = "completed_with_warning" if verified_partial else (
                    "blocked" if result.get("blocked") is True or failure_code == "AGENT_PROJECT_TRUST_REQUIRED"
                    else "failed"
                )
                run.classification = str(result.get("classification") or "AUTONOMOUS_WORKFLOW_FAILED")
                if run.classification == "MODEL_RETIRED":
                    run.recoverable = True
                    run.recovery_actions = ["resume", "change_model"]
                    failure_message = "The provider retired the selected model. Choose another configured model to continue the saved work."
                if run.classification in TRANSIENT_PROVIDER_ERRORS:
                    run.status = "paused_model_unavailable"
                    run.recoverable = True
                    run.recovery_actions = ["resume"]
                    node = self._nodes[run.run_id][active_failure_node]
                    node.status = "waiting"
                    node.message = ("Waiting for another ForgeX task to release this model endpoint"
                                    if run.classification == "PROVIDER_BUSY" else "Waiting for the selected model endpoint")
                    self.store.save_node(run.run_id, node.node_id, node.status, node.to_dict())
                if run.pending_decision and run.suggested_alternatives:
                    failure_message += " Compatible options: " + ", ".join(run.suggested_alternatives) + "."
                run.assistant_message_override = (
                    "Firmware build verified. Changes are saved in a review draft; "
                    "Task completion still needs verification. Review the draft or continue the task."
                    if verified_partial else failure_message[:1000]
                )
                self._skip_pending_nodes(run, "Not reached after project validation stopped the workflow")
                self._event(
                    run,
                    "runtime.paused_model_unavailable" if run.status == "paused_model_unavailable" else ("runtime.completed_with_warning" if verified_partial else ("runtime.blocked" if run.status == "blocked" else "runtime.failed")),
                    status=run.status,
                    message=failure_message,
                    classification=run.classification,
                )
                self._persist_run(run)
                return
            from backend.security.workspace_io import iter_workspace_files
            def discover_authorized_paths():
                return {
                    path.relative_to(stage).as_posix()
                    for path in iter_workspace_files(stage, ignored_dirs=IGNORED_DIRS)
                }
            authorized_paths = await workspace_io(discover_authorized_paths)
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
                self._complete_node(run, "validation", "Current build and requirement reviews completed")
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
                if not scoped_apply or run.autonomy == "review_changes":
                    run.status = "completed"
                    run.classification = "CHANGESET_APPROVAL_REQUIRED"
                    self._skip_node(run, "apply", "Staged changes require explicit approval")
                    self._skip_node(run, "flash", "Hardware action blocked until changes are approved")
                    self._event(run, "runtime.completed", status="completed", message="Build passed; staged changes require approval")
                    self._persist_run(run)
                    return
                self._start_node(run, "apply")
                applied = self._apply_changes(run, change_set.change_set_id)
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
                    workspace_root=workspace,
                    board_id=run.flash_board_type,
                )
            else:
                self._skip_node(run, "review", "No workspace diff required review")
                self._skip_node(run, "apply", "No workspace diff to apply")
            if run.build_result:
                self.memory.add_verified(
                    project_id=run.project_id,
                    kind="verified_build" if run.build_result.get("success") is True and run.build_verified else "build_attempt",
                    value=_verified_build_memory_value(run.build_result),
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
                    workspace_root=workspace,
                )
            if run.autonomy == "build_then_confirm_flash":
                if _approved_artifact_hash(run) is None or not (run.build_result or {}).get("artifact_manifest"):
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
            if run.status not in TERMINAL_STATUSES and run.status != "cancelling":
                run.status = "cancelled"
                run.classification = "AGENT_RUNTIME_CANCELLED"
                self._cancel_active_nodes(run.run_id)
                self._event(run, "runtime.cancelled", message="Action run cancelled")
            raise
        except Exception as exc:
            if run.cancel_event.is_set():
                return
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
                    if run.cancel_event.is_set() or run.classification == "AGENT_RECOVERY_OUTCOME_UNKNOWN":
                        self._recovery_context[run.run_id]["quarantined_stage_path"] = str(stage)
                    else:
                        await workspace_io(shutil.rmtree, stage, ignore_errors=True)

    def _retain_sealed_checkpoint(self, run: Any) -> None:
        """Idempotent finalization from a sealed boundary, never a live worker stage."""
        if run.change_set_id:
            return
        try:
            from .recovery_draft import retain_checkpoint_draft
            checkpoint = self._recovery_checkpoint(run.run_id)
            context = self._recovery_context.get(run.run_id, {})
            if not checkpoint or not isinstance(checkpoint.get("resume"), dict):
                return
            self.store.require_healthy_journal(self._session_for_run[run.run_id])
            workspace_error = self._recovery_workspace_error(run.run_id)
            if workspace_error:
                if workspace_error == "AGENT_RESUME_WORKSPACE_UNAVAILABLE":
                    self._record_unavailable_workspace(run)
                return
            current_revision = self.change_service.inspect_workspace(context["workspace_root"]).revision
            if current_revision != checkpoint["resume"]["workspace_revision"]:
                self._record_retention_conflict(run, checkpoint, current_revision)
                return
            context.pop("retention_conflict", None)
            draft = retain_checkpoint_draft(self.change_service, provider_id=run.provider_id,
                workspace_root=context["workspace_root"], checkpoint=checkpoint["resume"],
                authorization_id=run.run_id + ":" + str(checkpoint.get("checkpoint_id")))
            if draft is None:
                return
            run.change_set_id = draft.change_set_id
            for kind in ("created", "modified", "deleted"):
                paths = [item.path for item in draft.changed_files if item.change_type == kind]
                setattr(run, kind + "_files", paths)
                setattr(run, kind + "_file_count", len(paths))
            unknown = any(item["status"] in {"started", "unknown"} for item in self.store.operations(run.run_id))
            run.recoverable = not unknown
            run.recovery_actions = ["resume"] if not unknown else []
            context["last_safe_checkpoint"] = checkpoint
        except ValueError as exc:
            if str(exc) == "AGENT_RESUME_WORKSPACE_CHANGED":
                # Recheck a workspace that changed between inspection and retention.
                try:
                    current_revision = self.change_service.inspect_workspace(context["workspace_root"]).revision
                    self._record_retention_conflict(run, checkpoint, current_revision)
                except (OSError, ValueError, KeyError):
                    logger.warning("Cannot inspect recovery baseline run_id=%s", run.run_id, exc_info=True)
            else:
                logger.warning("Cannot retain sealed recovery draft run_id=%s", run.run_id, exc_info=True)
        except (ChangeSetError, OSError, KeyError):
            logger.warning("Cannot retain sealed recovery draft run_id=%s", run.run_id, exc_info=True)

    def _record_retention_conflict(self, run, checkpoint, current_revision):
        context = self._recovery_context[run.run_id]
        conflict = {"code": "AGENT_RESUME_WORKSPACE_CHANGED", "checkpoint_id": checkpoint.get("checkpoint_id"),
                    "expected_revision": checkpoint["resume"]["workspace_revision"],
                    "current_revision": current_revision,
                    "message": "The project changed since this checkpoint. Inspect the saved work; automatic continuation and apply are blocked."}
        if context.get("retention_conflict") == conflict:
            return
        context["retention_conflict"] = conflict
        payload = {**run.to_safe_dict(), "session_id": self._session_for_run[run.run_id],
                   "_recovery_context": context}
        # Keep the historical terminal timestamp, job budgets and journal intact.
        self.store.save_run(run.run_id, self._session_for_run[run.run_id], run.project_id, run.status, payload)
        logger.info("Recovery retention conflict run_id=%s code=%s checkpoint_id=%s",
            run.run_id, conflict["code"], conflict["checkpoint_id"])

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
        clock = self._attempt_clocks.get(run.run_id)
        if clock:
            self.jobs.record_elapsed(run.job_id, clock[1] + time.monotonic() - clock[0])
        self._start_node(run, "review")
        if self.reviewer is None:
            run.requirement_report = {**(run.requirement_report or {}), "risk_review": {
                "verdict": "request_changes", "status": "needs_review", "failure_code": "RISK_REVIEW_UNAVAILABLE"}}
            self._fail_node(run, "review", "Reviewer unavailable; explicit approval required")
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
                "run_id": run.run_id, "job_id": run.job_id,
            },
        })
        verdict = str(response.get("verdict") or "block").casefold()
        if verdict not in {"approve", "request_changes", "block"}:
            verdict = "block"
        run.requirement_report = {**(run.requirement_report or {}), "risk_review": {
            key: response[key] for key in ("verdict", "status", "summary", "failure_code", "attempts", "diagnostics") if key in response}}
        message = str(response.get("summary") or f"Reviewer verdict: {verdict}")
        if verdict != "approve" or response.get("failure_code"):
            self._fail_node(run, "review", message)
            self._event(run, "node.failed", node_id="review", status="failed", message=message)
        else:
            self._complete_node(run, "review", message)
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
        run.requirement_report = dict(result.requirement_report) if result.requirement_report else None
        run.agent_plan = [dict(item) for item in result.plan]
        run.active_workspace_unchanged = result.active_workspace_unchanged
        apply_provider_diagnostics(run, result.provider_diagnostics)
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
        if run.classification == "MODEL_RETIRED":
            run.recoverable = True
            run.recovery_actions = ["resume", "change_model"]
            run.assistant_message_override = "The provider retired the selected model. Choose another configured model to continue the saved work."

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

    async def _runtime_event_async(self, run: Any, event: Mapping[str, object]) -> None:
        """Freeze state on the loop; await ordered durability away from it."""
        async with self._persistence_gate:
            prepared = self._runtime_event(run, event, persist=False)
            if prepared is None:
                return
            event_type, payload = prepared
            if payload.get("transient"):
                self._event(run, event_type, **payload)
                return
            view = copy.copy(run)
            for field in fields(run):
                value = getattr(run, field.name)
                if isinstance(value, (dict, list, tuple, set)):
                    setattr(view, field.name, copy.deepcopy(value))
            checkpoint = copy.deepcopy(self._checkpoint_payloads.get(run.run_id))
            context = copy.deepcopy(self._recovery_context.get(run.run_id, {}))
            grant = self._leases.get(run.run_id)
            session_id = self._session_for_run[run.run_id]
            clock = self._attempt_clocks.get(run.run_id)
            elapsed = clock[1] + time.monotonic() - clock[0] if clock else None
            safe = {key: value for key, value in payload.items() if value is not None}
            safe.update(event_type=event_type, run_id=run.run_id)
            safe.update(run_activity_fields(safe))
            safe = copy.deepcopy(safe)

            def commit():
                with self.store.atomic():
                    if elapsed is not None:
                        self.jobs.record_elapsed(view.job_id, elapsed)
                    if view.job_id:
                        view.job = self.jobs.public(self.jobs.update_run(view.job_id, view, checkpoint))
                    view.updated_at = utc_now()
                    projection = {**view.to_safe_dict(), "session_id": session_id, "_recovery_context": context}
                    committed = self.store.append_event(session_id, safe, grant=grant, run_projection=projection)
                    if event_type in {"turn.settled", "runtime.admitted", "tool.checkpoint"}:
                        self.store.save_run_checkpoint(view.run_id, session_id,
                            turn=0 if event_type == "runtime.admitted" else int(safe.get("turn") or 1),
                            session_seq=int(committed["sequence"]), payload={
                                "step": int(safe.get("step") or safe.get("turn") or 1),
                                "attempt_id": str(safe.get("attempt_id") or ""),
                                "outcome": str(safe.get("outcome") or "unknown"),
                                "tool_count": int(safe.get("tool_count") or 0),
                                "run_status": view.status, "stage_statuses": dict(view.stage_statuses),
                                "resume": checkpoint})
                _emit_orchestrator_diagnostic(view, committed, session_id=session_id)
                return committed

            committed = await workspace_io(commit)
            # The worker only changes its detached view. Mutable run state stays
            # on this loop, and cancellation drains the commit before cleanup.
            run.job = view.job
            run.updated_at = view.updated_at
            run.events.append(dict(committed))
            if event_type in {"model.attempt.settled", "model.attempt.abandoned", "MODEL_ATTEMPT_SETTLED"}:
                attempt_id = str(safe.get("attempt_id") or "")
                if attempt_id:
                    with self._lock:
                        self._transient_events[run.run_id] = [item for item in self._transient_events.get(run.run_id, [])
                            if str(item.get("attempt_id") or "") != attempt_id]
            await workspace_io(self._publish_run, view)

    def _runtime_event(self, run: Any, event: Mapping[str, object], *, persist=True):
        if run.cancel_event.is_set() and run.status in TERMINAL_STATUSES:
            return
        if run.cancel_event.is_set() and str(event.get("event_type") or "").casefold() not in {
            "runtime.cancelled", "cancellation.requested", "turn.settled", "tool.checkpoint",
            "tool.result.committed", "tool.execution.settled", "tool_completed", "tool_failed",
            "tool_outcome_unknown", "build_completed", "build_failed"}:
            return
        payload = dict(event)
        if payload.get("event_type") == "model.request.progress":
            for key in ("physical_attempt_id", "response_headers_received", "request_reached_provider", "http_status", "first_event_ms", "last_event_ms", "stream_event_count"):
                if key in payload:
                    setattr(run, key, payload[key])
        if str(payload.get("event_type", "")).startswith("model.retry.") and run.job_id:
            run.job = self.jobs.public(self.jobs.record_retry(run.job_id, run.run_id, payload))
        resume_payload = payload.pop("_resume_checkpoint", None)
        if isinstance(resume_payload, dict):
            self._checkpoint_payloads[run.run_id] = resume_payload
            self._recovery_context[run.run_id]["sealed_resume_boundary"] = {
                "event_type": str(payload.get("event_type")), "turn": int(payload.get("turn") or 0),
                "resume": resume_payload}
            if isinstance(resume_payload.get("task_state"), dict):
                run.task_state = dict(resume_payload["task_state"])
        kind = str(payload.get("tool") or payload.get("name") or ("build" if str(payload.get("event_type", "")).casefold().startswith("build_") else "tool"))
        call_id = str(payload.get("call_id") or payload.get("tool_call_id") or payload.get("build_id") or "")
        operation_id = f"{run.run_id}:{call_id}"
        operation_payload = {"call_id": call_id, "kind": kind}
        event_name = str(payload.get("event_type") or "").casefold()
        if event_name == "verification_started" and "validation" in self._nodes.get(run.run_id, {}):
            validation = self._nodes[run.run_id]["validation"]
            if validation.status != "running":
                self._start_node(run, "validation")
        if call_id and event_name in {"tool.execution.started", "tool_started", "build_started"}:
            if run.job_id:
                self.jobs.charge_tool(run.job_id,operation_id)
            self.store.operation(operation_id, run.run_id, kind, status="started", payload=operation_payload)
        elif call_id and event_name == "tool_outcome_unknown":
            self.store.operation(operation_id,run.run_id,kind,status="unknown",payload=operation_payload)
        elif call_id and event_name in {"tool.result.committed", "tool.execution.settled", "tool_completed", "tool_failed", "build_completed", "build_failed"}:
            existing = next((item for item in self.store.operations(run.run_id) if item["operation_id"] == operation_id), None)
            if existing and existing["status"] not in {"completed", "failed", "unknown"}:
                self.store.operation(operation_id, run.run_id, existing["kind"],
                    status="failed" if payload.get("status") == "failed" or event_name in {"tool_failed", "build_failed"} else "completed", payload=existing["payload"])
        payload.setdefault("event_type", "tool.updated")
        updated_contract = payload.pop("_task_contract", None)
        if isinstance(updated_contract, dict):
            run.task_contract = updated_contract
            run.requirement_report = None
            self._recovery_context[run.run_id]["active_workspace"]["task_contract"] = updated_contract
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
            if stage == "build" and normalized in {"BUILD_COMPLETED", "BUILD_FAILED", "BUILD_BLOCKED"}:
                run.build_result = _safe_mapping(payload) or run.build_result
            if normalized == "BUILD_REPAIR_COMPLETED":
                _set_run_stage(run, "build", "completed", message)
        if stage is not None:
            payload.setdefault("stage", stage)
        if not persist:
            return event_type, payload
        self._event(run, event_type, **payload)

    def _event(self, run: Any, event_type: str, **payload: object) -> dict[str, object]:
        if payload.get("transient"):
            return self._event_transaction(run, event_type, **payload)
        with self._lock, self.store.atomic():
            event = self._event_transaction(run, event_type, **payload)
        if event:
            self._publish_run(run)
        return event

    def _event_transaction(self, run: Any, event_type: str, **payload: object) -> dict[str, object]:
        safe = {key: value for key, value in payload.items() if value is not None}
        safe["event_type"] = event_type
        safe["run_id"] = run.run_id
        if "flash" in event_type or event_type.startswith("monitor"):
            safe.setdefault("port", run.flash_port)
            safe.setdefault("board", run.flash_board_type)
            safe.setdefault("environment", run.flash_environment)
        if event_type in {"runtime.completed", "runtime.completed_with_warning", "runtime.failed", "runtime.cancelled", "runtime.blocked", "runtime.interrupted"}:
            if run.run_id in self._cancelling_runs:
                run.status = "cancelling"
                return {}
            # Repeated cleanup/cancellation must not publish another settlement.
            for prior in reversed(run.events):
                if prior.get("event_type") == "runtime.awaiting_flash_confirmation":
                    break  # A newly confirmed hardware phase has its own settlement.
                if prior.get("event_type") in {"runtime.completed", "runtime.completed_with_warning", "runtime.failed", "runtime.cancelled", "runtime.blocked", "runtime.interrupted"}:
                    return dict(prior)
            if event_type != "runtime.completed":
                self._retain_sealed_checkpoint(run)
            if run.classification == "AGENT_RECOVERY_OUTCOME_UNKNOWN":
                run.recoverable = False
                run.recovery_actions = []
            self._transient_events.pop(run.run_id, None)
            for node in self._nodes.get(run.run_id, {}).values():
                if node.status in {"running", "pending"}:
                    node.status = "skipped" if node.status == "pending" else ("completed" if run.status.startswith("completed") else "failed")
                    node.message = "Run settled: " + run.status
                    self.store.save_node(run.run_id, node.node_id, node.status, node.to_dict())
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
            self.store.notify_observers(self._session_for_run[run.run_id])
            return event
        self._persist_run(run, notify_listeners=False)
        event = self.store.append_event(
            self._session_for_run[run.run_id],
            safe,
            grant=self._leases.get(run.run_id),
            run_projection={**run.to_safe_dict(), "session_id": self._session_for_run[run.run_id],
                            "_recovery_context": self._recovery_context.get(run.run_id, {})},
        )
        run.events.append(dict(event))
        if event_type in {"turn.settled", "runtime.admitted", "tool.checkpoint"}:
            try:
                self.store.save_run_checkpoint(
                    run.run_id,
                    self._session_for_run[run.run_id],
                    # Admission is turn zero of this attempt. The inherited
                    # reasoning turn remains in resume["turn"].
                    turn=0 if event_type == "runtime.admitted" else int(safe.get("turn") or 1),
                    session_seq=int(event["sequence"]),
                    payload={
                        "step": int(safe.get("step") or safe.get("turn") or 1),
                        "attempt_id": str(safe.get("attempt_id") or ""),
                        "outcome": str(safe.get("outcome") or "unknown"),
                        "tool_count": int(safe.get("tool_count") or 0),
                        "run_status": str(getattr(run, "status", "running")),
                        "stage_statuses": dict(getattr(run, "stage_statuses", {}) or {}),
                        "resume": self._checkpoint_payloads.get(run.run_id),
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

    def _persist_run(self, run: Any, *, notify_listeners=True) -> None:
        with self._lock, self.store.atomic():
            self._persist_run_transaction(run)
        if notify_listeners:
            self._publish_run(run)

    def _persist_run_transaction(self, run: Any) -> None:
        run.updated_at = utc_now()
        if getattr(run, "job_id", None):
            clock = self._attempt_clocks.get(run.run_id)
            if clock:
                self.jobs.record_elapsed(run.job_id,clock[1]+time.monotonic()-clock[0])
                if run.status in TERMINAL_STATUSES or run.status == "paused_model_unavailable":
                    self._attempt_clocks.pop(run.run_id,None)
            checkpoint = self._checkpoint_payloads.get(run.run_id)
            run.job = self.jobs.public(self.jobs.update_run(run.job_id, run, checkpoint))
            if run.job.get("paused_by") == "budget" and run.status not in {"completed","completed_with_warning"}:
                run.assistant_message_override = (
                    "Generation paused to preserve verification budget. Build the saved draft, "
                    "or add task budget to continue generation. Your saved work is preserved."
                    if run.job.get("blocking_reason") == "AGENT_VERIFICATION_RESERVE_REACHED" else
                    "Task paused because its remaining budget cannot cover another attempt. "
                    "Your settled work is saved. Add budget, then continue from the saved work."
                )
                try:
                    saved = self.store.latest_run_checkpoint(run.run_id)
                except ValueError:
                    saved = None
                if saved and saved.get("resume"):
                    run.recoverable = True
                    run.recovery_actions = ["resume","extend_budget"]
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
    def _publish_run(self, run: Any) -> None:
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
                journal_healthy = bool(self.store.verify_journal(session_id)["healthy"])
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
                    "created_at", "updated_at",
                    "tool_execution_count", "active_workspace_unchanged", "active_workflow_task_id",
                    "build_result", "build_verified", "simulation_result", "flash_result", "monitor_result", "flash_port", "flash_board_type",
                    "flash_environment", "start_monitor_after_flash", "approval_id", "approval_expires_at",
                    "assistant_message_override", "stage_statuses", "stage_messages",
                    "validation_report", "repair_attempt_count", "pending_decision", "suggested_alternatives",
                    "repair_history", "budget_failure",
                    "agent_plan", "created_files", "modified_files", "deleted_files",
                    "actual_provider_id", "fallback_reason", "outbound_request_count",
                    "physical_request_count", "request_reached_provider", "request_dispatched", "http_status",
                    "provider_request_id", "retry_after_ms",
                    "last_turn", "response_mode", "last_failure_code", "last_repair_outcome",
                    "last_parse_reason", "last_failure_origin", "last_response_shape", "last_response_tool_count", "stream_fallback_used", "initial_failure_code", "recovery_failure_code",
                    "budget_persistence_cause", "failure_shape",
                    "requested_output_tokens", "effective_output_tokens", "finish_reason", "input_tokens", "output_tokens", "reasoning_tokens", "total_tokens",
                    "text_event_count", "reasoning_event_count", "tool_event_count", "worker_exit_code", "worker_cleanup_incomplete",
                    "repair_skip_reason", "initial_attempt_diagnostics", "repair_attempt_diagnostics",
                    "physical_attempt_id", "response_headers_received", "first_event_ms", "last_event_ms", "stream_event_count", "completion_received", "timeout_stage", "duration_ms",
                    "retry_of_run_id", "recovered_by_run_id", "interrupted_at",
                    "recoverable", "recovery_actions",
                    "task_contract", "task_state", "requirement_report",
                    "job_id", "job",
                ):
                    if name in payload and hasattr(run, name):
                        setattr(run, name, payload[name])
                provider_diagnostics = payload.get("provider_diagnostics")
                apply_provider_diagnostics(run, provider_diagnostics)
                recovery_context = payload.get("_recovery_context")
                if isinstance(recovery_context, Mapping):
                    self._recovery_context[run_id] = _json_safe_mapping(recovery_context)
                    contract = (recovery_context.get("active_workspace") or {}).get("task_contract")
                    if isinstance(contract, Mapping):
                        run.task_contract = dict(contract)
                if (journal_healthy and run.http_status == 410 and self._recovery_context.get(run_id)
                        and run.status in {"failed", "blocked", "timed_out", "paused_model_unavailable", "completed_with_warning"}):
                    # Upgrade the projection of old generic 410 errors. The
                    # original events and their diagnostic evidence stay sealed.
                    run.classification = "MODEL_RETIRED"
                    if run.status == "paused_model_unavailable":
                        run.status = "failed"
                    run.last_failure_code = "MODEL_RETIRED"
                    run.recoverable = not bool(run.recovered_by_run_id)
                    run.recovery_actions = ["resume", "change_model"] if run.recoverable else []
                    run.assistant_message_override = "The provider retired the selected model. Choose another configured model to continue the saved work."
                if run.classification in {"provider_smoke_toolplan_unsafe", "provider_smoke_call_count_invalid"} and self._recovery_context.get(run_id) and not any(
                    item["status"] in {"started", "unknown"} for item in self.store.operations(run_id)
                ):
                    run.recoverable = True
                    run.recovery_actions = ["retry"]
                if not journal_healthy:
                    run.status = "blocked"
                    run.classification = "AGENT_JOURNAL_QUARANTINED"
                    run.recoverable = False
                    run.recovery_actions = []
                    run.approval_id = None
                    run.approval_expires_at = None
                    self.store.invalidate_pending_approvals(run_id, "journal_quarantined")
                elif run.status not in TERMINAL_STATUSES and run.status != "paused_model_unavailable":
                    self.store.interrupt_operations(run_id)
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
            legacy_job = not run.job_id
            if legacy_job:
                context = self._recovery_context.get(run_id, {})
                job = self.jobs.create(project_id=run.project_id, session_id=session_id,
                    workspace_root=context.get("workspace_root", ""), run_id=run_id, legacy=True)
                run.job_id = job["job_id"]
                run.job = self.jobs.public(job)
            self._nodes[run_id] = nodes or {node.node_id: node for node in self._initial_graph(run.autonomy)}
            self._session_for_run[run_id] = session_id
            self._steering[run_id] = []
            if journal_healthy and run.recoverable and self._recovery_workspace_error(run_id) == "AGENT_RESUME_WORKSPACE_UNAVAILABLE":
                self._record_unavailable_workspace(run)
            # Opening a terminal job must not reset its retirement clock.
            if legacy_job:
                self._persist_run(run)
            if journal_healthy:
                run.events = [dict(event) for event in self.store.snapshot_events(session_id, run_id=run_id)]
                if run.status == "cancelled" and not run.change_set_id:
                    self._retain_sealed_checkpoint(run)
                    if run.change_set_id:
                        self._event(run, "recovery.draft.retained", message="Retained the validated checkpoint from a stopped run")
            if run.classification == "AGENT_RUNTIME_INTERRUPTED" and not any(
                event.get("event_type") == "runtime.interrupted"
                for event in self.store.snapshot_events(session_id, run_id=run_id)
            ):
                try:
                    checkpoint = self.store.latest_run_checkpoint(run_id)
                except ValueError:
                    checkpoint = None
                    run.recoverable = False
                    run.classification = "AGENT_CHECKPOINT_CORRUPT"
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
            elif journal_healthy and run.status in TERMINAL_STATUSES and not any(
                event.get("event_type") in {"runtime.completed", "runtime.completed_with_warning", "runtime.failed", "runtime.cancelled", "runtime.blocked", "runtime.interrupted"}
                for event in run.events
            ):
                self.store.backup_before_reconciliation()
                try:
                    lease = self.store.acquire_lease(session_id, run_id, ttl_seconds=self._lease_ttl_seconds)
                except (AgentSessionLeaseLost, ValueError):
                    continue
                self._leases[run_id] = lease
                try:
                    terminal = "runtime." + (run.status if run.status in {"completed", "completed_with_warning", "cancelled", "blocked"} else "failed")
                    self._event(run, terminal, status=run.status, classification=run.classification,
                                message="Historical run status reconciled", reconciliation_version=1)
                finally:
                    self._leases.pop(run_id, None)
                    self.store.release_lease(lease)
        for source in tuple(self._runs):
            replacement = self.store.recovery_replacement(source)
            if replacement and replacement in self._runs:
                self._runs[source].recovered_by_run_id = replacement
                self._runs[source].recoverable = False
                self._runs[source].recovery_actions = []
                self._persist_run(self._runs[source])


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
    artifact_manifest: dict | None = None


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
    if event == "BUILD_BLOCKED":
        return "blocked"
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
        if len(encoded) > 4_000_000:
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
            "artifact_manifest",
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


def _verified_build_memory_value(build: Mapping[str, object]) -> str:
    """Verified memory contains host facts, never the model's final prose."""
    facts = {key: build[key] for key in (
        "success", "environment", "board_id", "board_type", "firmware_hash",
        "artifact_hash", "manifest_hash", "exit_code", "returncode", "hardware_verified",
    ) if key in build and isinstance(build[key], (str, int, float, bool, type(None)))}
    return "Build observation: " + json.dumps(facts, ensure_ascii=True, sort_keys=True)


def _emit_orchestrator_diagnostic(run: Any, event: Mapping[str, object], *, session_id: str) -> None:
    event_type = str(event.get("event_type") or "agent.event").replace("_", ".")
    mapped = _diagnostic_event_name(event_type)
    status = str(event.get("status") or getattr(run, "status", ""))
    # Severity belongs to this event, not the historical run it describes.
    level = "ERROR" if mapped.endswith(".failed") else (
        "WARNING" if mapped.endswith((".slow", ".blocked", ".paused_model_unavailable")) else "INFO")
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
        copy_ms=event.get("copy_ms"), inspection_ms=event.get("inspection_ms"), total_ms=event.get("total_ms"),
        source_event=event.get("source_event"), loop_delay_ms=event.get("loop_delay_ms"),
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
        "runtime.completed.with.warning": "agent.run.completed_with_warning",
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
