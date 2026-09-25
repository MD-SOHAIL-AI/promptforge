"""Async autonomous Forge Agent V3 loop for edit/build/repair verification."""

from __future__ import annotations

import asyncio
import hashlib
import json
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
from .canonical_runtime import AttemptAccumulator, LoopGuard, OrderedToolScheduler, ToolInvocation
from .api_planner_provider import ApiPlannerError
from .public_progress import LivePlan, public_text
from .run_reporting import actionable_message, normalize_provider_error
from .skills import SkillRegistry
from .subagents import ForgeSubagentRunner
from .tool_contracts import ProductToolCall, ProductToolPlan, RuntimeClassification, ToolName, WRITE_TOOLS
from .tool_executor import ForgeXToolExecutor
from .tool_runtime import _safe_provider_diagnostics, _tool_arguments_summary, _tool_result_summary
from .tool_policy import ToolPermissionPolicy, product_agent_policy, ToolPolicyError

ProgressCallback = Callable[[str, Mapping[str, object]], Awaitable[None]]
SteeringCallback = Callable[[], Sequence[str]]


@dataclass(frozen=True, slots=True)
class AutonomousAgentResult:
    success: bool
    classification: str
    message: str
    build_result: Mapping[str, object] = field(default_factory=dict)
    simulation_result: Mapping[str, object] = field(default_factory=dict)
    tool_execution_count: int = 0
    repair_attempt_count: int = 0
    plan: tuple[Mapping[str, str], ...] = ()
    provider_diagnostics: Mapping[str, object] = field(default_factory=dict)
    build_verified: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "classification": self.classification,
            "message": self.message,
            "build_result": dict(self.build_result),
            "simulation_result": dict(self.simulation_result),
            "tool_execution_count": self.tool_execution_count,
            "repair_attempt_count": self.repair_attempt_count,
            "plan": [dict(item) for item in self.plan],
            "provider_diagnostics": dict(self.provider_diagnostics),
            "build_verified": self.build_verified,
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
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve(strict=True)
        self.planner = planner
        self.platformio = platformio
        self.progress = progress
        self.memory_entries = tuple(memory_entries)
        self.steering_callback = steering_callback
        self.max_turns = max(1, min(max_turns, 60))
        self.max_tool_calls = max(1, min(max_tool_calls, 100))
        self.max_progress_extension_turns = max(
            0, min(max_progress_extension_turns, 60 - self.max_turns)
        )
        self.retry_context = dict(retry_context or {})
        # A provider turn may legitimately spend several minutes reasoning before
        # returning a plan.  An absolute wall-clock budget used to charge that
        # provider time against the tool loop and could reject a valid plan less
        # than a second after its files were written.  Turn and tool-call bounds
        # remain the deterministic loop guards; callers may still opt into a wall
        # clock limit explicitly for constrained environments.
        self.max_runtime_seconds = (
            None
            if max_runtime_seconds is None
            else max(30.0, min(float(max_runtime_seconds), 900.0))
        )
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
        self.scheduler = OrderedToolScheduler(max_parallel_reads=6)
        self.loop_guard = LoopGuard(
            max_steps=self.max_turns + self.max_progress_extension_turns,
            max_tool_calls=self.max_tool_calls,
        )

    async def run(
        self,
        *,
        task: str,
        require_build: bool = True,
        require_simulation: bool = False,
        environment: str | None = None,
    ) -> AutonomousAgentResult:
        if not callable(getattr(self.planner, "request_turn_async", None)) and not callable(
            getattr(self.planner, "request_turn", None)
        ):
            return AutonomousAgentResult(
                False,
                "AGENT_PROVIDER_INVALID",
                "Selected provider cannot run the Forge V3 tool loop.",
            )
        snapshot = _snapshot_for_context(self.workspace_root)
        context = (
            ContextAssembler(self.workspace_root)
            .assemble(
                task=task,
                snapshot=snapshot,
                memories=self.memory_entries,
                skills=tuple(item.label() for item in self.skills.discover()),
            )
            .render()
        )
        allowed_tools = tuple(
            tool.value
            for tool in ToolName
            if self.policy.decisions.get(tool, "deny") != "deny"
            and tool is not ToolName.RUN_COMMAND
        )
        seen_observations: set[str] = set()
        seen_build_errors: set[str] = set()
        meaningful_turns: list[int] = []
        stalled_turns = 0
        verified_build_turn: int | None = None
        started = time.monotonic()
        await self.progress(
            "AGENT_LOOP_STARTED",
            {"message": "Forge Agent V3 continuous tool loop started"},
        )
        if self.retry_context:
            self.observations.append({
                "call_id": "retry_context", "status": "completed",
                "tool": "previous_run_failure", "output": self.retry_context,
            })
        for turn in range(1, self.max_turns + self.max_progress_extension_turns + 1):
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
                and time.monotonic() - started > self.max_runtime_seconds
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

                    raw = await request_turn_async(
                        task=context,
                        tool_results=compacted,
                        allowed_tools=allowed_tools,
                        run_id=f"v3:{self.workspace_root.name}",
                        turn=turn,
                        stream=True,
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
                    message = actionable_message(
                        error_code,
                        str(getattr(self.planner, "provider_id", "The provider")),
                    )
                else:
                    classification = type(exc).__name__
                    message = str(exc) or "Agent provider turn failed."
                return self._result(False, classification, message)
            settlement = attempt.settle(plan.tool_calls, content=public_text(plan.summary))
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
                if require_build and not self.build_attempted:
                    build_observation = await self._build(environment)
                    self.observations.append(build_observation)
                    if _terminal_build_tool_failure(build_observation):
                        return self._result(
                            False,
                            "BUILD_TOOL_UNAVAILABLE",
                            _build_failure_message(build_observation),
                        )
                    if self.build_passed:
                        if not require_simulation:
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
                return self._result(
                    True,
                    "AGENT_V3_COMPLETED",
                    plan.summary.strip() or "Forge Agent completed the requested task.",
                )

            invocations = settlement.invocations
            if (
                require_build
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

            scheduled = await self.scheduler.run(
                invocations,
                lambda invocation: self._execute_invocation(invocation, environment=environment),
            )
            made_progress = False
            meaningful_progress = False
            for scheduled_result in scheduled:
                invocation = scheduled_result.invocation
                observation = dict(scheduled_result.result)
                output = observation.get("output")
                if observation.get("status") == "completed" and invocation.tool in WRITE_TOOLS:
                    changed = isinstance(output, Mapping) and bool(
                        output.get("created") or output.get("deleted")
                        or output.get("moved") or output.get("diff")
                    )
                    made_progress = made_progress or changed
                    meaningful_progress = meaningful_progress or changed
                    if changed:
                        self.loop_guard.workspace_changed()
                elif observation.get("status") == "completed" and invocation.tool is ToolName.BUILD_FIRMWARE:
                    if isinstance(output, Mapping) and output.get("success") is True:
                        made_progress = True
                        meaningful_progress = True
                    elif isinstance(output, Mapping) and not output.get("cached"):
                        error_fingerprint = hashlib.sha256(
                            str(output.get("diagnostics") or output.get("message") or "").encode("utf-8")
                        ).hexdigest()
                        if error_fingerprint not in seen_build_errors:
                            seen_build_errors.add(error_fingerprint)
                            made_progress = True
                            meaningful_progress = True
                elif observation.get("status") == "completed" and invocation.tool is ToolName.RUN_SIMULATION:
                    simulated = isinstance(output, Mapping) and output.get("success") is True
                    made_progress = made_progress or simulated
                    meaningful_progress = meaningful_progress or simulated
                elif invocation.tool not in {ToolName.UPDATE_PLAN, ToolName.SPAWN_SUBAGENT}:
                    fingerprint = hashlib.sha256(
                        json.dumps(
                            [invocation.tool.value, dict(invocation.arguments), observation.get("status"), observation.get("output"), observation.get("error")],
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
                    return self._result(False, "BUILD_TOOL_UNAVAILABLE", _build_failure_message(observation))

            if require_build and not require_simulation:
                verified = self._refresh_verification()
                if not verified:
                    verified_build_turn = None
                elif any(invocation.tool is ToolName.BUILD_FIRMWARE for invocation in invocations):
                    verified_build_turn = turn
                elif verified_build_turn is not None and turn > verified_build_turn and not any(
                    invocation.tool in WRITE_TOOLS for invocation in invocations
                ):
                    return self._result(
                        True,
                        "AGENT_V3_COMPLETED",
                        "Firmware files are complete and the PlatformIO build is verified.",
                    )

            if meaningful_progress:
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
                    return self._result(
                        False,
                        "AGENT_RUNTIME_NO_PROGRESS",
                        "Forge Agent stopped after repeated tool turns with no new information or file changes.",
                    )

            await self.progress(
                "STEP_SETTLED",
                {"turn": turn, "step": turn, "attempt_id": attempt.attempt_id, "tool_count": len(invocations)},
            )

        return self._result(
            False,
            "AGENT_RUNTIME_MAX_TURNS",
            "Forge Agent reached the maximum number of reasoning turns.",
        )

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
                observation = await self._build(selected_env, call_id=invocation.call_id)
            elif call.tool is ToolName.RUN_SIMULATION:
                timeout = call.arguments.get("timeout_seconds", 15)
                observation = await self._simulate(float(timeout), call_id=invocation.call_id)
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
            else:
                # Filesystem inspection and edits are synchronous host operations.
                # Parallel read batches run them in worker threads; workspace
                # mutations remain scheduler barriers.
                output = await asyncio.to_thread(self.executor.execute, call)
                if call.tool in WRITE_TOOLS and isinstance(output, Mapping) and (
                    output.get("created") or output.get("deleted")
                    or output.get("moved") or output.get("diff")
                ):
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

    def _compacted_observations(
        self, *, keep: int = 24
    ) -> tuple[tuple[Mapping[str, object], ...], Mapping[str, object] | None]:
        """Keep recent detailed observations and replace older history with a bounded checkpoint."""
        if len(self.observations) <= keep:
            return tuple(self.observations), None
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
        return (checkpoint, *self.observations[-keep:]), new_checkpoint

    def _invalidate_verification(self) -> None:
        """Drop executable artifact references; leave files in the stage alone."""
        self.build_attempted = False
        self.build_passed = False
        self.last_build = {}
        self.simulation_attempted = False
        self.simulation_passed = False
        self.last_simulation = {}

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

    async def _build(
        self, environment: str | None, *, call_id: str = "required_build"
    ) -> dict[str, object]:
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
        if self.build_passed or _terminal_build_tool_failure(observation):
            self._remember_failed_build(environment, observation)
            return observation
        repairs = []
        for _ in range(3):
            repair = await asyncio.to_thread(
                repair_verified_cpp_build_errors,
                self.workspace_root,
                self.last_build,
            )
            if repair is None:
                break
            repairs.append(repair.to_dict())
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
        revision = _snapshot_for_context(self.workspace_root).revision
        self._failed_build_cache[(revision, environment)] = dict(output)

    async def _build_once(
        self, environment: str | None, *, call_id: str = "required_build"
    ) -> dict[str, object]:
        self._invalidate_verification()
        self.build_attempted = True
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
        elif (
            self._last_build_source_manifest_hash is not None
            and self._last_build_source_manifest_hash != source_manifest_hash
        ):
            clean_reason = "project sources or build configuration changed"
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
        await self.progress(
            "BUILD_STARTED",
            {"message": "Building staged firmware", "environment": environment},
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
                result = await self.platformio.build(
                    self.workspace_root, environment=environment
                )
        except Exception as exc:
            self.build_passed = False
            self._platformio_state_requires_clean = True
            message = str(exc)[:4000]
            self.last_build = {
                "success": False,
                "build_id": build_id,
                "workspace_revision": workspace_revision,
                "source_manifest_hash": source_manifest_hash,
                "message": message,
                "error_code": _build_exception_code(exc),
                "terminal": _is_missing_platformio_error(exc),
            }
            await self.progress(
                "BUILD_FAILED", {"message": str(exc)[:1000] or "Build failed"}
            )
            return {
                "call_id": call_id,
                "status": "completed",
                "tool": ToolName.BUILD_FIRMWARE.value,
                "output": self.last_build,
            }
        data = result.api_dict()
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
                        self._dependency_search_cache[query] = tuple(
                            await self.platformio.search_libraries(query, limit=5)
                        )
                    except Exception:
                        self._dependency_search_cache[query] = ()
                dependency_candidates.extend(
                    {"query": query, "package": package, "source": "platformio_registry"}
                    for package in self._dependency_search_cache[query]
                )
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
            "dependency_candidates": dependency_candidates[:10],
            "failure": failure if isinstance(failure, Mapping) else None,
            "error_code": (
                "platformio_executable_missing"
                if toolchain_missing
                else failure_category
            ),
            "terminal": toolchain_missing,
        }
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
            plan=tuple(self.executor.current_plan),
            provider_diagnostics=_safe_provider_diagnostics(self.planner),
            build_verified=self.build_passed and self._refresh_verification(),
        )


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
