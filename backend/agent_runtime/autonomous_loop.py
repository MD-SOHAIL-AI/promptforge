"""Async autonomous Forge Agent V3 loop for edit/build/repair verification."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Sequence

from backend.services.platformio_service import PlatformIOService

from .api_planner_provider import ApiPlannerError
from .context_assembler import ContextAssembler
from .skills import SkillRegistry
from .subagents import ForgeSubagentRunner
from .tool_contracts import ProductToolPlan, ToolName, WRITE_TOOLS
from .tool_executor import ForgeXToolExecutor
from .tool_policy import product_agent_policy, ToolPolicyError

ProgressCallback = Callable[[str, Mapping[str, object]], Awaitable[None]]
SteeringCallback = Callable[[], Sequence[str]]


@dataclass(frozen=True, slots=True)
class AutonomousAgentResult:
    success: bool
    classification: str
    message: str
    build_result: Mapping[str, object] = field(default_factory=dict)
    tool_execution_count: int = 0
    repair_attempt_count: int = 0
    plan: tuple[Mapping[str, str], ...] = ()
    provider_id: str | None = None
    model_id: str | None = None
    provider_diagnostics: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "classification": self.classification,
            "message": self.message,
            "build_result": dict(self.build_result),
            "tool_execution_count": self.tool_execution_count,
            "repair_attempt_count": self.repair_attempt_count,
            "plan": [dict(item) for item in self.plan],
            "actual_provider_id": self.provider_id,
            "model_id": self.model_id,
            "provider_diagnostics": dict(self.provider_diagnostics),
        }


class AutonomousForgeAgentLoop:
    """Continuous model/tool loop running inside an already-isolated stage workspace."""

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        planner: object,
        platformio: PlatformIOService,
        progress: ProgressCallback,
        memory_entries: Sequence[Mapping[str, object]] = (),
        steering_callback: SteeringCallback | None = None,
        max_turns: int = 14,
        max_tool_calls: int = 60,
        max_runtime_seconds: float = 360.0,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve(strict=True)
        self.planner = planner
        self.platformio = platformio
        self.progress = progress
        self.memory_entries = tuple(memory_entries)
        self.steering_callback = steering_callback
        self.max_turns = max(1, min(max_turns, 30))
        self.max_tool_calls = max(1, min(max_tool_calls, 100))
        self.max_runtime_seconds = max(30.0, min(float(max_runtime_seconds), 900.0))
        self.policy = product_agent_policy()
        self.skills = SkillRegistry(self.workspace_root)
        self.executor = ForgeXToolExecutor(
            write_root=self.workspace_root,
            read_root=self.workspace_root,
            policy=self.policy,
            skill_registry=self.skills,
        )
        self.observations: list[Mapping[str, object]] = []
        self.tool_count = 0
        self.repair_attempts = 0
        self.last_build: dict[str, object] = {}
        self.build_attempted = False
        self.build_passed = False
        self._compaction_count = 0

    async def run(
        self,
        *,
        task: str,
        require_build: bool = True,
        allow_write: bool = True,
        allow_repair: bool = True,
        environment: str | None = None,
    ) -> AutonomousAgentResult:
        request_turn = getattr(self.planner, "request_turn", None)
        if not callable(request_turn):
            return AutonomousAgentResult(False, "AGENT_PROVIDER_INVALID", "Selected provider cannot run the Forge V3 tool loop.")
        snapshot = _snapshot_for_context(self.workspace_root)
        context = ContextAssembler(self.workspace_root).assemble(
            task=task,
            snapshot=snapshot,
            memories=self.memory_entries,
            skills=tuple(item.label() for item in self.skills.discover()),
        ).render()
        tools = [
            ToolName.LIST_FILES,
            ToolName.GLOB_FILES,
            ToolName.GREP_SEARCH,
            ToolName.READ_FILE,
            ToolName.UPDATE_PLAN,
            ToolName.LOAD_SKILL,
            ToolName.MEMORY_SEARCH,
            ToolName.SPAWN_SUBAGENT,
            ToolName.BUILD_FIRMWARE,
        ]
        if allow_write:
            tools.extend((ToolName.WRITE_FILE, ToolName.EDIT_FILE_SIMPLE))
        allowed_tools = tuple(tool.value for tool in tools)
        event_loop = asyncio.get_running_loop()
        attach_delta = getattr(self.planner, "set_delta_listener", None)
        if callable(attach_delta):
            delta_index = {"value": 0}

            def on_delta(turn: int, delta: str) -> None:
                delta_index["value"] += 1
                asyncio.run_coroutine_threadsafe(
                    self.progress("message.delta", {
                        "event_type": "message.delta",
                        "status": "running",
                        "turn": int(turn),
                        "index": delta_index["value"],
                        "delta": delta,
                    }),
                    event_loop,
                )

            attach_delta(on_delta)
        started = time.monotonic()
        await self.progress("AGENT_LOOP_STARTED", {"message": "Forge Agent V3 continuous tool loop started"})
        for turn in range(1, self.max_turns + 1):
            if time.monotonic() - started > self.max_runtime_seconds:
                return self._result(False, "AGENT_RUNTIME_LIMIT_EXCEEDED", "Forge Agent reached the runtime limit.")
            steering = tuple(self.steering_callback() if self.steering_callback is not None else ())
            if steering:
                for message in steering:
                    self.observations.append({
                        "call_id": f"steer_{turn}",
                        "status": "completed",
                        "tool": "user_steer",
                        "output": str(message)[:4000],
                    })
                await self.progress("AGENT_STEERED", {"message": steering[-1][:500], "count": len(steering)})
            compacted = self._compacted_observations()
            await self.progress("AGENT_TURN_STARTED", {"message": f"Agent turn {turn}", "turn": turn})
            try:
                raw = await asyncio.to_thread(
                    request_turn,
                    task=context,
                    tool_results=compacted,
                    allowed_tools=allowed_tools,
                    run_id=f"v3:{self.workspace_root.name}",
                    turn=turn,
                )
                plan = raw if isinstance(raw, ProductToolPlan) else ProductToolPlan.parse(raw, max_calls=6)
            except ApiPlannerError as exc:
                return self._result(False, exc.classification, exc.safe_code)
            except Exception as exc:
                return self._result(False, type(exc).__name__, str(exc) or "Agent provider turn failed.")
            await self.progress("AGENT_TURN_COMPLETED", {"message": plan.summary, "turn": turn, "tool_count": len(plan.tool_calls)})

            if plan.final:
                if require_build and not self.build_attempted:
                    build_observation = await self._build(environment)
                    self.observations.append(build_observation)
                    if self.build_passed:
                        return self._result(True, "AGENT_V3_COMPLETED", "Firmware changes were built and verified.")
                    if not allow_repair:
                        return self._result(False, "BUILD_ERROR", "The read-only build failed; no repair was authorized.")
                    # A failed required build is an observation; authorized edit runs get a chance to repair.
                    self.repair_attempts += 1
                    continue
                if require_build and not self.build_passed:
                    return self._result(False, "BUILD_ERROR", "Forge Agent stopped without a successful required build.")
                return self._result(True, "AGENT_V3_COMPLETED", "Forge Agent completed the requested task.")

            for product_call in plan.tool_calls:
                if self.tool_count >= self.max_tool_calls:
                    return self._result(False, "AGENT_RUNTIME_LIMIT_EXCEEDED", "Forge Agent reached the tool-call limit.")
                call = product_call.as_runtime_call()
                await self.progress("TOOL_STARTED", {"tool": call.tool.value, "message": _tool_message(call.tool)})
                try:
                    if call.tool is ToolName.BUILD_FIRMWARE:
                        selected = call.arguments.get("environment")
                        selected_env = str(selected) if isinstance(selected, str) and selected.strip() else environment
                        observation = await self._build(selected_env, call_id=product_call.call_id)
                    elif call.tool is ToolName.MEMORY_SEARCH:
                        observation = {
                            "call_id": product_call.call_id,
                            "status": "completed",
                            "tool": call.tool.value,
                            "output": self._memory_search(str(call.arguments.get("query") or ""), int(call.arguments.get("limit", 5))),
                        }
                    elif call.tool is ToolName.SPAWN_SUBAGENT:
                        child = ForgeSubagentRunner(
                            workspace_root=self.workspace_root,
                            planner=self.planner,
                            platformio=self.platformio,
                            progress=self.progress,
                            memory_entries=self.memory_entries,
                        )
                        child_result = await child.run(
                            role=str(call.arguments.get("role") or "explore"),
                            task=str(call.arguments.get("task") or ""),
                            environment=environment,
                        )
                        observation = {
                            "call_id": product_call.call_id,
                            "status": "completed" if child_result.success else "failed",
                            "tool": call.tool.value,
                            "output": child_result.to_dict(),
                        }
                    else:
                        self.executor.validate(call)
                        output = self.executor.execute(call)
                        observation = _observation(product_call.call_id, call.tool, output)
                except (ToolPolicyError, ValueError, OSError, KeyError) as exc:
                    observation = {
                        "call_id": product_call.call_id,
                        "status": "failed",
                        "tool": call.tool.value,
                        "error": str(exc)[:1000],
                    }
                self.tool_count += 1
                self.observations.append(observation)
                await self.progress("TOOL_COMPLETED", {"tool": call.tool.value, "message": _tool_message(call.tool) + " completed", "tool_execution_count": self.tool_count})

        return self._result(False, "AGENT_RUNTIME_MAX_TURNS", "Forge Agent reached the maximum number of reasoning turns.")

    def _compacted_observations(self, *, keep: int = 24) -> tuple[Mapping[str, object], ...]:
        """Keep recent detailed observations and replace older history with a bounded checkpoint."""
        if len(self.observations) <= keep:
            return tuple(self.observations)
        older = self.observations[:-keep]
        checkpoint = {
            "call_id": f"context_checkpoint_{self._compaction_count + 1}",
            "status": "completed",
            "tool": "context_compaction",
            "output": {
                "compacted_observations": len(older),
                "tools": [str(item.get("tool") or "unknown") for item in older[-30:]],
                "failures": sum(item.get("status") in {"failed", "denied"} for item in older),
                "last_plan": [dict(item) for item in self.executor.current_plan],
                "build_attempted": self.build_attempted,
                "build_passed": self.build_passed,
            },
        }
        self._compaction_count += 1
        return (checkpoint, *self.observations[-keep:])

    async def _build(self, environment: str | None, *, call_id: str = "required_build") -> dict[str, object]:
        self.build_attempted = True
        await self.progress("BUILD_STARTED", {"message": "Building staged firmware"})
        try:
            result = await self.platformio.build(self.workspace_root, environment=environment)
        except Exception as exc:
            self.build_passed = False
            self.last_build = {"success": False, "message": str(exc)[:4000]}
            await self.progress("BUILD_FAILED", {"message": str(exc)[:1000] or "Build failed"})
            return {"call_id": call_id, "status": "completed", "tool": ToolName.BUILD_FIRMWARE.value, "output": self.last_build}
        data = result.api_dict()
        diagnostics = ""
        if result.process_result is not None:
            diagnostics = (result.process_result.stderr_text + "\n" + result.process_result.stdout_text)[-16_000:]
        firmware_hash = None
        if result.success and result.firmware_path:
            try:
                artifact = Path(result.firmware_path).resolve(strict=True)
                artifact.relative_to(self.workspace_root)
                if artifact.is_file() and not artifact.is_symlink():
                    firmware_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
            except (OSError, ValueError):
                firmware_hash = None
        self.last_build = {
            "success": bool(result.success),
            "status": str(getattr(result.status, "value", result.status)),
            "message": result.message,
            "firmware_path": result.firmware_path,
            "firmware_hash": firmware_hash,
            "build_size_bytes": result.build_size_bytes,
            "board": result.board,
            "platform": result.platform,
            "warnings_count": result.warnings_count,
            "diagnostics": diagnostics,
        }
        self.build_passed = bool(result.success and result.firmware_path and firmware_hash)
        await self.progress("BUILD_COMPLETED" if self.build_passed else "BUILD_FAILED", {"message": result.message})
        return {"call_id": call_id, "status": "completed", "tool": ToolName.BUILD_FIRMWARE.value, "output": dict(self.last_build)}

    def _memory_search(self, query: str, limit: int) -> list[dict[str, object]]:
        terms = [term for term in query.casefold().split() if term]
        scored: list[tuple[int, Mapping[str, object]]] = []
        for memory in self.memory_entries:
            if memory.get("status") != "active" or memory.get("verified") is not True:
                continue
            haystack = (str(memory.get("kind") or "") + " " + str(memory.get("value") or "")).casefold()
            score = sum(term in haystack for term in terms) if terms else 1
            if score:
                scored.append((score, memory))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {"kind": str(item.get("kind") or "memory"), "value": str(item.get("value") or "")[:1200], "source_id": str(item.get("source_id") or "")}
            for _, item in scored[: max(1, min(limit, 20))]
        ]

    def _result(self, success: bool, classification: str, message: str) -> AutonomousAgentResult:
        diagnostics = _safe_planner_metadata(self.planner)
        return AutonomousAgentResult(
            success=success,
            classification=classification,
            message=message,
            build_result=dict(self.last_build),
            tool_execution_count=self.tool_count,
            repair_attempt_count=self.repair_attempts,
            plan=tuple(self.executor.current_plan),
            provider_id=_safe_optional_string(diagnostics.get("provider_id")) or _safe_optional_string(getattr(self.planner, "provider_id", None)),
            model_id=_safe_optional_string(diagnostics.get("model_id")) or _safe_optional_string(getattr(self.planner, "model_id", None)),
            provider_diagnostics=diagnostics,
        )


def _safe_optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _safe_planner_metadata(planner: object) -> dict[str, object]:
    getter = getattr(planner, "to_safe_metadata", None)
    if not callable(getter):
        return {}
    try:
        metadata = getter()
    except Exception:
        return {}
    if not isinstance(metadata, Mapping):
        return {}
    allowed = {
        "provider_id", "model_id", "requested_model_id", "classification",
        "outbound_request_count", "request_reached_provider", "http_status",
        "provider_request_id", "safe_error_code", "toolplan_normalized",
        "toolplan_repair_attempted", "toolplan_mode", "fallback_used", "fallback_reason",
    }
    return {
        key: value for key, value in metadata.items()
        if key in allowed and isinstance(value, (str, int, bool, type(None)))
    }


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
    return {"call_id": call_id, "status": "completed", "tool": tool.value, "output": safe}


def _tool_message(tool: ToolName) -> str:
    return {
        ToolName.LIST_FILES: "Listing project files",
        ToolName.GLOB_FILES: "Finding relevant files",
        ToolName.GREP_SEARCH: "Searching project source",
        ToolName.READ_FILE: "Reading source",
        ToolName.WRITE_FILE: "Writing staged source",
        ToolName.EDIT_FILE_SIMPLE: "Editing staged source",
        ToolName.UPDATE_PLAN: "Updating implementation plan",
        ToolName.LOAD_SKILL: "Loading project skill",
        ToolName.MEMORY_SEARCH: "Searching verified project memory",
        ToolName.SPAWN_SUBAGENT: "Delegating bounded specialist investigation",
        ToolName.BUILD_FIRMWARE: "Building firmware",
        ToolName.RUN_COMMAND: "Running verification command",
    }.get(tool, tool.value)


def _snapshot_for_context(root: Path):
    # Reuse ChangeSet workspace snapshot semantics without owning persistent state.
    from backend.changes import ChangeSetService
    temp = root / ".forgex-agent-context"
    # State/staging are outside source discovery by using sibling temporary paths.
    service = ChangeSetService(state_root=root.parent / ".forgex-context-state", staging_root=root.parent / ".forgex-context-stage")
    return service.inspect_workspace(root)
