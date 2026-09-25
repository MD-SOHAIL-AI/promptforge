"""Canonical ForgeX agent runtime primitives.

This module deliberately keeps provider wire formats out of execution.  A model
attempt must settle first, then its calls become detached ToolInvocations.  The
scheduler may overlap safe reads but preserves logical result order.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Awaitable, Callable, Mapping, Sequence

from .tool_contracts import ProductToolCall, ToolName, WRITE_TOOLS


class SchedulerClass(str, Enum):
    READ = "read"
    WORKSPACE = "workspace"
    PROCESS = "process"
    DEVICE = "device"
    CONTROL = "control"


class RetryClass(str, Enum):
    PURE = "pure"
    IDEMPOTENT = "idempotent"
    RECONCILABLE_MUTATION = "reconcilable_mutation"
    NON_IDEMPOTENT = "non_idempotent"
    HARDWARE_NON_REPLAYABLE = "hardware_non_replayable"


_READ_TOOLS = frozenset(
    {
        ToolName.LIST_FILES,
        ToolName.GLOB_FILES,
        ToolName.GREP_SEARCH,
        ToolName.READ_FILE,
        ToolName.LOAD_SKILL,
        ToolName.MEMORY_SEARCH,
        ToolName.SESSION_EVENT_READ,
        ToolName.SESSION_EVENT_SEARCH,
        ToolName.SESSION_TRACE,
    }
)
_WORKSPACE_TOOLS = WRITE_TOOLS
_PROCESS_TOOLS = frozenset(
    {ToolName.BUILD_FIRMWARE, ToolName.RUN_SIMULATION, ToolName.RUN_COMMAND}
)
_CONTROL_TOOLS = frozenset({ToolName.UPDATE_PLAN, ToolName.SPAWN_SUBAGENT, ToolName.PRESENT_FILES})


HARNESS_TOOL_ALIASES: Mapping[str, ToolName] = {
    "read": ToolName.READ_FILE,
    "write": ToolName.WRITE_FILE,
    "edit": ToolName.EDIT_FILE_SIMPLE,
    "list": ToolName.LIST_FILES,
    "glob": ToolName.GLOB_FILES,
    "grep": ToolName.GREP_SEARCH,
    "bash": ToolName.RUN_COMMAND,
    "pwsh": ToolName.RUN_COMMAND,
    "todo_write": ToolName.UPDATE_PLAN,
    "skill": ToolName.LOAD_SKILL,
    "memory_search": ToolName.MEMORY_SEARCH,
    "subagent": ToolName.SPAWN_SUBAGENT,
    "present": ToolName.PRESENT_FILES,
    "session_event_read": ToolName.SESSION_EVENT_READ,
    "session_event_search": ToolName.SESSION_EVENT_SEARCH,
    "session_trace": ToolName.SESSION_TRACE,
    "build": ToolName.BUILD_FIRMWARE,
    "simulate": ToolName.RUN_SIMULATION,
}


def normalize_tool_name(value: str) -> ToolName:
    if value in HARNESS_TOOL_ALIASES:
        return HARNESS_TOOL_ALIASES[value]
    return ToolName(value)


def scheduler_class(tool: ToolName) -> SchedulerClass:
    if tool in _READ_TOOLS:
        return SchedulerClass.READ
    if tool in _WORKSPACE_TOOLS:
        return SchedulerClass.WORKSPACE
    if tool in _PROCESS_TOOLS:
        return SchedulerClass.PROCESS
    if tool in _CONTROL_TOOLS:
        return SchedulerClass.CONTROL
    return SchedulerClass.CONTROL


def retry_class(tool: ToolName) -> RetryClass:
    if tool in _READ_TOOLS:
        return RetryClass.PURE
    if tool in _WORKSPACE_TOOLS:
        return RetryClass.RECONCILABLE_MUTATION
    if tool in _PROCESS_TOOLS:
        return RetryClass.NON_IDEMPOTENT
    return RetryClass.IDEMPOTENT


@dataclass(frozen=True, slots=True)
class AssistantAttemptFrame:
    attempt_id: str
    revision: int
    frame_index: int
    turn: int
    step: int
    frame_type: str
    delta: str = ""
    tool_call: Mapping[str, object] = field(default_factory=dict)

    def to_event(self) -> dict[str, object]:
        event: dict[str, object] = {
            "event_type": f"assistant.{self.frame_type}",
            "attempt_id": self.attempt_id,
            "revision": self.revision,
            "frame_index": self.frame_index,
            "turn": self.turn,
            "step": self.step,
            "transient": True,
        }
        if self.delta:
            event["delta"] = self.delta
            event["message"] = self.delta
        if self.tool_call:
            event["tool_call"] = dict(self.tool_call)
        return event


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Frozen metadata with detached, top-level read-only arguments.

    Nested dicts/lists remain mutable for tool policy and JSON compatibility;
    callers must treat them as read-only after construction.
    """

    call_id: str
    tool: ToolName
    arguments: Mapping[str, object]
    turn: int
    step: int
    attempt_id: str
    invocation_index: int
    scheduler_class: SchedulerClass
    retry_class: RetryClass
    idempotency_key: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "arguments", MappingProxyType(deepcopy(dict(self.arguments)))
        )

    @classmethod
    def from_product_call(
        cls,
        call: ProductToolCall,
        *,
        turn: int,
        step: int,
        attempt_id: str,
        invocation_index: int,
    ) -> "ToolInvocation":
        tool = normalize_tool_name(call.tool_type)
        arguments = deepcopy(dict(call.arguments))
        document = json.dumps(
            {
                "call_id": call.call_id,
                "tool": tool.value,
                "arguments": arguments,
                "turn": turn,
                "step": step,
                "attempt_id": attempt_id,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return cls(
            call_id=call.call_id,
            tool=tool,
            arguments=arguments,
            turn=turn,
            step=step,
            attempt_id=attempt_id,
            invocation_index=invocation_index,
            scheduler_class=scheduler_class(tool),
            retry_class=retry_class(tool),
            idempotency_key=hashlib.sha256(document.encode("utf-8")).hexdigest(),
        )

    def to_event_payload(self) -> dict[str, object]:
        return {
            "call_id": self.call_id,
            "tool": self.tool.value,
            "turn": self.turn,
            "step": self.step,
            "attempt_id": self.attempt_id,
            "invocation_index": self.invocation_index,
            "execution_mode": self.scheduler_class.value,
            "retry_class": self.retry_class.value,
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True, slots=True)
class ModelAttemptSettlement:
    attempt_id: str
    turn: int
    step: int
    revision: int
    frame_count: int
    content: str
    invocations: tuple[ToolInvocation, ...]
    finish_reason: str = "stop"
    usage: Mapping[str, object] = field(default_factory=dict)
    abandoned: bool = False

    def to_event(self) -> dict[str, object]:
        return {
            "event_type": (
                "model.attempt.abandoned" if self.abandoned else "model.attempt.settled"
            ),
            "attempt_id": self.attempt_id,
            "turn": self.turn,
            "step": self.step,
            "revision": self.revision,
            "frame_count": self.frame_count,
            "content": self.content,
            "finish_reason": self.finish_reason,
            "usage": dict(self.usage),
            "tool_calls": [item.to_event_payload() for item in self.invocations],
        }


def _reject_duplicate_call_ids(
    calls: Sequence[ProductToolCall | ToolInvocation],
) -> None:
    seen: set[str] = set()
    for call in calls:
        if call.call_id in seen:
            raise ValueError("AGENT_RUNTIME_DUPLICATE_CALL_ID")
        seen.add(call.call_id)


class AttemptAccumulator:
    """Collect sequential public frames until one successful settlement.

    Failed validation leaves the attempt open; successful settlement rejects
    all subsequent settlement and content-frame requests.
    """

    def __init__(self, *, turn: int, step: int, attempt_id: str | None = None) -> None:
        self.attempt_id = attempt_id or f"attempt-{uuid.uuid4().hex}"
        self.turn = turn
        self.step = step
        self.revision = 1
        self._next_index = 0
        self._content: list[str] = []
        self._settled = False

    def _ensure_open(self) -> None:
        if self._settled:
            raise RuntimeError("AGENT_RUNTIME_ATTEMPT_ALREADY_SETTLED")

    def content_delta(self, delta: str) -> AssistantAttemptFrame:
        self._ensure_open()
        frame = AssistantAttemptFrame(
            self.attempt_id,
            self.revision,
            self._next_index,
            self.turn,
            self.step,
            "content.delta",
            delta=str(delta),
        )
        self._next_index += 1
        self._content.append(str(delta))
        return frame

    def settle(
        self,
        calls: Sequence[ProductToolCall],
        *,
        content: str | None = None,
        finish_reason: str = "stop",
        usage: Mapping[str, object] | None = None,
    ) -> ModelAttemptSettlement:
        self._ensure_open()
        calls = tuple(calls)
        _reject_duplicate_call_ids(calls)
        text = "".join(self._content) if content is None else str(content)
        invocations = tuple(
            ToolInvocation.from_product_call(
                call,
                turn=self.turn,
                step=self.step,
                attempt_id=self.attempt_id,
                invocation_index=index,
            )
            for index, call in enumerate(calls)
        )
        settlement = ModelAttemptSettlement(
            attempt_id=self.attempt_id,
            turn=self.turn,
            step=self.step,
            revision=self.revision,
            frame_count=self._next_index,
            content=text,
            invocations=invocations,
            finish_reason=finish_reason,
            usage=dict(usage or {}),
        )
        self._settled = True
        return settlement


@dataclass(frozen=True, slots=True)
class ScheduledToolResult:
    invocation: ToolInvocation
    result: Mapping[str, object]
    queued_at: float
    started_at: float
    completed_at: float
    execution_completion_index: int


ToolRunner = Callable[[ToolInvocation], Awaitable[Mapping[str, object]]]


class OrderedToolScheduler:
    """Bounded scheduler with read parallelism and mutation/process barriers.

    Execution may overlap for consecutive read invocations.  Results are always
    returned in invocation order, which is the order supplied back to the model.
    """

    def __init__(self, *, max_parallel_reads: int = 6) -> None:
        self.max_parallel_reads = max(1, min(int(max_parallel_reads), 16))

    async def run(
        self, invocations: Sequence[ToolInvocation], runner: ToolRunner
    ) -> tuple[ScheduledToolResult, ...]:
        invocations = tuple(invocations)
        _reject_duplicate_call_ids(invocations)
        output: list[ScheduledToolResult] = []
        read_batch: list[ToolInvocation] = []
        completion_counter = 0

        async def flush_reads() -> None:
            nonlocal completion_counter
            if not read_batch:
                return
            semaphore = asyncio.Semaphore(self.max_parallel_reads)
            completed: dict[int, ScheduledToolResult] = {}
            counter_lock = asyncio.Lock()

            async def one(invocation: ToolInvocation) -> None:
                nonlocal completion_counter
                queued = time.monotonic()
                async with semaphore:
                    started = time.monotonic()
                    result = await runner(invocation)
                    finished = time.monotonic()
                async with counter_lock:
                    index = completion_counter
                    completion_counter += 1
                completed[invocation.invocation_index] = ScheduledToolResult(
                    invocation,
                    result,
                    queued,
                    started,
                    finished,
                    index,
                )

            await asyncio.gather(*(one(item) for item in tuple(read_batch)))
            for invocation in read_batch:
                output.append(completed[invocation.invocation_index])
            read_batch.clear()

        for invocation in invocations:
            if invocation.scheduler_class is SchedulerClass.READ:
                read_batch.append(invocation)
                continue
            await flush_reads()
            queued = time.monotonic()
            started = time.monotonic()
            result = await runner(invocation)
            finished = time.monotonic()
            output.append(
                ScheduledToolResult(
                    invocation,
                    result,
                    queued,
                    started,
                    finished,
                    completion_counter,
                )
            )
            completion_counter += 1
        await flush_reads()
        return tuple(output)


@dataclass(slots=True)
class LoopGuard:
    max_steps: int = 30
    max_tool_calls: int = 100
    max_same_call_repeats: int = 4
    _steps: int = 0
    _tool_calls: int = 0
    _fingerprints: dict[str, int] = field(default_factory=dict)

    def before_step(self) -> None:
        self._steps += 1
        if self._steps > self.max_steps:
            raise RuntimeError("AGENT_RUNTIME_MAX_STEPS")

    def admit(self, invocation: ToolInvocation) -> None:
        self._tool_calls += 1
        if self._tool_calls > self.max_tool_calls:
            raise RuntimeError("AGENT_RUNTIME_TOOL_BUDGET_EXCEEDED")
        # A build may follow an edit in the same plan, before the scheduler has
        # reported that edit's result. Failed builds are cached by source
        # revision, and the no-progress guard handles unchanged retries.
        if invocation.tool is ToolName.BUILD_FIRMWARE:
            return
        key = hashlib.sha256(
            json.dumps(
                [invocation.tool.value, dict(invocation.arguments)],
                sort_keys=True,
                ensure_ascii=True,
            ).encode("utf-8")
        ).hexdigest()
        count = self._fingerprints.get(key, 0) + 1
        self._fingerprints[key] = count
        if count > self.max_same_call_repeats:
            raise RuntimeError("AGENT_RUNTIME_REPEATED_CALL")

    def workspace_changed(self) -> None:
        """The same build or read is useful again after a real source edit."""
        self._fingerprints.clear()

    @property
    def remaining_tool_calls(self) -> int:
        return max(0, self.max_tool_calls - self._tool_calls)

    def consume_child_tool_calls(self, count: int) -> None:
        """Charge bounded child work to the parent run without replay checks."""
        child_calls = int(count)
        if child_calls < 0:
            raise ValueError("AGENT_RUNTIME_CHILD_TOOL_COUNT_INVALID")
        self._tool_calls += child_calls
        if self._tool_calls > self.max_tool_calls:
            raise RuntimeError("AGENT_RUNTIME_TOOL_BUDGET_EXCEEDED")
