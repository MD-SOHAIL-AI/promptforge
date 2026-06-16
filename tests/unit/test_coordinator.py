from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any

import pytest

from backend.agent.coordinator import (
    Coordinator,
    CoordinatorConfigurationError,
    ExecutionFailure,
    ExecutionOutcome,
    ExecutionStatus,
    StepResult,
    ToolInvocation,
)
from backend.agent.planner import (
    ExecutionPlan,
    ExecutionStep,
    Framework,
    TargetBoard,
    TaskType,
)
from backend.runtime.result import FailureResult, ResultStatus


def plan_with(*steps: ExecutionStep) -> ExecutionPlan:
    tool_names = {
        ExecutionStep.BUILD_FIRMWARE: "build_firmware",
        ExecutionStep.DETECT_BOARD: "board_detector",
        ExecutionStep.FLASH_FIRMWARE: "flash_firmware",
        ExecutionStep.START_MONITOR: "serial_monitor",
        ExecutionStep.START_SIMULATION: "wokwi_simulator",
    }
    return ExecutionPlan(
        task_id="task-test",
        task_type=TaskType.FIRMWARE_GENERATION,
        target_board=TargetBoard.ESP32,
        framework=Framework.PLATFORMIO,
        requirements=("LED output",),
        execution_steps=steps,
        estimated_tools=tuple(
            tool_names[step] for step in steps if step in tool_names
        ),
        confidence=0.9,
    )


