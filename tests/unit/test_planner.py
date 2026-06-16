from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from backend.agent.planner import (
    ExecutionPlan,
    ExecutionStep,
    Framework,
    Planner,
    PlannerInputError,
    TargetBoard,
    TaskType,
)


@pytest.fixture
def planner() -> Planner:
    return Planner()


def test_plans_esp32_led_generation_workflow(planner: Planner) -> None:
    plan = planner.plan("Blink an LED on ESP32")

    assert plan.task_type is TaskType.FIRMWARE_GENERATION
    assert plan.target_board is TargetBoard.ESP32
    assert plan.framework is Framework.PLATFORMIO
    assert plan.requirements == ("LED output",)
    assert plan.execution_steps == (
        ExecutionStep.GENERATE_CODE,
        ExecutionStep.BUILD_FIRMWARE,
        ExecutionStep.DETECT_BOARD,
        ExecutionStep.FLASH_FIRMWARE,
        ExecutionStep.START_MONITOR,
    )
    assert plan.estimated_tools == (
        "build_firmware",
        "board_detector",
        "flash_firmware",
        "serial_monitor",
    )


def test_plans_debugging_as_a_single_non_executing_decision(
    planner: Planner,
) -> None:
    plan = planner.plan("Debug this ESP32 build error")

    assert plan.task_type is TaskType.DEBUGGING
    assert plan.execution_steps == (ExecutionStep.DEBUG_FAILURE,)
    assert plan.estimated_tools == ()


def test_plans_esp32_simulation_without_physical_hardware_steps(
    planner: Planner,
) -> None:
    plan = planner.plan("Simulate ESP32 weather station")

    assert plan.task_type is TaskType.SIMULATION
    assert plan.execution_steps == (
        ExecutionStep.GENERATE_CODE,
        ExecutionStep.BUILD_FIRMWARE,
        ExecutionStep.START_SIMULATION,
    )
    assert plan.estimated_tools == (
        "build_firmware",
        "wokwi_simulator",
    )
    assert plan.metadata["simulation_supported"] is True


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Create firmware for ESP32", TargetBoard.ESP32),
        ("Create firmware for ESP32-S3", TargetBoard.ESP32_S3),
        ("Create firmware for esp32_c3", TargetBoard.ESP32_C3),
        ("Build an STM32F411 motor controller", TargetBoard.STM32),
        ("Blink Arduino Uno R3 LED", TargetBoard.ARDUINO_UNO),
        ("Create firmware for a custom RISC-V board", TargetBoard.UNKNOWN),
    ],
)
def test_detects_supported_and_unknown_boards(
    planner: Planner,
    prompt: str,
    expected: TargetBoard,
) -> None:
    assert planner.plan(prompt).target_board is expected


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Build ESP32 firmware with Arduino framework", Framework.ARDUINO),
        ("Build ESP32 firmware using ESP-IDF", Framework.ESP_IDF),
        ("Build STM32 firmware in STM32CubeIDE", Framework.STM32_CUBE),
        ("Build ESP32 firmware using PlatformIO", Framework.PLATFORMIO),
    ],
)
def test_detects_explicit_frameworks(
    planner: Planner,
    prompt: str,
    expected: Framework,
) -> None:
    plan = planner.plan(prompt)

    assert plan.framework is expected
    assert plan.metadata["framework_inferred"] is False


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("Create ESP32 firmware", Framework.PLATFORMIO),
        ("Create ESP32-S3 firmware", Framework.PLATFORMIO),
        ("Build an STM32 motor controller", Framework.STM32_CUBE),
        ("Blink an LED on Arduino Uno", Framework.ARDUINO),
        ("Create firmware for a custom board", Framework.UNKNOWN),
    ],
)
def test_infers_framework_from_board(
    planner: Planner,
    prompt: str,
    expected: Framework,
) -> None:
    plan = planner.plan(prompt)

    assert plan.framework is expected
    assert plan.metadata["framework_inferred"] is True


def test_earliest_explicit_board_and_framework_win_deterministically(
    planner: Planner,
) -> None:
    plan = planner.plan(
        "Port ESP32 firmware to STM32 using PlatformIO and Arduino"
    )

    assert plan.target_board is TargetBoard.ESP32
    assert plan.framework is Framework.PLATFORMIO
    assert plan.metadata["board_ambiguous"] is True
    assert plan.metadata["framework_ambiguous"] is True
    assert plan.metadata["board_mentions"] == ("ESP32", "STM32")
    assert plan.confidence < 0.8


def test_flash_only_workflow(planner: Planner) -> None:
    plan = planner.plan("Flash existing firmware to ESP32-C3")

    assert plan.task_type is TaskType.FLASH_ONLY
    assert plan.execution_steps == (
        ExecutionStep.DETECT_BOARD,
        ExecutionStep.FLASH_FIRMWARE,
    )


