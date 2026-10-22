"""GenerateCodeHandler composition with WorkflowRunner and Coordinator."""

from __future__ import annotations

from backend.agent.coordinator import (
    Coordinator,
    InvocationProvider,
    StepResult,
    ToolInvocation,
)
from backend.agent.planner import Planner
from backend.contracts.execution_context import ExecutionContext
from backend.contracts.execution_plan import ExecutionPlan, ExecutionStep
from backend.contracts.task import Task
from backend.services.code_generation_service import CodeGenerationService
from backend.services.project_service import ProjectService
from backend.workflow.generate_code_handler import (
    GenerateCodeHandler,
    GenerateCodeResult,
)
from backend.workflow.workflow_runner import WorkflowRunner


def create_runner(
    code_generation_service: CodeGenerationService,
    project_service: ProjectService,
    context: ExecutionContext,
) -> WorkflowRunner:
    generate_handler = GenerateCodeHandler(
        code_generation_service,
        project_service,
        context,
    )

    def build_invocation(
        plan: ExecutionPlan,
        step: ExecutionStep,
        completed: tuple[StepResult, ...],
    ) -> ToolInvocation:
        del plan, step
        generation = completed[0].result
        if not isinstance(generation, GenerateCodeResult):
            raise ValueError("GENERATE_CODE did not return GenerateCodeResult")
        updated_context = generation.execution_context
        if updated_context is None:
            raise ValueError("generation did not produce an execution context")

        # GenerationAdapter can derive BuildConfig from the generated project
        # and updated_context. Runtime-owned subprocess dependencies are added
        # here by the application composition root.
        return ToolInvocation()

    coordinator = Coordinator(
        step_handlers={ExecutionStep.GENERATE_CODE: generate_handler},
    )

    def invocations(
        task: Task,
        plan: ExecutionPlan,
        execution_context: ExecutionContext,
    ) -> dict[ExecutionStep, InvocationProvider]:
        del task, plan, execution_context
        return {ExecutionStep.BUILD_FIRMWARE: build_invocation}

    return WorkflowRunner(
        Planner(),
        coordinator,
        context_factory=lambda task, plan: context,
        invocation_factory=invocations,
    )