def tool_result(
    *,
    success: bool = True,
    status: ResultStatus = ResultStatus.SUCCESS,
    failure: FailureResult | None = None,
    message: str = "ok",
) -> SimpleNamespace:
    return SimpleNamespace(
        success=success,
        status=status,
        failure=failure,
        message=message,
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_executes_registry_steps_sequentially() -> None:
    calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def executor(name: str, *args: object, **kwargs: object) -> object:
        calls.append((name, args, kwargs))
        return tool_result()

    plan = plan_with(
        ExecutionStep.BUILD_FIRMWARE,
        ExecutionStep.DETECT_BOARD,
        ExecutionStep.FLASH_FIRMWARE,
    )
    outcome = run(
        Coordinator(tool_executor=executor).execute(
            plan,
            invocations={
                ExecutionStep.BUILD_FIRMWARE: ToolInvocation(
                    args=("build-config", "manager")
                ),
                ExecutionStep.FLASH_FIRMWARE: ToolInvocation(
                    args=("artifact", "board", "flash-config", "manager")
                ),
            },
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    assert calls == [
        ("build_firmware", ("build-config", "manager"), {}),
        ("board_detector", (), {}),
        (
            "flash_firmware",
            ("artifact", "board", "flash-config", "manager"),
            {},
        ),
    ]
    assert [item.step for item in outcome.step_results] == list(
        plan.execution_steps
    )


def test_lazy_invocation_resolver_receives_prior_results() -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []
    build_result = tool_result()

    async def executor(name: str, *args: object, **kwargs: object) -> object:
        calls.append((name, args))
        return build_result if name == "build_firmware" else tool_result()

    def flash_invocation(
        plan: ExecutionPlan,
        step: ExecutionStep,
        completed: tuple[StepResult, ...],
    ) -> ToolInvocation:
        assert plan.task_id == "task-test"
        assert step is ExecutionStep.FLASH_FIRMWARE
        assert completed[0].result is build_result
        return ToolInvocation(args=(completed[0].result, "board"))

    outcome = run(
        Coordinator(tool_executor=executor).execute(
            plan_with(
                ExecutionStep.BUILD_FIRMWARE,
                ExecutionStep.FLASH_FIRMWARE,
            ),
            invocations={
                ExecutionStep.FLASH_FIRMWARE: flash_invocation,
            },
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    assert calls[1] == ("flash_firmware", (build_result, "board"))


def test_async_invocation_resolver_is_supported() -> None:
    calls: list[tuple[object, ...]] = []

    async def resolver(
        plan: ExecutionPlan,
        step: ExecutionStep,
        completed: tuple[StepResult, ...],
    ) -> ToolInvocation:
        await asyncio.sleep(0)
        return ToolInvocation(args=(plan.task_id, step.value, len(completed)))

    def executor(name: str, *args: object, **kwargs: object) -> object:
        calls.append(args)
        return object()

    outcome = run(
        Coordinator(tool_executor=executor).execute(
            plan_with(ExecutionStep.BUILD_FIRMWARE),
            invocations={ExecutionStep.BUILD_FIRMWARE: resolver},
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    assert calls == [("task-test", "BUILD_FIRMWARE", 0)]


def test_generate_code_uses_injected_handler_before_tools() -> None:
    order: list[str] = []

    def generate(
        plan: ExecutionPlan, completed: tuple[StepResult, ...]
    ) -> str:
        assert completed == ()
        order.append("generate")
        return "project-path"

    async def executor(name: str, *args: object, **kwargs: object) -> object:
        order.append(name)
        return tool_result()

    outcome = run(
        Coordinator(
            tool_executor=executor,
            step_handlers={ExecutionStep.GENERATE_CODE: generate},
        ).execute(
            plan_with(
                ExecutionStep.GENERATE_CODE,
                ExecutionStep.BUILD_FIRMWARE,
            )
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    assert order == ["generate", "build_firmware"]
    assert outcome.step_results[0].result == "project-path"


def test_missing_non_tool_handler_is_a_structured_fatal_failure() -> None:
    outcome = run(
        Coordinator(tool_executor=lambda name: object()).execute(
            plan_with(ExecutionStep.GENERATE_CODE)
        )
    )

    assert outcome.status is ExecutionStatus.FAILED
    assert len(outcome.failures) == 1
    failure = outcome.failures[0]
    assert failure.step is ExecutionStep.GENERATE_CODE
    assert failure.fatal is True
    assert failure.exception_type == "CoordinatorConfigurationError"
    assert "injected step handler" in failure.message


def test_failed_tool_stops_dependent_steps() -> None:
    calls: list[str] = []
    failure = FailureResult(
        category="COMPILATION_ERROR",
        message="source did not compile",
        retryable=False,
        stage="build",
    )

    async def executor(name: str, *args: object, **kwargs: object) -> object:
        calls.append(name)
        if name == "build_firmware":
            return tool_result(
                success=False,
                status=ResultStatus.FAILED,
                failure=failure,
            )
        return tool_result()

    outcome = run(
        Coordinator(tool_executor=executor).execute(
            plan_with(
                ExecutionStep.BUILD_FIRMWARE,
                ExecutionStep.DETECT_BOARD,
                ExecutionStep.FLASH_FIRMWARE,
            )
        )
    )

    assert outcome.status is ExecutionStatus.FAILED
    assert calls == ["build_firmware"]
    assert outcome.failures[0].category == "COMPILATION_ERROR"
    assert outcome.failures[0].fatal is True
    assert outcome.failures[0].raw_failure is failure


def test_recoverable_failure_is_recorded_but_not_retried_or_continued() -> None:
    calls: list[str] = []
    failure = FailureResult(
        category="PORT_BUSY",
        message="serial port is busy",
        retryable=True,
        stage="flash",
    )

    def executor(name: str, *args: object, **kwargs: object) -> object:
        calls.append(name)
        return tool_result(
            success=False,
            status=ResultStatus.FAILED,
            failure=failure,
        )

    outcome = run(
        Coordinator(tool_executor=executor).execute(
            plan_with(
                ExecutionStep.FLASH_FIRMWARE,
                ExecutionStep.START_MONITOR,
            )
        )
    )

    assert calls == ["flash_firmware"]
    assert outcome.failures[0].recoverable is True
    assert outcome.failures[0].fatal is False


def test_debugger_is_invoked_after_tool_failure() -> None:
    debug_calls: list[tuple[ExecutionFailure, int]] = []
    failure = FailureResult(
        category="BUILD_ERROR",
        message="build failed",
        retryable=False,
        stage="build",
    )

    def executor(name: str, *args: object, **kwargs: object) -> object:
        return tool_result(
            success=False,
            status=ResultStatus.FAILED,
            failure=failure,
        )

    async def debugger(
        plan: ExecutionPlan,
        failures: tuple[ExecutionFailure, ...],
        completed: tuple[StepResult, ...],
    ) -> dict[str, str]:
        debug_calls.append((failures[-1], len(completed)))
        return {"diagnosis": "invalid source"}

    outcome = run(
        Coordinator(tool_executor=executor, debugger=debugger).execute(
            plan_with(ExecutionStep.BUILD_FIRMWARE)
        )
    )

    assert outcome.status is ExecutionStatus.FAILED
    assert debug_calls[0][0].category == "BUILD_ERROR"
    assert debug_calls[0][1] == 1
    assert [result.step for result in outcome.step_results] == [
        ExecutionStep.BUILD_FIRMWARE,
        ExecutionStep.DEBUG_FAILURE,
    ]
    assert len(outcome.failures) == 1


def test_debugger_failure_is_aggregated() -> None:
    def executor(name: str, *args: object, **kwargs: object) -> object:
        return tool_result(success=False, status=ResultStatus.FAILED)

    def debugger(
        plan: ExecutionPlan,
        failures: tuple[ExecutionFailure, ...],
        completed: tuple[StepResult, ...],
    ) -> object:
        raise RuntimeError("debugger unavailable")

    outcome = run(
        Coordinator(tool_executor=executor, debugger=debugger).execute(
            plan_with(ExecutionStep.BUILD_FIRMWARE)
        )
    )

    assert len(outcome.failures) == 2
    assert outcome.failures[-1].step is ExecutionStep.DEBUG_FAILURE
    assert outcome.failures[-1].message == "debugger unavailable"


def test_debugger_can_be_disabled_for_automatic_failures() -> None:
    called = False

    def executor(name: str, *args: object, **kwargs: object) -> object:
        return tool_result(success=False, status=ResultStatus.FAILED)

    def debugger(
        plan: ExecutionPlan,
        failures: tuple[ExecutionFailure, ...],
        completed: tuple[StepResult, ...],
    ) -> object:
        nonlocal called
        called = True
        return object()

    outcome = run(
        Coordinator(
            tool_executor=executor,
            debugger=debugger,
            debug_on_failure=False,
        ).execute(plan_with(ExecutionStep.BUILD_FIRMWARE))
    )

    assert outcome.status is ExecutionStatus.FAILED
    assert called is False


def test_explicit_debug_plan_uses_debugger() -> None:
    calls: list[int] = []

    def debugger(
        plan: ExecutionPlan,
        failures: tuple[ExecutionFailure, ...],
        completed: tuple[StepResult, ...],
    ) -> str:
        calls.append(len(failures))
        return "diagnosis"

    outcome = run(
        Coordinator(debugger=debugger).execute(
            plan_with(ExecutionStep.DEBUG_FAILURE)
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    assert calls == [0]
    assert outcome.step_results[0].result == "diagnosis"


def test_explicit_debug_plan_requires_debugger() -> None:
    outcome = run(
        Coordinator().execute(plan_with(ExecutionStep.DEBUG_FAILURE))
    )

    assert outcome.status is ExecutionStatus.FAILED
    assert outcome.failures[0].exception_type == (
        "CoordinatorConfigurationError"
    )


def test_tool_executor_exception_becomes_structured_failure() -> None:
    def executor(name: str, *args: object, **kwargs: object) -> object:
        raise LookupError("tool is not registered")

    outcome = run(
        Coordinator(tool_executor=executor).execute(
            plan_with(ExecutionStep.BUILD_FIRMWARE)
        )
    )

    failure = outcome.failures[0]
    assert outcome.status is ExecutionStatus.FAILED
    assert failure.tool_name == "build_firmware"
    assert failure.exception_type == "LookupError"


def test_invalid_invocation_resolver_result_is_structured_failure() -> None:
    def bad_resolver(
        plan: ExecutionPlan,
        step: ExecutionStep,
        completed: tuple[StepResult, ...],
    ) -> object:
        return ("not", "an", "invocation")

    outcome = run(
        Coordinator(tool_executor=lambda name: object()).execute(
            plan_with(ExecutionStep.BUILD_FIRMWARE),
            invocations={ExecutionStep.BUILD_FIRMWARE: bad_resolver},
        )
    )

    assert outcome.status is ExecutionStatus.FAILED
    assert outcome.failures[0].exception_type == (
        "CoordinatorConfigurationError"
    )


def test_runtime_cancelled_result_produces_cancelled_outcome() -> None:
    def executor(name: str, *args: object, **kwargs: object) -> object:
        return tool_result(
            success=False,
            status=ResultStatus.CANCELLED,
            message="tool cancelled",
        )

    outcome = run(
        Coordinator(tool_executor=executor).execute(
            plan_with(ExecutionStep.BUILD_FIRMWARE)
        )
    )

    assert outcome.status is ExecutionStatus.CANCELLED
    assert outcome.failures[0].category == "CANCELLED"


def test_asyncio_cancellation_produces_cancelled_outcome() -> None:
    async def executor(name: str, *args: object, **kwargs: object) -> object:
        raise asyncio.CancelledError

    outcome = run(
        Coordinator(tool_executor=executor).execute(
            plan_with(ExecutionStep.BUILD_FIRMWARE)
        )
    )

    assert outcome.status is ExecutionStatus.CANCELLED
    assert outcome.failures[0].category == "CANCELLED"
    assert outcome.step_results[0].success is False


def test_cancellation_during_debugger_produces_cancelled_outcome() -> None:
    def executor(name: str, *args: object, **kwargs: object) -> object:
        return tool_result(success=False, status=ResultStatus.FAILED)

    async def debugger(
        plan: ExecutionPlan,
        failures: tuple[ExecutionFailure, ...],
        completed: tuple[StepResult, ...],
    ) -> object:
        raise asyncio.CancelledError

    outcome = run(
        Coordinator(tool_executor=executor, debugger=debugger).execute(
            plan_with(ExecutionStep.BUILD_FIRMWARE)
        )
    )

    assert outcome.status is ExecutionStatus.CANCELLED
    assert outcome.failures[-1].step is ExecutionStep.DEBUG_FAILURE
    assert outcome.failures[-1].category == "CANCELLED"


def test_plain_false_return_value_is_a_failure() -> None:
    outcome = run(
        Coordinator(tool_executor=lambda name: False).execute(
            plan_with(ExecutionStep.DETECT_BOARD)
        )
    )

    assert outcome.status is ExecutionStatus.FAILED
    assert outcome.failures[0].message == "DETECT_BOARD returned False"


def test_plain_registry_return_value_counts_as_success() -> None:
    boards = [object()]
    outcome = run(
        Coordinator(tool_executor=lambda name: boards).execute(
            plan_with(ExecutionStep.DETECT_BOARD)
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    assert outcome.step_results[0].result is boards


def test_outcome_and_collections_are_immutable() -> None:
    outcome = run(
        Coordinator(tool_executor=lambda name: object()).execute(
            plan_with(ExecutionStep.DETECT_BOARD)
        )
    )

    with pytest.raises(FrozenInstanceError):
        outcome.status = ExecutionStatus.FAILED  # type: ignore[misc]
    assert isinstance(outcome.step_results, tuple)
    assert isinstance(outcome.failures, tuple)
    assert outcome.execution_time >= 0


def test_outcome_rejects_mismatched_failure_aggregation() -> None:
    failure = ExecutionFailure(
        step=ExecutionStep.BUILD_FIRMWARE,
        message="failed",
    )
    step_result = StepResult(
        step=ExecutionStep.BUILD_FIRMWARE,
        success=False,
        failure=failure,
    )

    with pytest.raises(ValueError, match="match"):
        ExecutionOutcome(
            plan=plan_with(ExecutionStep.BUILD_FIRMWARE),
            status=ExecutionStatus.FAILED,
            step_results=(step_result,),
            failures=(),
            execution_time=0.1,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tool_executor": None},
        {"debugger": object()},
        {"debug_on_failure": 1},
        {"step_handlers": {ExecutionStep.GENERATE_CODE: object()}},
    ],
)
def test_rejects_invalid_dependencies(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        Coordinator(**kwargs)  # type: ignore[arg-type]


def test_rejects_non_plan_and_invalid_invocation_mapping() -> None:
    coordinator = Coordinator()

    with pytest.raises(TypeError, match="ExecutionPlan"):
        run(coordinator.execute(object()))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="mapping"):
        run(
            coordinator.execute(
                plan_with(ExecutionStep.DETECT_BOARD),
                invocations=[],  # type: ignore[arg-type]
            )
        )


def test_tool_invocation_defensively_copies_kwargs() -> None:
    source = {"runtime": "serial"}
    invocation = ToolInvocation(kwargs=source)
    source["runtime"] = "changed"

    assert invocation.kwargs == {"runtime": "serial"}
    with pytest.raises(TypeError):
        invocation.kwargs["new"] = True  # type: ignore[index]


def test_configuration_error_is_public_runtime_error() -> None:
    assert issubclass(CoordinatorConfigurationError, RuntimeError)