def test_monitor_only_workflow(planner: Planner) -> None:
    plan = planner.plan("Monitor serial output from Arduino Uno")

    assert plan.task_type is TaskType.MONITOR_ONLY
    assert plan.execution_steps == (
        ExecutionStep.DETECT_BOARD,
        ExecutionStep.START_MONITOR,
    )


def test_modification_workflow(planner: Planner) -> None:
    plan = planner.plan("Update ESP32 firmware to add Bluetooth")

    assert plan.task_type is TaskType.FIRMWARE_MODIFICATION
    assert "Bluetooth connectivity" in plan.requirements


def test_extracts_embedded_requirements_in_stable_order(
    planner: Planner,
) -> None:
    plan = planner.plan(
        "Read temperature from DS18B20 over UART and print it on ESP32"
    )

    assert plan.requirements == (
        "DS18B20 sensor",
        "temperature sensing",
        "serial output",
        "UART communication",
    )


def test_unknown_hardware_is_a_valid_low_confidence_plan(
    planner: Planner,
) -> None:
    plan = planner.plan("Create a soil monitor for my custom board")

    assert plan.target_board is TargetBoard.UNKNOWN
    assert plan.framework is Framework.UNKNOWN
    assert plan.metadata["board_inferred"] is True
    assert 0.0 <= plan.confidence < 0.8


def test_unsupported_wokwi_target_is_flagged_without_changing_intent(
    planner: Planner,
) -> None:
    plan = planner.plan("Simulate an STM32 motor controller")

    assert plan.task_type is TaskType.SIMULATION
    assert ExecutionStep.START_SIMULATION in plan.execution_steps
    assert plan.metadata["simulation_supported"] is False
    assert plan.confidence < 0.7


def test_planning_is_deterministic_and_normalizes_case_and_spacing(
    planner: Planner,
) -> None:
    first = planner.plan("  Blink   an LED on ESP32  ")
    second = planner.plan("blink an led on esp32")

    assert first.task_id == second.task_id
    assert first.task_type is second.task_type
    assert first.target_board is second.target_board
    assert first.execution_steps == second.execution_steps


def test_planner_is_stateless_and_aliases_return_the_same_plan(
    planner: Planner,
) -> None:
    prompt = "Create a WiFi weather station on ESP32"

    assert planner.plan(prompt) == planner.create_plan(prompt)
    assert planner.analyze(prompt) == planner(prompt)


def test_execution_plan_is_immutable(planner: Planner) -> None:
    plan = planner.plan("Blink an LED on ESP32")

    with pytest.raises(FrozenInstanceError):
        plan.confidence = 0.0  # type: ignore[misc]
    with pytest.raises(TypeError):
        plan.metadata["new"] = True  # type: ignore[index]
    assert isinstance(plan.requirements, tuple)
    assert isinstance(plan.execution_steps, tuple)
    assert isinstance(plan.estimated_tools, tuple)


def test_execution_plan_deep_freezes_caller_metadata() -> None:
    source = {"nested": {"items": ["one"]}}
    plan = ExecutionPlan(
        task_id="task-id",
        task_type=TaskType.DEBUGGING,
        target_board=TargetBoard.UNKNOWN,
        framework=Framework.UNKNOWN,
        requirements=("failure diagnosis",),
        execution_steps=(ExecutionStep.DEBUG_FAILURE,),
        estimated_tools=(),
        confidence=0.5,
        metadata=source,
    )
    source["nested"]["items"].append("two")  # type: ignore[index,union-attr]

    assert plan.metadata["nested"]["items"] == ("one",)  # type: ignore[index]
    with pytest.raises(TypeError):
        plan.metadata["nested"]["new"] = True  # type: ignore[index]


@pytest.mark.parametrize("prompt", ["", "   ", "bad\x00prompt", None, 123])
def test_rejects_invalid_prompts(planner: Planner, prompt: object) -> None:
    with pytest.raises(PlannerInputError):
        planner.plan(prompt)  # type: ignore[arg-type]


def test_execution_plan_validates_confidence_and_duplicates() -> None:
    common = {
        "task_id": "task-id",
        "task_type": TaskType.DEBUGGING,
        "target_board": TargetBoard.UNKNOWN,
        "framework": Framework.UNKNOWN,
        "requirements": ("failure diagnosis",),
        "estimated_tools": (),
    }

    with pytest.raises(ValueError, match="confidence"):
        ExecutionPlan(
            **common,
            execution_steps=(ExecutionStep.DEBUG_FAILURE,),
            confidence=1.1,
        )
    with pytest.raises(ValueError, match="duplicates"):
        ExecutionPlan(
            **common,
            execution_steps=(
                ExecutionStep.DEBUG_FAILURE,
                ExecutionStep.DEBUG_FAILURE,
            ),
            confidence=0.5,
        )
