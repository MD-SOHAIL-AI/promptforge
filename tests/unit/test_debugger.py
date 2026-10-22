from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from backend.agent.coordinator import (
    Coordinator,
    ExecutionFailure,
    ExecutionStatus,
)
from backend.agent.debugger import (
    DebugRecommendation,
    DebugReport,
    DebugSeverity,
    Debugger,
    DebuggerInputError,
)
from backend.agent.planner import (
    ExecutionPlan,
    ExecutionStep,
    Framework,
    TargetBoard,
    TaskType,
)
from backend.runtime.failure_classifier import (
    FailureCategory,
    FailureClassification,
    FailureSeverity,
)
from backend.runtime.result import FailureResult, ResultStatus


@pytest.fixture
def debugger() -> Debugger:
    return Debugger()


def classification(
    category: FailureCategory,
    *,
    severity: FailureSeverity = FailureSeverity.ERROR,
    confidence: float = 0.9,
    retryable: bool = False,
    signal: str = "failure signal",
) -> FailureClassification:
    return FailureClassification(
        category=category,
        severity=severity,
        retryable=retryable,
        confidence=confidence,
        message=f"classified as {category.value}",
        raw_signal=signal,
    )


def test_preserves_classifier_category_severity_and_confidence(
    debugger: Debugger,
) -> None:
    report = debugger.analyze(
        classification(
            FailureCategory.PORT_BUSY,
            severity=FailureSeverity.WARNING,
            confidence=0.97,
            retryable=True,
        ),
        stage="flash",
        tool="esptool",
    )

    assert report.failure_category == "PORT_BUSY"
    assert report.severity is DebugSeverity.MEDIUM
    assert report.confidence == 0.97
    assert "stage=flash" in report.root_cause
    assert "tool=esptool" in report.root_cause
    assert report.suggested_actions[0] == "close_serial_monitor"


def test_build_missing_dependency_gets_specific_recommendation(
    debugger: Debugger,
) -> None:
    report = debugger.analyze(
        classification(
            FailureCategory.COMPILATION_ERROR,
            signal="fatal error: OneWire.h: No such file or directory",
        )
    )

    assert "dependency" in report.root_cause.lower()
    assert report.recommendations[0].title == (
        "Install the missing dependency"
    )
    assert report.suggested_actions[0] == "install_missing_dependency"


def test_flash_port_busy_raw_text_is_classified(debugger: Debugger) -> None:
    report = debugger.analyze(
        "serial port COM4 is busy and in use",
        stage="flash",
        tool="esptool",
    )

    assert report.failure_category == "PORT_BUSY"
    assert report.suggested_actions[0] == "close_serial_monitor"


def test_invalid_wokwi_project_gets_regeneration_action(
    debugger: Debugger,
) -> None:
    report = debugger.analyze(
        classification(
            FailureCategory.BUILD_ERROR,
            signal="ERROR: BuildError: invalid Wokwi project diagram.json",
        ),
        stage="simulate",
        tool="wokwi-cli",
    )

    assert "Wokwi project" in report.root_cause
    assert report.suggested_actions[0] == "regenerate_wokwi_project"


