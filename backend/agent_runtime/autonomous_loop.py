"""Async autonomous Forge Agent V3 loop for edit/build/repair verification."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Sequence

from backend.changes import IGNORED_DIRS
from backend.services.platformio_service import PlatformIOService
from backend.services.artifact_service import ArtifactService, ArtifactType
from backend.runtime.subprocess_mgr import SubprocessManager
from backend.runtime.process_output import observe_output
from backend.tools.board_detector import BoardType
from backend.tools.build_firmware import BuildArtifact
from backend.tools.wokwi_simulator import SimulationConfig, WokwiSimulator

from .context_assembler import ContextAssembler
from .firmware_repair import repair_verified_cpp_build_errors
from .diagnostic_progress import diagnostic_fingerprint, stable_value
from .canonical_runtime import AttemptAccumulator, LoopGuard, OrderedToolScheduler, ToolInvocation
from .api_planner_provider import ApiPlannerError
from .public_progress import LivePlan, public_text
from .run_reporting import actionable_message, normalize_provider_error, model_recovery_message
from .skills import SkillRegistry
from .subagents import ForgeSubagentRunner
from .tool_contracts import ProductToolCall, ProductToolPlan, RuntimeClassification, ToolName, WRITE_TOOLS
from .tool_executor import ForgeXToolExecutor
from .tool_runtime import _safe_provider_diagnostics, _tool_arguments_summary, _tool_result_summary
from .tool_policy import ToolPermissionPolicy, product_agent_policy, ToolPolicyError
from .task_contract import verify_requirement_evidence, create_task_contract, revise_task_contract
from .task_state import task_state_v1

ProgressCallback = Callable[[str, Mapping[str, object]], Awaitable[None]]
SteeringCallback = Callable[[], Sequence[str]]


class _OperationDeadlineExpired(TimeoutError):
    """The run's own deadline cancelled an operation."""


async def _await_before_deadline(operation, deadline: float):
    work = asyncio.create_task(operation)
    timer = asyncio.create_task(asyncio.sleep(max(0.001, deadline - time.monotonic())))
    try:
        done, _ = await asyncio.wait((work, timer), return_when=asyncio.FIRST_COMPLETED)
        if work in done:
            return await work
        raise _OperationDeadlineExpired("AGENT_RUNTIME_LIMIT_EXCEEDED")
    finally:
        timer.cancel()
        if not work.done():
            work.cancel()
        await asyncio.gather(work, timer, return_exceptions=True)


@dataclass(frozen=True, slots=True)
class AutonomousAgentResult:
    success: bool
    classification: str
    message: str
    build_result: Mapping[str, object] = field(default_factory=dict)
    simulation_result: Mapping[str, object] = field(default_factory=dict)
    tool_execution_count: int = 0
    repair_attempt_count: int = 0
    repair_history: tuple[Mapping[str, object], ...] = ()
    plan: tuple[Mapping[str, str], ...] = ()
    provider_diagnostics: Mapping[str, object] = field(default_factory=dict)
    budget_failure: Mapping[str, object] = field(default_factory=dict)
    build_verified: bool = False
    requirement_report: Mapping[str, object] = field(default_factory=dict)
    task_state: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "classification": self.classification,
            "message": self.message,
            "build_result": dict(self.build_result),
            "simulation_result": dict(self.simulation_result),
            "tool_execution_count": self.tool_execution_count,
            "repair_attempt_count": self.repair_attempt_count,
            "repair_history": [dict(item) for item in self.repair_history],
            "plan": [dict(item) for item in self.plan],
            "provider_diagnostics": dict(self.provider_diagnostics),
            "budget_failure": dict(self.budget_failure),
            "build_verified": self.build_verified,
            "requirement_report": dict(self.requirement_report),
            "task_state": dict(self.task_state),
        }


