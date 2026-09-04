from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.agent.coordinator import (
    ExecutionOutcome,
    ExecutionStatus,
    ToolInvocation,
)
from backend.contracts.execution_context import ExecutionContext
from backend.contracts.execution_plan import (
    ExecutionPlan,
    ExecutionStep,
    TaskType,
)
from backend.contracts.task import Task, TaskPriority, TaskSource
from backend.workflow.workflow_runner import (
    WorkflowRunner,
    WorkflowRunnerConfigurationError,
    WorkflowRunnerContextError,
)


def make_task(**overrides: Any) -> Task:
    values = {
        "task_id": "task-api-123",
        "prompt": "Simulate an ESP32 LED using PlatformIO",
        "source": TaskSource.API,
        "priority": TaskPriority.HIGH,
        "created_at": datetime(2026, 6, 12, tzinfo=timezone.utc),
        "metadata": {"project_id": "blink"},
    }
    values.update(overrides)
    return Task(**values)


def make_plan(
    *,
    task_id: str = "planner-generated-id",
    steps: tuple[ExecutionStep, ...] = (
        ExecutionStep.START_SIMULATION,
    ),
) -> ExecutionPlan:
    return ExecutionPlan(
        task_id=task_id,
        task_type=TaskType.SIMULATION,
        target_board="ESP32",
        framework="PlatformIO",
        requirements=("LED output",),
        execution_steps=steps,
        confidence=0.9,
        metadata={"planner": "rules"},
    )