@pytest.mark.parametrize(
    ("category", "severity", "action"),
    [
        (
            FailureCategory.TOOLCHAIN_MISSING,
            DebugSeverity.CRITICAL,
            "install_required_toolchain",
        ),
        (
            FailureCategory.COMPILATION_ERROR,
            DebugSeverity.HIGH,
            "inspect_first_compiler_error",
        ),
        (
            FailureCategory.LINKER_ERROR,
            DebugSeverity.HIGH,
            "resolve_linker_symbols",
        ),
        (
            FailureCategory.BUILD_ERROR,
            DebugSeverity.HIGH,
            "inspect_build_output",
        ),
        (
            FailureCategory.PORT_BUSY,
            DebugSeverity.MEDIUM,
            "close_serial_monitor",
        ),
        (
            FailureCategory.DEVICE_DISCONNECTED,
            DebugSeverity.HIGH,
            "reconnect_target_board",
        ),
        (
            FailureCategory.FLASH_TIMEOUT,
            DebugSeverity.HIGH,
            "enter_bootloader_mode",
        ),
        (
            FailureCategory.INVALID_FIRMWARE,
            DebugSeverity.CRITICAL,
            "rebuild_for_target",
        ),
        (
            FailureCategory.TRANSIENT_USB_FAILURE,
            DebugSeverity.MEDIUM,
            "stabilize_usb_connection",
        ),
        (
            FailureCategory.OBSERVE_TIMEOUT,
            DebugSeverity.MEDIUM,
            "verify_firmware_logging",
        ),
        (
            FailureCategory.SERIAL_PERMISSION_ERROR,
            DebugSeverity.HIGH,
            "fix_serial_permissions",
        ),
        (
            FailureCategory.WATCHDOG_RESET,
            DebugSeverity.HIGH,
            "inspect_watchdog_blocking_paths",
        ),
        (
            FailureCategory.SERIAL_FRAMING_ERROR,
            DebugSeverity.MEDIUM,
            "match_serial_parameters",
        ),
        (
            FailureCategory.UNKNOWN,
            DebugSeverity.HIGH,
            "collect_complete_diagnostics",
        ),
    ],
)
def test_category_recovery_policies(
    debugger: Debugger,
    category: FailureCategory,
    severity: DebugSeverity,
    action: str,
) -> None:
    report = debugger.analyze(
        FailureResult(
            category=category.value,
            message="structured failure",
            retryable=False,
            stage="test",
        )
    )

    assert report.failure_category == category.value
    assert report.severity is severity
    assert report.suggested_actions[0] == action


def test_failure_result_metadata_overrides_default_diagnostics(
    debugger: Debugger,
) -> None:
    report = debugger.analyze(
        FailureResult(
            category="PORT_BUSY",
            message="busy",
            retryable=True,
            stage="flash",
            metadata={"severity": "CRITICAL", "confidence": 0.72},
        )
    )

    assert report.severity is DebugSeverity.CRITICAL
    assert report.confidence == 0.72


def test_unknown_structured_failure_is_reclassified_from_raw_output(
    debugger: Debugger,
) -> None:
    report = debugger.analyze(
        FailureResult(
            category="UNKNOWN",
            message="flash failed",
            retryable=False,
            stage="flash",
            raw_output="device port busy and locked by another process",
        )
    )

    assert report.failure_category == "PORT_BUSY"


def test_runtime_result_uses_nested_failure(debugger: Debugger) -> None:
    nested = FailureResult(
        category="FLASH_TIMEOUT",
        message="timed out waiting for packet header",
        retryable=True,
        stage="flash",
    )
    result = SimpleNamespace(
        success=False,
        status=ResultStatus.TIMEOUT,
        message="flash timed out",
        tool="esptool",
        failure=nested,
        process_result=None,
    )

    report = debugger.analyze(result)

    assert report.failure_category == "FLASH_TIMEOUT"
    assert "tool=esptool" in report.root_cause


def test_runtime_result_without_failure_uses_classifier(
    debugger: Debugger,
) -> None:
    result = SimpleNamespace(
        success=False,
        status=ResultStatus.FAILED,
        message="Guru Meditation Error: task watchdog got triggered",
        tool="serial_monitor",
        failure=None,
        process_result=None,
    )

    report = debugger.analyze(result, stage="observe")

    assert report.failure_category == "WATCHDOG_RESET"


def test_coordinator_execution_failure_is_supported(
    debugger: Debugger,
) -> None:
    failure = ExecutionFailure(
        step=ExecutionStep.FLASH_FIRMWARE,
        message="port is busy",
        category="PORT_BUSY",
        recoverable=True,
        fatal=False,
        tool_name="flash_firmware",
    )

    report = debugger.analyze(failure)

    assert report.failure_category == "PORT_BUSY"
    assert "tool=flash_firmware" in report.root_cause


def test_exception_is_classified_deterministically(debugger: Debugger) -> None:
    report = debugger.analyze(
        PermissionError("access denied to COM5"),
        stage="observe",
    )

    assert report.failure_category == "SERIAL_PERMISSION_ERROR"
    assert report.confidence == 0.98