class AutonomousForgeAgentLoop:
    """Continuous model/tool loop running inside an already-isolated stage workspace."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        skill_root: str | Path | None = None,
        planner: object,
        platformio: PlatformIOService,
        progress: ProgressCallback,
        memory_entries: Sequence[Mapping[str, object]] = (),
        steering_callback: SteeringCallback | None = None,
        policy: ToolPermissionPolicy | None = None,
        subprocess_manager: SubprocessManager | None = None,
        artifact_service: ArtifactService | None = None,
        max_turns: int = 40,
        max_tool_calls: int = 100,
        max_progress_extension_turns: int = 20,
        max_runtime_seconds: float | None = None,
        retry_context: Mapping[str, object] | None = None,
        resume_checkpoint: Mapping[str, object] | None = None,
        task_contract: Mapping[str, object] | None = None,
        trusted_project: bool | None = None,
        project_trust_getter=None,
        serial_service_getter=None,
        job_limits=None,
        job_consumed=None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve(strict=True)
        self.task_contract = dict(task_contract or {})
        self.trusted_project = trusted_project is True
        self.project_trust_getter = project_trust_getter
        self.enforce_project_trust = (trusted_project is not None or project_trust_getter is not None) and os.getenv("FORGEX_REQUIRE_PROJECT_TRUST","1") == "1"
        self.serial_service_getter = serial_service_getter
        self.acceptance_evidence = list((resume_checkpoint or {}).get("acceptance_evidence", ()))
        self.requirement_report: dict = {}
        self._requirement_checks: dict = {}
        self.planner = planner
        self.platformio = platformio
        self.progress = progress
        self.memory_entries = tuple(memory_entries)
        self.steering_callback = steering_callback
        self.max_turns = max(1, min(max_turns + int((resume_checkpoint or {}).get("turn",0)), 600))
        self.max_tool_calls = max(1, min(int((job_limits or {}).get("tool_calls_used",max_tool_calls)), 1000))
        self.max_progress_extension_turns = max(
            0, min(max_progress_extension_turns, 600 - self.max_turns)
        )
        self.retry_context = dict(retry_context or {})
        self.resume_checkpoint = dict(resume_checkpoint or {})
        for counter in ("tool_calls_used","elapsed_seconds"):
            self.resume_checkpoint[counter] = max(self.resume_checkpoint.get(counter,0),(job_consumed or {}).get(counter,0))
        if self.resume_checkpoint and callable(getattr(planner, "restore_budget", None)):
            planner.restore_budget(self.resume_checkpoint.get("provider_budget"))
        # Provider waits and tools share a finite run deadline, including resume.
        self.max_runtime_seconds = (
            float((job_limits or {}).get("elapsed_seconds",900.0))
            if max_runtime_seconds is None
            else max(30.0, min(float(max_runtime_seconds), 9000.0))
        )
        self._deadline = time.monotonic() + self.max_runtime_seconds
        self._job_started = time.monotonic()
        self.policy = policy or product_agent_policy()
        self.subprocess_manager = subprocess_manager
        self.artifact_service = artifact_service
        self.skills = SkillRegistry(skill_root or self.workspace_root)
        self.executor = ForgeXToolExecutor(
            write_root=self.workspace_root,
            read_root=self.workspace_root,
            policy=self.policy,
            skill_registry=self.skills,
        )
        self.observations: list[Mapping[str, object]] = []
        self.tool_count = 0
        self.repair_attempts = 0
        self.live_plan = LivePlan()
        self.last_build: dict[str, object] = {}
        self._last_build_source_manifest_hash: str | None = None
        self.build_durations = list(self.resume_checkpoint.get("build_durations", ()))
        self._platformio_state_requires_clean = False
        self.build_attempted = False
        self.build_passed = False
        self.simulation_attempted = False
        self.simulation_passed = False
        self.last_simulation: dict[str, object] = {}
        self._compaction_count = 0
        self._last_compacted_observation_count = 0
        self._failed_build_cache: dict[tuple[str, str | None], dict[str, object]] = {}
        self._transient_build_retries: set[tuple[str, str | None]] = set()
        self._dependency_search_cache: dict[str, tuple[str, ...]] = {}
        self._dependency_search_count = 0
        self._requirement_reminders = 0
        self._quality_review_attempts = 0
        self._quality_review_revision = None
        self._child_slots = asyncio.Semaphore(2)
        self._quality_review_required = (os.getenv("FORGEX_ENABLE_COMPLEX_REVIEW","1") == "1"
            and getattr(planner,"provider_id","") not in {"fake_planner","verified_template"}
            and len(self.task_contract.get("requirements",[])) >= 8)
        self._repair_groups: dict[str, int] = dict(self.resume_checkpoint.get("repair_groups", {}))
        self._seen_build_pairs: set[str] = set(self.resume_checkpoint.get("seen_build_pairs", ()))
        self.repair_history: list[dict[str, object]] = list(self.resume_checkpoint.get("repair_history", ()))
        self._stalled_turns = int(self.resume_checkpoint.get("stalled_turns", 0))
        self._reorientation_started_at = self.resume_checkpoint.get("reorientation_started_at")
        self._durable_progress = (getattr(planner, "supports_durable_continuation", False)
                                  and os.getenv("FORGEX_ENABLE_PROGRESS_CONTINUATION", "1") == "1")
        if self._durable_progress:
            self.max_progress_extension_turns = max(0, 600 - self.max_turns)
        self._last_build_config_hash: str | None = None
        from .engine import AsyncAgentEngine
        self.engine = AsyncAgentEngine()
        self.scheduler = self.engine.scheduler
        self.loop_guard = LoopGuard(
            max_steps=self.max_turns + self.max_progress_extension_turns,
            max_tool_calls=self.max_tool_calls,
        )
        if self.resume_checkpoint:
            self.observations = list(self.resume_checkpoint.get("observations", ()))
            self.tool_count = int(self.resume_checkpoint.get("tool_calls_used", 0))
            self.loop_guard.consume_child_tool_calls(self.tool_count)
            self.executor.current_plan = tuple(self.resume_checkpoint.get("plan", ()))

    async def verify_saved_draft(self, *, environment: str | None = None) -> AutonomousAgentResult:
        """Gather fresh host build evidence without model calls or source repair."""
        self._job_started = time.monotonic() - float(self.resume_checkpoint.get("elapsed_seconds", 0))
        self._deadline = self._job_started + self.max_runtime_seconds - 5.0
        if not self._trust_now():
            await self._build_permission_blocked("host_verify_draft")
            return self._result(False, "AGENT_PROJECT_TRUST_REQUIRED", "Trust this project before running a local build.")
        if self.policy.decisions.get(ToolName.BUILD_FIRMWARE, "deny") == "deny":
            return self._result(False, "TOOL_NOT_ALLOWED", "Local builds are not enabled for this task.")
        if self.tool_count >= self.max_tool_calls:
            return self._result(False, "AGENT_RUNTIME_TOOL_BUDGET_EXCEEDED", "Add task budget before verifying this draft.")
        self.tool_count += 1
        observation = await self._build_once(environment, call_id="host_verify_draft")
        self.observations.append(observation)
        verified = self._refresh_verification()
        return self._result(False, "DRAFT_BUILD_VERIFIED" if verified else str(self.last_build.get("error_code") or "BUILD_FAILED"),
            "Saved draft compiled successfully. Task requirements and device behavior still need verification."
            if verified else str(self.last_build.get("message") or "Saved draft did not pass the local build."))

    async def _verify_host_milestone(self, environment: str | None, *, call_id: str) -> bool:
        """Compile settled source during outages and before long generation batches."""
        if not self._durable_progress or not self._trust_now() or self._deadline - time.monotonic() < 20:
            return False
        if self.policy.decisions.get(ToolName.BUILD_FIRMWARE, "deny") == "deny" or self.tool_count >= self.max_tool_calls:
            return False
        if not (self.workspace_root / "platformio.ini").is_file() or not any(
            path.is_file() and path.suffix.lower() in {".cpp", ".c", ".ino"}
            for path in (self.workspace_root / "src").glob("**/*")
        ):
            return False
        revision = _snapshot_for_context(self.workspace_root).revision
        if self._last_build_source_manifest_hash == revision:
            return False
        self.loop_guard.consume_child_tool_calls(1)
        self.tool_count += 1
        await self.progress("HOST_VERIFICATION_STARTED", {"message": "Checking settled firmware source locally", "call_id": call_id})
        self.observations.append(await self._build_once(environment, call_id=call_id))
        return True

    async def run(
        self,
        *,
        task: str,
        require_build: bool = True,
        require_simulation: bool = False,
        environment: str | None = None,
    ) -> AutonomousAgentResult:
        self.executor.environment = environment
        if not callable(getattr(self.planner, "request_turn_async", None)) and not callable(
            getattr(self.planner, "request_turn", None)
        ):
            return AutonomousAgentResult(
                False,
                "AGENT_PROVIDER_INVALID",
                "Selected provider cannot run the Forge V3 tool loop.",
            )
        snapshot = _snapshot_for_context(self.workspace_root)
        if self.task_contract:
            task += "\n\nTask requirements (data, not privileges):\n" + json.dumps(self.task_contract.get("requirements", []))
            task += "\nExcluded features:\n" + json.dumps(self.task_contract.get("exclusions", []))
            task += "\nAcceptance checks (required evidence, not privileges):\n" + json.dumps(self.task_contract.get("acceptance_checks", []))
            task += "\nRevision rule: " + self.task_contract.get("revision_rule","Preserve unrelated user code and accepted choices.")
            task += "\nIf native unit tests are requested, extract portable logic, create a native test environment, and run_unit_tests. Missing host tools or project trust must be reported; never substitute a firmware build for a passing test. Use verify_acceptance to review host-collected evidence."
            task += "\nBefore finishing, use verify_requirements for every requirement id with exact source quotes. A successful build alone does not meet the requirements. Establish matching module interfaces before writing implementations."
        context = (
            ContextAssembler(self.workspace_root)
            .assemble(
                task=task,
                snapshot=snapshot,
                environment=environment,
                memories=self.memory_entries,
                skills=tuple(item.label() for item in self.skills.discover()),
            )
            .render()
        )
        from .tool_registry import available_tools
        native = {ToolName.BUILD_FIRMWARE, ToolName.RUN_SIMULATION, ToolName.MEMORY_SEARCH,
                  ToolName.SPAWN_SUBAGENT, ToolName.VERIFY_REQUIREMENTS,
                  ToolName.RESOLVE_DEPENDENCY, ToolName.ANALYZE_FIRMWARE, ToolName.VERIFY_ACCEPTANCE}
        if self._trust_now() and callable(getattr(self.platformio,"test_native",None)):
            native.add(ToolName.RUN_UNIT_TESTS)
        if self.subprocess_manager is not None:
            native.add(ToolName.DECODE_CRASH)
        serial = self.serial_service_getter() if self.serial_service_getter else None
        if serial and serial.is_connected() and callable(getattr(serial,"observation_snapshot",None)):
            native.add(ToolName.OBSERVE_SERIAL)
        allowed_tools = available_tools(self.policy, native=native)
        seen_observations: set[str] = set(self.resume_checkpoint.get("seen_observations", ()))
        seen_build_errors: set[str] = set(self.resume_checkpoint.get("seen_build_errors", ()))
        meaningful_turns: list[int] = list(self.resume_checkpoint.get("meaningful_turns", ()))
        stalled_turns = self._stalled_turns
        reorientation_started_at = self._reorientation_started_at
        if not isinstance(reorientation_started_at, int):
            reorientation_started_at = None
        repair_without_build_turns = int(self.resume_checkpoint.get("repair_without_build_turns", 0))
        if self.resume_checkpoint.get("continuation_summary"):
            self.observations.append({"tool": "saved_task_summary", "status": "completed",
                                      "output": self.resume_checkpoint["continuation_summary"]})
        if isinstance(self.resume_checkpoint.get("task_state"), Mapping):
            saved_state = self.resume_checkpoint["task_state"]
            self.observations.append({"call_id": "saved_task_state", "tool": "saved_task_state",
                "status": "completed", "output": {key: saved_state.get(key) for key in
                ("revisions", "requirements", "exclusions", "decisions", "milestone", "evidence",
                 "blockers", "failed_approaches", "next_action", "recovery_state")}})
        verified_build_turn: int | None = None
        started = time.monotonic() - float(self.resume_checkpoint.get("elapsed_seconds", 0))
        self._job_started = started
        self._deadline = started + self.max_runtime_seconds - 5.0
        if callable(getattr(self.planner, "bind_deadline", None)):
            self.planner.bind_deadline(self._deadline)
        if callable(getattr(self.planner, "bind_verification_reserve", None)):
            from .engine import PhaseBudget
            self.planner.bind_verification_reserve(PhaseBudget.for_task(require_build, self.build_durations).generation_reserve)
        write_turns_since_build = 0
        await self.progress(
            "AGENT_LOOP_STARTED",
            {"message": "Forge Agent V3 continuous tool loop started"},
        )
        if self.retry_context:
            self.observations.append({
                "call_id": "retry_context", "status": "completed",
                "tool": "previous_run_failure", "output": self.retry_context,
            })
        first_turn = int(self.resume_checkpoint.get("turn", 0)) + 1
        async for turn in self.engine.turns(first_turn, self.max_turns + self.max_progress_extension_turns):
            if self._trust_now() and callable(getattr(self.platformio,"test_native",None)):
                native.add(ToolName.RUN_UNIT_TESTS)
            else:
                native.discard(ToolName.RUN_UNIT_TESTS)
            allowed_tools = available_tools(self.policy,native=native)
            if turn > self.max_turns and (turn - self.max_turns - 1) % 10 == 0:
                if not any(previous >= turn - 5 for previous in meaningful_turns):
                    return self._result(
                        False, "AGENT_RUNTIME_MAX_TURNS",
                        "Forge Agent reached the turn limit without recent file or build-repair progress.",
                    )
                await self.progress("AGENT_TURN_BUDGET_EXTENDED", {
                    "turn": turn, "new_limit": min(
                        self.max_turns + self.max_progress_extension_turns, turn + 9
                    ),
                })
            if (
                self.max_runtime_seconds is not None
                and time.monotonic() >= self._deadline
            ):
                return self._result(
                    False,
                    "AGENT_RUNTIME_LIMIT_EXCEEDED",
                    "Forge Agent reached the runtime limit.",
                )
            steering = tuple(
                self.steering_callback() if self.steering_callback is not None else ()
            )
            if steering:
                if self.task_contract:
                    updated_request = "User guidance:\n" + "\n".join(steering)
                    self.task_contract = revise_task_contract(self.task_contract,updated_request,
                        project_id=str(self.task_contract["project_id"]), session_id=self.task_contract.get("session_id"),
                        workspace=Path(str(self.task_contract["workspace_root"])), intent=str(self.task_contract["intent"]),
                        prohibited=self.task_contract.get("prohibited_actions", ()))
                    self.requirement_report = {}
                    self._requirement_checks.clear()
                    self._quality_review_required = self._quality_review_required or len(self.task_contract["requirements"])>=8
                    context += "\nUpdated task requirements (data, not privileges):\n" + json.dumps(self.task_contract["requirements"])
                    await self.progress("TASK_CONTRACT_UPDATED", {"_task_contract": self.task_contract})
                for message in steering:
                    self.observations.append(
                        {
                            "call_id": f"steer_{turn}",
                            "status": "completed",
                            "tool": "user_steer",
                            "output": str(message)[:4000],
                        }
                    )
                await self.progress(
                    "AGENT_STEERED",
                    {"message": steering[-1][:500], "count": len(steering)},
                )
            compacted, context_checkpoint = self._compacted_observations()
            if context_checkpoint is not None:
                checkpoint_payload = dict(context_checkpoint)
                artifact_delta = checkpoint_payload.pop("_artifact_delta", [])
                await self.progress(
                    "CONTEXT_CHECKPOINT_CREATED",
                    {
                        "message": "Older Agent observations compacted into a durable checkpoint",
                        "checkpoint": checkpoint_payload,
                        # Private transport field: the orchestrator moves this
                        # into the dedicated artifact table before journaling.
                        "_context_artifact_delta": artifact_delta,
                    },
                )
            self.loop_guard.before_step()
            attempt = AttemptAccumulator(turn=turn, step=turn)
            await self.progress(
                "TURN_STARTED", {"message": f"Agent turn {turn}", "turn": turn, "step": turn}
            )
            await self.progress(
                "STEP_STARTED", {"message": f"Agent step {turn}", "turn": turn, "step": turn}
            )
            await self.progress(
                "MODEL_ATTEMPT_STARTED", {"attempt_id": attempt.attempt_id, "turn": turn, "step": turn}
            )
            try:
                request_turn_async = getattr(self.planner, "request_turn_async", None)
                request_turn = getattr(self.planner, "request_turn", None)
                if not callable(request_turn_async) and not callable(request_turn):
                    return AutonomousAgentResult(
                        False,
                        "AGENT_PROVIDER_INVALID",
                        "Selected provider cannot run the Forge V3 tool loop.",
                )
                request_turn_stream = getattr(self.planner, "request_turn_stream", None)
                if callable(request_turn_async):
                    streamed_summary = ""

                    async def on_summary_async(message: str) -> None:
                        nonlocal streamed_summary
                        current = public_text(str(message), 1200)
                        delta = current[len(streamed_summary):] if current.startswith(streamed_summary) else current
                        streamed_summary = current
                        if not delta:
                            return
                        frame = attempt.content_delta(delta)
                        await self.progress(
                            "ASSISTANT_CONTENT_DELTA",
                            {**frame.to_event(), "message": delta},
                        )

                    raw = await self.engine.request(self.planner,
                        task=context,
                        tool_results=compacted,
                        allowed_tools=allowed_tools,
                        run_id=f"v3:{self.workspace_root.name}",
                        turn=turn,
                        on_summary=on_summary_async,
                    )
                elif callable(request_turn_stream):
                    loop = asyncio.get_running_loop()
                    accepting = True

                    streamed_summary = ""

                    def on_summary(message: str) -> None:
                        nonlocal streamed_summary
                        if accepting and not loop.is_closed():
                            current = public_text(str(message), 1200)
                            delta = current[len(streamed_summary):] if current.startswith(streamed_summary) else current
                            streamed_summary = current
                            if not delta:
                                return
                            frame = attempt.content_delta(delta)
                            future = asyncio.run_coroutine_threadsafe(
                                self.progress(
                                    "ASSISTANT_CONTENT_DELTA",
                                    {**frame.to_event(), "message": delta},
                                ),
                                loop,
                            )
                            future.result(timeout=10)

                    try:
                        raw = await asyncio.to_thread(
                            request_turn_stream,
                            task=context,
                            tool_results=compacted,
                            allowed_tools=allowed_tools,
                            run_id=f"v3:{self.workspace_root.name}",
                            turn=turn,
                            on_summary=on_summary,
                        )
                    finally:
                        accepting = False
                else:
                    raw = await asyncio.to_thread(
                        request_turn,
                        task=context,
                        tool_results=compacted,
                        allowed_tools=allowed_tools,
                        run_id=f"v3:{self.workspace_root.name}",
                        turn=turn,
                    )
                plan = (
                    raw
                    if isinstance(raw, ProductToolPlan)
                    else ProductToolPlan.parse(raw, max_calls=6)
                )
            except Exception as exc:
                if isinstance(exc, ApiPlannerError):
                    error_code = normalize_provider_error(exc.safe_code)
                    classification = exc.safe_code
                    message = (
                        "ForgeX could not save task budget accounting. The draft is preserved; retry after storage is available."
                        if classification == "AGENT_BUDGET_PERSISTENCE_FAILED" else
                        "This agent turn reached its local deadline. The saved draft can be inspected or continued."
                        if classification == "AGENT_TURN_LIMIT_EXCEEDED" else
                        "The remaining job time is reserved for safe recovery. Extend the time budget before retrying; the draft is preserved."
                        if classification == "AGENT_RUNTIME_LIMIT_EXCEEDED" and getattr(exc, "details", {}).get("origin") == "job_time_reserve" else
                        actionable_message(error_code, str(getattr(self.planner, "provider_id", "The provider")))
                    )
                else:
                    classification = type(exc).__name__
                    message = str(exc) or "Agent provider turn failed."
                diagnostics = _safe_provider_diagnostics(self.planner)
                message = model_recovery_message(diagnostics.get("initial_failure_code"), classification,
                    diagnostics.get("recovery_failure_code"), diagnostics.get("repair_skip_reason")) or message
                await self.progress("MODEL_ATTEMPT_SETTLED", {"attempt_id": attempt.attempt_id,
                    "turn": turn, "step": turn, "status": "failed", "classification": classification,
                    "content": "Model request or plan validation failed; see the run failure."})
                verification_reserve_reached = classification == "AGENT_RUNTIME_LIMIT_EXCEEDED" and getattr(exc, "details", {}).get("origin") == "job_time_reserve"
                if require_build and (verification_reserve_reached or classification in {
                    "PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE", "PROVIDER_BUSY", "API_NETWORK_ERROR",
                    "AGENT_TURN_LIMIT_EXCEEDED",
                    "AGENT_VERIFICATION_RESERVE_REACHED",
                    "MODEL_OUTPUT_TRUNCATED", "MODEL_OUTPUT_NOT_JSON", "TOOL_ARGUMENTS_INVALID",
                    "TOOLPLAN_SCHEMA_INVALID", "TOOLPLAN_VERSION_INVALID", "TOOLPLAN_TOOL_UNKNOWN",
                }):
                    await self._verify_host_milestone(environment, call_id=f"host_outage_build_{turn}")
                return self._result(False, classification, message)
            settlement = self.engine.settle(attempt, plan.tool_calls, content=public_text(plan.summary))
            await self.progress(
                "MODEL_ATTEMPT_SETTLED", settlement.to_event()
            )
            await self.progress(
                "AGENT_TURN_COMPLETED",
                {
                    "message": public_text(plan.summary),
                    "turn": turn,
                    "step": turn,
                    "attempt_id": attempt.attempt_id,
                    "tool_count": len(plan.tool_calls),
                },
            )
            await self.progress(
                "ASSISTANT_SUMMARY",
                {"message": public_text(plan.summary), "turn": turn, "step": turn, "attempt_id": attempt.attempt_id},
            )

            if plan.final:
                self._refresh_verification()
                from .task_contract import acceptance_report
                acceptance = acceptance_report(self.task_contract,self.requirement_report,self.last_build,
                    self.acceptance_evidence,_snapshot_for_context(self.workspace_root).revision,self.last_simulation)
                if self.task_contract and any(check["kind"] == "unit_tests" and not check["passed"] for check in acceptance["checks"]):
                    self._requirement_reminders += 1
                    if self._requirement_reminders <= 2:
                        self.observations.append({"call_id":f"acceptance_{turn}","tool":"acceptance_validation","status":"failed",
                            "output":{"report":acceptance,"message":"Current native test evidence is missing. Run the requested tests or report the specific blocker; completion cannot claim tests passed."}})
                        continue
                    return self._result(False,"ACCEPTANCE_UNVERIFIED","Requested native tests have not passed for the current source.")
                if self.task_contract and (not self.requirement_report.get("references_valid") or
                        self.requirement_report.get("workspace_revision") != _snapshot_for_context(self.workspace_root).revision):
                    self._requirement_reminders += 1
                    if self._requirement_reminders <= 2:
                        self.observations.append({"call_id": f"requirements_{turn}", "tool": "requirement_validation",
                            "status": "completed", "output": {"message": "Call verify_requirements with every requirement id and exact source quotes before finishing. Repair unmet requirements; do not just claim completion.",
                            "report": self.requirement_report}})
                        continue
                    return self._result(False, "REQUIREMENTS_UNVERIFIED", "Required source evidence is incomplete.")
                if require_build and not self.build_attempted:
                    build_observation = await self._build(environment)
                    self.observations.append(build_observation)
                    if _terminal_build_tool_failure(build_observation):
                        return self._result(
                            False,
                            _terminal_build_classification(build_observation),
                            _build_failure_message(build_observation),
                        )
                    if self.build_passed:
                        if not require_simulation:
                            if self._quality_review_required or self.task_contract:
                                continue
                            return self._result(
                                True,
                                "AGENT_V3_COMPLETED",
                                plan.summary.strip()
                                or "Firmware changes were built and verified.",
                            )
                        # Simulation is a separate trusted verification step; feed the
                        # successful build back into the next turn before running it.
                        continue
                    # A failed required build is an observation; API agents get a chance to repair.
                    self.repair_attempts += 1
                    await self.progress(
                        "BUILD_REPAIR_STARTED",
                        {
                            "message": "Repairing compiler errors",
                            "attempt": self.repair_attempts,
                        },
                    )
                    continue
                if require_build and not self.build_passed:
                    return self._result(
                        False,
                        "BUILD_ERROR",
                        "Forge Agent stopped without a successful required build.",
                    )
                if require_simulation and not self.simulation_passed:
                    simulation_observation = await self._simulate(
                        15.0, call_id="required_simulation"
                    )
                    self.observations.append(simulation_observation)
                    if not self.simulation_passed:
                        detail = simulation_observation.get("error")
                        if detail is None and isinstance(
                            simulation_observation.get("output"), Mapping
                        ):
                            detail = simulation_observation["output"].get("message")
                        return self._result(
                            False,
                            "SIMULATION_ERROR",
                            str(detail or "Required simulation failed."),
                        )
                    # Give the model one more turn with the real simulation output so
                    # its final user-facing summary is grounded in observed behavior.
                    continue
                if self.task_contract:
                    from .verification import review_requirements, reviewed_current
                    revision = _snapshot_for_context(self.workspace_root).revision
                    if not reviewed_current(self.requirement_report, revision, self.task_contract.get("revision")):
                        try:
                            self.requirement_report = await review_requirements(
                                self.planner, self.task_contract, self.requirement_report,
                                self.executor._read_overlay_text, revision, deadline=self._deadline,
                                run_id=str(getattr(self.planner, "_current_run_id", None) or "agent"))
                        except (ValueError, RuntimeError, TimeoutError) as exc:
                            return self._result(False, "AGENT_REVIEW_REQUIRED", str(exc))
                        if _snapshot_for_context(self.workspace_root).revision != revision:
                            return self._result(False, "REQUIREMENTS_UNVERIFIED", "Source changed during review.")
                        if not self.requirement_report["valid"]:
                            self._requirement_reminders += 1
                            self.observations.append({"call_id": f"semantic_review_{turn}", "tool": "semantic_review",
                                "status": "failed", "output": self.requirement_report})
                            if self._requirement_reminders <= 2:
                                continue
                            return self._result(False, "REQUIREMENTS_UNVERIFIED", "Implementation review found unmet requirements.")
                if self._quality_review_required:
                    revision = _snapshot_for_context(self.workspace_root).revision
                    if self._quality_review_revision != revision:
                        review = await self._review_completion(revision)
                        self.observations.append({"call_id":f"quality_review_{turn}","tool":"completion_review",
                            "status":"completed" if review["passed"] else "failed","output":review})
                        if not review["passed"]:
                            if self._quality_review_attempts < 2:
                                continue
                            return self._result(False,"AGENT_REVIEW_REQUIRED","The complex task review did not pass; inspect the saved draft and review findings.")
                return self._result(
                    True,
                    "AGENT_V3_COMPLETED",
                    plan.summary.strip() or "Forge Agent completed the requested task.",
                )

            invocations = settlement.invocations
            if (
                require_build
                and not self._quality_review_required
                and not self.task_contract
                and not require_simulation
                and invocations
                and all(invocation.tool is ToolName.BUILD_FIRMWARE for invocation in invocations)
                and self._refresh_verification()
            ):
                return self._result(
                    True,
                    "AGENT_V3_COMPLETED",
                    "Firmware files are complete and the PlatformIO build is verified.",
                )
            for invocation in invocations:
                try:
                    self.loop_guard.admit(invocation)
                except RuntimeError as exc:
                    return self._result(False, str(exc), "Forge Agent stopped a repeated or over-budget tool loop.")

            self._boundary_observations = {}
            self._boundary_turn = turn
            scheduled = await self.engine.execute(
                invocations,
                lambda invocation: self._execute_bounded_invocation(invocation, environment=environment),
            )
            made_progress = False
            meaningful_progress = False
            fresh_build_attempted = False
            for scheduled_result in scheduled:
                invocation = scheduled_result.invocation
                observation = dict(scheduled_result.result)
                output = observation.get("output")
                if invocation.tool is ToolName.VERIFY_REQUIREMENTS and isinstance(output, Mapping) and (output.get("valid") is True or output.get("new_evidence_count", 0) > 0):
                    made_progress = True
                if observation.get("status") == "completed" and invocation.tool in WRITE_TOOLS:
                    changed = isinstance(output, Mapping) and bool(
                        output.get("created") or output.get("deleted") or output.get("modified")
                        or output.get("moved") or output.get("change_type") in {"moved", "deleted"} or output.get("diff")
                    )
                    made_progress = made_progress or changed
                    meaningful_progress = meaningful_progress or changed
                    if changed:
                        self.loop_guard.workspace_changed()
                elif observation.get("status") == "completed" and invocation.tool is ToolName.BUILD_FIRMWARE:
                    if isinstance(output, Mapping) and not output.get("cached"):
                        fresh_build_attempted = True
                    if isinstance(output, Mapping) and output.get("success") is True:
                        made_progress = True
                        meaningful_progress = True
                    elif isinstance(output, Mapping) and not output.get("cached"):
                        error_fingerprint = diagnostic_fingerprint(output)
                        if error_fingerprint not in seen_build_errors:
                            seen_build_errors.add(error_fingerprint)
                            made_progress = True
                            meaningful_progress = True
                elif observation.get("status") == "completed" and invocation.tool is ToolName.RUN_SIMULATION:
                    simulated = isinstance(output, Mapping) and output.get("success") is True
                    made_progress = made_progress or simulated
                    meaningful_progress = meaningful_progress or simulated
                elif invocation.tool not in {ToolName.UPDATE_PLAN, ToolName.SPAWN_SUBAGENT, ToolName.VERIFY_REQUIREMENTS}:
                    fingerprint = hashlib.sha256(
                        json.dumps(
                            [invocation.tool.value, dict(invocation.arguments), observation.get("status"),
                             stable_value(observation.get("output")), observation.get("error")],
                            sort_keys=True,
                            default=str,
                        ).encode("utf-8")
                    ).hexdigest()
                    if fingerprint not in seen_observations:
                        seen_observations.add(fingerprint)
                        made_progress = True
                self.live_plan.observe(
                    invocation.call_id,
                    invocation.tool is not ToolName.UPDATE_PLAN
                    and observation.get("status") == "completed"
                    and not (
                        isinstance(observation.get("output"), Mapping)
                        and observation["output"].get("success") is False
                    ),
                    invocation.tool.value,
                )
                if invocation.tool is ToolName.UPDATE_PLAN and observation.get("status") == "completed":
                    plan_snapshot = self.live_plan.update(self.executor.current_plan)
                    self.executor.current_plan = tuple(plan_snapshot["steps"])
                    await self.progress("PLAN_UPDATED", plan_snapshot)
                self.tool_count += 1
                if invocation.tool is ToolName.SPAWN_SUBAGENT:
                    child_output = observation.get("output")
                    if isinstance(child_output, Mapping):
                        self.tool_count += max(
                            0, int(child_output.get("tool_execution_count") or 0)
                        )
                self.observations.append(observation)
                if invocation.tool is ToolName.BUILD_FIRMWARE and _terminal_build_tool_failure(observation):
                    code = _terminal_build_classification(observation)
                    return self._result(False, code, _build_failure_message(observation))

            if any(invocation.tool in WRITE_TOOLS for invocation in invocations):
                write_turns_since_build += 1
            if fresh_build_attempted:
                write_turns_since_build = 0
            if require_build and write_turns_since_build and (
                write_turns_since_build >= 3 or self._deadline - time.monotonic() <= 180
            ):
                if await self._verify_host_milestone(environment, call_id=f"host_milestone_build_{turn}"):
                    fresh_build_attempted = True
                    write_turns_since_build = 0
            if time.monotonic() >= self._deadline:
                return self._result(False, "AGENT_RUNTIME_LIMIT_EXCEEDED",
                                    "Forge Agent reached the task runtime limit; the saved draft remains unverified.")

            if require_build and not require_simulation:
                verified = self._refresh_verification()
                if not verified:
                    verified_build_turn = None
                elif any(invocation.tool is ToolName.BUILD_FIRMWARE for invocation in invocations):
                    verified_build_turn = turn
                elif not self._quality_review_required and verified_build_turn is not None and turn > verified_build_turn and (not self.task_contract or self.requirement_report.get("valid")) and not any(
                    invocation.tool in WRITE_TOOLS for invocation in invocations
                ):
                    return self._result(
                        True,
                        "AGENT_V3_COMPLETED",
                        "Firmware files are complete and the PlatformIO build is verified.",
                    )

            if require_build and self.build_attempted and not self.build_passed:
                repair_without_build_turns = 0 if fresh_build_attempted else repair_without_build_turns + 1
                if repair_without_build_turns >= 6:
                    return self._result(False, "BUILD_REPAIR_EXHAUSTED",
                                        "No new firmware build followed the failed build after six tool turns; inspect the saved compiler diagnostics and draft.")
            else:
                repair_without_build_turns = 0
            if meaningful_progress or (self._durable_progress and made_progress):
                meaningful_turns.append(turn)
            if made_progress:
                stalled_turns = 0
            else:
                stalled_turns += 1
                if stalled_turns == 2:
                    hint = (
                        "The last tool turns produced no new workspace information. "
                        "Create or edit the requested files now, or finish with a clear explanation of the blocker."
                        if ToolName.WRITE_FILE.value in allowed_tools
                        else "The last tool turns produced no new information. Answer from the observations already gathered."
                    )
                    self.observations.append({
                        "call_id": f"progress_hint_{turn}", "tool": "agent_progress_hint",
                        "status": "completed", "output": hint,
                    })
                    await self.progress("AGENT_NO_PROGRESS_WARNING", {"message": hint, "turn": turn})
                elif stalled_turns >= 3:
                    if reorientation_started_at is None:
                        reorientation_started_at = turn
                        hint = (
                            "Recovery attempt: stop rereading unchanged files. Use the saved task requirements and "
                            "the last build diagnostics to make one relevant edit or run the required build. "
                            "If an external dependency blocks progress, explain the blocker and stop."
                        )
                        self.observations.append({"call_id": f"reorientation_{turn}",
                            "tool": "agent_reorientation", "status": "completed", "output": hint})
                        await self.progress("AGENT_REORIENTATION_STARTED", {"message": hint, "turn": turn})
                    elif stalled_turns >= 5 or turn - reorientation_started_at >= 2:
                        self._stalled_turns = stalled_turns
                        self._reorientation_started_at = reorientation_started_at
                        return self._result(False, "AGENT_RUNTIME_NO_PROGRESS",
                            "Forge Agent stopped after a bounded recovery attempt produced no new evidence or file changes.")

            self._stalled_turns = stalled_turns
            self._reorientation_started_at = reorientation_started_at

            await self.progress(
                "STEP_SETTLED",
                {"turn": turn, "step": turn, "attempt_id": attempt.attempt_id, "tool_count": len(invocations)},
            )
            task_snapshot = _snapshot_for_context(self.workspace_root)
            task_projection = task_state_v1(
                contract=self.task_contract, plan=self.executor.current_plan,
                observations=self.observations, build=self.last_build,
                report=self.requirement_report, repair_history=self.repair_history,
                source_revision=task_snapshot.revision, source_paths=tuple(task_snapshot.files),
                stalled_turns=stalled_turns, reorientation_started_at=reorientation_started_at)
            await self.progress("turn.settled", {
                "turn": turn, "step": turn, "attempt_id": attempt.attempt_id,
                "tool_count": len(invocations), "outcome": "continue",
                "_resume_checkpoint": {"observations": list(self.observations), "next_operation": "generation",
                    "acceptance_evidence": list(self.acceptance_evidence),
                    "plan": list(self.executor.current_plan), "tool_calls_used": self.tool_count,
                    "deleted_paths": sorted(self.executor.deleted_paths),
                    "authorized_paths": [],
                    "provider_budget": self.planner.budget_snapshot() if callable(getattr(self.planner, "budget_snapshot", None)) else {},
                    "elapsed_seconds": time.monotonic() - started, "turn": turn,
                    "build_durations": self.build_durations,
                    "repair_groups": dict(self._repair_groups), "task_revision": self.task_contract.get("revision"),
                    "seen_build_pairs": sorted(self._seen_build_pairs), "repair_history": self.repair_history[-32:],
                    "repair_without_build_turns": repair_without_build_turns,
                    "seen_observations": sorted(seen_observations), "seen_build_errors": sorted(seen_build_errors),
                    "meaningful_turns": meaningful_turns[-10:], "stalled_turns": stalled_turns,
                    "task_state": task_projection,
                    "reorientation_started_at": reorientation_started_at,
                    "continuation_summary": {"objective": task[:4000], "milestones": list(self.executor.current_plan),
                                             "turn": turn, "next_action": task_projection["next_action"]}},
            })

        return self._result(
            False,
            "AGENT_RUNTIME_MAX_TURNS",
            "Forge Agent reached the maximum number of reasoning turns.",
        )

    async def _execute_bounded_invocation(self, invocation, *, environment):
        from .tool_registry import NEW_TOOLS
        definition=NEW_TOOLS.get(invocation.tool)
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            return {"call_id": invocation.call_id, "tool": invocation.tool.value,
                    "status": "failed", "error_code": "AGENT_RUNTIME_LIMIT_EXCEEDED"}
        try:
            timeout = min(definition.timeout_seconds, remaining) if definition else remaining
            observation = await asyncio.wait_for(self._execute_invocation(invocation,environment=environment), timeout=timeout)
            self._boundary_observations[invocation.invocation_index] = observation
            if invocation.tool in WRITE_TOOLS and observation.get("status") == "completed":
                checkpoint = {**self.resume_checkpoint, "next_operation": "generation",
                    "observations": [*self.observations, *(self._boundary_observations[index] for index in sorted(self._boundary_observations))],
                    "plan": list(self.executor.current_plan), "tool_calls_used": self.tool_count + len(self._boundary_observations),
                    "deleted_paths": sorted(self.executor.deleted_paths), "authorized_paths": [],
                    "provider_budget": self.planner.budget_snapshot() if callable(getattr(self.planner, "budget_snapshot", None)) else {},
                    "elapsed_seconds": time.monotonic() - self._job_started, "turn": self._boundary_turn,
                    "build_durations": self.build_durations}
                await self.progress("tool.checkpoint", {"turn": self._boundary_turn,
                    "call_id": invocation.call_id, "_resume_checkpoint": checkpoint})
            return observation
        except TimeoutError:
            expired = time.monotonic() >= self._deadline - 0.01
            code = "AGENT_RUNTIME_LIMIT_EXCEEDED" if expired else "TOOL_DEADLINE_EXCEEDED"
            pure = definition is None or definition.recovery == "pure"
            await self.progress("TOOL_FAILED" if pure else "TOOL_OUTCOME_UNKNOWN",{
                **invocation.to_event_payload(),"status":"failed","error_code":code,
                "message":"The tool exceeded its deadline; uncertain effects require outcome review." if not pure else "The inspection tool exceeded its deadline."})
            return {"call_id":invocation.call_id,"tool":invocation.tool.value,"status":"failed","error_code":code}

    async def _execute_invocation(
        self, invocation: ToolInvocation, *, environment: str | None
    ) -> Mapping[str, object]:
        call = ProductToolCall(
            call_id=invocation.call_id,
            tool_type=invocation.tool.value,
            arguments=dict(invocation.arguments),
        ).as_runtime_call()
        await self.progress(
            "TOOL_PENDING",
            {**invocation.to_event_payload(), "message": "Tool call queued"},
        )
        tool_started = time.monotonic()
        if call.tool not in {ToolName.BUILD_FIRMWARE, ToolName.RUN_SIMULATION}:
            await self.progress(
                "TOOL_STARTED",
                {
                    **invocation.to_event_payload(),
                    "arguments": _tool_arguments_summary(call.arguments),
                    "plan_step_id": self.live_plan.active_step,
                    "message": _tool_message(call.tool),
                },
            )
        try:
            self.executor.validate(call)
            if call.tool is ToolName.BUILD_FIRMWARE:
                selected = call.arguments.get("environment")
                selected_env = str(selected) if isinstance(selected, str) and selected.strip() else environment
                if environment and selected_env != environment:
                    raise ValueError("BUILD_ENVIRONMENT_MISMATCH: preserve the user's selected environment")
                observation = await self._build(selected_env, call_id=invocation.call_id)
            elif call.tool is ToolName.RUN_SIMULATION:
                timeout = call.arguments.get("timeout_seconds", 15)
                observation = await self._simulate(float(timeout), call_id=invocation.call_id)
            elif call.tool is ToolName.RESOLVE_DEPENDENCY:
                result = await self.platformio.search_libraries(call.arguments["query"])
                observation = _observation(invocation.call_id,call.tool,{"candidates":list(result),"installed":False})
            elif call.tool is ToolName.RUN_UNIT_TESTS:
                before_revision = _snapshot_for_context(self.workspace_root).revision
                result = await self.platformio.test_native(self.workspace_root,
                    environment=call.arguments["environment"],trusted=self._trust_now())
                revision = _snapshot_for_context(self.workspace_root).revision
                evidence = {"kind":"unit_tests","success":result.get("success") is True and before_revision == revision,
                            "source_revision":revision,"environment":call.arguments["environment"],"call_id":invocation.call_id}
                evidence["test_cases_executed"] = result.get("test_cases_executed",0)
                self.acceptance_evidence.append(evidence)
                observation = _observation(invocation.call_id,call.tool,result | {"source_revision":revision})
                if not evidence["success"]:
                    observation = {**observation,"status":"failed"}
            elif call.tool is ToolName.ANALYZE_FIRMWARE:
                self._refresh_verification()
                observation = _observation(invocation.call_id,call.tool,{"build":self.last_build,
                    "current":self.build_passed,"tests":self.acceptance_evidence[-10:]})
            elif call.tool is ToolName.VERIFY_ACCEPTANCE:
                self._refresh_verification()
                from .task_contract import acceptance_report
                report = acceptance_report(self.task_contract,self.requirement_report,self.last_build,
                    self.acceptance_evidence,_snapshot_for_context(self.workspace_root).revision,
                    self.last_simulation)
                observation = _observation(invocation.call_id,call.tool,report)
            elif call.tool is ToolName.DECODE_CRASH:
                observation = _observation(invocation.call_id,call.tool,await self._decode_crash(call.arguments["addresses"]))
            elif call.tool is ToolName.OBSERVE_SERIAL:
                observation = _observation(invocation.call_id,call.tool,await self._observe_serial(int(call.arguments.get("duration_seconds",5))))
            elif call.tool is ToolName.MEMORY_SEARCH:
                observation = {
                    "call_id": invocation.call_id,
                    "status": "completed",
                    "tool": call.tool.value,
                    "output": self._memory_search(str(call.arguments.get("query") or ""), int(call.arguments.get("limit", 5))),
                }
            elif call.tool is ToolName.SPAWN_SUBAGENT:
                # A verify child can overwrite the parent's firmware output, but
                # its result does not certify the parent's build or environment.
                if str(call.arguments.get("role") or "explore") == "verify":
                    if self.enforce_project_trust and not self._trust_now():
                        raise ValueError("AGENT_PROJECT_TRUST_REQUIRED")
                    self._invalidate_verification()
                child_budget = self.loop_guard.remaining_tool_calls
                if child_budget <= 0:
                    raise ValueError("AGENT_RUNTIME_TOOL_BUDGET_EXCEEDED")
                child = ForgeSubagentRunner(
                    workspace_root=self.workspace_root,
                    planner=self.planner,
                    platformio=self.platformio,
                    progress=self.progress,
                    memory_entries=self.memory_entries,
                    max_tool_calls=child_budget,
                )
                async with self._child_slots:
                    child_result = await child.run(
                        role=str(call.arguments.get("role") or "explore"),
                        task=str(call.arguments.get("task") or ""),
                        environment=environment,
                    )
                self.loop_guard.consume_child_tool_calls(
                    child_result.tool_execution_count
                )
                observation = {
                    "call_id": invocation.call_id,
                    "status": "completed" if child_result.success else "failed",
                    "tool": call.tool.value,
                    "output": child_result.to_dict(),
                }
            elif call.tool is ToolName.VERIFY_REQUIREMENTS:
                if not self.task_contract:
                    raise ValueError("TASK_CONTRACT_UNAVAILABLE")
                revision = _snapshot_for_context(self.workspace_root).revision
                if self.requirement_report.get("workspace_revision") != revision:
                    self._requirement_checks.clear()
                checks = call.arguments["checks"]
                verify_requirement_evidence(self.task_contract, checks, self.workspace_root, revision)
                prior_count = len(self._requirement_checks)
                self._requirement_checks.update({check["id"]: check for check in checks})
                self.requirement_report = verify_requirement_evidence(
                    self.task_contract, list(self._requirement_checks.values()), self.workspace_root, revision)
                self.requirement_report["new_evidence_count"] = max(0, len(self._requirement_checks) - prior_count)
                observation = _observation(invocation.call_id, call.tool, self.requirement_report)
            else:
                # Filesystem inspection and edits are synchronous host operations.
                # Parallel read batches run them in worker threads; workspace
                # mutations remain scheduler barriers.
                prior_failure = (dict(self.last_build) if call.tool in WRITE_TOOLS
                    and self.build_attempted and not self.build_passed
                    and self.last_build.get("diagnostics") else None)
                output = await asyncio.to_thread(self.executor.execute, call)
                if call.tool in WRITE_TOOLS and isinstance(output, Mapping) and (
                    output.get("created") or output.get("deleted") or output.get("modified")
                    or output.get("moved") or output.get("change_type") in {"moved", "deleted"} or output.get("diff")
                ):
                    if prior_failure is not None:
                        self._record_model_repair(invocation.call_id, call.arguments, prior_failure)
                    self._invalidate_verification()
                observation = _observation(invocation.call_id, call.tool, output)
        except ToolPolicyError as exc:
            missing = exc.classification is RuntimeClassification.FILE_NOT_FOUND
            observation = {
                "call_id": invocation.call_id,
                "status": "failed",
                "tool": call.tool.value,
                "error_code": exc.classification.value,
                "error": (
                    f"File or directory not found: {call.arguments.get('path', '.')}"
                    if missing else str(exc)[:1000]
                ),
                "recoverable": missing,
                "policy_details": dict(exc.details),
            }
        except (ValueError, OSError, KeyError) as exc:
            observation = {
                "call_id": invocation.call_id,
                "status": "failed",
                "tool": call.tool.value,
                "error": str(exc)[:1000],
            }

        if call.tool not in {ToolName.BUILD_FIRMWARE, ToolName.RUN_SIMULATION}:
            failed = observation.get("status") == "failed"
            progress_result = observation.get("output")
            if failed and progress_result is None:
                progress_result = {"success": False, "error": str(observation.get("error") or "Tool execution failed")[:1000]}
            await self.progress(
                "TOOL_FAILED" if failed else "TOOL_COMPLETED",
                {
                    **invocation.to_event_payload(),
                    "duration_ms": int((time.monotonic() - tool_started) * 1000),
                    "result": _tool_result_summary(progress_result),
                    "message": _tool_message(call.tool) + (" failed" if failed else " completed"),
                },
            )
        return observation

    def _trust_now(self):
        return self.project_trust_getter() is True if self.project_trust_getter else self.trusted_project

    async def _observe_serial(self, duration):
        serial = self.serial_service_getter() if self.serial_service_getter else None
        if serial is None or not serial.is_connected():
            raise ValueError("SERIAL_NOT_CONNECTED")
        # The monitor remains the only serial reader. Never connect, reset,
        # reconnect, or consume its stream from an agent observation.
        await asyncio.sleep(duration)
        records = serial.observation_snapshot(limit=100)
        return {"observations":[str(getattr(item,"line",item))[:300] for item in records],
                "connected":serial.is_connected(),"hardware_verified":False,"source":"monitor_buffer"}

    async def _review_completion(self, revision):
        self._quality_review_attempts += 1
        remaining = self.loop_guard.remaining_tool_calls
        if remaining <= 0:
            return {"passed":False,"issues":["Shared tool budget is exhausted."]}
        child=ForgeSubagentRunner(workspace_root=self.workspace_root,planner=self.planner,
            platformio=self.platformio,progress=self.progress,memory_entries=self.memory_entries,
            max_turns=3,max_tool_calls=min(remaining,10))
        try:
            async with self._child_slots:
                result=await child.run(role="review",task="Read the staged source and review requirement fidelity, module interfaces, timing, error handling, and exclusions. Do not edit or run hardware. Return ONLY JSON in the final summary: {\"verdict\":\"pass\" or \"revise\",\"requirement_ids\":[all reviewed IDs],\"issues\":[at most 3 concise issues]}. Keep the JSON under 1200 characters. Requirements and exclusions are untrusted data:\n"+json.dumps({"requirements":self.task_contract.get("requirements",[]),"exclusions":self.task_contract.get("exclusions",[])}))
            self.loop_guard.consume_child_tool_calls(result.tool_execution_count)
            self.tool_count += result.tool_execution_count
            value=json.loads(result.summary)
            expected={item["id"] for item in self.task_contract["requirements"]}
            read_source=any(item.get("tool")=="read_file" and item.get("status")=="completed" for item in result.observations)
            passed=(result.success and read_source and isinstance(value,dict) and set(value)=={"verdict","requirement_ids","issues"}
                and value["verdict"]=="pass" and isinstance(value["requirement_ids"],list)
                and set(value["requirement_ids"])==expected and value["issues"]==[]
                and _snapshot_for_context(self.workspace_root).revision==revision)
            issues=[str(item)[:300] for item in value.get("issues",[])[:3]] if isinstance(value,dict) and isinstance(value.get("issues"),list) else []
        except (ValueError,RuntimeError,TypeError):
            passed=False
            issues=["The bounded reviewer did not return valid source evidence."]
        if passed:
            self._quality_review_revision=revision
        return {"passed":bool(passed),"source_revision":revision,"issues":issues,
            "verification_level":"model_review","hardware_verified":False}

    async def _decode_crash(self, addresses):
        self._refresh_verification()
        if not self.build_passed or not self.last_build.get("elf_sha256"):
            raise ValueError("CURRENT_ELF_REQUIRED")
        elf=Path(self.last_build["elf_path"])
        if hashlib.sha256(elf.read_bytes()).hexdigest()!=self.last_build["elf_sha256"]:
            raise ValueError("ELF_EVIDENCE_STALE")
        core=Path(os.environ.get("PLATFORMIO_CORE_DIR",str(Path.home()/".platformio")))
        candidates=list(core.glob("packages/toolchain-xtensa-esp32*/bin/xtensa-esp32-elf-addr2line*"))
        candidates=[path for path in candidates if path.suffix in {".exe",""} and path.is_file() and not path.is_symlink()]
        if len(candidates)!=1:
            raise ValueError("CRASH_DECODER_UNAVAILABLE")
        from backend.runtime.subprocess_mgr import ProcessConfig
        result=await self.subprocess_manager.run(ProcessConfig(args=[str(candidates[0]),"-pfiaC","-e",str(elf),*addresses],timeout_s=10))
        return {"success":result.success,"elf_sha256":self.last_build["elf_sha256"],"decoded":result.stdout_text[:4000]}

    def _compacted_observations(
        self, *, keep: int = 24
    ) -> tuple[tuple[Mapping[str, object], ...], Mapping[str, object] | None]:
        """Keep recent detailed observations and replace older history with a bounded checkpoint."""
        from .context_memory import detailed_tail, summarize_observations, bounded_observation
        # A provider turn can issue five calls. Keep their complete settled
        # observations plus a progress hint even when an earlier large result
        # forces the ordinary character-based tail to shrink.
        keep = min(len(self.observations), max(6, detailed_tail(self.observations, keep=keep)))
        if len(self.observations) <= keep:
            return tuple(bounded_observation(item) for item in self.observations), None
        older = self.observations[:-keep]
        tool_counts: dict[str, int] = {}
        for item in older:
            tool = str(item.get("tool") or "unknown")
            tool_counts[tool] = tool_counts.get(tool, 0) + 1
        failures = [
            {
                "call_id": str(item.get("call_id") or ""),
                "tool": str(item.get("tool") or "unknown"),
                "status": str(item.get("status") or "failed"),
                "error": str(item.get("error") or "")[:500],
            }
            for item in older
            if item.get("status") in {"failed", "denied"}
        ]
        digest = hashlib.sha256(
            json.dumps(older, ensure_ascii=True, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        output = {
            **summarize_observations(older),
            "task_revision": self.task_contract.get("revision"),
            "accepted_choices": self.task_contract.get("accepted_choices", []),
            "pending_requirements": self.requirement_report.get("missing", [item["id"] for item in self.task_contract.get("requirements", [])]),
            "checkpoint_version": "forgex.context-checkpoint.v1",
            "compacted_observations": len(older),
            "observation_digest": digest,
            "tool_counts": tool_counts,
            "failure_count": len(failures),
            "recent_failures": failures[-8:],
            "last_plan": [dict(item) for item in self.executor.current_plan],
            "build_attempted": self.build_attempted,
            "build_passed": self.build_passed,
            "last_build_failure": (
                _build_failure_summary(self.last_build)
                if self.build_attempted and not self.build_passed and self.last_build
                else None
            ),
            "simulation_attempted": self.simulation_attempted,
            "simulation_passed": self.simulation_passed,
            "retrieval": "session_event_search",
        }
        checkpoint = {
            "call_id": f"context_checkpoint_{len(older)}",
            "status": "completed",
            "tool": "context_compaction",
            "output": output,
        }
        new_checkpoint: Mapping[str, object] | None = None
        if len(older) > self._last_compacted_observation_count:
            previous_count = self._last_compacted_observation_count
            self._compaction_count += 1
            self._last_compacted_observation_count = len(older)
            new_checkpoint = {
                **output,
                "_artifact_delta": [
                    dict(item) for item in older[previous_count:]
                ],
            }
        from .context_memory import bounded_observation
        return (checkpoint, *(bounded_observation(item) for item in self.observations[-keep:])), new_checkpoint

    def _invalidate_verification(self) -> None:
        """Drop executable artifact references; leave files in the stage alone."""
        self.build_attempted = False
        self.acceptance_evidence.clear()
        if self.requirement_report and self.requirement_report.get("workspace_revision") != _snapshot_for_context(self.workspace_root).revision:
            self.requirement_report = {}
            self._requirement_checks.clear()
        self.build_passed = False
        if self.last_build:
            self.last_build = {**self.last_build, "success": False, "verification_stale": True,
                               "message": "Build evidence belongs to an earlier source or artifact revision; rebuild required."}
        self.simulation_attempted = False
        self.simulation_passed = False
        if self.last_simulation:
            self.last_simulation = {**self.last_simulation, "success": False, "verification_stale": True}

    def _record_model_repair(self, call_id: str, arguments: Mapping[str, object],
                             prior_failure: Mapping[str, object]) -> None:
        path = str(arguments.get("path") or arguments.get("source") or "")[:500]
        self.repair_history.append({"strategy": "model_edit", "tool_call_id": call_id,
            "source_revision": prior_failure.get("workspace_revision"),
            "resulting_revision": _snapshot_for_context(self.workspace_root).revision,
            "diagnostic_fingerprint": diagnostic_fingerprint(prior_failure),
            "changed_paths": [path] if path else [], "result_success": None})

    def _refresh_verification(self) -> bool:
        if not self.build_passed:
            return False
        try:
            revision = _snapshot_for_context(self.workspace_root).revision
            artifact = Path(str(self.last_build["firmware_path"])).resolve(strict=True)
            artifact.relative_to(self.workspace_root)
            fresh = (
                revision == self.last_build.get("workspace_revision")
                and hashlib.sha256(artifact.read_bytes()).hexdigest()
                == self.last_build.get("firmware_hash")
            )
        except (OSError, ValueError, KeyError):
            fresh = False
        if not fresh:
            self._invalidate_verification()
        return fresh

    async def _build_permission_blocked(self, call_id: str) -> dict[str, object]:
        self._invalidate_verification()
        self.last_build = {
            "success": False, "status": "blocked", "execution_attempted": False,
            "terminal": True, "error_code": "AGENT_PROJECT_TRUST_REQUIRED",
            "message": "The draft is saved. Mark this project as trusted before running its local build or native tests.",
        }
        await self.progress("BUILD_BLOCKED", dict(self.last_build))
        return _observation(call_id, ToolName.BUILD_FIRMWARE, dict(self.last_build))

    async def _build(
        self, environment: str | None, *, call_id: str = "required_build"
    ) -> dict[str, object]:
        if self.enforce_project_trust and not self._trust_now():
            return await self._build_permission_blocked(call_id)
        if self.last_build and not self.last_build.get("success") and self._repair_groups.get(_diagnostic_group(self.last_build), 0) >= 4:
            self.last_build.update({"repair_exhausted": True, "terminal": True, "error_code": "BUILD_REPAIR_EXHAUSTED"})
            return _observation(call_id, ToolName.BUILD_FIRMWARE, dict(self.last_build))
        revision = _snapshot_for_context(self.workspace_root).revision
        cache_key = (revision, environment)
        cached = self._failed_build_cache.get(cache_key)
        if cached is not None:
            transient = _transient_build_failure(cached)
            if not transient or cache_key in self._transient_build_retries:
                output = {**cached, "cached": True}
                await self.progress("BUILD_FAILED", {
                    "message": "Source files are unchanged; the previous build errors still apply.",
                    "cached": True, "dependency_candidates": output.get("dependency_candidates", []),
                })
                return {
                    "call_id": call_id, "status": "completed",
                    "tool": ToolName.BUILD_FIRMWARE.value, "output": output,
                }
            self._transient_build_retries.add(cache_key)
        observation = await self._build_once(environment, call_id=call_id)
        if not self.build_passed and not _terminal_build_tool_failure(observation) and _transient_build_failure(self.last_build):
            if cache_key not in self._transient_build_retries and time.monotonic() < self._deadline:
                self._transient_build_retries.add(cache_key)
                observation = await self._build_once(environment, call_id=call_id)
            if not self.build_passed and not _terminal_build_tool_failure(observation) and _transient_build_failure(self.last_build):
                self.last_build["terminal"] = True
                self.last_build.setdefault("error_code", "BUILD_INFRASTRUCTURE_UNAVAILABLE")
                observation = _observation(call_id, ToolName.BUILD_FIRMWARE, dict(self.last_build))
            if self.build_passed or _terminal_build_tool_failure(observation):
                self._remember_failed_build(environment, observation)
                return observation
        if self.build_passed or _terminal_build_tool_failure(observation):
            self._remember_failed_build(environment, observation)
            return observation
        repairs = []
        for _ in range(3):
            if time.monotonic() >= self._deadline:
                self.last_build.update({"success": False, "terminal": True,
                                        "error_code": "AGENT_RUNTIME_LIMIT_EXCEEDED",
                                        "message": "The task deadline was reached before another repair could start."})
                return _observation(call_id, ToolName.BUILD_FIRMWARE, self.last_build)
            group = _diagnostic_group(self.last_build)
            if self._repair_groups.get(group, 0) >= 4:
                self.last_build["repair_exhausted"] = True
                break
            repair = await asyncio.to_thread(
                repair_verified_cpp_build_errors,
                self.workspace_root,
                self.last_build,
            )
            if repair is None:
                break
            repairs.append(repair.to_dict())
            self.repair_history.append({"source_revision": revision,
                "diagnostic_fingerprint": group, "strategy": repair.repair_id,
                "changed_paths": list(repair.changed_paths)})
            self.repair_attempts += 1
            await self.progress(
                "BUILD_REPAIR_STARTED",
                {
                    "message": "Applying a repair verified by build diagnostics",
                    "attempt": self.repair_attempts,
                    "repair": repair.to_dict(),
                },
            )
            observation = await self._build_once(environment, call_id=call_id)
            self.repair_history[-1]["result_fingerprint"] = _diagnostic_group(self.last_build)
            self.repair_history[-1]["result_success"] = self.build_passed
            if self.build_passed or _terminal_build_tool_failure(observation):
                break
        output = observation.get("output")
        if isinstance(output, dict):
            if repairs:
                output["deterministic_repairs"] = repairs
        if repairs and not self.build_passed:
            await self.progress(
                "BUILD_REPAIR_FAILED",
                {
                    "message": "The verified repair did not resolve every build error",
                    "repairs": repairs,
                },
            )
        self._remember_failed_build(environment, observation)
        return observation

    def _remember_failed_build(
        self, environment: str | None, observation: Mapping[str, object]
    ) -> None:
        output = observation.get("output")
        if self.build_passed or not isinstance(output, Mapping):
            self._failed_build_cache.clear()
            return
        if output.get("error_code") == "AGENT_PROJECT_TRUST_REQUIRED":
            return
        revision = _snapshot_for_context(self.workspace_root).revision
        self._failed_build_cache[(revision, environment)] = dict(output)

    async def _build_once(
        self, environment: str | None, *, call_id: str = "required_build"
    ) -> dict[str, object]:
        self._invalidate_verification()
        if self.enforce_project_trust and not self._trust_now():
            return await self._build_permission_blocked(call_id)
        from .engine import PhaseBudget
        build_deadline = PhaseBudget.for_task(True, self.build_durations).build_deadline(self._deadline)
        if time.monotonic() >= self._deadline:
            self.last_build = {"success": False, "terminal": True,
                               "error_code": "AGENT_RUNTIME_LIMIT_EXCEEDED",
                               "message": "The task deadline was reached before another firmware build could start."}
            return _observation(call_id, ToolName.BUILD_FIRMWARE, self.last_build)
        if self.task_contract and callable(getattr(self.platformio, "validate_dependencies", None)):
            try:
                dependencies = await _await_before_deadline(
                    self.platformio.validate_dependencies(self.workspace_root), build_deadline)
            except Exception as exc:
                policy_failure = str(exc).startswith(("BUILD_CONFIGURATION_REQUIRES_TRUST", "BUILD_TARGET_FORBIDDEN"))
                deadline_exhausted = isinstance(exc, _OperationDeadlineExpired)
                self.last_build = {"success": False, "error_code": "AGENT_RUNTIME_LIMIT_EXCEEDED" if deadline_exhausted else (str(exc).split(":", 1)[0] if policy_failure else "DEPENDENCY_RESOLUTION_UNAVAILABLE"),
                                   "message": str(exc)[:1000] if policy_failure or deadline_exhausted else
                                   "PlatformIO registry validation is unavailable; source repair cannot resolve this infrastructure failure.",
                                   "terminal": policy_failure or deadline_exhausted}
                await self.progress("BUILD_FAILED", dict(self.last_build))
                return _observation(call_id, ToolName.BUILD_FIRMWARE, dict(self.last_build))
            if not dependencies["valid"]:
                self.last_build = {"success": False, "error_code": "DEPENDENCY_UNRESOLVED", "dependencies": dependencies,
                    "message": "Declared libraries were not found in the registry. Use verified package IDs; preserve the requested sensor.", "terminal": False}
                group = _diagnostic_group(self.last_build)
                self._repair_groups[group] = self._repair_groups.get(group, 0) + 1
                await self.progress("BUILD_FAILED", dict(self.last_build))
                return _observation(call_id, ToolName.BUILD_FIRMWARE, dict(self.last_build))
        # Dependency validation can yield while the operator revokes trust.
        if self.enforce_project_trust and not self._trust_now():
            return await self._build_permission_blocked(call_id)
        try:
            source_snapshot = _snapshot_for_context(self.workspace_root)
            source_manifest_hash = source_snapshot.revision
            workspace_revision = source_snapshot.revision
        except Exception:
            source_manifest_hash = _source_manifest_hash(self.workspace_root)
            workspace_revision = source_manifest_hash
        clean_reason: str | None = None
        if self._platformio_state_requires_clean:
            clean_reason = "previous build failed or was interrupted"
        config_path = self.workspace_root / "platformio.ini"
        config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest() if config_path.is_file() else None
        if self._last_build_config_hash is not None and self._last_build_config_hash != config_hash:
            clean_reason = "build configuration changed"
        self._last_build_config_hash = config_hash
        # Ordinary compiler failures and source edits are handled by PlatformIO's
        # incremental build graph; they do not justify deleting dependencies.
        if clean_reason == "previous build failed or was interrupted":
            clean_reason = None
        if clean_reason and await asyncio.to_thread(
            _remove_platformio_build_state, self.workspace_root
        ):
            await self.progress(
                "BUILD_STATE_RESET",
                {
                    "message": "Cleared stale PlatformIO build state",
                    "reason": clean_reason,
                },
            )
        self._last_build_source_manifest_hash = source_manifest_hash
        self._platformio_state_requires_clean = False
        build_id = f"build-{uuid.uuid4().hex}"
        build_started = time.monotonic()
        await self.progress(
            "BUILD_STARTED",
            {"message": "Building staged firmware", "environment": environment, "build_id": build_id},
        )
        pending_output = ""
        last_output = 0.0

        async def output_chunk(chunk: str) -> None:
            nonlocal pending_output, last_output
            pending_output = (pending_output + chunk)[-16000:]
            now = time.monotonic()
            if now - last_output >= 0.15:
                output = pending_output
                pending_output = ""
                last_output = now
                await self.progress("BUILD_OUTPUT", {"output": output})

        try:
            with observe_output(output_chunk):
                # Progress persistence and build-state cleanup also yield.
                if self.enforce_project_trust and not self._trust_now():
                    return await self._build_permission_blocked(call_id)
                self.build_attempted = True
                result = await _await_before_deadline(
                    self.platformio.build(self.workspace_root, environment=environment), build_deadline)
        except Exception as exc:
            self.build_passed = False
            self._platformio_state_requires_clean = True
            message = str(exc)[:4000]
            deadline_exhausted = isinstance(exc, _OperationDeadlineExpired)
            self.last_build = {
                "success": False,
                "build_id": build_id,
                "workspace_revision": workspace_revision,
                "source_manifest_hash": source_manifest_hash,
                "message": message,
                "error_code": "AGENT_RUNTIME_LIMIT_EXCEEDED" if deadline_exhausted else _build_exception_code(exc),
                "terminal": _is_missing_platformio_error(exc) or deadline_exhausted,
            }
            await self.progress(
                "BUILD_FAILED", {"message": str(exc)[:1000] or "Build failed", "build_id": build_id}
            )
            return {
                "call_id": call_id,
                "status": "completed",
                "tool": ToolName.BUILD_FIRMWARE.value,
                "output": self.last_build,
            }
        data = result.api_dict()
        build_metadata = getattr(result, "metadata", {})
        built_environment = build_metadata.get("environment") or environment
        self._platformio_state_requires_clean = not bool(result.success)
        diagnostics = ""
        if result.process_result is not None:
            diagnostics = (
                result.process_result.stderr_text
                + "\n"
                + result.process_result.stdout_text
            )[-16_000:]
        compiler_diagnostics = []
        for line in diagnostics.splitlines():
            match = re.match(
                r"^(.+?):(\d+)(?::(\d+))?:\s*(warning|error|fatal error):\s*(.+)$", line
            )
            if match:
                compiler_diagnostics.append(
                    {
                        "path": match[1],
                        "line": int(match[2]),
                        "column": int(match[3] or 0),
                        "severity": match[4],
                        "message": match[5][:1000],
                    }
                )
        compiler_diagnostics = compiler_diagnostics[-100:]
        build_log_artifact_id: str | None = None
        firmware_artifact_id: str | None = None
        if self.artifact_service is not None and result.process_result is not None:
            try:
                full_log = result.process_result.stderr_text + "\n" + result.process_result.stdout_text
                log_meta = await self.artifact_service.save_artifact(
                    ArtifactType.BUILD_LOG,
                    f"{build_id}.log",
                    full_log,
                    artifact_id=f"{build_id}-log",
                    content_type="text/plain; charset=utf-8",
                    metadata={
                        "build_id": build_id,
                        "workspace_revision": workspace_revision,
                        "source_manifest_hash": source_manifest_hash,
                        "environment": environment,
                    },
                )
                build_log_artifact_id = log_meta.artifact_id
            except Exception:
                build_log_artifact_id = None
        failure = data.get("failure")
        failure_category = (
            failure.get("category") if isinstance(failure, Mapping) else None
        )
        toolchain_missing = failure_category == "TOOLCHAIN_MISSING"
        firmware_hash = None
        if result.success and result.firmware_path:
            try:
                artifact = Path(result.firmware_path).resolve(strict=True)
                artifact.relative_to(self.workspace_root)
                if artifact.is_file() and not artifact.is_symlink():
                    firmware_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
            except (OSError, ValueError):
                firmware_hash = None
            if firmware_hash and self.artifact_service is not None:
                try:
                    fw_meta = await self.artifact_service.save_artifact(
                        ArtifactType.FIRMWARE_BINARY,
                        Path(result.firmware_path).name,
                        Path(result.firmware_path),
                        artifact_id=f"{build_id}-firmware",
                        metadata={
                            "build_id": build_id,
                            "workspace_revision": workspace_revision,
                            "source_manifest_hash": source_manifest_hash,
                            "environment": environment,
                        },
                    )
                    firmware_artifact_id = fw_meta.artifact_id
                except Exception:
                    firmware_artifact_id = None
        dependency_candidates: list[dict[str, str]] = []
        if not result.success and callable(getattr(self.platformio, "search_libraries", None)):
            for query in _dependency_search_queries(diagnostics):
                if query not in self._dependency_search_cache:
                    if self._dependency_search_count >= 4:
                        continue
                    self._dependency_search_count += 1
                    try:
                        remaining = self._deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self._dependency_search_cache[query] = tuple(
                            await asyncio.wait_for(self.platformio.search_libraries(query, limit=5),
                                                   timeout=remaining)
                        )
                    except Exception:
                        self._dependency_search_cache[query] = ()
                dependency_candidates.extend(
                    {"query": query, "package": package, "source": "platformio_registry"}
                    for package in self._dependency_search_cache[query]
                )
        self.build_durations = (self.build_durations + [time.monotonic() - build_started])[-20:]
        from .engine import PhaseBudget
        if callable(getattr(self.planner, "bind_verification_reserve", None)):
            self.planner.bind_verification_reserve(PhaseBudget.for_task(True, self.build_durations).generation_reserve)
        self.last_build = {
            "success": bool(result.success),
            "build_id": build_id,
            "workspace_revision": workspace_revision,
            "source_manifest_hash": source_manifest_hash,
            "build_log_artifact_id": build_log_artifact_id,
            "firmware_artifact_id": firmware_artifact_id,
            "status": str(getattr(result.status, "value", result.status)),
            "message": result.message,
            "firmware_path": result.firmware_path,
            "firmware_hash": firmware_hash,
            "build_size_bytes": result.build_size_bytes,
            "board": result.board,
            "platform": result.platform,
            "warnings_count": result.warnings_count,
            "diagnostics": diagnostics,
            "compiler_diagnostics": compiler_diagnostics,
            "environment": built_environment,
            "toolchain": dict(getattr(result, "metadata", {}).get("toolchain", {})),
            "artifact_manifest": getattr(result, "metadata", {}).get("artifact_manifest"),
            "flash_ready": getattr(result, "metadata", {}).get("flash_ready", False),
            "flash_readiness_error": getattr(result, "metadata", {}).get("flash_readiness_error"),
            "exit_code": result.process_result.returncode if result.process_result else None,
            "dependency_candidates": dependency_candidates[:10],
            "failure": failure if isinstance(failure, Mapping) else None,
            "error_code": (
                "platformio_executable_missing"
                if toolchain_missing
                else (getattr(result, "metadata", {}).get("error_code") or failure_category)
            ),
            "terminal": toolchain_missing or getattr(result, "metadata", {}).get("error_code") in {"PARTITION_TOOL_UNAVAILABLE", "PARTITION_FLASH_SIZE_UNKNOWN"},
        }
        if not result.success:
            group = _diagnostic_group(self.last_build)
            self._repair_groups[group] = self._repair_groups.get(group, 0) + 1
            pair = f"{workspace_revision}:{environment}:{group}"
            if pair in self._seen_build_pairs:
                self.last_build.update({"repair_exhausted": True, "terminal": True,
                                        "error_code": "BUILD_REPAIR_EXHAUSTED"})
            self._seen_build_pairs.add(pair)
        self.build_passed = bool(
            result.success and result.firmware_path and firmware_hash
        )
        if self.build_passed and not self._refresh_verification():
            self.last_build = {
                "success": False,
                "message": "Workspace or firmware changed during the build; rebuild required.",
                "error_code": "VERIFICATION_STALE",
            }
        elif not self.build_passed:
            self.last_build["success"] = False
            for key in ("firmware_path", "firmware_hash", "firmware_artifact_id"):
                self.last_build.pop(key, None)
        if self.build_passed:
            elf = Path(str(self.last_build["firmware_path"])).with_suffix(".elf")
            if elf.is_file() and not elf.is_symlink() and elf.resolve().is_relative_to(self.workspace_root):
                self.last_build.update(elf_path=str(elf),elf_sha256=hashlib.sha256(elf.read_bytes()).hexdigest())
        for repair in self.repair_history:
            if repair.get("strategy") == "model_edit" and repair.get("result_success") is None:
                repair["result_fingerprint"] = diagnostic_fingerprint(self.last_build)
                repair["result_success"] = self.build_passed
                repair["build_id"] = self.last_build.get("build_id")
        await self.progress(
            "BUILD_COMPLETED" if self.build_passed else "BUILD_FAILED",
            {
                "message": result.message,
                "output": diagnostics,
                "board": result.board,
                "warnings": result.warnings_count,
                "diagnostics": compiler_diagnostics,
                "errors": sum(d["severity"] != "warning" for d in compiler_diagnostics),
                "exit_code": (
                    result.process_result.returncode if result.process_result else None
                ),
                "build_id": build_id,
                "firmware_hash": firmware_hash,
                "firmware_path": result.firmware_path if self.build_passed else None,
                "success": self.build_passed,
                "workspace_revision": workspace_revision,
                "source_manifest_hash": source_manifest_hash,
            },
        )
        if dependency_candidates:
            await self.progress("BUILD_DEPENDENCY_CANDIDATES", {
                "message": "PlatformIO registry returned possible library dependencies",
                "candidates": dependency_candidates[:10],
            })
        if self.repair_attempts and self.build_passed:
            await self.progress(
                "BUILD_REPAIR_COMPLETED", {"message": "Build verified after repair"}
            )
        return {
            "call_id": call_id,
            "status": "completed",
            "tool": ToolName.BUILD_FIRMWARE.value,
            "output": dict(self.last_build),
        }

    async def _simulate(
        self, timeout_seconds: float, *, call_id: str
    ) -> dict[str, object]:
        self._refresh_verification()
        self.simulation_passed = False
        self.last_simulation = {}
        self.simulation_attempted = True
        if self.subprocess_manager is None:
            self.simulation_passed = False
            return {
                "call_id": call_id,
                "status": "failed",
                "tool": ToolName.RUN_SIMULATION.value,
                "error": "simulation_runtime_unavailable",
            }
        firmware_path = self.last_build.get("firmware_path")
        if (
            not self.build_passed
            or not isinstance(firmware_path, str)
            or not firmware_path
        ):
            self.simulation_passed = False
            return {
                "call_id": call_id,
                "status": "failed",
                "tool": ToolName.RUN_SIMULATION.value,
                "error": "successful_build_required_before_simulation",
            }
        board_type = _wokwi_board_type(str(self.last_build.get("board") or ""))
        if board_type is BoardType.UNKNOWN:
            self.simulation_passed = False
            return {
                "call_id": call_id,
                "status": "failed",
                "tool": ToolName.RUN_SIMULATION.value,
                "error": "wokwi_board_not_supported",
            }
        artifact_path = Path(firmware_path).resolve(strict=True)
        artifact_path.relative_to(self.workspace_root)
        artifact = BuildArtifact(
            path=artifact_path,
            environment=_platformio_environment_from_artifact(artifact_path),
            artifact_type=artifact_path.suffix.lstrip(".") or "bin",
            size_bytes=artifact_path.stat().st_size,
        )
        await self.progress(
            "SIMULATION_STARTED",
            {"message": "Running Wokwi virtual hardware simulation"},
        )
        simulation_path: str | None = None
        try:
            result = await WokwiSimulator(self.subprocess_manager).simulate(
                artifact,
                board_type,
                SimulationConfig(timeout_s=max(1.0, min(timeout_seconds, 120.0))),
            )
            if result.simulation_artifact is not None:
                simulation_path = result.simulation_artifact.project_path
            if not self._refresh_verification():
                raise ValueError("verification_changed_during_simulation")
            payload = {
                "success": bool(result.success),
                "status": str(getattr(result.status, "value", result.status)),
                "message": result.message,
                "board": result.board,
                "serial_output": list(result.serial_output[-200:]),
                "lines_captured": result.lines_captured,
                "output_truncated": result.output_truncated,
                "simulation_duration_ms": result.simulation_duration_ms,
                "failure": result.failure.api_dict() if result.failure else None,
                "build_id": self.last_build["build_id"],
                "workspace_revision": self.last_build["workspace_revision"],
                "firmware_hash": self.last_build["firmware_hash"],
            }
            self.last_simulation = dict(payload)
            self.simulation_passed = bool(result.success)
            await self.progress(
                "SIMULATION_COMPLETED" if result.success else "SIMULATION_FAILED",
                {"message": result.message, "success": bool(result.success)},
            )
            return {
                "call_id": call_id,
                "status": "completed" if result.success else "failed",
                "tool": ToolName.RUN_SIMULATION.value,
                "output": payload,
            }
        except (OSError, ValueError, RuntimeError) as exc:
            self.simulation_passed = False
            self.last_simulation = {
                "success": False,
                "message": str(exc)[:2000] or type(exc).__name__,
            }
            await self.progress(
                "SIMULATION_FAILED", {"message": str(exc)[:1000] or "Simulation failed"}
            )
            return {
                "call_id": call_id,
                "status": "failed",
                "tool": ToolName.RUN_SIMULATION.value,
                "error": str(exc)[:2000] or type(exc).__name__,
            }
        finally:
            if simulation_path:
                shutil.rmtree(simulation_path, ignore_errors=True)

    def _memory_search(self, query: str, limit: int) -> list[dict[str, object]]:
        terms = [term for term in query.casefold().split() if term]
        scored: list[tuple[int, Mapping[str, object]]] = []
        for memory in self.memory_entries:
            if memory.get("status") != "active" or memory.get("verified") is not True:
                continue
            haystack = (
                str(memory.get("kind") or "") + " " + str(memory.get("value") or "")
            ).casefold()
            score = sum(term in haystack for term in terms) if terms else 1
            if score:
                scored.append((score, memory))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "kind": str(item.get("kind") or "memory"),
                "value": str(item.get("value") or "")[:1200],
                "source_id": str(item.get("source_id") or ""),
            }
            for _, item in scored[: max(1, min(limit, 20))]
        ]

    def _result(
        self, success: bool, classification: str, message: str
    ) -> AutonomousAgentResult:
        had_verified_build = self.build_passed
        task_snapshot = _snapshot_for_context(self.workspace_root)
        from .task_contract import acceptance_report
        acceptance = acceptance_report(self.task_contract,self.requirement_report,self.last_build,
            self.acceptance_evidence,task_snapshot.revision,self.last_simulation)
        if self.task_contract:
            self.requirement_report["acceptance"] = acceptance
        if success and self.task_contract.get("acceptance_checks") and not acceptance["passed"]:
            success = False
            classification = "ACCEPTANCE_UNVERIFIED"
            message = "The draft is saved, but a requested acceptance check has not passed for current source."
        from .verification import reviewed_current
        if success and self.task_contract and not reviewed_current(self.requirement_report,
                task_snapshot.revision, self.task_contract.get("revision")):
            success = False
            classification = "REQUIREMENTS_UNVERIFIED"
            message = "Files are saved, but the requested requirements have not all been verified against the final source. Review the draft or retry."
        elif success and self.task_contract:
            message = "Requested firmware passed requirement review and compilation. Device behavior remains unqualified." if self.build_passed else "Requested changes passed requirement review. Review the staged files."
        if had_verified_build and not self._refresh_verification() and success:
            success = False
            classification = "VERIFICATION_STALE"
            message = "Workspace or firmware changed after verification; rebuild required."
        return AutonomousAgentResult(
            success=success,
            classification=classification,
            message=message,
            build_result=dict(self.last_build),
            simulation_result=dict(self.last_simulation),
            tool_execution_count=self.tool_count,
            repair_attempt_count=self.repair_attempts,
            repair_history=tuple(self.repair_history[-32:]),
            plan=tuple(self.executor.current_plan),
            provider_diagnostics=_safe_provider_diagnostics(self.planner),
            budget_failure=(
                {"dimension": "elapsed_seconds", "consumed": round(time.monotonic() - self._job_started, 3),
                 "limit": self.max_runtime_seconds, "attempted_charge": 0,
                 "origin": _safe_provider_diagnostics(self.planner).get("last_failure_origin") or "job_deadline"}
                if classification == "AGENT_RUNTIME_LIMIT_EXCEEDED" else
                {"dimension": "tool_calls_used", "consumed": self.tool_count,
                 "limit": self.max_tool_calls, "attempted_charge": 1, "origin": "job_budget"}
                if classification == "AGENT_RUNTIME_TOOL_BUDGET_EXCEEDED"
                else {}
            ),
            build_verified=self.build_passed and self._refresh_verification(),
            requirement_report=dict(self.requirement_report),
            task_state=task_state_v1(
                contract=self.task_contract, plan=self.executor.current_plan,
                observations=self.observations, build=self.last_build,
                report=self.requirement_report, repair_history=self.repair_history,
                source_revision=task_snapshot.revision, source_paths=tuple(task_snapshot.files),
                stalled_turns=self._stalled_turns,
                reorientation_started_at=self._reorientation_started_at),
        )


def _diagnostic_group(build: Mapping) -> str:
    return diagnostic_fingerprint(build)


def _observation(call_id: str, tool: ToolName, output: object) -> dict[str, object]:
    if isinstance(output, str):
        safe: object = output[:16_000]
    elif isinstance(output, tuple):
        safe = list(output[:200])
    elif isinstance(output, Mapping):
        safe = dict(output)
        for key, value in list(safe.items()):
            if isinstance(value, str):
                safe[key] = value[:16_000]
    else:
        safe = str(output)[:16_000]
    return {
        "call_id": call_id,
        "status": "completed",
        "tool": tool.value,
        "output": safe,
    }


def _terminal_build_tool_failure(observation: Mapping[str, object]) -> bool:
    if observation.get("tool") != ToolName.BUILD_FIRMWARE.value:
        return False
    output = observation.get("output")
    return isinstance(output, Mapping) and output.get("terminal") is True


def _terminal_build_classification(observation: Mapping[str, object]) -> str:
    output = observation.get("output")
    if isinstance(output, Mapping):
        # A host permission gate is not a missing compiler. Keep the concrete
        # reason so recovery controls can offer the project trust action.
        if output.get("error_code") == "AGENT_PROJECT_TRUST_REQUIRED":
            return "AGENT_PROJECT_TRUST_REQUIRED"
        if output.get("error_code") == "AGENT_RUNTIME_LIMIT_EXCEEDED":
            return "AGENT_RUNTIME_LIMIT_EXCEEDED"
        if output.get("repair_exhausted") or output.get("error_code") == "BUILD_REPAIR_EXHAUSTED":
            return "BUILD_REPAIR_EXHAUSTED"
        if output.get("error_code") in {"DEPENDENCY_RESOLUTION_UNAVAILABLE", "BUILD_INFRASTRUCTURE_UNAVAILABLE"}:
            return str(output["error_code"])
        if str(output.get("error_code", "")).startswith("PARTITION_"):
            return str(output["error_code"])
    return "BUILD_TOOL_UNAVAILABLE"


def _build_failure_message(observation: Mapping[str, object]) -> str:
    output = observation.get("output")
    if isinstance(output, Mapping):
        message = output.get("message")
        if isinstance(message, str) and message.strip():
            return message[:1000]
    return "PlatformIO is unavailable, so Forge Agent cannot complete the required firmware build."


def _build_exception_code(exc: Exception) -> str:
    if _is_missing_platformio_error(exc):
        return "platformio_executable_missing"
    return type(exc).__name__


def _build_failure_summary(build: Mapping[str, object]) -> dict[str, object]:
    diagnostics = str(build.get("diagnostics") or "")
    errors: list[str] = []
    for line in diagnostics.splitlines():
        stripped = line.strip()
        if re.search(r"(?:fatal )?error:", stripped, re.IGNORECASE) and stripped not in errors:
            errors.append(stripped[:300])
        if len(errors) >= 12:
            break
    missing_package = "could not find the package with" in diagnostics.casefold()
    return {
        "message": str(build.get("message") or "Build failed")[:300],
        "error_code": str(build.get("error_code") or "")[:100],
        "compiler_errors": errors,
        "dependency_candidates": list(build.get("dependency_candidates") or [])[:10],
        "next_action": (
            "Use a package ID from dependency_candidates to correct platformio.ini before rebuilding."
            if missing_package and build.get("dependency_candidates") else
            "Correct the missing PlatformIO package in platformio.ini before rebuilding."
            if missing_package else
            "Read and edit the files named in compiler_errors before rebuilding. An unchanged build repeats the same errors."
        ),
    }


def _transient_build_failure(output: Mapping[str, object]) -> bool:
    if output.get("error_code") == "DEPENDENCY_RESOLUTION_UNAVAILABLE":
        return True
    message = " ".join(str(output.get(key) or "") for key in ("message", "diagnostics"))
    return bool(re.search(
        r"(?i)\b(?:timed? out|connection (?:reset|refused|aborted)|"
        r"temporary failure|network is unreachable|rate limit|http (?:429|502|503|504))\b",
        message[:16_000],
    ))


def _dependency_search_queries(diagnostics: str) -> tuple[str, ...]:
    queries: list[str] = []
    for match in re.finditer(
        r"(?im)could not find the package with ['\"]([^'\"\r\n]{1,100})['\"] requirements"
        r"|fatal error:\s*([^\s:<>\r\n]{1,100}\.(?:h|hpp)): No such file or directory",
        diagnostics[-16_000:],
    ):
        query = (match.group(1) or ("header:" + match.group(2) if match.group(2) else "")).strip()
        if match.group(1):
            # PlatformIO's search command treats the failed lib_deps spec as a
            # literal phrase and returns nothing. Search the package name.
            query = query.split("@", 1)[0].strip().rsplit("/", 1)[-1].strip()
            query = re.sub(r"(?i)^(?:adafruit|sparkfun)[ _-]+", "", query)
        if query and query not in queries and all(ord(char) >= 32 for char in query):
            queries.append(query)
        if len(queries) >= 2:
            break
    return tuple(queries)


def _remove_platformio_build_state(workspace_root: Path) -> bool:
    """Remove only PlatformIO's generated state inside a managed stage.

    A failed SCons graph is not safe to reuse after a deterministic repair or
    model edit.  Keeping it produced dependency-cycle failures even when the
    repaired firmware was valid.  The target is a fixed direct child of the
    already-resolved stage; symlinks are unlinked rather than traversed.
    """

    root = workspace_root.resolve(strict=True)
    target = root / ".pio"
    if not target.exists() and not target.is_symlink():
        return False
    if target.parent != root or target.name != ".pio":
        raise ValueError("platformio_build_state_target_invalid")
    if target.is_symlink() or target.is_file():
        target.unlink()
    else:
        shutil.rmtree(target)
    return True


def _is_missing_platformio_error(exc: Exception) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True
    text = str(exc).casefold()
    return (
        "winerror 2" in text
        or "cannot find the file specified" in text
        or "pio was not found" in text
    )


def _tool_message(tool: ToolName) -> str:
    return {
        ToolName.LIST_FILES: "Listing project files",
        ToolName.GLOB_FILES: "Finding relevant files",
        ToolName.GREP_SEARCH: "Searching project source",
        ToolName.READ_FILE: "Reading source",
        ToolName.WRITE_FILE: "Writing staged source",
        ToolName.EDIT_FILE_SIMPLE: "Editing staged source",
        ToolName.APPLY_PATCH: "Applying precise staged patch",
        ToolName.UPDATE_PLAN: "Updating implementation plan",
        ToolName.LOAD_SKILL: "Loading project skill",
        ToolName.MEMORY_SEARCH: "Searching verified project memory",
        ToolName.SPAWN_SUBAGENT: "Delegating bounded specialist investigation",
        ToolName.BUILD_FIRMWARE: "Building firmware",
        ToolName.RUN_SIMULATION: "Running virtual hardware simulation",
        ToolName.RUN_COMMAND: "Running verification command",
    }.get(tool, tool.value)


def _wokwi_board_type(board: str) -> BoardType:
    normalized = board.casefold().replace("-", "_")
    if "esp32" not in normalized:
        return BoardType.UNKNOWN
    if "s3" in normalized:
        return BoardType.ESP32_S3
    if "c3" in normalized:
        return BoardType.ESP32_C3
    return BoardType.ESP32


def _platformio_environment_from_artifact(path: Path) -> str:
    # PlatformIO artifacts normally live under .pio/build/<environment>/.
    parts = path.parts
    try:
        index = parts.index("build")
        if index + 1 < len(parts):
            return parts[index + 1]
    except ValueError:
        pass
    return "default"


def _source_manifest_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if any(part.casefold() in IGNORED_DIRS or part.casefold() == ".pio" for part in relative.parts):
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(64 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            continue
    return digest.hexdigest()


def _snapshot_for_context(root: Path):
    # Reuse ChangeSet workspace snapshot semantics without owning persistent state.
    from backend.changes import ChangeSetService

    temp = root / ".forgex-agent-context"
    # State/staging are outside source discovery by using sibling temporary paths.
    service = ChangeSetService(
        state_root=root.parent / ".forgex-context-state",
        staging_root=root.parent / ".forgex-context-stage",
    )
    return service.inspect_workspace(root)
