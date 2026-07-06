"""Sequential ExecutionPlan coordinator for PromptForge AI.

The coordinator bridges planner output to the existing tool registry. It
owns ordering and aggregation only: tool arguments, code generation, and
debugging behavior are supplied by callers through explicit invocation
providers and handlers.

Example::

    coordinator = Coordinator(
        step_handlers={ExecutionStep.GENERATE_CODE: generate_project},
        debugger=debug_failure,
    )
    outcome = await coordinator.execute(
        plan,
        invocations={
            ExecutionStep.BUILD_FIRMWARE: ToolInvocation(
                args=(build_config, subprocess_manager)
            ),
        },
    )
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum, unique
from types import MappingProxyType
from typing import Any, Optional, Protocol, TypeAlias

from ..contracts.execution_plan import ExecutionPlan, ExecutionStep
from ..tools.tool_registry import execute_tool

__all__ = [
    "Coordinator",
    "CoordinatorConfigurationError",
    "CoordinatorProgressCallback",
    "DebuggerHandler",
    "ExecutionFailure",
    "ExecutionOutcome",
    "ExecutionStatus",
    "InvocationProvider",
    "StepHandler",
    "StepResult",
    "ToolInvocation",
]


@unique
class ExecutionStatus(str, Enum):
    """Lifecycle states exposed by a coordinator execution."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_PENDING_HARDWARE = "COMPLETED_WITH_PENDING_HARDWARE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CoordinatorConfigurationError(RuntimeError):
    """A plan step lacks the external dependency required to execute it."""


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Positional and keyword arguments for one registry tool call."""

    args: tuple[object, ...] = ()
    kwargs: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "args", tuple(self.args))
        if not isinstance(self.kwargs, Mapping):
            raise ValueError("kwargs must be a mapping")
        copied: dict[str, object] = {}
        for key, value in self.kwargs.items():
            if not isinstance(key, str) or not key:
                raise ValueError("kwargs keys must be non-empty strings")
            copied[key] = value
        object.__setattr__(self, "kwargs", MappingProxyType(copied))


@dataclass(frozen=True, slots=True)
class ExecutionFailure:
    """Coordinator-level view of a failed or cancelled step."""

    step: ExecutionStep
    message: str
    category: str = "UNKNOWN"
    recoverable: bool = False
    fatal: bool = True
    tool_name: Optional[str] = None
    exception_type: Optional[str] = None
    raw_failure: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.step, ExecutionStep):
            raise ValueError("step must be an ExecutionStep")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message must be non-empty")
        if not isinstance(self.category, str) or not self.category.strip():
            raise ValueError("category must be non-empty")
        if self.fatal == self.recoverable:
            raise ValueError("fatal must be the inverse of recoverable")


@dataclass(frozen=True, slots=True)
class StepResult:
    """Immutable record of one attempted execution step."""

    step: ExecutionStep
    success: bool
    result: object = field(default=None, repr=False)
    failure: Optional[ExecutionFailure] = None
    tool_name: Optional[str] = None
    execution_time: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.step, ExecutionStep):
            raise ValueError("step must be an ExecutionStep")
        if not isinstance(self.success, bool):
            raise ValueError("success must be a boolean")
        if self.execution_time < 0:
            raise ValueError("execution_time cannot be negative")
        if self.success and self.failure is not None:
            raise ValueError("successful step cannot contain a failure")
        if not self.success and self.failure is None:
            raise ValueError("failed step must contain a failure")


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """Final immutable aggregate returned by :class:`Coordinator`."""

    plan: ExecutionPlan
    status: ExecutionStatus
    step_results: tuple[StepResult, ...]
    failures: tuple[ExecutionFailure, ...]
    execution_time: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_results", tuple(self.step_results))
        object.__setattr__(self, "failures", tuple(self.failures))
        if not isinstance(self.plan, ExecutionPlan):
            raise ValueError("plan must be an ExecutionPlan")
        if not isinstance(self.status, ExecutionStatus):
            raise ValueError("status must be an ExecutionStatus")
        if self.status in {ExecutionStatus.PENDING, ExecutionStatus.RUNNING}:
            raise ValueError("ExecutionOutcome status must be terminal")
        if self.execution_time < 0:
            raise ValueError("execution_time cannot be negative")
        recorded_failures = tuple(
            result.failure
            for result in self.step_results
            if result.failure is not None
        )
        if recorded_failures != self.failures:
            raise ValueError("failures must match failed step_results")
        if self.status in {
            ExecutionStatus.COMPLETED,
            ExecutionStatus.COMPLETED_WITH_PENDING_HARDWARE,
        } and self.failures:
            raise ValueError("completed outcome cannot contain failures")


StepHandler: TypeAlias = Callable[
    [ExecutionPlan, tuple[StepResult, ...]],
    object | Awaitable[object],
]
DebuggerHandler: TypeAlias = Callable[
    [
        ExecutionPlan,
        tuple[ExecutionFailure, ...],
        tuple[StepResult, ...],
    ],
    object | Awaitable[object],
]
InvocationResolver: TypeAlias = Callable[
    [ExecutionPlan, ExecutionStep, tuple[StepResult, ...]],
    ToolInvocation | Awaitable[ToolInvocation],
]
InvocationProvider: TypeAlias = ToolInvocation | InvocationResolver
ToolExecutor: TypeAlias = Callable[..., object | Awaitable[object]]
CoordinatorProgressCallback: TypeAlias = Callable[
    [str, Mapping[str, object]],
    object | Awaitable[object],
]


logger = logging.getLogger(__name__)


class FailureProtocol(Protocol):
    # fix: describe the result failure shape without changing runtime behavior.
    success: bool
    status: object
    failure: object | None


_TOOL_BY_STEP: Mapping[ExecutionStep, str] = MappingProxyType(
    {
        ExecutionStep.BUILD_FIRMWARE: "build_firmware",
        ExecutionStep.DETECT_BOARD: "board_detector",
        ExecutionStep.FLASH_FIRMWARE: "flash_firmware",
        ExecutionStep.START_MONITOR: "serial_monitor",
        ExecutionStep.START_SIMULATION: "wokwi_simulator",
    }
)


class Coordinator:
    """Execute an ``ExecutionPlan`` sequentially through the tool registry.

    The class is safe to reuse because execution state is local to
    :meth:`execute`. It performs no retries and never mutates the plan.
    """

    __slots__ = (
        "_debug_on_failure",
        "_debugger",
        "_progress_callback",
        "_step_handlers",
        "_tool_executor",
    )

    def __init__(
        self,
        *,
        tool_executor: ToolExecutor = execute_tool,
        step_handlers: Optional[Mapping[ExecutionStep, StepHandler]] = None,
        debugger: Optional[DebuggerHandler] = None,
        debug_on_failure: bool = True,
        progress_callback: CoordinatorProgressCallback | None = None,
    ) -> None:
        if not callable(tool_executor):
            raise ValueError("tool_executor must be callable")
        if not isinstance(debug_on_failure, bool):
            raise ValueError("debug_on_failure must be a boolean")
        if debugger is not None and not callable(debugger):
            raise ValueError("debugger must be callable")
        if progress_callback is not None and not callable(progress_callback):
            raise ValueError("progress_callback must be callable")

        handlers: dict[ExecutionStep, StepHandler] = {}
        for step, handler in (step_handlers or {}).items():
            if not isinstance(step, ExecutionStep):
                raise ValueError("step handler keys must be ExecutionStep values")
            if not callable(handler):
                raise ValueError("step handlers must be callable")
            handlers[step] = handler

        self._tool_executor = tool_executor
        self._step_handlers = MappingProxyType(handlers)
        self._debugger = debugger
        self._debug_on_failure = debug_on_failure
        self._progress_callback = progress_callback

    async def execute(
        self,
        plan: ExecutionPlan,
        *,
        invocations: Optional[
            Mapping[ExecutionStep, InvocationProvider]
        ] = None,
    ) -> ExecutionOutcome:
        """Execute ``plan`` in order and return a terminal outcome.

        A failed step always stops dependent work. Recoverability is recorded
        for the runtime/retry layer, but this coordinator never retries or
        proceeds with potentially invalid downstream inputs.

        External ``asyncio`` cancellation is converted into a ``CANCELLED``
        outcome so the caller receives the requested structured terminal
        record. The coordinator does not own subprocess cleanup; tools and the
        runtime retain that responsibility.
        """
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("plan must be an ExecutionPlan")
        providers = _validate_invocations(invocations)
        started = time.monotonic()
        step_results: list[StepResult] = []
        failures: list[ExecutionFailure] = []

        for step in plan.execution_steps:
            await _emit_progress(
                self._progress_callback,
                "STEP_STARTED",
                {"plan": plan, "step": step},
            )
            step_started = time.monotonic()
            try:
                step_result = await self._execute_step(
                    plan,
                    step,
                    tuple(step_results),
                    providers,
                )
            except asyncio.CancelledError as exc:
                failure = ExecutionFailure(
                    step=step,
                    message="Execution was cancelled",
                    category="CANCELLED",
                    recoverable=False,
                    fatal=True,
                    tool_name=_TOOL_BY_STEP.get(step),
                    exception_type=type(exc).__name__,
                    raw_failure=exc,
                )
                step_results.append(
                    StepResult(
                        step=step,
                        success=False,
                        failure=failure,
                        tool_name=failure.tool_name,
                        execution_time=_elapsed(step_started),
                    )
                )
                await _emit_progress(
                    self._progress_callback,
                    "STEP_COMPLETED",
                    {"plan": plan, "step": step, "result": step_results[-1]},
                )
                failures.append(failure)
                return _outcome(
                    plan,
                    ExecutionStatus.CANCELLED,
                    step_results,
                    failures,
                    started,
                )

            step_results.append(step_result)
            await _emit_progress(
                self._progress_callback,
                "STEP_COMPLETED",
                {"plan": plan, "step": step, "result": step_result},
            )
            if step_result.success:
                continue

            failure = step_result.failure
            assert failure is not None
            failures.append(failure)

            if failure.category == ExecutionStatus.CANCELLED.value:
                return _outcome(
                    plan,
                    ExecutionStatus.CANCELLED,
                    step_results,
                    failures,
                    started,
                )

            if (
                self._debug_on_failure
                and self._debugger is not None
                and step is not ExecutionStep.DEBUG_FAILURE
            ):
                try:
                    debug_result = await self._execute_debugger(
                        plan,
                        tuple(failures),
                        tuple(step_results),
                    )
                except asyncio.CancelledError as exc:
                    debug_result = _cancelled_step_result(
                        ExecutionStep.DEBUG_FAILURE,
                        exc,
                    )
                step_results.append(debug_result)
                if debug_result.failure is not None:
                    failures.append(debug_result.failure)
                    if debug_result.failure.category == (
                        ExecutionStatus.CANCELLED.value
                    ):
                        return _outcome(
                            plan,
                            ExecutionStatus.CANCELLED,
                            step_results,
                            failures,
                            started,
                        )

            return _outcome(
                plan,
                ExecutionStatus.FAILED,
                step_results,
                failures,
                started,
            )

        return _outcome(
            plan,
            ExecutionStatus.COMPLETED,
            step_results,
            failures,
            started,
        )
    async def _execute_step(
        self,
        plan: ExecutionPlan,
        step: ExecutionStep,
        completed: tuple[StepResult, ...],
        providers: Mapping[ExecutionStep, InvocationProvider],
    ) -> StepResult:
        started = time.monotonic()
        tool_name = _TOOL_BY_STEP.get(step)
        try:
            if tool_name is not None:
                invocation = await _resolve_invocation(
                    providers.get(step, ToolInvocation()),
                    plan,
                    step,
                    completed,
                )
                result = self._tool_executor(
                    tool_name,
                    *invocation.args,
                    **dict(invocation.kwargs),
                )
                result = await _maybe_await(result)
            elif step is ExecutionStep.DEBUG_FAILURE:
                if self._debugger is None:
                    raise CoordinatorConfigurationError(
                        "DEBUG_FAILURE requires an injected debugger"
                    )
                result = self._debugger(plan, (), completed)
                result = await _maybe_await(result)
            else:
                handler = self._step_handlers.get(step)
                if handler is None:
                    raise CoordinatorConfigurationError(
                        f"{step.value} requires an injected step handler"
                    )
                result = handler(plan, completed)
                result = await _maybe_await(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = _failure_from_exception(step, tool_name, exc)
            return StepResult(
                step=step,
                success=False,
                failure=failure,
                tool_name=tool_name,
                execution_time=_elapsed(started),
            )

        failure = _failure_from_result(step, tool_name, result)
        return StepResult(
            step=step,
            success=failure is None,
            result=result,
            failure=failure,
            tool_name=tool_name,
            execution_time=_elapsed(started),
        )

    async def _execute_debugger(
        self,
        plan: ExecutionPlan,
        failures: tuple[ExecutionFailure, ...],
        completed: tuple[StepResult, ...],
    ) -> StepResult:
        started = time.monotonic()
        assert self._debugger is not None
        try:
            result = self._debugger(plan, failures, completed)
            result = await _maybe_await(result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = _failure_from_exception(
                ExecutionStep.DEBUG_FAILURE,
                None,
                exc,
            )
            return StepResult(
                step=ExecutionStep.DEBUG_FAILURE,
                success=False,
                failure=failure,
                execution_time=_elapsed(started),
            )

        failure = _failure_from_result(
            ExecutionStep.DEBUG_FAILURE,
            None,
            result,
        )
        return StepResult(
            step=ExecutionStep.DEBUG_FAILURE,
            success=failure is None,
            result=result,
            failure=failure,
            execution_time=_elapsed(started),
        )


async def _emit_progress(
    callback: CoordinatorProgressCallback | None,
    event: str,
    payload: Mapping[str, object],
) -> None:
    if callback is None:
        return
    try:
        result = callback(event, payload)
        if inspect.isawaitable(result):
            await result
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "Coordinator progress callback failed",
            extra={"event": event},
        )


def _validate_invocations(
    invocations: Optional[Mapping[ExecutionStep, InvocationProvider]],
) -> Mapping[ExecutionStep, InvocationProvider]:
    if invocations is None:
        return MappingProxyType({})
    if not isinstance(invocations, Mapping):
        raise TypeError("invocations must be a mapping")
    copied: dict[ExecutionStep, InvocationProvider] = {}
    for step, provider in invocations.items():
        if not isinstance(step, ExecutionStep):
            raise ValueError("invocation keys must be ExecutionStep values")
        if not isinstance(provider, ToolInvocation) and not callable(provider):
            raise ValueError(
                "invocation providers must be ToolInvocation or callable"
            )
        copied[step] = provider
    return MappingProxyType(copied)


async def _resolve_invocation(
    provider: InvocationProvider,
    plan: ExecutionPlan,
    step: ExecutionStep,
    completed: tuple[StepResult, ...],
) -> ToolInvocation:
    if isinstance(provider, ToolInvocation):
        return provider
    invocation = provider(plan, step, completed)
    invocation = await _maybe_await(invocation)
    if not isinstance(invocation, ToolInvocation):
        raise CoordinatorConfigurationError(
            "invocation resolver must return ToolInvocation"
        )
    return invocation


async def _maybe_await(value: object | Awaitable[object]) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


def _failure_from_result(
    step: ExecutionStep,
    tool_name: Optional[str],
    result: FailureProtocol,
) -> Optional[ExecutionFailure]:
    if result is False:
        return ExecutionFailure(
            step=step,
            message=f"{step.value} returned False",
            category="FAILED",
            recoverable=False,
            fatal=True,
            tool_name=tool_name,
            raw_failure=result,
        )

    success = getattr(result, "success", None)
    status = getattr(result, "status", None)
    status_value = getattr(status, "value", status)

    if success is not False and status_value in {None, "SUCCESS"}:
        return None
    if success is not False and status_value not in {
        "FAILED",
        "TIMEOUT",
        "CANCELLED",
        "PARTIAL",
        "SKIPPED",
    }:
        return None

    raw_failure = getattr(result, "failure", None)
    recoverable = bool(getattr(raw_failure, "retryable", False))
    category = _string_value(
        getattr(raw_failure, "category", status_value or "UNKNOWN")
    )
    message = str(
        getattr(raw_failure, "message", "")
        or getattr(result, "message", "")
        or f"{step.value} failed"
    )
    exception_type = getattr(raw_failure, "exception_type", None)
    return ExecutionFailure(
        step=step,
        message=message,
        category=category,
        recoverable=recoverable,
        fatal=not recoverable,
        tool_name=tool_name,
        exception_type=exception_type,
        raw_failure=raw_failure or result,
    )


def _failure_from_exception(
    step: ExecutionStep,
    tool_name: Optional[str],
    exc: Exception,
) -> ExecutionFailure:
    message = str(exc).strip() or type(exc).__name__
    return ExecutionFailure(
        step=step,
        message=message,
        category="COORDINATOR_ERROR",
        recoverable=False,
        fatal=True,
        tool_name=tool_name,
        exception_type=type(exc).__name__,
        raw_failure=exc,
    )


def _cancelled_step_result(
    step: ExecutionStep,
    exc: asyncio.CancelledError,
) -> StepResult:
    failure = ExecutionFailure(
        step=step,
        message="Execution was cancelled",
        category=ExecutionStatus.CANCELLED.value,
        recoverable=False,
        fatal=True,
        exception_type=type(exc).__name__,
        raw_failure=exc,
    )
    return StepResult(
        step=step,
        success=False,
        failure=failure,
    )


def _string_value(value: object) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value)


def _outcome(
    plan: ExecutionPlan,
    status: ExecutionStatus,
    step_results: list[StepResult],
    failures: list[ExecutionFailure],
    started: float,
) -> ExecutionOutcome:
    return ExecutionOutcome(
        plan=plan,
        status=status,
        step_results=tuple(step_results),
        failures=tuple(failures),
        execution_time=_elapsed(started),
    )


def _elapsed(started: float) -> float:
    return max(0.0, time.monotonic() - started)
