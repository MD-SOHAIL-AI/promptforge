"""Durable action-run owner for the chat-first ForgeX product agent."""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from backend.changes import ChangeSetError, ChangeSetService, IGNORED_DIRS

from .activity import run_activity_fields
from .approvals import ApprovalService
from .capabilities import ToolPolicyEngine
from .memory import ProjectMemoryService
from .orchestration_store import AgentOrchestrationStore, utc_now
from .risk import RiskAssessment, RiskAssessmentService
from .tool_contracts import RuntimeClassification, ToolName
from .tool_policy import product_agent_policy, subagent_policy
from .tool_runtime import ForgeXToolRuntime, ProductRuntimeLimits, ToolRuntimeResult


TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "blocked", "timed_out"})
AUTONOMOUS_AUTONOMIES = frozenset({"build_only", "build_then_confirm_flash"})
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AgentTaskNode:
    node_id: str
    role: str
    goal: str
    dependencies: tuple[str, ...] = ()
    status: str = "pending"
    attempt: int = 0
    risk_level: str = "unassessed"
    reviewer_required: bool = False
    message: str | None = None
    artifact_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "role": self.role,
            "goal": self.goal,
            "dependencies": list(self.dependencies),
            "status": self.status,
            "attempt": self.attempt,
            "risk_level": self.risk_level,
            "reviewer_required": self.reviewer_required,
            "message": self.message,
            "artifact_ids": list(self.artifact_ids),
        }


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
        self.policy_engine = ToolPolicyEngine()
        self.risk_service = RiskAssessmentService()
        self.memory = ProjectMemoryService(store)
        self.approvals = ApprovalService(store)
        self._runs: dict[str, Any] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._nodes: dict[str, dict[str, AgentTaskNode]] = {}
        self._session_for_run: dict[str, str] = {}
        self._steering: dict[str, list[str]] = {}
        self._lock = threading.RLock()
        self._restore_runs()

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
        )
        run.active_workflow_task_id = f"task-{run_id}"
        nodes = self._initial_graph(autonomy)
        with self._lock:
            self._runs[run_id] = run
            self._nodes[run_id] = {node.node_id: node for node in nodes}
            self._session_for_run[run_id] = session_key
            self._steering[run_id] = []
        self._persist_graph(run)
        self._event(run, "runtime.queued", stage="planning", message="Action run queued")

        async def execute() -> None:
            try:
                if autonomy in AUTONOMOUS_AUTONOMIES:
                    await self._execute_autonomous(
                        run,
                        workspace=workspace,
                        instruction=instruction,
                        active_workspace=active_workspace or {},
                        fallback_provider_id=fallback_provider_id,
                        explicit_edit_authorized=explicit_edit_authorized,
                    )
                else:
                    await self._execute_staged(
                        run,
                        workspace=workspace,
                        instruction=instruction,
                        fallback_provider_id=fallback_provider_id,
                        explicit_edit_authorized=explicit_edit_authorized,
                    )
            finally:
                self.policy_engine.revoke_run(run.run_id)

        self._tasks[run_id] = asyncio.create_task(execute(), name=f"orchestrator:{run_id}")
        return run

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

    def events(self, run_id: str, *, after_sequence: int = 0) -> tuple[dict[str, object], ...]:
        self.get_run(run_id)
        session_id = self._session_for_run[run_id]
        return self.store.events(session_id, after_sequence=after_sequence, run_id=run_id)

    def session_events(self, session_id: str, *, after_sequence: int = 0) -> tuple[dict[str, object], ...]:
        return self.store.events(session_id, after_sequence=after_sequence)

    def steer(self, run_id: str, message: str) -> Any:
        """Queue user guidance for an active V3 loop without cancelling the run."""
        run = self.get_run(run_id)
        text = str(message).strip()
        if not text or len(text) > 8_000:
            raise ValueError("AGENT_RUNTIME_STEERING_INVALID")
        if run.status in TERMINAL_STATUSES or run.status == "awaiting_flash_confirmation":
            raise ValueError("AGENT_RUNTIME_STEERING_NOT_ACTIVE")
        with self._lock:
            queue = self._steering.setdefault(run_id, [])
            if len(queue) >= 16:
                queue.pop(0)
            queue.append(text)
        self._event(run, "agent.steering.queued", status=run.status, message=text[:500])
        self._persist_run(run)
        return run

    def _consume_steering(self, run_id: str) -> tuple[str, ...]:
        with self._lock:
            items = tuple(self._steering.get(run_id, ()))
            self._steering[run_id] = []
        return items

    def append_chat_event(self, session_id: str, payload: Mapping[str, object]) -> dict[str, object]:
        return self.store.append_event(session_id, payload)

    async def cancel(self, run_id: str) -> Any:
        run = self.get_run(run_id)
        if run.status in TERMINAL_STATUSES:
            return run
        run.status = "cancelling"
        run.cancel_event.set()
        self._event(run, "runtime.cancelling", message="Cancellation requested")
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        if self.cancel_executor is not None:
            await self.cancel_executor(run)
        run.status = "cancelled"
        run.classification = "AGENT_RUNTIME_CANCELLED"
        self.policy_engine.revoke_run(run_id)
        self._cancel_active_nodes(run_id)
        self._event(run, "runtime.cancelled", message="Action run cancelled")
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
        approval = self.approvals.issue(
            run_id=run.run_id,
            action_type="flash",
            binding=_flash_binding(run),
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
        self.approvals.consume(
            run.approval_id,
            action_type="flash",
            binding=_flash_binding(
                run,
                port=proposed_port,
                board_type=proposed_board_type,
                environment=proposed_environment,
                start_monitor_after_flash=proposed_monitor,
            ),
        )
        run.flash_port = proposed_port
        run.flash_board_type = proposed_board_type
        run.flash_environment = proposed_environment
        run.start_monitor_after_flash = proposed_monitor
        run.status = "running"
        self._start_node(run, "flash")
        self._event(run, "flash.confirmed", node_id="flash", status="running", message="One-time flash confirmation accepted")

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
                    self._complete_node(run, "flash", str(result.get("message") or "Flash completed"))
                    self._event(run, "runtime.completed", status="completed", message="Hardware workflow completed")
                else:
                    run.status = "failed"
                    run.classification = str(result.get("classification") or "FLASH_FAILED")
                    self._fail_node(run, "flash", str(result.get("message") or "Flash failed"))
                    self._event(run, "runtime.failed", status="failed", message="Flash failed")
                self._persist_run(run)
            except asyncio.CancelledError:
                run.status = "cancelled"
                run.classification = "AGENT_RUNTIME_CANCELLED"
                self._cancel_active_nodes(run.run_id)
                self._event(run, "runtime.cancelled", message="Flash cancelled")
                raise
            except Exception as exc:
                run.status = "failed"
                run.classification = type(exc).__name__
                self._fail_node(run, "flash", str(exc) or type(exc).__name__)
                self._event(run, "runtime.failed", status="failed", message="Flash failed")
                self._persist_run(run)
            finally:
                self.policy_engine.revoke_run(run.run_id)

        self._tasks[run_id] = asyncio.create_task(execute(), name=f"orchestrator:{run_id}:flash")
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
    ) -> None:
        try:
            self._start_node(run, "planning")
            planner = self.provider_registry.resolve(run.provider_id)
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

                return await asyncio.to_thread(
                    runtime.run_product,
                    task=instruction,
                    planner=selected_planner,
                    policy=(
                        subagent_policy(allow_build=False, max_bytes_per_file=self.limits.max_bytes_per_file)
                        if run.autonomy == "plan_only"
                        else product_agent_policy(max_bytes_per_file=self.limits.max_bytes_per_file)
                    ),
                    limits=self.limits,
                    cancel_event=run.cancel_event,
                    event_callback=lambda event: self._runtime_event(run, event),
                    policy_engine=self.policy_engine,
                    capability_authorization_source=("explicit_edit_request" if explicit_edit_authorized else "explicit_review"),
                    capability_run_id=run.run_id,
                    capability_node_id="implementation",
                    capability_session_id=self._session_for_run[run.run_id],
                    service_handlers={ToolName.MEMORY_SEARCH: memory_search},
                    memory_entries=memories,
                    steering_callback=lambda: self._consume_steering(run.run_id),
                    allow_no_changes_success=(run.autonomy == "plan_only"),
                )

            result = await run_planner(planner)
            if (
                result.classification is RuntimeClassification.PROVIDER_INVALID
                and fallback_provider_id
                and fallback_provider_id != run.provider_id
                and result.provider_classification not in {
                    "API_AUTH_INVALID",
                    "API_PROVIDER_KEY_MISSING",
                    "API_BILLING_REQUIRED",
                }
            ):
                primary_diagnostics = dict(result.provider_diagnostics)
                fallback = self.provider_registry.resolve(fallback_provider_id)
                run.fallback_reason = result.provider_classification or result.classification.value
                run.actual_provider_id = fallback_provider_id
                fallback_model = getattr(fallback, "model_id", None)
                if isinstance(fallback_model, str) and fallback_model:
                    run.model_id = fallback_model
                self._event(
                    run,
                    "provider.fallback",
                    node_id="implementation",
                    status="running",
                    message=f"Configured fallback selected after {run.fallback_reason}",
                )
                result = await run_planner(fallback)
                fallback_diagnostics = dict(result.provider_diagnostics)
                fallback_diagnostics["outbound_request_count"] = (
                    int(primary_diagnostics.get("outbound_request_count", 0) or 0)
                    + int(fallback_diagnostics.get("outbound_request_count", 0) or 0)
                )
                fallback_diagnostics["request_reached_provider"] = bool(
                    primary_diagnostics.get("request_reached_provider")
                    or fallback_diagnostics.get("request_reached_provider")
                )
                result = replace(result, provider_diagnostics=fallback_diagnostics)
            self._apply_runtime_result(run, result)
            if run.autonomy == "plan_only" and result.classification is RuntimeClassification.PASS:
                self._complete_node(run, "implementation", "Read-only implementation plan prepared")
                self._skip_node(run, "validation", "Plan mode does not build or mutate the project")
                self._skip_node(run, "review", "Plan mode is read-only")
                self._skip_node(run, "apply", "Plan mode is read-only")
                self._skip_node(run, "flash", "Plan mode cannot perform hardware actions")
                run.status = "completed"
                run.classification = "PLAN_READY"
                run.active_workspace_unchanged = True
                self._event(run, "plan.ready", status="completed", message="Read-only Forge plan is ready", plan=run.agent_plan)
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
        try:
            baseline = self.change_service.inspect_workspace(workspace)
            stage = self.change_service.create_stage(f"workflow-{run.run_id}")
            for relative in sorted(baseline.files):
                source = workspace.joinpath(*relative.split("/"))
                target = stage.joinpath(*relative.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            sandbox_workspace = dict(active_workspace)
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
                fallback_provider_id=fallback_provider_id,
                progress_callback=progress,
                steering_callback=lambda: self._consume_steering(run.run_id),
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
            diagnostics = _safe_mapping(result.get("provider_diagnostics")) or {}
            actual_provider = result.get("actual_provider_id")
            if isinstance(actual_provider, str) and actual_provider:
                run.actual_provider_id = actual_provider
            actual_model = result.get("model_id") or diagnostics.get("model_id")
            if isinstance(actual_model, str) and actual_model:
                run.model_id = actual_model
            fallback_reason = result.get("fallback_reason") or diagnostics.get("fallback_reason")
            if isinstance(fallback_reason, str) and fallback_reason:
                run.fallback_reason = fallback_reason
            run.outbound_request_count = int(diagnostics.get("outbound_request_count", 0) or 0)
            run.request_reached_provider = diagnostics.get("request_reached_provider") is True
            http_status = diagnostics.get("http_status")
            run.http_status = http_status if isinstance(http_status, int) and not isinstance(http_status, bool) else None
            provider_request_id = diagnostics.get("provider_request_id")
            run.provider_request_id = provider_request_id if isinstance(provider_request_id, str) else None
            run.build_result = _safe_mapping(result.get("build_result"))
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
            try:
                change_set = self.change_service.create_from_stage(
                    provider_id=run.provider_id,
                    workspace_root=workspace,
                    stage_root=stage,
                    baseline=baseline,
                    authorized_paths=authorized_paths,
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
                approval = self.approvals.issue(
                    run_id=run.run_id,
                    action_type="flash",
                    binding=_flash_binding(run),
                    summary="Flash the verified build artifact to the selected device once.",
                )
                run.approval_id = approval.approval_id
                run.approval_expires_at = approval.expires_at
                run.status = "awaiting_flash_confirmation"
                run.classification = "BUILD_READY_FOR_FLASH"
                self._event(run, "runtime.awaiting_flash_confirmation", status=run.status, message="Build completed; waiting for flash confirmation")
            else:
                self._skip_node(run, "flash", "Build-only autonomy does not touch hardware")
                run.status = "completed"
                run.classification = "AUTONOMOUS_WORKFLOW_COMPLETED"
                self._event(run, "runtime.completed", status="completed", message="Build completed")
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
            self._event(
                run,
                "runtime.failed",
                status="failed",
                message="Embedded workflow finalization failed",
                classification=run.classification,
            )
            self._persist_run(run)

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
        response = await self.reviewer({"change_set": dict(change_set), "risk": assessment.to_dict()})
        verdict = str(response.get("verdict") or "block").casefold()
        if verdict not in {"approve", "request_changes", "block"}:
            verdict = "block"
        self._complete_node(run, "review", str(response.get("summary") or f"Reviewer verdict: {verdict}"))
        return verdict

    def _capability_root(self, run: Any) -> Path:
        if run.change_set_id:
            return Path(self.change_service.get(run.change_set_id).workspace_root).resolve(strict=True)
        build = run.build_result if isinstance(run.build_result, Mapping) else {}
        firmware_path = build.get("firmware_path") or build.get("artifact_path")
        if isinstance(firmware_path, str) and firmware_path:
            return Path(firmware_path).resolve(strict=True).parent
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
        http_status = diagnostics.get("http_status")
        run.http_status = http_status if isinstance(http_status, int) and not isinstance(http_status, bool) else None
        request_id = diagnostics.get("provider_request_id")
        run.provider_request_id = request_id if isinstance(request_id, str) else None
        if result.classification is RuntimeClassification.CANCELLED:
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
        if event_type == "message.delta":
            # High-frequency token deltas update the transcript events without
            # rewriting the durable run row on every chunk.
            self._event(run, event_type, **payload)
            return
        normalized = event_type.upper()
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
        safe.update(run_activity_fields(safe))
        event = self.store.append_event(self._session_for_run[run.run_id], safe)
        run.events.append(dict(event))
        return event

    def _persist_graph(self, run: Any) -> None:
        for node in self._nodes[run.run_id].values():
            self.store.save_node(run.run_id, node.node_id, node.status, node.to_dict())
        self._persist_run(run)

    def _persist_run(self, run: Any) -> None:
        run.updated_at = utc_now()
        payload = run.to_safe_dict()
        payload["session_id"] = self._session_for_run[run.run_id]
        self.store.save_run(
            run.run_id,
            self._session_for_run[run.run_id],
            run.project_id,
            run.status,
            payload,
        )

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
                    "build_result", "flash_result", "monitor_result", "flash_port", "flash_board_type",
                    "flash_environment", "start_monitor_after_flash", "approval_id", "approval_expires_at",
                    "assistant_message_override", "stage_statuses", "stage_messages",
                    "validation_report", "repair_attempt_count", "pending_decision", "suggested_alternatives",
                ):
                    if name in payload and hasattr(run, name):
                        setattr(run, name, payload[name])
                if run.status not in TERMINAL_STATUSES and run.status != "awaiting_flash_confirmation":
                    run.status = "failed"
                    run.classification = "AGENT_RUNTIME_INTERRUPTED"
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
            if run.classification == "AGENT_RUNTIME_INTERRUPTED":
                self._event(run, "runtime.interrupted", status="failed", message="The backend restarted during this action run")
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


def _safe_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return {str(key): item for key, item in value.items() if isinstance(item, (str, int, bool, float, type(None), dict, list))}


def _flash_binding(
    run: Any,
    *,
    port: str | None = None,
    board_type: str | None = None,
    environment: str | None = None,
    start_monitor_after_flash: bool | None = None,
) -> dict[str, object]:
    build = run.build_result if isinstance(run.build_result, Mapping) else {}
    artifact_identity = {
        key: build.get(key)
        for key in (
            "artifact_hash",
            "firmware_hash",
            "checksum",
            "artifact_path",
            "firmware_path",
            "environment",
            "project_id",
        )
        if build.get(key) is not None
    }
    return {
        "run_id": run.run_id,
        "project_id": run.project_id,
        "port": port if port is not None else run.flash_port,
        "board_type": board_type if board_type is not None else run.flash_board_type,
        "environment": environment if environment is not None else run.flash_environment,
        "artifact": artifact_identity,
        "start_monitor_after_flash": (
            bool(start_monitor_after_flash)
            if start_monitor_after_flash is not None
            else bool(run.start_monitor_after_flash)
        ),
    }


def _approved_artifact_hash(run: Any) -> str | None:
    build = run.build_result if isinstance(run.build_result, Mapping) else {}
    value = build.get("firmware_hash") or build.get("artifact_hash") or build.get("checksum")
    return str(value) if isinstance(value, str) and value else None
