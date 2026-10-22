"""Deterministic intent-to-execution planning for PromptForge AI.

The planner is deliberately isolated from runtime orchestration and tool
execution. It converts a user prompt into an immutable description of the
work that a coordinator may later execute.

Example::

    planner = Planner()
    plan = planner.plan("Blink an LED on ESP32")

    assert plan.target_board is TargetBoard.ESP32
    assert plan.execution_steps[0] is ExecutionStep.GENERATE_CODE
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from enum import Enum, unique
from types import MappingProxyType
from typing import Mapping, Pattern

from ..contracts.execution_plan import (
    ExecutionPlan as CanonicalExecutionPlan,
    ExecutionStep,
    TaskType,
)

__all__ = [
    "ExecutionPlan",
    "ExecutionStep",
    "Framework",
    "Planner",
    "PlannerInputError",
    "TargetBoard",
    "TaskType",
]


@unique
class TargetBoard(str, Enum):
    """Board families that can be selected from user intent."""

    ESP32 = "ESP32"
    ESP32_S3 = "ESP32-S3"
    ESP32_C3 = "ESP32-C3"
    STM32 = "STM32"
    ARDUINO_UNO = "Arduino Uno"
    UNKNOWN = "UNKNOWN"


@unique
class Framework(str, Enum):
    """Supported firmware frameworks and project systems."""

    ARDUINO = "Arduino"
    ESP_IDF = "ESP-IDF"
    STM32_CUBE = "STM32Cube"
    PLATFORMIO = "PlatformIO"
    UNKNOWN = "UNKNOWN"


class PlannerInputError(ValueError):
    """The planner cannot create a meaningful plan from the supplied input."""


@dataclass(frozen=True, slots=True, init=False)
class ExecutionPlan(CanonicalExecutionPlan):
    """Deprecated planner import compatible with the canonical contract.

    New code should import :class:`backend.contracts.execution_plan.ExecutionPlan`.
    ``estimated_tools`` remains here temporarily for existing planner callers;
    it is deliberately absent from canonical serialization.
    """

    estimated_tools: tuple[str, ...] = field(default=(), compare=False)

    def __init__(
        self,
        *,
        task_id: str,
        task_type: TaskType,
        target_board: str,
        framework: str,
        requirements: tuple[str, ...],
        execution_steps: tuple[ExecutionStep, ...],
        confidence: float,
        metadata: Mapping[str, object] | None = None,
        estimated_tools: tuple[str, ...] = (),
    ) -> None:
        CanonicalExecutionPlan.__init__(
            self,
            task_id=task_id,
            task_type=task_type,
            target_board=target_board,
            framework=framework,
            requirements=requirements,
            execution_steps=execution_steps,
            confidence=confidence,
            metadata={} if metadata is None else metadata,
        )
        tools = tuple(estimated_tools)
        if any(not isinstance(tool, str) or not tool.strip() for tool in tools):
            raise ValueError("estimated_tools must contain non-empty strings")
        if len(set(tools)) != len(tools):
            raise ValueError("estimated_tools cannot contain duplicates")
        object.__setattr__(self, "estimated_tools", tools)


@dataclass(frozen=True, slots=True)
class _MatchRule:
    value: object
    pattern: Pattern[str]


_BOARD_RULES: tuple[_MatchRule, ...] = (
    _MatchRule(
        TargetBoard.ESP32_S3,
        re.compile(r"\besp32[\s_-]*s3\b|\besp[\s_-]*s3\b", re.I),
    ),
    _MatchRule(
        TargetBoard.ESP32_C3,
        re.compile(r"\besp32[\s_-]*c3\b|\besp[\s_-]*c3\b", re.I),
    ),
    _MatchRule(
        TargetBoard.ESP32,
        re.compile(r"\besp32\b(?![\s_-]*(?:s3|c3)\b)", re.I),
    ),
    _MatchRule(TargetBoard.STM32, re.compile(r"\bstm32[a-z0-9_-]*\b", re.I)),
    _MatchRule(
        TargetBoard.ARDUINO_UNO,
        re.compile(
            r"\b(?:arduino|genuino)[\s_-]*uno\b|\buno[\s_-]*r3\b",
            re.I,
        ),
    ),
)

_FRAMEWORK_RULES: tuple[_MatchRule, ...] = (
    _MatchRule(
        Framework.ESP_IDF,
        re.compile(r"\besp[\s_-]*idf\b|\bidf\.py\b", re.I),
    ),
    _MatchRule(
        Framework.STM32_CUBE,
        re.compile(r"\bstm32[\s_-]*cube(?:ide|mx)?\b|\bcubemx\b", re.I),
    ),
    _MatchRule(Framework.PLATFORMIO, re.compile(r"\bplatformio\b|\bpio\b", re.I)),
    _MatchRule(
        Framework.ARDUINO,
        re.compile(
            r"\barduino\b(?![\s_-]*uno\b)(?:[\s_-]*(?:ide|framework))?"
            r"|\.ino\b",
            re.I,
        ),
    ),
)

_DEBUG_RE = re.compile(
    r"\b(?:debug|diagnos(?:e|is|tic)|troubleshoot|"
    r"build[\s_-]*error|compile[\s_-]*error|linker[\s_-]*error|"
    r"build[\s_-]*fail(?:ure|ed|ing)?|fix[\s_-]*(?:this[\s_-]*)?error)\b",
    re.I,
)
_SIMULATION_RE = re.compile(r"\b(?:simulate|simulation|wokwi)\b", re.I)
_MONITOR_RE = re.compile(
    r"\b(?:serial[\s_-]*monitor|monitor[\s_-]*serial|"
    r"watch[\s_-]*serial|read[\s_-]*serial[\s_-]*output)\b",
    re.I,
)
_FLASH_RE = re.compile(
    r"\b(?:flash|upload|burn)(?:ing|ed)?\b", re.I
)
_MODIFICATION_RE = re.compile(
    r"\b(?:modify|update|change|edit|refactor|extend|port)\b", re.I
)
_GENERATION_RE = re.compile(
    r"\b(?:create|build|generate|write|make|implement|develop|blink|read)\b",
    re.I,
)

_REQUIREMENT_RULES: tuple[tuple[str, Pattern[str]], ...] = (
    ("LED output", re.compile(r"\bLEDs?\b|\bblink(?:ing)?\b", re.I)),
    ("WiFi connectivity", re.compile(r"\bWi[\s_-]*Fi\b|\bwireless\b", re.I)),
    ("HTTP web server", re.compile(r"\bweb[\s_-]*server\b|\bHTTP[\s_-]*server\b|\bREST[\s_-]*API\b", re.I)),
    ("browser control", re.compile(r"\bbrowser\b|\bweb[\s_-]*(?:page|dashboard|interface)\b", re.I)),
    ("weather data acquisition", re.compile(r"\bweather\b", re.I)),
    ("motor control", re.compile(r"\bmotor(?:s|[\s_-]*controller)?\b", re.I)),
    ("DS18B20 sensor", re.compile(r"\bDS18B20\b", re.I)),
    (
        "temperature sensing",
        re.compile(r"\btemperature\b|\btherm(?:al|ometer)\b", re.I),
    ),
    (
        "serial output",
        re.compile(r"\bserial\b|\bprint(?:ln|f)?\b|\blog(?:ging)?\b", re.I),
    ),
    ("Bluetooth connectivity", re.compile(r"\bBluetooth\b|\bBLE\b", re.I)),
    ("I2C communication", re.compile(r"\bI2C\b|\bIIC\b", re.I)),
    ("SPI communication", re.compile(r"\bSPI\b", re.I)),
    ("UART communication", re.compile(r"\bUART\b", re.I)),
    ("PWM output", re.compile(r"\bPWM\b", re.I)),
    ("ADC input", re.compile(r"\bADC\b|\banalog[\s_-]*(?:read|input)\b", re.I)),
    ("OLED display", re.compile(r"\bOLED\b|\bSSD[\s_-]*1306\b", re.I)),
)

_HARDWARE_PERIPHERAL_RULES: tuple[tuple[str, Pattern[str]], ...] = (
    ("WiFi", re.compile(r"\bWi[\s_-]*Fi\b|\bwireless\b|\bweb[\s_-]*server\b", re.I)),
    ("Bluetooth", re.compile(r"\bBluetooth\b|\bBLE\b", re.I)),
    ("I2C", re.compile(r"\bI2C\b|\bIIC\b|\bOLED\b|\bSSD[\s_-]*1306\b", re.I)),
    ("SPI", re.compile(r"\bSPI\b", re.I)),
    ("UART", re.compile(r"\bUART\b", re.I)),
    ("PWM", re.compile(r"\bPWM\b", re.I)),
    ("ADC", re.compile(r"\bADC\b|\banalog[\s_-]*(?:read|input)\b", re.I)),
)

_HARDWARE_LIBRARY_RULES: tuple[tuple[str, Pattern[str]], ...] = (
    ("Adafruit SSD1306", re.compile(r"\bOLED\b|\bSSD[\s_-]*1306\b", re.I)),
)

_WORKFLOWS: Mapping[TaskType, tuple[ExecutionStep, ...]] = MappingProxyType(
    {
        TaskType.FIRMWARE_GENERATION: (
            ExecutionStep.GENERATE_CODE,
            ExecutionStep.BUILD_FIRMWARE,
        ),
        TaskType.FIRMWARE_MODIFICATION: (
            # fix: modification workflows reuse existing source before build.
            ExecutionStep.BUILD_FIRMWARE,
        ),
        TaskType.DEBUGGING: (ExecutionStep.DEBUG_FAILURE,),
        TaskType.SIMULATION: (
            ExecutionStep.GENERATE_CODE,
            ExecutionStep.BUILD_FIRMWARE,
            ExecutionStep.START_SIMULATION,
        ),
        TaskType.FLASH_ONLY: (
            ExecutionStep.DETECT_BOARD,
            ExecutionStep.FLASH_FIRMWARE,
        ),
        TaskType.MONITOR_ONLY: (
            ExecutionStep.DETECT_BOARD,
            ExecutionStep.START_MONITOR,
        ),
    }
)

_STEP_TO_TOOL: Mapping[ExecutionStep, str] = MappingProxyType(
    {
        ExecutionStep.BUILD_FIRMWARE: "build_firmware",
        ExecutionStep.DETECT_BOARD: "board_detector",
        ExecutionStep.FLASH_FIRMWARE: "flash_firmware",
        ExecutionStep.START_MONITOR: "serial_monitor",
        ExecutionStep.START_SIMULATION: "wokwi_simulator",
    }
)

_DEFAULT_FRAMEWORKS: Mapping[TargetBoard, Framework] = MappingProxyType(
    {
        TargetBoard.ESP32: Framework.PLATFORMIO,
        TargetBoard.ESP32_S3: Framework.PLATFORMIO,
        TargetBoard.ESP32_C3: Framework.PLATFORMIO,
        TargetBoard.STM32: Framework.STM32_CUBE,
        TargetBoard.ARDUINO_UNO: Framework.ARDUINO,
        TargetBoard.UNKNOWN: Framework.UNKNOWN,
    }
)

_WOKWI_BOARDS = frozenset(
    {TargetBoard.ESP32, TargetBoard.ESP32_S3, TargetBoard.ESP32_C3}
)
_TASK_NAMESPACE = uuid.UUID("21422a36-c229-4e9d-86aa-2d6da381d4aa")


class Planner:
    """Stateless, rule-based planner for embedded firmware requests."""

    __slots__ = ()

    def plan(self, prompt: str) -> ExecutionPlan:
        """Analyze ``prompt`` and return a deterministic execution plan."""
        normalized_prompt = _normalize_prompt(prompt)
        task_type = _detect_task_type(normalized_prompt)
        target_board, board_mentions = _detect_value(
            normalized_prompt, _BOARD_RULES, TargetBoard.UNKNOWN
        )
        explicit_framework, framework_mentions = _detect_value(
            normalized_prompt, _FRAMEWORK_RULES, Framework.UNKNOWN
        )
        framework = (
            explicit_framework
            if explicit_framework is not Framework.UNKNOWN
            else _DEFAULT_FRAMEWORKS[target_board]
        )
        requirements = _extract_requirements(normalized_prompt, task_type)
        execution_steps = _WORKFLOWS[task_type]
        estimated_tools = tuple(
            _STEP_TO_TOOL[step]
            for step in execution_steps
            if step in _STEP_TO_TOOL
        )

        board_ambiguous = len(board_mentions) > 1
        framework_ambiguous = len(framework_mentions) > 1
        simulation_feasible = (
            task_type is not TaskType.SIMULATION
            or target_board in _WOKWI_BOARDS
        )
        metadata = {
            "normalized_prompt": normalized_prompt,
            "board_inferred": target_board is TargetBoard.UNKNOWN,
            "framework_inferred": explicit_framework is Framework.UNKNOWN,
            "board_ambiguous": board_ambiguous,
            "framework_ambiguous": framework_ambiguous,
            "board_mentions": tuple(item.value for item in board_mentions),
            "framework_mentions": tuple(
                item.value for item in framework_mentions
            ),
            "simulation_feasible": simulation_feasible,
            "simulation_supported": simulation_feasible,
            "hardware_requirements": _extract_hardware_requirements(normalized_prompt),
        }

        return ExecutionPlan(
            task_id=_task_id(normalized_prompt),
            task_type=task_type,
            target_board=target_board,
            framework=framework,
            requirements=requirements,
            execution_steps=execution_steps,
            estimated_tools=estimated_tools,
            confidence=_confidence(
                task_type=task_type,
                target_board=target_board,
                explicit_framework=explicit_framework,
                board_ambiguous=board_ambiguous,
                framework_ambiguous=framework_ambiguous,
                simulation_feasible=simulation_feasible,
            ),
            metadata=metadata,
        )

    def create_plan(self, prompt: str) -> ExecutionPlan:
        """Compatibility-friendly alias for :meth:`plan`."""
        return self.plan(prompt)

    def analyze(self, prompt: str) -> ExecutionPlan:
        """Return the plan produced by analyzing ``prompt``."""
        return self.plan(prompt)

    def __call__(self, prompt: str) -> ExecutionPlan:
        return self.plan(prompt)


def _normalize_prompt(prompt: str) -> str:
    if not isinstance(prompt, str):
        raise PlannerInputError("prompt must be a string")
    if "\x00" in prompt:
        raise PlannerInputError("prompt cannot contain NUL characters")
    normalized = " ".join(prompt.split())
    if not normalized:
        raise PlannerInputError("prompt must be non-empty")
    return normalized


def _detect_task_type(prompt: str) -> TaskType:
    # fix: generation and modification intent take precedence over debugging terms.
    debug_matches = tuple(_DEBUG_RE.finditer(prompt))
    generation_matches = tuple(_GENERATION_RE.finditer(prompt))
    generation_intent = any(
        not any(
            debug.start() <= generation.start()
            and generation.end() <= debug.end()
            for debug in debug_matches
        )
        for generation in generation_matches
    )
    if debug_matches and not (
        generation_intent or _MODIFICATION_RE.search(prompt)
    ):
        return TaskType.DEBUGGING
    if _SIMULATION_RE.search(prompt):
        return TaskType.SIMULATION
    if _MONITOR_RE.search(prompt) and not _GENERATION_RE.search(prompt):
        return TaskType.MONITOR_ONLY
    if _FLASH_RE.search(prompt) and not (
        _GENERATION_RE.search(prompt) or _MODIFICATION_RE.search(prompt)
    ):
        return TaskType.FLASH_ONLY
    if _MODIFICATION_RE.search(prompt):
        return TaskType.FIRMWARE_MODIFICATION
    return TaskType.FIRMWARE_GENERATION


def _detect_value(
    prompt: str,
    rules: tuple[_MatchRule, ...],
    unknown: object,
) -> tuple[object, tuple[object, ...]]:
    matches: list[tuple[int, int, object]] = []
    for priority, rule in enumerate(rules):
        match = rule.pattern.search(prompt)
        if match is not None:
            matches.append((match.start(), priority, rule.value))

    if not matches:
        return unknown, ()

    matches.sort(key=lambda item: (item[0], item[1]))
    mentions: list[object] = []
    for _, _, value in matches:
        if value not in mentions:
            mentions.append(value)
    return matches[0][2], tuple(mentions)


def _extract_requirements(
    prompt: str, task_type: TaskType
) -> tuple[str, ...]:
    requirements = [
        requirement
        for requirement, pattern in _REQUIREMENT_RULES
        if pattern.search(prompt)
    ]
    if requirements:
        return tuple(requirements)

    fallback = {
        TaskType.DEBUGGING: "failure diagnosis",
        TaskType.SIMULATION: "firmware simulation",
        TaskType.FLASH_ONLY: "firmware deployment",
        TaskType.MONITOR_ONLY: "serial observation",
        TaskType.FIRMWARE_MODIFICATION: "existing firmware changes",
        TaskType.FIRMWARE_GENERATION: "user-defined firmware behavior",
    }
    return (fallback[task_type],)


def _extract_hardware_requirements(prompt: str) -> dict[str, tuple[str, ...]]:
    """Create the only metadata contract consumed by hardware validation."""

    peripherals = tuple(
        name for name, pattern in _HARDWARE_PERIPHERAL_RULES if pattern.search(prompt)
    )
    libraries = tuple(
        name for name, pattern in _HARDWARE_LIBRARY_RULES if pattern.search(prompt)
    )
    return {"peripherals": peripherals, "libraries": libraries}


def _task_id(normalized_prompt: str) -> str:
    canonical_prompt = normalized_prompt.casefold()
    return f"task-{uuid.uuid5(_TASK_NAMESPACE, canonical_prompt).hex}"


def _confidence(
    *,
    task_type: TaskType,
    target_board: TargetBoard,
    explicit_framework: Framework,
    board_ambiguous: bool,
    framework_ambiguous: bool,
    simulation_feasible: bool,
) -> float:
    score = 0.62
    if target_board is not TargetBoard.UNKNOWN:
        score += 0.18
    if explicit_framework is not Framework.UNKNOWN:
        score += 0.10
    elif target_board is not TargetBoard.UNKNOWN:
        score += 0.05
    if task_type in {
        TaskType.DEBUGGING,
        TaskType.SIMULATION,
        TaskType.FLASH_ONLY,
        TaskType.MONITOR_ONLY,
        TaskType.FIRMWARE_MODIFICATION,
    }:
        score += 0.05
    if board_ambiguous:
        score -= 0.20
    if framework_ambiguous:
        score -= 0.10
    if not simulation_feasible:
        score -= 0.25
    return round(max(0.0, min(score, 0.99)), 2)