def test_analyze_many_preserves_input_order(debugger: Debugger) -> None:
    reports = debugger.analyze_many(
        [
            classification(FailureCategory.PORT_BUSY),
            classification(FailureCategory.LINKER_ERROR),
        ]
    )

    assert [report.failure_category for report in reports] == [
        "PORT_BUSY",
        "LINKER_ERROR",
    ]


def test_coordinator_callback_analyzes_latest_failure(
    debugger: Debugger,
) -> None:
    failures = [
        FailureResult(
            category="BUILD_ERROR",
            message="build failed",
            retryable=False,
            stage="build",
        ),
        FailureResult(
            category="PORT_BUSY",
            message="port busy",
            retryable=True,
            stage="flash",
        ),
    ]

    report = debugger(object(), failures, [object()])

    assert report.failure_category == "PORT_BUSY"


def test_explicit_debug_callback_without_failure_returns_unknown_report(
    debugger: Debugger,
) -> None:
    report = debugger(object(), (), ())

    assert report.failure_category == "UNKNOWN"
    assert report.suggested_actions[0] == "collect_complete_diagnostics"


def test_integrates_with_coordinator_debugger_callback() -> None:
    plan = ExecutionPlan(
        task_id="task-debug-integration",
        task_type=TaskType.FLASH_ONLY,
        target_board=TargetBoard.ESP32,
        framework=Framework.PLATFORMIO,
        requirements=("firmware deployment",),
        execution_steps=(ExecutionStep.FLASH_FIRMWARE,),
        estimated_tools=("flash_firmware",),
        confidence=0.9,
    )
    failure = FailureResult(
        category="PORT_BUSY",
        message="serial port is busy",
        retryable=True,
        stage="flash",
    )

    def executor(name: str, *args: object, **kwargs: object) -> object:
        return SimpleNamespace(
            success=False,
            status=ResultStatus.FAILED,
            message="flash failed",
            failure=failure,
        )

    outcome = asyncio.run(
        Coordinator(tool_executor=executor, debugger=Debugger()).execute(plan)
    )

    assert outcome.status is ExecutionStatus.FAILED
    assert outcome.step_results[-1].step is ExecutionStep.DEBUG_FAILURE
    report = outcome.step_results[-1].result
    assert isinstance(report, DebugReport)
    assert report.failure_category == "PORT_BUSY"
    assert report.suggested_actions[0] == "close_serial_monitor"


def test_reports_are_deterministic(debugger: Debugger) -> None:
    failure = classification(
        FailureCategory.INVALID_FIRMWARE,
        confidence=0.96,
    )

    assert debugger.analyze(failure) == debugger.analyze(failure)


def test_report_and_collections_are_immutable(debugger: Debugger) -> None:
    report = debugger.analyze(classification(FailureCategory.PORT_BUSY))

    with pytest.raises(FrozenInstanceError):
        report.confidence = 0.0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        report.recommendations[0].title = "changed"  # type: ignore[misc]
    assert isinstance(report.recommendations, tuple)
    assert isinstance(report.suggested_actions, tuple)


@pytest.mark.parametrize("value", [None, "", "   ", object(), 123])
def test_rejects_unsupported_or_empty_inputs(
    debugger: Debugger,
    value: object,
) -> None:
    with pytest.raises(DebuggerInputError):
        debugger.analyze(value)


def test_analyze_many_rejects_strings_and_non_sequences(
    debugger: Debugger,
) -> None:
    with pytest.raises(DebuggerInputError):
        debugger.analyze_many("failure")  # type: ignore[arg-type]
    with pytest.raises(DebuggerInputError):
        debugger.analyze_many({1, 2})  # type: ignore[arg-type]


def test_debug_recommendation_validates_fields() -> None:
    with pytest.raises(ValueError, match="title"):
        DebugRecommendation("", "description", "action")


def test_debug_report_validates_action_consistency() -> None:
    recommendation = DebugRecommendation("title", "description", "action")

    with pytest.raises(ValueError, match="match"):
        DebugReport(
            root_cause="root cause",
            failure_category="UNKNOWN",
            severity=DebugSeverity.HIGH,
            confidence=0.5,
            recommendations=(recommendation,),
            suggested_actions=("different",),
        )
