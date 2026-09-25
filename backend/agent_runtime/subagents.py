"""Bounded one-level child agents for Forge Agent V3."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Sequence

from backend.services.platformio_service import PlatformIOService

from .context_assembler import ContextAssembler
from .public_progress import LivePlan, public_text
from .tool_runtime import _tool_arguments_summary, _tool_result_summary
from .skills import SkillRegistry
from .tool_contracts import ProductToolPlan, ToolName
from .tool_executor import ForgeXToolExecutor
from .tool_policy import ToolPolicyError, subagent_policy

ProgressCallback = Callable[[str, Mapping[str, object]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class SubagentResult:
    role: str
    success: bool
    summary: str
    tool_execution_count: int
    observations: tuple[Mapping[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "success": self.success,
            "summary": self.summary,
            "tool_execution_count": self.tool_execution_count,
            "observations": [dict(item) for item in self.observations[-8:]],
        }


class ForgeSubagentRunner:
    """Run Explore/Review/Verify children with independent bounded context.

    Children are intentionally one level deep: SPAWN_SUBAGENT is never included
    in their tool surface. Explore/Review are read-only; Verify may run the
    trusted PlatformIO build service but cannot edit files.
    """

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        planner: object,
        platformio: PlatformIOService,
        progress: ProgressCallback,
        memory_entries: Sequence[Mapping[str, object]] = (),
        max_turns: int = 8,
        max_tool_calls: int = 24,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve(strict=True)
        self.planner = planner
        self.platformio = platformio
        self.progress = progress
        self.memory_entries = tuple(memory_entries)
        self.max_turns = max(1, min(max_turns, 12))
        self.max_tool_calls = max(1, min(max_tool_calls, 40))
        self.skills = SkillRegistry(self.workspace_root)

    async def run(
        self, *, role: str, task: str, environment: str | None = None
    ) -> SubagentResult:
        identity = f"specialist-{uuid.uuid4().hex}"
        upstream = self.progress
        active = True

        async def progress(event: str, payload: Mapping[str, object]) -> None:
            if active:
                await upstream(
                    event,
                    {
                        **payload,
                        "agent_id": identity,
                        "parent_agent_id": "main",
                        "role": role,
                        "assignment": public_text(task, 500),
                    },
                )

        self.progress = progress
        try:
            return await self._run(role=role, task=task, environment=environment)
        except asyncio.CancelledError:
            await progress("SUBAGENT_CANCELLED", {"message": "Specialist cancelled"})
            raise
        except Exception:
            await progress(
                "SUBAGENT_FAILED",
                {"message": "Specialist could not finish the assignment"},
            )
            raise
        finally:
            active = False
            self.progress = upstream

    async def _run(
        self, *, role: str, task: str, environment: str | None = None
    ) -> SubagentResult:
        role = role.casefold().strip()
        if role not in {"explore", "review", "verify"}:
            raise ValueError("AGENT_SUBAGENT_ROLE_INVALID")
        select_role = getattr(self.planner, "for_role", None)
        planner = select_role(role) if callable(select_role) else self.planner
        request_turn_async = getattr(planner, "request_turn_async", None)
        request_turn = getattr(planner, "request_turn", None)
        if not callable(request_turn_async) and not callable(request_turn):
            raise ValueError("AGENT_PROVIDER_INVALID")

        allow_build = role == "verify"
        policy = subagent_policy(allow_build=allow_build)
        executor = ForgeXToolExecutor(
            write_root=self.workspace_root,
            read_root=self.workspace_root,
            policy=policy,
            skill_registry=self.skills,
        )
        # Context is intentionally independently assembled instead of copying the
        # parent's whole observation history.
        snapshot = _snapshot(self.workspace_root)
        role_rules = {
            "explore": "Investigate the repository and return evidence. Do not modify files.",
            "review": "Review the existing/staged implementation for correctness and risks. Do not modify files.",
            "verify": "Verify with inspection and trusted builds. Do not modify files.",
        }[role]
        context = (
            ContextAssembler(self.workspace_root)
            .assemble(
                task=f"SUBAGENT ROLE: {role.upper()}\n{role_rules}\n\nTASK:\n{task}",
                snapshot=snapshot,
                memories=self.memory_entries,
                skills=tuple(item.label() for item in self.skills.discover()),
            )
            .render()
        )
        allowed = [
            ToolName.LIST_FILES,
            ToolName.GLOB_FILES,
            ToolName.GREP_SEARCH,
            ToolName.READ_FILE,
            ToolName.UPDATE_PLAN,
            ToolName.LOAD_SKILL,
            ToolName.MEMORY_SEARCH,
        ]
        if allow_build:
            allowed.append(ToolName.BUILD_FIRMWARE)
        allowed_tools = tuple(tool.value for tool in allowed)
        observations: list[Mapping[str, object]] = []
        tool_count = 0
        live_plan = LivePlan()
        last_build_success: bool | None = None
        last_summary = f"{role} subagent completed."

        await self.progress(
            "SUBAGENT_STARTED",
            {"role": role, "message": f"{role.title()} subagent started"},
        )
        for turn in range(1, self.max_turns + 1):
            kwargs = dict(
                task=context,
                tool_results=tuple(_compact(observations)),
                allowed_tools=allowed_tools,
                run_id=f"subagent:{role}:{self.workspace_root.name}",
                turn=turn,
            )
            stream_turn = getattr(planner, "request_turn_stream", None)
            if callable(request_turn_async):
                async def on_summary_async(message: str) -> None:
                    await self.progress(
                        "ASSISTANT_SUMMARY_DELTA",
                        {"message": public_text(message, 1200), "turn": turn},
                    )

                raw = await request_turn_async(
                    **kwargs, stream=True, on_summary=on_summary_async
                )
            elif callable(stream_turn):
                loop = asyncio.get_running_loop()
                progress = self.progress
                accepting = True

                def on_summary(message: str) -> None:
                    if accepting and not loop.is_closed():
                        future = asyncio.run_coroutine_threadsafe(
                            progress(
                                "ASSISTANT_SUMMARY_DELTA",
                                {"message": public_text(message, 1200), "turn": turn},
                            ),
                            loop,
                        )
                        # Backpressure bounds callbacks and preserves summary ordering.
                        future.result(timeout=10)

                try:
                    raw = await asyncio.to_thread(
                        stream_turn, **kwargs, on_summary=on_summary
                    )
                finally:
                    accepting = False
            else:
                raw = await asyncio.to_thread(request_turn, **kwargs)
            plan = (
                raw
                if isinstance(raw, ProductToolPlan)
                else ProductToolPlan.parse(raw, max_calls=5)
            )
            last_summary = public_text(plan.summary, 1200) or last_summary
            await self.progress(
                "ASSISTANT_SUMMARY", {"message": last_summary, "turn": turn}
            )
            if plan.final:
                if role == "verify" and last_build_success is False:
                    summary = "Verification ended with a failed build. Review the build diagnostics."
                    await self.progress(
                        "SUBAGENT_FAILED", {"role": role, "message": summary}
                    )
                    return SubagentResult(
                        role, False, summary, tool_count, tuple(observations)
                    )
                await self.progress(
                    "SUBAGENT_COMPLETED", {"role": role, "message": last_summary}
                )
                return SubagentResult(
                    role, True, last_summary, tool_count, tuple(observations)
                )

            for product_call in plan.tool_calls:
                if tool_count >= self.max_tool_calls:
                    summary = f"{role.title()} subagent reached its tool limit."
                    await self.progress(
                        "SUBAGENT_FAILED", {"role": role, "message": summary}
                    )
                    return SubagentResult(
                        role, False, summary, tool_count, tuple(observations)
                    )
                call = product_call.as_runtime_call()
                started = time.monotonic()
                await self.progress(
                    "TOOL_STARTED",
                    {
                        "tool": call.tool.value,
                        "tool_call_id": product_call.call_id,
                        "arguments": _tool_arguments_summary(call.arguments),
                        "plan_step_id": live_plan.active_step,
                    },
                )
                try:
                    if call.tool is ToolName.MEMORY_SEARCH:
                        output: object = self._memory_search(
                            str(call.arguments.get("query") or ""),
                            int(call.arguments.get("limit", 5)),
                        )
                    elif call.tool is ToolName.BUILD_FIRMWARE:
                        last_build_success = False
                        if not allow_build:
                            raise ToolPolicyError(None, "subagent_build_forbidden")  # type: ignore[arg-type]
                        selected = call.arguments.get("environment")
                        selected_env = (
                            str(selected)
                            if isinstance(selected, str) and selected.strip()
                            else environment
                        )
                        build = await self.platformio.build(
                            self.workspace_root, environment=selected_env
                        )
                        output = build.api_dict()
                        last_build_success = output.get("success") is True
                    else:
                        executor.validate(call)
                        output = executor.execute(call)
                    observation = _observation(product_call.call_id, call.tool, output)
                except (
                    Exception
                ) as exc:  # child failures become evidence for the child, not parent crashes
                    observation = {
                        "call_id": product_call.call_id,
                        "status": "failed",
                        "tool": call.tool.value,
                        "error": str(exc)[:1000],
                    }
                live_plan.observe(
                    product_call.call_id,
                    call.tool is not ToolName.UPDATE_PLAN
                    and observation.get("status") == "completed"
                    and not (
                        isinstance(observation.get("output"), Mapping)
                        and observation["output"].get("success") is False
                    ),
                    call.tool.value,
                )
                if (
                    call.tool is ToolName.UPDATE_PLAN
                    and observation.get("status") == "completed"
                ):
                    await self.progress(
                        "PLAN_UPDATED", live_plan.update(executor.current_plan)
                    )
                await self.progress(
                    (
                        "TOOL_FAILED"
                        if observation.get("status") == "failed"
                        else "TOOL_COMPLETED"
                    ),
                    {
                        "tool": call.tool.value,
                        "tool_call_id": product_call.call_id,
                        "result": _tool_result_summary(observation.get("output")),
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    },
                )
                observations.append(observation)
                tool_count += 1

        summary = f"{role.title()} subagent reached its turn limit. Last state: {last_summary}"
        await self.progress("SUBAGENT_FAILED", {"role": role, "message": summary})
        return SubagentResult(role, False, summary, tool_count, tuple(observations))

    def _memory_search(self, query: str, limit: int) -> list[dict[str, object]]:
        terms = [term for term in query.casefold().split() if term]
        scored: list[tuple[int, Mapping[str, object]]] = []
        for item in self.memory_entries:
            if item.get("status") != "active" or item.get("verified") is not True:
                continue
            text = (
                str(item.get("kind") or "") + " " + str(item.get("value") or "")
            ).casefold()
            score = sum(term in text for term in terms) if terms else 1
            if score:
                scored.append((score, item))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {
                "kind": str(item.get("kind") or "memory"),
                "value": str(item.get("value") or "")[:1200],
            }
            for _, item in scored[: max(1, min(limit, 20))]
        ]


def _snapshot(root: Path):
    from backend.changes import ChangeSetService

    service = ChangeSetService(
        state_root=root.parent / ".forgex-subagent-state",
        staging_root=root.parent / ".forgex-subagent-stage",
    )
    return service.inspect_workspace(root)


def _observation(call_id: str, tool: ToolName, output: object) -> dict[str, object]:
    if isinstance(output, str):
        safe: object = output[:12_000]
    elif isinstance(output, tuple):
        safe = list(output[:160])
    elif isinstance(output, Mapping):
        safe_map: dict[str, object] = {}
        for key, value in output.items():
            if isinstance(value, str):
                safe_map[str(key)] = value[:12_000]
            elif isinstance(value, (str, int, float, bool, type(None))):
                safe_map[str(key)] = value
            elif isinstance(value, (list, tuple)):
                safe_map[str(key)] = list(value[:80])
        safe = safe_map
    else:
        safe = str(output)[:12_000]
    return {
        "call_id": call_id,
        "status": "completed",
        "tool": tool.value,
        "output": safe,
    }


def _compact(
    observations: Sequence[Mapping[str, object]], *, keep: int = 18
) -> tuple[Mapping[str, object], ...]:
    if len(observations) <= keep:
        return tuple(observations)
    older = observations[:-keep]
    checkpoint = {
        "call_id": "context_checkpoint",
        "status": "completed",
        "tool": "context_compaction",
        "output": {
            "compacted_observations": len(older),
            "tools": [str(item.get("tool") or "unknown") for item in older[-20:]],
            "failures": sum(item.get("status") == "failed" for item in older),
        },
    }
    return (checkpoint, *observations[-keep:])
