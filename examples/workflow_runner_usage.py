"""Minimal WorkflowRunner composition example.

Run from the repository root with::

    python -m examples.workflow_runner_usage
"""

from __future__ import annotations

import asyncio

from backend.agent.coordinator import Coordinator, ToolInvocation
from backend.agent.planner import Planner
from backend.contracts.execution_context import ExecutionContext
from backend.contracts.execution_plan import ExecutionPlan, ExecutionStep
from backend.contracts.task import Task
from backend.workflow.workflow_runner import WorkflowRunner


def create_invocations(
    task: Task,
    plan: ExecutionPlan,
    context: ExecutionContext,
) -> dict[ExecutionStep, ToolInvocation]:
    """Bind runtime-owned arguments without implementing step behavior."""

    del task, plan
    return {
        ExecutionStep.DETECT_BOARD: ToolInvocation(),
        ExecutionStep.FLASH_FIRMWARE: ToolInvocation(),
        # Build, flash, and monitor bindings are supplied by their adapters in
        # a production application. The context is available here to derive
        # those immutable per-run arguments.
    } if context.target_board != "UNKNOWN" else {}


async def main() -> None:
    task = Task(
        task_id="task-01JX6RUNNER000000000000001",
        prompt="Detect the ESP32 board and flash existing firmware",
        metadata={"request_id": "api-42"},
    )
    runner = WorkflowRunner(
        Planner(),
        Coordinator(tool_executor=lambda name, *args, **kwargs: True),
        invocation_factory=create_invocations,
    )

    outcome = await runner.run(task)
    print(outcome.status.value)
    print(outcome.plan.to_dict())


if __name__ == "__main__":
    asyncio.run(main())