def outcome_for(plan: ExecutionPlan) -> ExecutionOutcome:
    return ExecutionOutcome(
        plan=plan,
        status=ExecutionStatus.COMPLETED,
        step_results=(),
        failures=(),
        execution_time=0.01,
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


class RecordingPlanner:
    def __init__(self, plan: ExecutionPlan) -> None:
        self.result = plan
        self.prompts: list[str] = []

    def plan(self, prompt: str) -> ExecutionPlan:
        self.prompts.append(prompt)
        return self.result


class RecordingCoordinator:
    def __init__(self) -> None:
        self.calls: list[tuple[ExecutionPlan, object]] = []

    async def execute(
        self,
        plan: ExecutionPlan,
        *,
        invocations: object = None,
    ) -> ExecutionOutcome:
        self.calls.append((plan, invocations))
        return outcome_for(plan)


def test_composes_task_planner_plan_and_coordinator() -> None:
    task = make_task()
    planner = RecordingPlanner(make_plan())
    coordinator = RecordingCoordinator()

    outcome = run(WorkflowRunner(planner, coordinator).run(task))

    assert planner.prompts == [task.prompt]
    executed_plan = coordinator.calls[0][0]
    assert executed_plan.task_id == task.task_id
    assert executed_plan.metadata == {"planner": "rules"}
    assert outcome.plan is executed_plan


def test_preserves_plan_instance_when_task_id_already_matches() -> None:
    task = make_task()
    plan = make_plan(task_id=task.task_id)
    coordinator = RecordingCoordinator()

    run(WorkflowRunner(RecordingPlanner(plan), coordinator).run(task))

    assert coordinator.calls[0][0] is plan


def test_default_context_is_propagated_to_invocation_factory() -> None:
    task = make_task()
    captured: list[ExecutionContext] = []

    def invocation_factory(
        received_task: Task,
        plan: ExecutionPlan,
        context: ExecutionContext,
    ) -> dict[ExecutionStep, ToolInvocation]:
        assert received_task is task
        assert plan.task_id == task.task_id
        captured.append(context)
        return {
            ExecutionStep.START_SIMULATION: ToolInvocation(
                args=(context.task_id,)
            )
        }

    coordinator = RecordingCoordinator()
    run(
        WorkflowRunner(
            RecordingPlanner(make_plan()),
            coordinator,
            invocation_factory=invocation_factory,
        ).run(task)
    )

    context = captured[0]
    assert context.task_id == task.task_id
    assert context.target_board == "ESP32"
    assert context.framework == "PlatformIO"
    assert context.simulation_enabled is True
    assert context.metadata["task"]["source"] == "API"
    assert context.metadata["task"]["priority"] == "HIGH"
    assert context.metadata["task"]["metadata"] == {"project_id": "blink"}

    invocations = coordinator.calls[0][1]
    assert invocations[ExecutionStep.START_SIMULATION].args == (
        task.task_id,
    )


def test_supplied_context_is_propagated_without_replacement() -> None:
    task = make_task()
    context = ExecutionContext(
        task_id=task.task_id,
        project_path="workspace/blink",
    )
    captured: list[ExecutionContext] = []

    def invocation_factory(
        task: Task,
        plan: ExecutionPlan,
        received: ExecutionContext,
    ) -> dict[ExecutionStep, ToolInvocation]:
        captured.append(received)
        return {}

    run(
        WorkflowRunner(
            RecordingPlanner(make_plan()),
            RecordingCoordinator(),
            invocation_factory=invocation_factory,
        ).run(task, context=context)
    )

    assert captured == [context]
    assert captured[0] is context


def test_explicit_invocations_override_factory_bindings() -> None:
    coordinator = RecordingCoordinator()
    factory_invocation = ToolInvocation(args=("factory",))
    explicit_invocation = ToolInvocation(args=("explicit",))

    run(
        WorkflowRunner(
            RecordingPlanner(make_plan()),
            coordinator,
            invocation_factory=lambda task, plan, context: {
                ExecutionStep.START_SIMULATION: factory_invocation
            },
        ).run(
            make_task(),
            invocations={
                ExecutionStep.START_SIMULATION: explicit_invocation
            },
        )
    )

    passed = coordinator.calls[0][1]
    assert passed[ExecutionStep.START_SIMULATION] is explicit_invocation


def test_async_dependencies_are_supported() -> None:
    task = make_task()
    plan = make_plan()

    class AsyncPlanner:
        async def plan(self, prompt: str) -> ExecutionPlan:
            await asyncio.sleep(0)
            return plan

    async def context_factory(
        task: Task,
        plan: ExecutionPlan,
    ) -> ExecutionContext:
        await asyncio.sleep(0)
        return ExecutionContext(task_id=task.task_id)

    async def invocation_factory(
        task: Task,
        plan: ExecutionPlan,
        context: ExecutionContext,
    ) -> dict[ExecutionStep, ToolInvocation]:
        await asyncio.sleep(0)
        return {}

    outcome = run(
        WorkflowRunner(
            AsyncPlanner(),
            RecordingCoordinator(),
            context_factory=context_factory,
            invocation_factory=invocation_factory,
        ).execute(task)
    )

    assert outcome.status is ExecutionStatus.COMPLETED


def test_context_task_id_must_match_task() -> None:
    runner = WorkflowRunner(
        RecordingPlanner(make_plan()),
        RecordingCoordinator(),
    )

    with pytest.raises(WorkflowRunnerContextError, match="task_id"):
        run(
            runner.run(
                make_task(),
                context=ExecutionContext(task_id="different-task"),
            )
        )


@pytest.mark.parametrize("invalid", [None, "task", object()])
def test_rejects_non_task_inputs(invalid: object) -> None:
    runner = WorkflowRunner(
        RecordingPlanner(make_plan()),
        RecordingCoordinator(),
    )

    with pytest.raises(TypeError, match="Task"):
        run(runner.run(invalid))  # type: ignore[arg-type]


def test_rejects_invalid_dependency_results() -> None:
    class BadPlanner:
        def plan(self, prompt: str) -> object:
            return object()

    with pytest.raises(WorkflowRunnerConfigurationError, match="ExecutionPlan"):
        run(WorkflowRunner(BadPlanner(), RecordingCoordinator()).run(make_task()))

    class BadCoordinator(RecordingCoordinator):
        async def execute(self, plan: ExecutionPlan, *, invocations: object = None) -> object:
            return object()

    with pytest.raises(WorkflowRunnerConfigurationError, match="ExecutionOutcome"):
        run(
            WorkflowRunner(
                RecordingPlanner(make_plan()),
                BadCoordinator(),
            ).run(make_task())
        )


def test_rejects_mismatched_outcome_plan() -> None:
    class WrongPlanCoordinator(RecordingCoordinator):
        async def execute(
            self,
            plan: ExecutionPlan,
            *,
            invocations: object = None,
        ) -> ExecutionOutcome:
            return outcome_for(make_plan(task_id="wrong"))

    with pytest.raises(WorkflowRunnerConfigurationError, match="executed plan"):
        run(
            WorkflowRunner(
                RecordingPlanner(make_plan()),
                WrongPlanCoordinator(),
            ).run(make_task())
        )


def test_planner_exceptions_propagate_and_coordinator_is_not_called() -> None:
    class FailingPlanner:
        def plan(self, prompt: str) -> ExecutionPlan:
            raise LookupError("planning unavailable")

    coordinator = RecordingCoordinator()
    with pytest.raises(LookupError, match="planning unavailable"):
        run(WorkflowRunner(FailingPlanner(), coordinator).run(make_task()))
    assert coordinator.calls == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"planner": object()},
        {"coordinator": object()},
        {"context_factory": object()},
        {"invocation_factory": object()},
    ],
)
def test_rejects_invalid_dependencies(kwargs: dict[str, object]) -> None:
    defaults: dict[str, object] = {
        "planner": RecordingPlanner(make_plan()),
        "coordinator": RecordingCoordinator(),
    }
    defaults.update(kwargs)

    with pytest.raises(WorkflowRunnerConfigurationError):
        WorkflowRunner(**defaults)  # type: ignore[arg-type]


def test_invocation_inputs_are_defensively_copied() -> None:
    coordinator = RecordingCoordinator()
    source = {
        ExecutionStep.START_SIMULATION: ToolInvocation(args=("original",))
    }

    run(
        WorkflowRunner(
            RecordingPlanner(make_plan()),
            coordinator,
        ).run(make_task(), invocations=source)
    )
    source.clear()

    passed = coordinator.calls[0][1]
    assert ExecutionStep.START_SIMULATION in passed
    with pytest.raises(TypeError):
        passed[ExecutionStep.START_SIMULATION] = ToolInvocation()  # type: ignore[index]
