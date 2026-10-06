"""Forge Agent V3 continuous model -> tool -> observation loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from backend.changes import ChangeSet, ChangeSetError, ChangeSetService, IGNORED_DIRS
from backend.changes.service import is_safe_relative_path

from .api_planner_provider import ApiPlannerError
from .capabilities import CapabilityGrant, ToolPolicyEngine
from .canonical_runtime import AttemptAccumulator, LoopGuard, OrderedToolScheduler, ToolInvocation
from .checkpoint_files import copy_checkpoint_tree
from .context_assembler import ContextAssembler
from .public_progress import LivePlan, public_text
from .skills import SkillRegistry
from .tool_contracts import (
    ProductToolPlan,
    RuntimeClassification,
    RuntimeEvent,
    RuntimeEventType,
    ToolCall,
    ToolName,
    WRITE_TOOLS,
)
from .tool_executor import ForgeXToolExecutor, ToolHandler
from .tool_policy import ToolPermissionPolicy, ToolPolicyError, is_sensitive_path


@dataclass(frozen=True, slots=True)
class ProductRuntimeLimits:
    max_turns: int = 12
    max_tool_calls_per_turn: int = 6
    max_total_tool_calls: int = 48
    max_runtime_seconds: float = 300.0
    max_created_files: int = 20
    max_modified_files: int = 30
    max_bytes_per_file: int = 128_000
    max_observation_chars: int = 16_000


@dataclass(frozen=True, slots=True)
class ToolRuntimeResult:
    classification: RuntimeClassification
    provider_id: str
    change_set_id: str | None = None
    created_file_count: int = 0
    modified_file_count: int = 0
    deleted_file_count: int = 0
    active_workspace_unchanged: bool = False
    provider_classification: str | None = None
    provider_diagnostics: Mapping[str, object] = field(default_factory=dict)
    execution_attempted: bool = False
    tool_execution_count: int = 0
    plan: tuple[Mapping[str, str], ...] = field(default_factory=tuple)
    assistant_message: str | None = None
    requirement_report: Mapping[str, object] = field(default_factory=dict)
    events: tuple[RuntimeEvent, ...] = field(default_factory=tuple)

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification.value,
            "provider_kind": "continuous_tool_agent",
            "provider_id": self.provider_id,
            "runtime": "forgex_agent_v3_loop",
            "change_set_id": self.change_set_id,
            "created_file_count": self.created_file_count,
            "modified_file_count": self.modified_file_count,
            "deleted_file_count": self.deleted_file_count,
            "active_workspace_unchanged": self.active_workspace_unchanged,
            "provider_classification": self.provider_classification,
            "provider_diagnostics": dict(self.provider_diagnostics),
            "execution_attempted": self.execution_attempted,
            "tool_execution_count": self.tool_execution_count,
            "plan": [dict(item) for item in self.plan],
            "assistant_message": self.assistant_message,
            "events": [event.to_safe_dict() for event in self.events],
        }


class ForgeXToolRuntime:
    def __init__(
        self,
        *,
        active_workspace_root: str | Path,
        change_service: ChangeSetService | None = None,
        managed_stage_root: str | Path | None = None,
    ) -> None:
        self.active_workspace_root = Path(active_workspace_root).resolve(strict=True)
        if change_service is None:
            stage_root = Path(
                managed_stage_root
                or (self.active_workspace_root.parent / ".forgex-changes")
            )
            change_service = ChangeSetService(
                state_root=stage_root / "state", staging_root=stage_root / "staging"
            )
        self.change_service = change_service
        self._events: list[RuntimeEvent] = []
        self._recovery_baseline = None
        self._recovery_executor = None
        self._recovery_active_snapshot = None
        self._recovery_planner = None
        self._recovery_authorized_paths: set[str] = set()

    def _save_resume_stage(self, stage, revision, observations, executor, authorized, calls, elapsed, turn):
        snapshot = self.change_service.create_stage("checkpoint-" + uuid.uuid4().hex)
        copy_checkpoint_tree(stage, snapshot)
        return {"stage_path": str(snapshot), "stage_revision": self.change_service.inspect_workspace(snapshot).revision,
                "workspace_revision": revision, "observations": list(_compact_tool_observations(observations)),
                "deleted_paths": sorted(executor.deleted_paths), "authorized_paths": sorted(authorized),
                "plan": list(executor.current_plan), "tool_calls_used": calls,
                "elapsed_seconds": elapsed, "turn": turn, "stage_mode": "overlay"}

    def run_product(self, **kwargs):
        """Synchronous compatibility seam; production uses the async engine."""
        return asyncio.run(self.run_product_async(**kwargs))

    async def run_product_async(
        self,
        *,
        task: str,
        planner: object,
        policy: ToolPermissionPolicy,
        limits: ProductRuntimeLimits | None = None,
        cancel_event: threading.Event | None = None,
        event_callback=None,
        policy_engine: ToolPolicyEngine | None = None,
        capability_grant: CapabilityGrant | None = None,
        capability_authorization_source: str = "explicit_edit_request",
        capability_run_id: str | None = None,
        capability_node_id: str = "firmware_implementer",
        capability_session_id: str | None = None,
        service_handlers: Mapping[ToolName, ToolHandler] | None = None,
        memory_entries: Sequence[Mapping[str, object]] = (),
        steering_callback=None,
        allow_no_changes_success: bool = False,
        retry_change_set_id: str | None = None,
        retry_context: Mapping[str, object] | None = None,
        resume_checkpoint: Mapping[str, object] | None = None,
        task_contract: Mapping[str, object] | None = None,
        budget_consumed: Mapping[str, object] | None = None,
        environment: str | None = None,
    ) -> ToolRuntimeResult:
        from .engine import AsyncAgentEngine, PhaseBudget
        engine = AsyncAgentEngine()
        bounds = limits or ProductRuntimeLimits()
        cancelled = cancel_event or threading.Event()
        publish = event_callback or (lambda event: None)

        def emit(event):
            if (
                cancelled.is_set()
                and event.get("event_type") == "assistant.summary.delta"
            ):
                return
            publish(event)

        self._events = []
        self._recovery_planner = planner
        self._event(RuntimeEventType.RUNTIME_STARTED)
        provider_id = str(getattr(planner, "provider_id", "unknown_planner"))
        if resume_checkpoint and callable(getattr(planner, "restore_budget", None)):
            planner.restore_budget(resume_checkpoint.get("provider_budget"))
        request_turn = getattr(planner, "request_turn", None)
        if not isinstance(task, str) or not task.strip() or not callable(request_turn):
            return self._failure(RuntimeClassification.PROVIDER_INVALID, provider_id)

        emit(
            {
                "event_type": "context.started",
                "status": "running",
                "message": "Reading project context and previous decisions",
            }
        )
        active_before = _active_integrity_snapshot(self.active_workspace_root)
        baseline = self.change_service.inspect_workspace(self.active_workspace_root)
        started = time.monotonic()
        stage = self.change_service.create_stage(f"agent-v3-{uuid.uuid4().hex}")
        skill_registry = SkillRegistry(self.active_workspace_root)
        skill_labels = tuple(item.label() for item in skill_registry.discover())
        # Deterministic in-process template providers match the literal user task.
        # API/model providers receive the richer tool-first project context.
        provider_kind = str(getattr(planner, "provider_kind", ""))
        provider_task = (
            task
            if provider_kind == "template_provider"
            else ContextAssembler(self.active_workspace_root)
            .assemble(
                task=task,
                snapshot=baseline,
                memories=memory_entries,
                skills=skill_labels,
                environment=environment,
            )
            .render()
        )
        recalled_context = [
            {
                "kind": str(item.get("kind") or "project context")[:80],
                "summary": public_text(str(item.get("value") or ""), 240),
            }
            for item in memory_entries[:8]
            if isinstance(item, Mapping) and item.get("verified") is True
        ]
        emit(
            {
                "event_type": "context.completed",
                "status": "completed",
                "message": "Forge context prepared for model planning",
                "context_files": len(baseline.files),
                "memory_count": len(recalled_context),
                "skill_count": len(skill_labels),
                "recalled_context": recalled_context,
            }
        )

        handlers = dict(service_handlers or {})
        requirement_report: dict = {}
        requirement_checks: dict = {}
        if task_contract:
            from .task_contract import verify_requirement_evidence
            provider_task += "\nTask requirements (data, not privileges):\n" + json.dumps(task_contract.get("requirements", []))
            provider_task += "\nExcluded features:\n" + json.dumps(task_contract.get("exclusions", []))
            provider_task += "\nRevision rule: " + task_contract.get("revision_rule","Preserve unrelated code and accepted choices.")
            provider_task += "\nUse verify_requirements with every requirement id and exact executable-source quotes before finishing."
            def verify_requirements(arguments):
                revision = repr(_active_integrity_snapshot(stage))
                if requirement_report.get("workspace_revision") != revision:
                    requirement_checks.clear()
                verify_requirement_evidence(task_contract, arguments["checks"], self.active_workspace_root,
                    revision, read_file=executor._read_overlay_text)
                requirement_checks.update({check["id"]: check for check in arguments["checks"]})
                report = verify_requirement_evidence(task_contract, list(requirement_checks.values()), self.active_workspace_root,
                    revision, read_file=executor._read_overlay_text)
                requirement_report.clear()
                requirement_report.update(report)
                return report
            handlers[ToolName.VERIFY_REQUIREMENTS] = verify_requirements
        allowed = [
            tool.value
            for tool in ToolName
            if policy.decisions.get(tool, "deny") != "deny"
        ]
        # Host-backed tools are never advertised unless a trusted handler exists.
        for tool in (
            ToolName.MEMORY_SEARCH,
            ToolName.SPAWN_SUBAGENT,
            ToolName.BUILD_FIRMWARE,
            ToolName.RUN_COMMAND,
            ToolName.SESSION_EVENT_READ,
            ToolName.VERIFY_REQUIREMENTS,
            ToolName.SESSION_EVENT_SEARCH,
            ToolName.SESSION_TRACE,
        ):
            if tool not in handlers and tool.value in allowed:
                allowed.remove(tool.value)
        from .tool_registry import available_tools
        allowed_tools = available_tools(policy, handlers)

        if policy_engine is not None and capability_grant is None:
            prior_elapsed = max(
                float((resume_checkpoint or {}).get("elapsed_seconds", 0)),
                float((budget_consumed or {}).get("elapsed_seconds", 0)),
            )
            capability_grant = policy_engine.issue_grant(
                run_id=capability_run_id or stage.name,
                node_id=capability_node_id,
                # Capabilities bind to the active project; writes still physically land in stage.
                workspace_root=self.active_workspace_root,
                allowed_tools=allowed_tools,
                authorization_source=capability_authorization_source,
                session_id=capability_session_id,
                ttl_seconds=max(1.0, bounds.max_runtime_seconds - prior_elapsed
                                - (time.monotonic() - started)),
                max_calls=bounds.max_total_tool_calls,
                allow_build=ToolName.BUILD_FIRMWARE.value in allowed_tools,
            )
        executor = ForgeXToolExecutor(
            write_root=stage,
            read_root=self.active_workspace_root,
            policy=policy,
            policy_engine=policy_engine,
            capability_grant=capability_grant,
            service_handlers=handlers,
            skill_registry=skill_registry,
            environment=environment,
        )
        self._recovery_baseline = baseline
        self._recovery_executor = executor
        self._recovery_active_snapshot = active_before
        if resume_checkpoint:
            source = Path(str(resume_checkpoint["stage_path"])).absolute()
            if source.parent != self.change_service.staging_root:
                raise ValueError("AGENT_RESUME_STAGE_INVALID")
            if self.change_service.inspect_workspace(source).revision != resume_checkpoint["stage_revision"]:
                raise ValueError("AGENT_RESUME_STAGE_CHANGED")
            copy_checkpoint_tree(source, stage)
            executor.deleted_paths.update(str(item) for item in resume_checkpoint.get("deleted_paths", ()))
            executor.current_plan = tuple(resume_checkpoint.get("plan", ()))
        if retry_change_set_id:
            try:
                draft = self.change_service.get(retry_change_set_id)
                self.change_service.materialize_review_draft(
                    retry_change_set_id, workspace_root=self.active_workspace_root,
                    target_stage=stage,
                )
                executor.deleted_paths.update(
                    item.path for item in draft.changed_files if item.change_type == "deleted"
                )
            except Exception:
                shutil.rmtree(stage, ignore_errors=True)
                raise
        if retry_change_set_id or resume_checkpoint:
            staged_files = sorted(self.change_service.inspect_workspace(stage).files)
            provider_task += (
                "\nRECOVERED STAGED WORK: The active-project baseline above is not "
                "the staged recovery overlay. These files already exist in the overlay: "
                + json.dumps(staged_files[:100])
                + ". Inspect them with list_files/read_file and continue from the saved "
                "work. Preserve completed files and confirmed choices; do not restart "
                "the project from an empty workspace."
            )
        self._event(RuntimeEventType.STAGE_CREATED, count=len(baseline.files))
        emit(
            {
                "event_type": "changes.stage.created",
                "status": "running",
                "message": "Forge V3 overlay workspace created",
            }
        )

        total_calls = int(resume_checkpoint.get("tool_calls_used", 0)) if resume_checkpoint else 0
        total_calls = max(total_calls,int((budget_consumed or {}).get("tool_calls_used",0)))
        denied_seen = False
        denied_classification: RuntimeClassification | None = None
        observations: list[Mapping[str, object]] = list(resume_checkpoint.get("observations", ())) if resume_checkpoint else []
        if retry_context:
            observations.append({"call_id": "retry_context", "status": "completed",
                                 "tool": "previous_run_failure", "output": dict(retry_context)})
        authorized_paths: set[str] = (
            {item.path for item in draft.changed_files} if retry_change_set_id else set()
        )
        self._recovery_authorized_paths = authorized_paths
        if resume_checkpoint:
            authorized_paths.update(str(item) for item in resume_checkpoint.get("authorized_paths", ()))
        final_received = False
        final_summary: str | None = None
        live_plan = LivePlan()
        from .progress_state import ProgressState, continuation_summary
        durable = (getattr(planner, "supports_durable_continuation", False)
                   and os.getenv("FORGEX_ENABLE_PROGRESS_CONTINUATION", "1") == "1")
        progress_state = ProgressState((resume_checkpoint or {}).get("progress_state"))
        if not (resume_checkpoint or {}).get("progress_state"):
            for previous in observations:
                progress_state.observe(previous)
        if resume_checkpoint and resume_checkpoint.get("continuation_summary"):
            observations.append({"tool": "saved_task_summary", "status": "completed",
                                 "output": resume_checkpoint["continuation_summary"]})
        extension_turns = max(0, 600 - bounds.max_turns) if durable else min(12, max(0, 60 - bounds.max_turns))
        meaningful_turns: list[int] = []
        compacted_prefix_count = 0
        loop_guard = LoopGuard(
            max_steps=bounds.max_turns + extension_turns,
            max_tool_calls=bounds.max_total_tool_calls,
            max_same_call_repeats=4 if durable else max(4, bounds.max_turns),
        )
        if total_calls:
            loop_guard.consume_child_tool_calls(total_calls)
        try:
            first_turn = int(resume_checkpoint.get("turn", 0)) + 1 if resume_checkpoint else 1
            prior_elapsed = float(resume_checkpoint.get("elapsed_seconds", 0)) if resume_checkpoint else 0
            prior_elapsed = max(prior_elapsed,float((budget_consumed or {}).get("elapsed_seconds",0)))
            started -= prior_elapsed
            bind_deadline = getattr(planner, "bind_deadline", None)
            if callable(bind_deadline):
                bind_deadline(started + bounds.max_runtime_seconds - 5.0)
            bind_reserve = getattr(planner, "bind_verification_reserve", None)
            if callable(bind_reserve):
                bind_reserve(PhaseBudget.for_task(policy.decisions.get(ToolName.BUILD_FIRMWARE) == "allow").generation_reserve)
            if durable:
                admission = self._save_resume_stage(stage, baseline.revision, observations,
                    executor, authorized_paths, total_calls, prior_elapsed, first_turn - 1)
                # Preserve continuation guards and context; the copied stage
                # and its seal above belong to this new attempt.
                admission = {**dict(resume_checkpoint or {}), **admission}
                emit({"event_type": "runtime.admitted", "turn": first_turn - 1, "tool_count": total_calls,
                      "_resume_checkpoint": admission})
            async for turn in engine.turns(first_turn, bounds.max_turns + extension_turns):
                if turn > bounds.max_turns and (turn - bounds.max_turns - 1) % 6 == 0:
                    if not (progress_state.last_progress_turn >= turn - 5 if durable else any(previous >= turn - 5 for previous in meaningful_turns)):
                        return self._abort_stage(
                            stage, RuntimeClassification.MAX_TURNS, provider_id, total_calls
                        )
                    emit({"event_type": "agent.turn_budget_extended", "status": "running",
                          "turn": turn, "new_limit": min(bounds.max_turns + extension_turns, turn + 5)})
                observation_start = len(observations)
                if cancelled.is_set():
                    return self._abort_stage(
                        stage, RuntimeClassification.CANCELLED, provider_id, total_calls
                    )
                if time.monotonic() - started > bounds.max_runtime_seconds:
                    return self._abort_stage(
                        stage,
                        RuntimeClassification.LIMIT_EXCEEDED,
                        provider_id,
                        total_calls,
                    )
                steering = tuple(
                    steering_callback() if callable(steering_callback) else ()
                )
                if steering:
                    for message in steering:
                        observations.append(
                            {
                                "call_id": f"steer_{turn}",
                                "status": "completed",
                                "tool": "user_steer",
                                "output": str(message)[:4000],
                            }
                        )
                    emit(
                        {
                            "event_type": "agent.steered",
                            "status": "running",
                            "turn": turn,
                            "message": str(steering[-1])[:500],
                        }
                    )
                compacted = _compact_tool_observations(observations)
                if compacted and compacted[0].get("tool") == "context_compaction":
                    count = int(compacted[0]["output"]["compacted_observations"])
                    if count > compacted_prefix_count:
                        older = observations[:count]
                        emit({"event_type":"CONTEXT_CHECKPOINT_CREATED","checkpoint":{
                            "compacted_observations":count,
                            "observation_digest":hashlib.sha256(json.dumps(older,ensure_ascii=True,sort_keys=True,default=str).encode()).hexdigest()},
                            "_context_artifact_delta":[dict(item) for item in older[compacted_prefix_count:]]})
                        compacted_prefix_count = count
                turn_id = f"{capability_run_id or stage.name}:turn-{turn}"
                step = turn
                loop_guard.before_step()
                attempt = AttemptAccumulator(turn=turn, step=step)
                emit(
                    {
                        "event_type": "turn.started",
                        "status": "running",
                        "turn": turn,
                        "step": step,
                        "turn_id": turn_id,
                        "message": f"Agent turn {turn}",
                    }
                )
                emit(
                    {
                        "event_type": "step.started",
                        "status": "running",
                        "turn": turn,
                        "step": step,
                        "turn_id": turn_id,
                        "message": f"Agent step {step}",
                    }
                )
                emit(
                    {
                        "event_type": "model.attempt.started",
                        "status": "running",
                        "turn": turn,
                        "step": step,
                        "turn_id": turn_id,
                        "attempt_id": attempt.attempt_id,
                        "message": "Waiting for model",
                        "model_id": str(getattr(planner, "model_id", "")),
                    }
                )
                # Legacy migration events remain until all existing projections consume
                # the canonical journal vocabulary above.
                emit(
                    {
                        "event_type": "agent.turn.started",
                        "status": "running",
                        "turn": turn,
                        "step": step,
                        "turn_id": turn_id,
                        "attempt_id": attempt.attempt_id,
                        "message": "Forge is deciding the next tool action",
                    }
                )
                emit(
                    {
                        "event_type": "thinking.started",
                        "status": "running",
                        "turn": turn,
                        "step": step,
                        "turn_id": turn_id,
                        "attempt_id": attempt.attempt_id,
                        "message": "Forge is deciding the next tool action",
                    }
                )
                emit(
                    {
                        "event_type": "agent.model.requested",
                        "status": "running",
                        "turn": turn,
                        "step": step,
                        "turn_id": turn_id,
                        "attempt_id": attempt.attempt_id,
                        "message": "Requesting next model tool plan",
                        "model_id": str(getattr(planner, "model_id", "")),
                    }
                )
                request_turn_stream = getattr(planner, "request_turn_stream", None)
                streamed_summary = ""

                def on_summary(message: str) -> None:
                    nonlocal streamed_summary
                    current = public_text(str(message), 1200)
                    delta = (
                        current[len(streamed_summary) :]
                        if current.startswith(streamed_summary)
                        else current
                    )
                    streamed_summary = current
                    if delta:
                        frame = attempt.content_delta(delta)
                        emit(
                            {
                                **frame.to_event(),
                                "status": "running",
                                "turn_id": turn_id,
                                "message": delta,
                            }
                        )
                    # Compatibility channel for existing clients during migration.
                    emit(
                        {
                            "event_type": "assistant.summary.delta",
                            "status": "running",
                            "turn": turn,
                            "step": step,
                            "turn_id": turn_id,
                            "attempt_id": attempt.attempt_id,
                            "message": current,
                            "transient": True,
                        }
                    )

                raw_plan = await engine.request(planner, task=provider_task,
                    tool_results=compacted, allowed_tools=allowed_tools,
                    run_id=capability_run_id or stage.name, turn=turn, on_summary=on_summary)
                plan = (
                    raw_plan
                    if isinstance(raw_plan, ProductToolPlan)
                    else ProductToolPlan.parse(
                        raw_plan, max_calls=bounds.max_tool_calls_per_turn
                    )
                )
                if len(plan.tool_calls) > bounds.max_tool_calls_per_turn:
                    return self._abort_stage(
                        stage,
                        RuntimeClassification.LIMIT_EXCEEDED,
                        provider_id,
                        total_calls,
                    )

                # The attempt settles durably before any provider-originated call can
                # enter validation or execution. This is the canonical execution barrier.
                settlement = engine.settle(attempt,
                    plan.tool_calls,
                    content=public_text(plan.summary),
                )
                emit({**settlement.to_event(), "status": "completed", "turn_id": turn_id})
                emit(
                    {
                        "event_type": "agent.model.responded",
                        "status": "running",
                        "turn": turn,
                        "step": step,
                        "turn_id": turn_id,
                        "attempt_id": attempt.attempt_id,
                        "tool_count": len(plan.tool_calls),
                        "message": public_text(plan.summary),
                        "model_id": str(getattr(planner, "model_id", "")),
                    }
                )
                emit(
                    {
                        "event_type": "assistant.summary",
                        "status": "running",
                        "turn": turn,
                        "step": step,
                        "turn_id": turn_id,
                        "attempt_id": attempt.attempt_id,
                        "message": public_text(plan.summary),
                    }
                )
                if plan.final:
                    if task_contract and (not requirement_report.get("references_valid") or
                            requirement_report.get("workspace_revision") != repr(_active_integrity_snapshot(stage))):
                        observations.append({"call_id": f"requirement_hint_{turn}", "tool": "requirement_validation", "status": "completed",
                            "output": {"message": "Call verify_requirements for every requirement id before finishing; supply exact source evidence.", "report": requirement_report}})
                        continue
                    if task_contract:
                        from .verification import review_requirements
                        revision = repr(_active_integrity_snapshot(stage))
                        reviewed = await review_requirements(planner, task_contract, requirement_report,
                            executor._read_overlay_text, revision, deadline=started + bounds.max_runtime_seconds,
                            run_id=capability_run_id or stage.name)
                        if revision != repr(_active_integrity_snapshot(stage)):
                            raise ValueError("SEMANTIC_REVIEW_SOURCE_CHANGED")
                        requirement_report.update(reviewed)
                        if not reviewed["valid"]:
                            observations.append({"call_id": f"semantic_review_{turn}", "tool": "semantic_review",
                                                 "status": "failed", "output": reviewed})
                            continue
                    final_received = True
                    final_summary = public_text(plan.summary).strip() or None
                    emit(
                        {
                            "event_type": "step.settled",
                            "status": "completed",
                            "turn": turn,
                            "step": step,
                            "turn_id": turn_id,
                            "attempt_id": attempt.attempt_id,
                            "tool_count": 0,
                        }
                    )
                    emit(
                        {
                            "event_type": "turn.settled",
                            "status": "completed",
                            "turn": turn,
                            "step": step,
                            "turn_id": turn_id,
                            "attempt_id": attempt.attempt_id,
                            "outcome": "completed",
                            "message": final_summary or "Agent turn completed",
                        }
                    )
                    emit(
                        {
                            "event_type": "agent.turn.completed",
                            "status": "completed",
                            "turn": turn,
                            "step": step,
                            "turn_id": turn_id,
                            "attempt_id": attempt.attempt_id,
                            "tool_count": 0,
                            "message": public_text(plan.summary),
                        }
                    )
                    break
                if total_calls + len(plan.tool_calls) > bounds.max_total_tool_calls:
                    return self._abort_stage(
                        stage,
                        RuntimeClassification.LIMIT_EXCEEDED,
                        provider_id,
                        total_calls,
                    )

                stage_before_turn = _active_integrity_snapshot(stage)
                deleted_before_turn = set(executor.deleted_paths)
                invocation_slots: dict[int, Mapping[str, object]] = {}
                admitted: list[ToolInvocation] = []
                calls_by_id = {item.call_id: item for item in plan.tool_calls}

                # Host validation/policy happens before scheduler admission. Denied
                # calls become deterministic observations but never execute.
                for invocation in settlement.invocations:
                    product_call = calls_by_id[invocation.call_id]
                    emit(
                        {
                            "event_type": "tool.pending",
                            "status": "running",
                            "tool": invocation.tool.value,
                            "tool_call_id": invocation.call_id,
                            "call_id": invocation.call_id,
                            "turn": turn,
                            "step": step,
                            "turn_id": turn_id,
                            "attempt_id": attempt.attempt_id,
                            "message": "Tool call queued",
                            "arguments": _tool_arguments_summary(invocation.arguments),
                        }
                    )
                    call = ToolCall(invocation.tool, dict(invocation.arguments))
                    try:
                        executor.validate(call)
                        loop_guard.admit(invocation)
                    except (ToolPolicyError, ValueError, RuntimeError) as exc:
                        denied_seen = True
                        classification = getattr(
                            exc, "classification", RuntimeClassification.POLICY_DENIED
                        )
                        if isinstance(classification, str):
                            try:
                                classification = RuntimeClassification(classification)
                            except ValueError:
                                classification = RuntimeClassification.LIMIT_EXCEEDED
                        denied_classification = denied_classification or classification
                        rejected = {
                            "call_id": invocation.call_id,
                            "status": "denied",
                            "tool": invocation.tool.value,
                            "classification": classification.value,
                            "error": str(exc)[:300],
                            "policy_details": dict(getattr(exc, "details", {})),
                        }
                        invocation_slots[invocation.invocation_index] = rejected
                        emit(
                            {
                                "event_type": "tool.invocation.rejected",
                                "status": "failed",
                                **invocation.to_event_payload(),
                                "turn_id": turn_id,
                                "classification": classification.value,
                                "message": str(exc)[:300],
                                "policy_details": dict(getattr(exc, "details", {})),
                            }
                        )
                        emit(
                            {
                                "event_type": "tool.failed",
                                "status": "failed",
                                "tool": invocation.tool.value,
                                "tool_call_id": invocation.call_id,
                                "turn_id": turn_id,
                                "classification": classification.value,
                                "message": str(exc)[:300],
                            }
                        )
                        continue
                    emit(
                        {
                            "event_type": "tool.invocation.admitted",
                            "status": "running",
                            **invocation.to_event_payload(),
                            "turn_id": turn_id,
                            "message": "Tool invocation admitted",
                        }
                    )
                    admitted.append(invocation)

                boundary_observations: dict[int, Mapping[str, object]] = {}
                boundary_authorized = set(authorized_paths)

                async def execute_invocation(invocation: ToolInvocation) -> Mapping[str, object]:
                    if cancelled.is_set():
                        raise RuntimeError("AGENT_RUNTIME_CANCELLED")
                    call = ToolCall(invocation.tool, dict(invocation.arguments))
                    emit(
                        {
                            "event_type": "tool.execution.started",
                            "status": "running",
                            **invocation.to_event_payload(),
                            "turn_id": turn_id,
                            "message": _tool_activity(call),
                            "arguments": _tool_arguments_summary(call.arguments),
                        }
                    )
                    emit(
                        {
                            "event_type": "tool.started",
                            "status": "running",
                            "tool": call.tool.value,
                            "tool_call_id": invocation.call_id,
                            "turn_id": turn_id,
                            "message": _tool_activity(call),
                            "arguments": _tool_arguments_summary(call.arguments),
                        }
                    )
                    try:
                        result = await asyncio.to_thread(executor.execute, call)
                    except Exception as exc:
                        missing_file = (
                            isinstance(exc, ToolPolicyError)
                            and exc.classification is RuntimeClassification.FILE_NOT_FOUND
                        )
                        emit(
                            {
                                "event_type": "tool.execution.settled",
                                "status": "failed",
                                **invocation.to_event_payload(),
                                "turn_id": turn_id,
                                "outcome": "failed",
                                "message": str(exc)[:500],
                                "error_code": (
                                    RuntimeClassification.FILE_NOT_FOUND.value
                                    if missing_file else type(exc).__name__
                                ),
                            }
                        )
                        emit(
                            {
                                "event_type": "tool.failed",
                                "status": "failed",
                                "tool": call.tool.value,
                                "tool_call_id": invocation.call_id,
                                "turn_id": turn_id,
                                "message": str(exc)[:500],
                                "error_code": (
                                    RuntimeClassification.FILE_NOT_FOUND.value
                                    if missing_file else type(exc).__name__
                                ),
                            }
                        )
                        if missing_file:
                            return {
                                "_forgex_error": f"File or directory not found: {call.arguments.get('path', '.')}",
                                "_forgex_classification": RuntimeClassification.FILE_NOT_FOUND.value,
                            }
                        if isinstance(exc, ValueError) and not isinstance(exc, ToolPolicyError):
                            return {"_forgex_error": str(exc)[:500]}
                        raise
                    boundary_observations[invocation.invocation_index] = _safe_tool_observation(
                        invocation.call_id, call.tool, result, bounds.max_observation_chars)
                    if call.tool in WRITE_TOOLS:
                        product_call = calls_by_id[invocation.call_id]
                        if product_call.path:
                            boundary_authorized.add(product_call.path)
                        if isinstance(result, Mapping):
                            for key in ("path", "destination"):
                                if isinstance(result.get(key), str):
                                    boundary_authorized.add(result[key])
                        # Seal the completed write before admitting the next effect.
                        emit({"event_type": "tool.execution.settled", "status": "completed",
                              **invocation.to_event_payload(), "turn_id": turn_id})
                        if event_callback is not None:
                            checkpoint = self._save_resume_stage(stage, baseline.revision,
                                [*observations, *(boundary_observations[index] for index in sorted(boundary_observations))],
                                executor, boundary_authorized, total_calls + len(boundary_observations),
                                time.monotonic() - started, turn)
                            checkpoint["provider_budget"] = planner.budget_snapshot() if callable(getattr(planner, "budget_snapshot", None)) else {}
                            emit({"event_type": "tool.checkpoint", "turn": turn, "status": "completed",
                                  "call_id": invocation.call_id, "_resume_checkpoint": checkpoint})
                    return {"_forgex_result": result}

                scheduled = await engine.execute(admitted, execute_invocation)
                duration_by_index: dict[int, int] = {}
                for scheduled_result in scheduled:
                    invocation = scheduled_result.invocation
                    if "_forgex_error" in scheduled_result.result:
                        invocation_slots[invocation.invocation_index] = {
                            "call_id": invocation.call_id,
                            "tool": invocation.tool.value,
                            "status": "failed",
                            "error": scheduled_result.result["_forgex_error"],
                            **(
                                {"classification": scheduled_result.result["_forgex_classification"]}
                                if "_forgex_classification" in scheduled_result.result else {}
                            ),
                        }
                        continue
                    result = scheduled_result.result.get("_forgex_result")
                    invocation_slots[invocation.invocation_index] = {"result": result}
                    duration_ms = max(
                        0,
                        int(
                            (scheduled_result.completed_at - scheduled_result.started_at)
                            * 1000
                        ),
                    )
                    duration_by_index[invocation.invocation_index] = duration_ms
                    emit(
                        {
                            "event_type": "tool.execution.settled",
                            "status": "completed",
                            **invocation.to_event_payload(),
                            "turn_id": turn_id,
                            "outcome": "completed",
                            "queued_at": scheduled_result.queued_at,
                            "started_at": scheduled_result.started_at,
                            "completed_at": scheduled_result.completed_at,
                            "execution_completion_index": scheduled_result.execution_completion_index,
                            "duration_ms": duration_ms,
                            "message": _tool_completion(
                                ToolCall(invocation.tool, dict(invocation.arguments))
                            ),
                        }
                    )

                # Commit observations/results in original model invocation order even
                # if safe reads completed out-of-order. This is model-visible history.
                for invocation in settlement.invocations:
                    outcome = invocation_slots.get(invocation.invocation_index)
                    if outcome is None:
                        continue
                    if outcome.get("status") == "denied":
                        observations.append(dict(outcome))
                        continue
                    if outcome.get("status") == "failed":
                        total_calls += 1
                        observations.append(dict(outcome))
                        emit({
                            "event_type": "tool.result.committed", "status": "failed",
                            **invocation.to_event_payload(), "turn_id": turn_id,
                            "message": outcome["error"],
                        })
                        continue
                    product_call = calls_by_id[invocation.call_id]
                    call = ToolCall(invocation.tool, dict(invocation.arguments))
                    result = outcome.get("result")
                    if call.tool in WRITE_TOOLS and product_call.path:
                        authorized_paths.add(product_call.path)
                        if isinstance(result, Mapping) and isinstance(result.get("path"), str):
                            authorized_paths.add(result["path"])
                        if call.tool is ToolName.MOVE_FILE:
                            authorized_paths.add(str(result["destination"]))
                    live_plan.observe(
                        invocation.call_id,
                        call.tool is not ToolName.UPDATE_PLAN
                        and not (
                            isinstance(result, Mapping)
                            and result.get("success") is False
                        ),
                        call.tool.value,
                    )
                    if call.tool is ToolName.UPDATE_PLAN:
                        snapshot = live_plan.update(executor.current_plan)
                        executor.current_plan = tuple(snapshot["steps"])
                        emit({"event_type": "plan.updated", **snapshot})
                    total_calls += 1
                    observation = _safe_tool_observation(
                        invocation.call_id,
                        call.tool,
                        result,
                        bounds.max_observation_chars,
                    )
                    observations.append(observation)
                    emit(
                        {
                            "event_type": "tool.result.committed",
                            "status": "completed",
                            **invocation.to_event_payload(),
                            "turn_id": turn_id,
                            "logical_result_index": invocation.invocation_index,
                            "message": _tool_completion(call),
                            "result": _tool_result_summary(result),
                        }
                    )
                    emit(
                        {
                            "event_type": "tool.completed",
                            "status": "completed",
                            "tool": call.tool.value,
                            "tool_call_id": invocation.call_id,
                            "turn_id": turn_id,
                            "tool_execution_count": total_calls,
                            "duration_ms": duration_by_index.get(invocation.invocation_index, 0),
                            "message": _tool_completion(call),
                            "result": _tool_result_summary(result),
                        }
                    )

                changed = stage_before_turn != _active_integrity_snapshot(stage) or deleted_before_turn != executor.deleted_paths
                for observation in observations[observation_start:]:
                    changed = progress_state.observe(observation) or changed
                progress_state.settle(turn, changed)
                if durable and progress_state.stalled_turns == 2:
                    hint = "The last turns added no new information. Use the saved observations to complete the task or explain the blocker."
                    observations.append({"tool": "agent_progress_hint", "status": "completed", "output": hint})
                    emit({"event_type": "agent.no_progress_warning", "turn": turn, "message": hint})
                emit(
                    {
                        "event_type": "step.settled",
                        "status": "completed",
                        "turn": turn,
                        "step": step,
                        "turn_id": turn_id,
                        "attempt_id": attempt.attempt_id,
                        "tool_count": len(settlement.invocations),
                    }
                )
                emit(
                    {
                        "event_type": "turn.settled",
                        "status": "completed",
                        "turn": turn,
                        "step": step,
                        "turn_id": turn_id,
                        "attempt_id": attempt.attempt_id,
                        "outcome": "continue",
                        "tool_count": len(settlement.invocations),
                        "_resume_checkpoint": {**self._save_resume_stage(
                            stage, baseline.revision, observations, executor, authorized_paths,
                            total_calls, time.monotonic() - started, turn),
                            "provider_budget": planner.budget_snapshot() if callable(getattr(planner, "budget_snapshot", None)) else {},
                            "progress_state": progress_state.snapshot(),
                            "continuation_summary": continuation_summary(task, executor.current_plan, observations, turn)}
                            if event_callback is not None else None,
                    }
                )
                emit(
                    {
                        "event_type": "agent.turn.completed",
                        "status": "running",
                        "turn": turn,
                        "step": step,
                        "turn_id": turn_id,
                        "attempt_id": attempt.attempt_id,
                        "tool_count": len(settlement.invocations),
                        "message": public_text(plan.summary),
                    }
                )
                if stage_before_turn != _active_integrity_snapshot(stage) or deleted_before_turn != executor.deleted_paths:
                    meaningful_turns.append(turn)
                if durable and progress_state.stalled_turns >= 3:
                    return self._abort_stage(stage, RuntimeClassification.NO_PROGRESS, provider_id, total_calls)

            if not final_received:
                return self._abort_stage(
                    stage, RuntimeClassification.MAX_TURNS, provider_id, total_calls
                )
            if denied_seen and total_calls == 0:
                return self._abort_stage(
                    stage,
                    denied_classification or RuntimeClassification.POLICY_DENIED,
                    provider_id,
                    total_calls,
                )
            if active_before != _active_integrity_snapshot(self.active_workspace_root):
                return self._abort_stage(
                    stage,
                    RuntimeClassification.UNSAFE_ABORTED,
                    provider_id,
                    total_calls,
                )

            try:
                change_set = self.change_service.create_from_stage(
                    provider_id=provider_id,
                    workspace_root=self.active_workspace_root,
                    stage_root=stage,
                    baseline=baseline,
                    authorized_paths=authorized_paths,
                    deleted_paths=executor.deleted_paths,
                )
            except ChangeSetError as exc:
                classification = _classification_for_change_error(exc)
                # A pure inspection/build turn is valid even if no workspace diff exists.
                if (
                    classification is RuntimeClassification.NO_CHANGES
                    and not authorized_paths
                    and (total_calls > 0 or allow_no_changes_success)
                ):
                    shutil.rmtree(stage, ignore_errors=True)
                    self._event(
                        RuntimeEventType.RUNTIME_COMPLETED,
                        classification=RuntimeClassification.PASS.value,
                    )
                    return ToolRuntimeResult(
                        classification=RuntimeClassification.PASS,
                        provider_id=provider_id,
                        active_workspace_unchanged=True,
                        provider_classification=_planner_classification(planner),
                        provider_diagnostics=_safe_provider_diagnostics(planner),
                        execution_attempted=True,
                        tool_execution_count=total_calls,
                        plan=tuple(executor.current_plan),
                        assistant_message=final_summary,
                        events=tuple(self._events),
                    )
                return self._abort_stage(
                    stage, classification, provider_id, total_calls
                )

            created, modified, deleted = _change_counts(change_set)
            if (
                created > bounds.max_created_files
                or modified + deleted > bounds.max_modified_files
            ):
                self.change_service.discard(change_set.change_set_id)
                return self._failure(
                    RuntimeClassification.LIMIT_EXCEEDED,
                    provider_id,
                    tool_execution_count=total_calls,
                )
            self._event(
                RuntimeEventType.CHANGESET_CREATED, count=len(change_set.changed_files)
            )
            self._event(
                RuntimeEventType.RUNTIME_COMPLETED,
                classification=RuntimeClassification.PASS.value,
            )
            return ToolRuntimeResult(
                classification=RuntimeClassification.PASS,
                provider_id=provider_id,
                change_set_id=change_set.change_set_id,
                created_file_count=created,
                modified_file_count=modified,
                deleted_file_count=deleted,
                active_workspace_unchanged=True,
                provider_classification=_planner_classification(planner),
                provider_diagnostics=_safe_provider_diagnostics(planner),
                execution_attempted=True,
                tool_execution_count=total_calls,
                plan=tuple(executor.current_plan),
                assistant_message=final_summary,
                requirement_report=dict(requirement_report),
                events=tuple(self._events),
            )
        except ApiPlannerError as exc:
            provider_failure = exc.safe_code.startswith("PROVIDER_")
            return self._abort_stage(
                stage,
                (
                    RuntimeClassification.PROVIDER_INVALID
                    if provider_failure
                    else RuntimeClassification.MODEL_OUTPUT_INVALID
                ),
                provider_id,
                total_calls,
                provider_classification=exc.safe_code,
                provider_diagnostics=_safe_provider_diagnostics(planner),
                execution_attempted=True,
            )
        except ToolPolicyError as exc:
            return self._abort_stage(stage, exc.classification, provider_id, total_calls)
        except PermissionError:
            return self._abort_stage(stage, RuntimeClassification.POLICY_DENIED, provider_id, total_calls)
        except (ValueError, TypeError):
            return self._abort_stage(
                stage, RuntimeClassification.UNKNOWN_SAFE_FAILURE, provider_id, total_calls,
                provider_classification="FORGEX_RUNTIME_ERROR",
            )
        except OSError:
            return self._abort_stage(stage, RuntimeClassification.UNSAFE_ABORTED, provider_id, total_calls)
        except Exception:
            return self._abort_stage(stage, RuntimeClassification.UNKNOWN_SAFE_FAILURE, provider_id, total_calls)

    def _abort_stage(
        self,
        stage: Path,
        classification: RuntimeClassification,
        provider_id: str,
        tool_execution_count: int,
        *,
        remove: bool = True,
        provider_classification: str | None = None,
        provider_diagnostics: Mapping[str, object] | None = None,
        execution_attempted: bool = False,
    ) -> ToolRuntimeResult:
        draft = None
        if remove and classification not in {
            RuntimeClassification.POLICY_DENIED,
            RuntimeClassification.PATH_UNSAFE, RuntimeClassification.UNSAFE_ABORTED,
        }:
            draft = self._save_recovery_draft(stage, provider_id)
        if remove and draft is None:
            shutil.rmtree(stage, ignore_errors=True)
        return self._failure(
            classification,
            provider_id,
            change_set_id=draft.change_set_id if draft else None,
            created=sum(item.change_type == "created" for item in draft.changed_files) if draft else 0,
            modified=sum(item.change_type == "modified" for item in draft.changed_files) if draft else 0,
            deleted=sum(item.change_type == "deleted" for item in draft.changed_files) if draft else 0,
            active_unchanged=classification is not RuntimeClassification.UNSAFE_ABORTED,
            provider_classification=provider_classification,
            provider_diagnostics=(provider_diagnostics if provider_diagnostics is not None
                                  else _safe_provider_diagnostics(self._recovery_planner)),
            execution_attempted=execution_attempted,
            tool_execution_count=tool_execution_count,
        )

    def _save_recovery_draft(self, stage: Path, provider_id: str) -> ChangeSet | None:
        baseline = self._recovery_baseline
        executor = self._recovery_executor
        if baseline is None or executor is None or self._recovery_active_snapshot != _active_integrity_snapshot(self.active_workspace_root):
            return None
        paths = []
        total_bytes = 0
        for path in stage.rglob("*"):
            if path.is_symlink():
                return None
            if not path.is_file():
                continue
            relative = path.relative_to(stage).as_posix()
            if not is_safe_relative_path(relative):
                return None
            paths.append(relative)
            total_bytes += path.stat().st_size
        deleted = set(executor.deleted_paths)
        if not set(paths).issubset(self._recovery_authorized_paths) or not deleted.issubset(self._recovery_authorized_paths):
            return None
        if len(paths) + len(deleted) > 100 or total_bytes > 20_000_000 or not all(is_safe_relative_path(path) for path in deleted):
            return None
        try:
            draft = self.change_service.create_from_stage(
                provider_id=provider_id, workspace_root=self.active_workspace_root,
                stage_root=stage, baseline=baseline,
                authorized_paths=(*paths, *deleted), deleted_paths=deleted,
                authorization_source="recovery_draft", draft=True,
            )
            self.change_service.prune_review_drafts(self.active_workspace_root, keep=3)
            return draft
        except ChangeSetError:
            return None

    def _failure(
        self,
        classification: RuntimeClassification,
        provider_id: str,
        *,
        created: int = 0,
        modified: int = 0,
        deleted: int = 0,
        active_unchanged: bool = False,
        provider_classification: str | None = None,
        provider_diagnostics: Mapping[str, object] | None = None,
        execution_attempted: bool = False,
        tool_execution_count: int = 0,
        change_set_id: str | None = None,
    ) -> ToolRuntimeResult:
        self._event(
            RuntimeEventType.RUNTIME_FAILED, classification=classification.value
        )
        return ToolRuntimeResult(
            classification=classification,
            provider_id=provider_id,
            change_set_id=change_set_id,
            created_file_count=created,
            modified_file_count=modified,
            deleted_file_count=deleted,
            active_workspace_unchanged=active_unchanged,
            provider_classification=provider_classification,
            provider_diagnostics=dict(provider_diagnostics or {}),
            execution_attempted=execution_attempted,
            tool_execution_count=tool_execution_count,
            events=tuple(self._events),
        )

    def _event(
        self,
        event_type: RuntimeEventType,
        *,
        classification: str | None = None,
        count: int | None = None,
    ) -> None:
        self._events.append(
            RuntimeEvent(event_type, len(self._events) + 1, classification, count)
        )


def _compact_tool_observations(
    observations: Sequence[Mapping[str, object]], *, keep: int = 24
) -> tuple[Mapping[str, object], ...]:
    from .context_memory import detailed_tail, summarize_observations, bounded_observation
    keep = detailed_tail(observations,keep=keep)
    if len(observations) <= keep:
        return tuple(bounded_observation(item) for item in observations)
    older = observations[:-keep]
    checkpoint = {
        "call_id": "context_checkpoint",
        "status": "completed",
        "tool": "context_compaction",
        "output": {
            **summarize_observations(older),
            "compacted_observations": len(older),
            "tools": [str(item.get("tool") or "unknown") for item in older[-30:]],
            "failures": sum(
                item.get("status") in {"failed", "denied"} for item in older
            ),
        },
    }
    return (checkpoint, *(bounded_observation(item) for item in observations[-keep:]))


def _safe_tool_observation(
    call_id: str, tool: ToolName, result: object, max_chars: int
) -> dict[str, object]:
    observation: dict[str, object] = {
        "call_id": call_id,
        "status": "completed",
        "tool": tool.value,
    }
    if isinstance(result, str):
        observation["output"] = result[:max_chars]
    elif isinstance(result, tuple):
        observation["output"] = list(result[:200])
    elif isinstance(result, Mapping):
        safe: dict[str, object] = {}
        for key, value in result.items():
            if isinstance(value, str):
                safe[str(key)] = value[:max_chars]
            elif isinstance(value, (int, float, bool, type(None))):
                safe[str(key)] = value
            elif isinstance(value, (list, tuple)):
                safe[str(key)] = list(value[:100])
        observation["output"] = safe
    else:
        observation["output"] = str(result)[:max_chars]
    return observation


def _tool_arguments_summary(arguments: Mapping[str, object]) -> dict[str, object]:
    summary: dict[str, object] = {}
    for key in ("path", "pattern", "query", "name", "environment", "role", "command"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            summary[key] = value[:500]
    if "content" in arguments and isinstance(arguments.get("content"), str):
        summary["content_bytes"] = len(str(arguments["content"]).encode("utf-8"))
    if "old_text" in arguments and isinstance(arguments.get("old_text"), str):
        summary["old_text_bytes"] = len(str(arguments["old_text"]).encode("utf-8"))
    if "new_text" in arguments and isinstance(arguments.get("new_text"), str):
        summary["new_text_bytes"] = len(str(arguments["new_text"]).encode("utf-8"))
    if isinstance(arguments.get("files"), list):
        summary["file_count"] = len(arguments["files"])
    if isinstance(arguments.get("after_sequence"), int):
        summary["after_sequence"] = int(arguments["after_sequence"])
    if isinstance(arguments.get("limit"), int):
        summary["limit"] = int(arguments["limit"])
    return summary


def _tool_result_summary(result: object) -> dict[str, object]:
    if isinstance(result, str):
        return {"bytes": len(result.encode("utf-8"))}
    if isinstance(result, tuple):
        return {"items": len(result)}
    if isinstance(result, Mapping):
        out: dict[str, object] = {}
        for key in (
            "path",
            "bytes",
            "created",
            "modified",
            "steps",
            "success",
            "status",
            "error",
            "exit_code",
            "output",
            "added",
            "removed",
            "diff",
            "count",
            "files",
        ):
            value = result.get(key)
            if isinstance(value, (str, int, bool, type(None))):
                out[key] = value
            elif isinstance(value, list):
                out[key] = value[:100]
        return out
    return {"type": type(result).__name__}


def _tool_activity(call) -> str:
    labels = {
        ToolName.LIST_FILES: "Listing project files",
        ToolName.GLOB_FILES: "Finding matching project files",
        ToolName.GREP_SEARCH: "Searching project source",
        ToolName.READ_FILE: "Reading project source",
        ToolName.WRITE_FILE: "Writing staged file",
        ToolName.EDIT_FILE_SIMPLE: "Applying staged edit",
        ToolName.APPLY_PATCH: "Applying precise staged patch",
        ToolName.UPDATE_PLAN: "Updating task plan",
        ToolName.LOAD_SKILL: "Loading Forge skill",
        ToolName.MEMORY_SEARCH: "Searching verified project memory",
        ToolName.SPAWN_SUBAGENT: "Delegating bounded specialist work",
        ToolName.PRESENT_FILES: "Preparing deliverables",
        ToolName.SESSION_EVENT_READ: "Reading durable agent history",
        ToolName.SESSION_EVENT_SEARCH: "Searching durable agent history",
        ToolName.SESSION_TRACE: "Inspecting agent execution trace",
        ToolName.BUILD_FIRMWARE: "Building staged firmware",
        ToolName.RUN_SIMULATION: "Running project simulation",
        ToolName.RUN_COMMAND: "Running sandboxed verification command",
    }
    return labels.get(call.tool, call.tool.value)


def _tool_completion(call) -> str:
    return _tool_activity(call) + " completed"


def _classification_for_change_error(exc: ChangeSetError) -> RuntimeClassification:
    if exc.code == "CHANGE_NO_CHANGES":
        return RuntimeClassification.NO_CHANGES
    if exc.code == "CHANGE_EXTRA_FILES":
        return RuntimeClassification.EXTRA_CHANGES
    if "PATH" in exc.code:
        return RuntimeClassification.PATH_UNSAFE
    return RuntimeClassification.UNSAFE_ABORTED


def _change_counts(change_set: ChangeSet) -> tuple[int, int, int]:
    created = sum(item.change_type == "created" for item in change_set.changed_files)
    modified = sum(item.change_type == "modified" for item in change_set.changed_files)
    deleted = sum(item.change_type == "deleted" for item in change_set.changed_files)
    return created, modified, deleted


def _planner_classification(planner: object) -> str | None:
    metadata = _safe_provider_diagnostics(planner)
    value = metadata.get("safe_error_code")
    return None if value is None else str(value)


def _safe_provider_diagnostics(planner: object) -> dict[str, object]:
    from backend.contracts.diagnostics import safe_provider_diagnostics
    getter = getattr(planner, "to_safe_metadata", None)
    if not callable(getter):
        return {}
    try:
        metadata = getter()
    except Exception:
        return {}
    return safe_provider_diagnostics(metadata)


def _active_integrity_snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    snapshot: dict[str, tuple[object, ...]] = {}
    for current_root, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(current_root)
        dirnames[:] = [name for name in dirnames if name.casefold() not in IGNORED_DIRS]
        for filename in filenames:
            path = current / filename
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if path.is_symlink():
                snapshot[relative] = ("link", info.st_size, info.st_mtime_ns)
            elif is_sensitive_path(relative):
                snapshot[relative] = ("sensitive", info.st_size, info.st_mtime_ns)
            else:
                snapshot[relative] = (
                    "file",
                    info.st_size,
                    info.st_mtime_ns,
                    _hash_file(path),
                )
    return snapshot


def _task_with_project_context(task: str, snapshot) -> str:
    """Backward-compatible helper now returning lean tool-first context."""
    root = getattr(snapshot, "workspace_root", None)
    if root:
        try:
            return (
                ContextAssembler(root).assemble(task=task, snapshot=snapshot).render()
            )
        except Exception:
            pass
    paths = sorted(getattr(snapshot, "files", {}))[:120]
    return (
        task.strip()
        + "\n\nWorkspace inventory (use read/search tools for contents):\n"
        + "\n".join(paths)
    )


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
