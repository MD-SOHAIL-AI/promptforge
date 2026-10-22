"""Production workflow composition for PromptForge AI.

``WorkflowRunner`` connects the canonical task, planner, coordinator, and
execution contracts. It deliberately contains no planning rules or execution
behavior; those remain owned by the injected planner and coordinator.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from enum import Enum
from types import MappingProxyType
from typing import Protocol, TypeAlias

from ..agent.coordinator import (
    Coordinator,
    ExecutionOutcome,
    InvocationProvider,
)
from ..agent.planner import Planner
from ..contracts.execution_context import ExecutionContext
from ..contracts.execution_plan import (
    ExecutionPlan,
    ExecutionStep,
)
from ..contracts.task import Task

__all__ = [
    "ContextFactory",
    "InvocationFactory",
    "WorkflowProgressCallback",
    "WorkflowRunner",
    "WorkflowRunnerConfigurationError",
    "WorkflowRunnerContextError",
]


class WorkflowRunnerConfigurationError(RuntimeError):
    """A workflow dependency or dependency result is invalid."""


class WorkflowRunnerContextError(ValueError):
    """An execution context does not belong to the submitted task."""


class _Planner(Protocol):
    def plan(
        self,
        prompt: str,
    ) -> ExecutionPlan | Awaitable[ExecutionPlan]: ...


class _Coordinator(Protocol):
    def execute(
        self,
        plan: ExecutionPlan,
        *,
        invocations: Mapping[ExecutionStep, InvocationProvider] | None = None,
    ) -> ExecutionOutcome | Awaitable[ExecutionOutcome]: ...


ContextFactory: TypeAlias = Callable[
    [Task, ExecutionPlan],
    ExecutionContext | Awaitable[ExecutionContext],
]
InvocationFactory: TypeAlias = Callable[
    [Task, ExecutionPlan, ExecutionContext],
    Mapping[ExecutionStep, InvocationProvider]
    | Awaitable[Mapping[ExecutionStep, InvocationProvider]],
]
WorkflowProgressCallback: TypeAlias = Callable[
    [str, Mapping[str, object]],
    object | Awaitable[object],
]


logger = logging.getLogger(__name__)


class WorkflowRunner:
    """Compose one task from planning through coordinated execution.

    Instances are reusable and hold dependencies only. All per-execution state
    is local to :meth:`run`, allowing independent tasks to run concurrently.

    ``invocation_factory`` is the context propagation boundary for registry
    tools. It receives the immutable task, plan, and execution context and
    returns the coordinator invocation bindings required for that execution.
    """

    __slots__ = (
        "_context_factory",
        "_coordinator",
        "_invocation_factory",
        "_planner",
        "_progress_callback",
    )

    def __init__(
        self,
        planner: _Planner | None = None,
        coordinator: _Coordinator | None = None,
        *,
        context_factory: ContextFactory | None = None,
        invocation_factory: InvocationFactory | None = None,
        progress_callback: WorkflowProgressCallback | None = None,
    ) -> None:
        resolved_planner = planner if planner is not None else Planner()
        resolved_coordinator = (
            coordinator if coordinator is not None else Coordinator()
        )
        if not callable(getattr(resolved_planner, "plan", None)):
            raise WorkflowRunnerConfigurationError(
                "planner must provide a callable plan method"
            )
        if not callable(getattr(resolved_coordinator, "execute", None)):
            raise WorkflowRunnerConfigurationError(
                "coordinator must provide a callable execute method"
            )
        if context_factory is not None and not callable(context_factory):
            raise WorkflowRunnerConfigurationError(
                "context_factory must be callable"
            )
        if invocation_factory is not None and not callable(
            invocation_factory
        ):
            raise WorkflowRunnerConfigurationError(
                "invocation_factory must be callable"
            )
        if progress_callback is not None and not callable(progress_callback):
            raise WorkflowRunnerConfigurationError(
                "progress_callback must be callable"
            )

        self._planner = resolved_planner
        self._coordinator = resolved_coordinator
        self._context_factory = context_factory or _default_context
        self._invocation_factory = invocation_factory
        self._progress_callback = progress_callback

    async def run(
        self,
        task: Task,
        *,
        context: ExecutionContext | None = None,
        invocations: Mapping[ExecutionStep, InvocationProvider] | None = None,
    ) -> ExecutionOutcome:
        """Plan and execute ``task``, returning the coordinator outcome.

        Dependency exceptions and cancellation are intentionally not hidden.
        The planner owns planning failures and the coordinator owns structured
        execution failures.
        """

        if not isinstance(task, Task):
            raise TypeError("task must be a Task")
        if context is not None and not isinstance(context, ExecutionContext):
            raise TypeError("context must be an ExecutionContext")
        explicit_invocations = _copy_invocations(invocations)

        planned = await _maybe_await(self._planner.plan(task.prompt))
        if not isinstance(planned, ExecutionPlan):
            raise WorkflowRunnerConfigurationError(
                "planner must return an ExecutionPlan"
            )
        plan = _bind_plan_to_task(planned, task)
        await _emit_progress(
            self._progress_callback,
            "PLAN_GENERATED",
            {"task": task, "plan": plan},
        )

        execution_context = context
        if execution_context is None:
            execution_context = await _maybe_await(
                self._context_factory(task, plan)
            )
        if not isinstance(execution_context, ExecutionContext):
            raise WorkflowRunnerConfigurationError(
                "context_factory must return an ExecutionContext"
            )
        _validate_context(execution_context, task)

        bound_invocations: dict[ExecutionStep, InvocationProvider] = {}
        if self._invocation_factory is not None:
            generated = await _maybe_await(
                self._invocation_factory(task, plan, execution_context)
            )
            bound_invocations.update(
                _copy_invocations(generated, source="invocation_factory")
            )
        bound_invocations.update(explicit_invocations)

        coordinator_invocations = (
            MappingProxyType(bound_invocations)
            if bound_invocations
            else None
        )
        outcome = await _maybe_await(
            self._coordinator.execute(
                plan,
                invocations=coordinator_invocations,
            )
        )
        if not isinstance(outcome, ExecutionOutcome):
            raise WorkflowRunnerConfigurationError(
                "coordinator must return an ExecutionOutcome"
            )
        if outcome.plan != plan:
            raise WorkflowRunnerConfigurationError(
                "coordinator outcome must reference the executed plan"
            )
        return outcome

    async def execute(
        self,
        task: Task,
        *,
        context: ExecutionContext | None = None,
        invocations: Mapping[ExecutionStep, InvocationProvider] | None = None,
    ) -> ExecutionOutcome:
        """Compatibility-friendly alias for :meth:`run`."""

        return await self.run(
            task,
            context=context,
            invocations=invocations,
        )


async def _emit_progress(
    callback: WorkflowProgressCallback | None,
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
        logger.exception("Workflow progress callback failed", extra={"event": event})


def _bind_plan_to_task(plan: ExecutionPlan, task: Task) -> ExecutionPlan:
    if plan.task_id == task.task_id:
        return plan
    return ExecutionPlan(
        task_id=task.task_id,
        task_type=plan.task_type,
        target_board=plan.target_board,
        framework=plan.framework,
        requirements=plan.requirements,
        execution_steps=plan.execution_steps,
        confidence=plan.confidence,
        metadata=plan.metadata,
    )


def _default_context(task: Task, plan: ExecutionPlan) -> ExecutionContext:
    serialized_task = task.to_dict()
    return ExecutionContext(
        task_id=task.task_id,
        target_board=_string_value(plan.target_board),
        framework=_string_value(plan.framework),
        simulation_enabled=(
            ExecutionStep.START_SIMULATION in plan.execution_steps
        ),
        metadata={
            # fix: populate workflow metadata consumed by generation validation.
            "workflow": {
                "task_type": plan.task_type.value,
                "target_board": _string_value(plan.target_board),
            },
            "task": {
                "source": serialized_task["source"],
                "priority": serialized_task["priority"],
                "created_at": serialized_task["created_at"],
                "metadata": serialized_task["metadata"],
            }
        },
    )


def _validate_context(context: ExecutionContext, task: Task) -> None:
    if context.task_id != task.task_id:
        raise WorkflowRunnerContextError(
            "context.task_id must match task.task_id"
        )


def _copy_invocations(
    invocations: Mapping[ExecutionStep, InvocationProvider] | None,
    *,
    source: str = "invocations",
) -> dict[ExecutionStep, InvocationProvider]:
    if invocations is None:
        return {}
    if not isinstance(invocations, Mapping):
        message = (
            f"{source} must return a mapping"
            if source != "invocations"
            else "invocations must be a mapping"
        )
        raise TypeError(message)
    return dict(invocations)


async def _maybe_await(value: object) -> object:
    if inspect.isawaitable(value):
        return await value
    return value


def _string_value(value: str) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return value
