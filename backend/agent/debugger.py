"""Deterministic failure analysis and recovery guidance for PromptForge AI.

The debugger consumes existing classifier output, runtime results, coordinator
failures, exceptions, or raw diagnostic text and returns an immutable
``DebugReport``. It never executes an action, retries a stage, calls a tool, or
modifies runtime state.

Example::

    report = Debugger().analyze(
        "could not open port COM4: device or resource busy",
        stage="flash",
        tool="esptool",
    )
    assert report.failure_category == "PORT_BUSY"
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, unique
from typing import Optional

from ..runtime.failure_classifier import (
    FailureCategory,
    FailureClassification,
    FailureSeverity,
    classify_failure,
)

__all__ = [
    "DebugRecommendation",
    "DebugReport",
    "DebugSeverity",
    "Debugger",
    "DebuggerInputError",
]


@unique
class DebugSeverity(str, Enum):
    """Operational urgency of a debugger finding."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DebuggerInputError(ValueError):
    """Raised when no analyzable failure signal is supplied."""


@dataclass(frozen=True, slots=True)
class DebugRecommendation:
    """One recovery recommendation; ``action`` is advisory, not executed."""

    title: str
    description: str
    action: str

    def __post_init__(self) -> None:
        for name in ("title", "description", "action"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class DebugReport:
    """Immutable diagnosis and ordered recovery guidance."""

    root_cause: str
    failure_category: str
    severity: DebugSeverity
    confidence: float
    recommendations: tuple[DebugRecommendation, ...]
    suggested_actions: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "recommendations", tuple(self.recommendations)
        )
        object.__setattr__(
            self, "suggested_actions", tuple(self.suggested_actions)
        )
        if not isinstance(self.root_cause, str) or not self.root_cause.strip():
            raise ValueError("root_cause must be a non-empty string")
        if (
            not isinstance(self.failure_category, str)
            or not self.failure_category.strip()
        ):
            raise ValueError("failure_category must be a non-empty string")
        if not isinstance(self.severity, DebugSeverity):
            raise ValueError("severity must be a DebugSeverity")
        if (
            not isinstance(self.confidence, (int, float))
            or isinstance(self.confidence, bool)
            or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("confidence must be between 0.0 and 1.0")
        if not self.recommendations:
            raise ValueError("recommendations must not be empty")
        if any(
            not isinstance(item, DebugRecommendation)
            for item in self.recommendations
        ):
            raise ValueError(
                "recommendations must contain DebugRecommendation values"
            )
        if any(
            not isinstance(action, str) or not action.strip()
            for action in self.suggested_actions
        ):
            raise ValueError("suggested_actions must contain non-empty strings")
        expected_actions = tuple(item.action for item in self.recommendations)
        if self.suggested_actions != expected_actions:
            raise ValueError(
                "suggested_actions must match recommendation actions"
            )


@dataclass(frozen=True, slots=True)
class _Diagnosis:
    category: str
    severity: DebugSeverity
    confidence: float
    message: str
    signal: str
    stage: str
    tool: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class _RecoveryPolicy:
    root_cause: str
    recommendations: tuple[DebugRecommendation, ...]


def _recommendation(
    title: str,
    description: str,
    action: str,
) -> DebugRecommendation:
    return DebugRecommendation(title, description, action)


_POLICIES: Mapping[str, _RecoveryPolicy] = {
    FailureCategory.TOOLCHAIN_MISSING.value: _RecoveryPolicy(
        root_cause="The required embedded toolchain or executable is unavailable.",
        recommendations=(
            _recommendation(
                "Install the missing toolchain",
                "Install the compiler or command reported by the failure and "
                "make it available on PATH.",
                "install_required_toolchain",
            ),
            _recommendation(
                "Verify toolchain discovery",
                "Run the tool's version command in the same environment used "
                "by PromptForge.",
                "verify_toolchain_version",
            ),
        ),
    ),
    FailureCategory.COMPILATION_ERROR.value: _RecoveryPolicy(
        root_cause="The source code failed compiler validation.",
        recommendations=(
            _recommendation(
                "Inspect the first compiler error",
                "Fix the earliest compiler diagnostic before addressing "
                "secondary errors caused by it.",
                "inspect_first_compiler_error",
            ),
            _recommendation(
                "Verify dependencies and declarations",
                "Confirm required headers, libraries, symbols, and board APIs "
                "are available to the selected environment.",
                "verify_source_dependencies",
            ),
        ),
    ),
    FailureCategory.LINKER_ERROR.value: _RecoveryPolicy(
        root_cause="Compilation completed, but required symbols or libraries "
        "could not be linked.",
        recommendations=(
            _recommendation(
                "Resolve missing symbols",
                "Match undefined symbols to their implementation or required "
                "library and include it in the build.",
                "resolve_linker_symbols",
            ),
            _recommendation(
                "Check duplicate definitions",
                "Ensure functions and globals are defined exactly once across "
                "translation units.",
                "check_duplicate_definitions",
            ),
        ),
    ),
    FailureCategory.BUILD_ERROR.value: _RecoveryPolicy(
        root_cause="The firmware build pipeline failed before producing a "
        "valid artifact.",
        recommendations=(
            _recommendation(
                "Review build output",
                "Locate the earliest actionable error in the compiler, linker, "
                "or build-system output.",
                "inspect_build_output",
            ),
            _recommendation(
                "Validate the build environment",
                "Confirm the selected board, environment, framework, and "
                "dependency configuration are consistent.",
                "validate_build_environment",
            ),
        ),
    ),
    FailureCategory.PORT_BUSY.value: _RecoveryPolicy(
        root_cause="Another process currently owns the target serial port.",
        recommendations=(
            _recommendation(
                "Close serial-port consumers",
                "Stop serial monitors, IDE terminals, and other applications "
                "using the target port.",
                "close_serial_monitor",
            ),
            _recommendation(
                "Retry after releasing the port",
                "Confirm the port can be opened exclusively before retrying "
                "the failed operation.",
                "retry_after_port_release",
            ),
        ),
    ),
    FailureCategory.DEVICE_DISCONNECTED.value: _RecoveryPolicy(
        root_cause="The target board disconnected or its device node vanished.",
        recommendations=(
            _recommendation(
                "Reconnect the board",
                "Check USB power, cable integrity, connectors, and hub stability.",
                "reconnect_target_board",
            ),
            _recommendation(
                "Redetect the serial port",
                "Enumerate connected boards again because the port may have "
                "changed after reconnection.",
                "redetect_board",
            ),
        ),
    ),
    FailureCategory.FLASH_TIMEOUT.value: _RecoveryPolicy(
        root_cause="The programmer could not complete communication with the "
        "target within the flash timeout.",
        recommendations=(
            _recommendation(
                "Enter bootloader mode",
                "Reset the board into its programming mode and confirm the "
                "selected port and target are correct.",
                "enter_bootloader_mode",
            ),
            _recommendation(
                "Stabilize the programming connection",
                "Use a data-capable USB cable, remove unstable hubs, and reduce "
                "the upload speed when supported.",
                "stabilize_flash_connection",
            ),
        ),
    ),
    FailureCategory.INVALID_FIRMWARE.value: _RecoveryPolicy(
        root_cause="The firmware artifact is invalid or incompatible with the "
        "selected target.",
        recommendations=(
            _recommendation(
                "Rebuild for the selected board",
                "Confirm the board, chip variant, memory layout, and artifact "
                "format before rebuilding.",
                "rebuild_for_target",
            ),
            _recommendation(
                "Validate the artifact",
                "Verify the binary exists, is non-empty, and belongs to the "
                "current build environment.",
                "validate_firmware_artifact",
            ),
        ),
    ),
    FailureCategory.TRANSIENT_USB_FAILURE.value: _RecoveryPolicy(
        root_cause="USB enumeration or transport failed transiently.",
        recommendations=(
            _recommendation(
                "Stabilize USB connectivity",
                "Reconnect the board directly, replace suspect cables, and "
                "avoid underpowered hubs.",
                "stabilize_usb_connection",
            ),
            _recommendation(
                "Redetect before retrying",
                "Wait for USB enumeration to settle and detect the board again.",
                "redetect_after_usb_settle",
            ),
        ),
    ),
    FailureCategory.OBSERVE_TIMEOUT.value: _RecoveryPolicy(
        root_cause="Expected serial output was not observed before timeout.",
        recommendations=(
            _recommendation(
                "Verify firmware logging",
                "Confirm the firmware initializes serial output and reaches the "
                "expected code path.",
                "verify_firmware_logging",
            ),
            _recommendation(
                "Check monitor settings",
                "Match the monitor baud rate, port, reset behavior, and success "
                "pattern to the firmware.",
                "validate_monitor_configuration",
            ),
        ),
    ),
    FailureCategory.SERIAL_PERMISSION_ERROR.value: _RecoveryPolicy(
        root_cause="The host does not have permission to access the serial device.",
        recommendations=(
            _recommendation(
                "Grant serial-device access",
                "Correct operating-system permissions, group membership, or "
                "device access policy for the port.",
                "fix_serial_permissions",
            ),
            _recommendation(
                "Reopen the development session",
                "Restart the shell or service after permission changes so new "
                "credentials take effect.",
                "refresh_permission_context",
            ),
        ),
    ),
    FailureCategory.WATCHDOG_RESET.value: _RecoveryPolicy(
        root_cause="The firmware stopped servicing the watchdog and reset.",
        recommendations=(
            _recommendation(
                "Inspect blocking code",
                "Find long loops, deadlocks, blocking I/O, and tasks that do not "
                "yield within the watchdog interval.",
                "inspect_watchdog_blocking_paths",
            ),
            _recommendation(
                "Analyze the reset trace",
                "Decode the backtrace and identify the task active immediately "
                "before the watchdog reset.",
                "decode_watchdog_backtrace",
            ),
        ),
    ),
    FailureCategory.SERIAL_FRAMING_ERROR.value: _RecoveryPolicy(
        root_cause="Serial bytes could not be decoded reliably, usually because "
        "communication settings or signal quality are incorrect.",
        recommendations=(
            _recommendation(
                "Match serial parameters",
                "Set baud rate, parity, data bits, and stop bits to the firmware "
                "configuration.",
                "match_serial_parameters",
            ),
            _recommendation(
                "Check electrical signal quality",
                "Verify shared ground, logic voltage, wiring length, and UART "
                "connections.",
                "inspect_uart_signal_integrity",
            ),
        ),
    ),
    FailureCategory.UNKNOWN.value: _RecoveryPolicy(
        root_cause="The available evidence does not identify a known failure mode.",
        recommendations=(
            _recommendation(
                "Collect complete diagnostics",
                "Capture the stage, tool, command, exit code, and untruncated "
                "stdout and stderr for review.",
                "collect_complete_diagnostics",
            ),
            _recommendation(
                "Reproduce with one controlled change",
                "Repeat the operation without changing multiple variables so "
                "the failing condition can be isolated.",
                "reproduce_failure_deterministically",
            ),
        ),
    ),
}

_FALLBACK_POLICY = _POLICIES[FailureCategory.UNKNOWN.value]

_SEVERITY_MAP: Mapping[str, DebugSeverity] = {
    FailureSeverity.INFO.value: DebugSeverity.LOW,
    FailureSeverity.WARNING.value: DebugSeverity.MEDIUM,
    FailureSeverity.ERROR.value: DebugSeverity.HIGH,
    FailureSeverity.CRITICAL.value: DebugSeverity.CRITICAL,
}

_CATEGORY_SEVERITY: Mapping[str, DebugSeverity] = {
    FailureCategory.TOOLCHAIN_MISSING.value: DebugSeverity.CRITICAL,
    FailureCategory.COMPILATION_ERROR.value: DebugSeverity.HIGH,
    FailureCategory.LINKER_ERROR.value: DebugSeverity.HIGH,
    FailureCategory.BUILD_ERROR.value: DebugSeverity.HIGH,
    FailureCategory.PORT_BUSY.value: DebugSeverity.MEDIUM,
    FailureCategory.DEVICE_DISCONNECTED.value: DebugSeverity.HIGH,
    FailureCategory.FLASH_TIMEOUT.value: DebugSeverity.HIGH,
    FailureCategory.INVALID_FIRMWARE.value: DebugSeverity.CRITICAL,
    FailureCategory.TRANSIENT_USB_FAILURE.value: DebugSeverity.MEDIUM,
    FailureCategory.OBSERVE_TIMEOUT.value: DebugSeverity.MEDIUM,
    FailureCategory.SERIAL_PERMISSION_ERROR.value: DebugSeverity.HIGH,
    FailureCategory.WATCHDOG_RESET.value: DebugSeverity.HIGH,
    FailureCategory.SERIAL_FRAMING_ERROR.value: DebugSeverity.MEDIUM,
    FailureCategory.UNKNOWN.value: DebugSeverity.HIGH,
    "COORDINATOR_ERROR": DebugSeverity.HIGH,
    "CANCELLED": DebugSeverity.LOW,
}


class Debugger:
    """Analyze failures and produce deterministic recovery recommendations."""

    __slots__ = ()

    def analyze(
        self,
        failure: object,
        *,
        stage: Optional[str] = None,
        tool: Optional[str] = None,
    ) -> DebugReport:
        """Return a diagnosis for one failure-like input.

        Supported inputs include ``FailureClassification``, ``FailureResult``,
        coordinator ``ExecutionFailure`` objects, failed runtime result objects,
        exceptions, and non-empty raw diagnostic strings.
        """
        diagnosis = _diagnose(failure, stage=stage, tool=tool)
        policy = _policy_for(diagnosis.category, diagnosis.signal)
        root_cause = _root_cause(policy.root_cause, diagnosis)
        return DebugReport(
            root_cause=root_cause,
            failure_category=diagnosis.category,
            severity=diagnosis.severity,
            confidence=diagnosis.confidence,
            recommendations=policy.recommendations,
            suggested_actions=tuple(
                recommendation.action
                for recommendation in policy.recommendations
            ),
        )

    def analyze_many(self, failures: Sequence[object]) -> tuple[DebugReport, ...]:
        """Analyze failures in input order without aggregation or mutation."""
        if isinstance(failures, (str, bytes)) or not isinstance(
            failures, Sequence
        ):
            raise DebuggerInputError("failures must be a sequence")
        return tuple(self.analyze(failure) for failure in failures)

    def __call__(
        self,
        plan: object,
        failures: Sequence[object],
        completed: Sequence[object],
    ) -> DebugReport:
        """Act as the debugger callback expected by ``Coordinator``.

        ``plan`` and ``completed`` are accepted for interface compatibility;
        diagnosis uses the latest failure only and does not mutate either.
        """
        del plan, completed
        if isinstance(failures, (str, bytes)) or not isinstance(
            failures, Sequence
        ):
            raise DebuggerInputError("failures must be a sequence")
        if not failures:
            return self.analyze(
                "No failure evidence was supplied for explicit debugging.",
                stage="debug",
            )
        return self.analyze(failures[-1])


def _diagnose(
    failure: object,
    *,
    stage: Optional[str],
    tool: Optional[str],
) -> _Diagnosis:
    if isinstance(failure, FailureClassification):
        return _from_classification(failure, stage=stage, tool=tool)
    if isinstance(failure, BaseException):
        classification = classify_failure(
            exception=failure,
            stage=stage,
            tool=tool,
        )
        return _from_classification(classification, stage=stage, tool=tool)
    if isinstance(failure, str):
        signal = failure.strip()
        if not signal:
            raise DebuggerInputError("failure text must be non-empty")
        classification = classify_failure(
            stderr=signal,
            stage=stage,
            tool=tool,
        )
        return _from_classification(classification, stage=stage, tool=tool)
    if failure is None:
        raise DebuggerInputError("failure must not be None")

    nested = getattr(failure, "failure", None)
    if nested is not None and nested is not failure:
        return _diagnose_runtime_result(failure, nested, stage=stage, tool=tool)

    category = _enum_string(getattr(failure, "category", ""))
    if category:
        return _from_structured_failure(failure, category, stage=stage, tool=tool)

    if hasattr(failure, "status") or hasattr(failure, "process_result"):
        return _diagnose_runtime_result(failure, None, stage=stage, tool=tool)

    raise DebuggerInputError(
        f"unsupported failure input type: {type(failure).__name__}"
    )


def _diagnose_runtime_result(
    result: object,
    nested_failure: object,
    *,
    stage: Optional[str],
    tool: Optional[str],
) -> _Diagnosis:
    resolved_stage = stage or _text(getattr(nested_failure, "stage", ""))
    resolved_tool = tool or _text(getattr(result, "tool", ""))
    category = _enum_string(getattr(nested_failure, "category", ""))
    if category:
        return _from_structured_failure(
            nested_failure,
            category,
            stage=resolved_stage,
            tool=resolved_tool,
            fallback_message=_text(getattr(result, "message", "")),
        )

    process_result = getattr(result, "process_result", None)
    message = _text(getattr(result, "message", ""))
    classification = classify_failure(
        stderr=message,
        process_result=process_result,
        stage=resolved_stage,
        tool=resolved_tool,
    )
    return _from_classification(
        classification,
        stage=resolved_stage,
        tool=resolved_tool,
    )


def _from_classification(
    classification: FailureClassification,
    *,
    stage: Optional[str],
    tool: Optional[str],
) -> _Diagnosis:
    category = classification.category.value
    return _Diagnosis(
        category=category,
        severity=_SEVERITY_MAP[classification.severity.value],
        confidence=classification.confidence,
        message=classification.message,
        signal=classification.raw_signal or classification.message,
        stage=(stage or "").strip(),
        tool=(tool or "").strip(),
        retryable=classification.retryable,
    )


def _from_structured_failure(
    failure: object,
    category: str,
    *,
    stage: Optional[str],
    tool: Optional[str],
    fallback_message: str = "",
) -> _Diagnosis:
    message = _text(getattr(failure, "message", "")) or fallback_message
    raw_output = _text(getattr(failure, "raw_output", ""))
    raw_failure = getattr(failure, "raw_failure", None)
    signal = raw_output or _raw_failure_text(raw_failure) or message
    metadata = getattr(failure, "metadata", {})
    confidence = _metadata_confidence(metadata, default=0.85)
    severity = _structured_severity(metadata, category)
    resolved_stage = stage or _text(getattr(failure, "stage", ""))
    resolved_tool = tool or _text(getattr(failure, "tool_name", ""))

    if category == FailureCategory.UNKNOWN.value and signal:
        classification = classify_failure(
            stderr=signal,
            stage=resolved_stage,
            tool=resolved_tool,
        )
        if classification.category is not FailureCategory.UNKNOWN:
            return _from_classification(
                classification,
                stage=resolved_stage,
                tool=resolved_tool,
            )

    return _Diagnosis(
        category=category,
        severity=severity,
        confidence=confidence,
        message=message or f"{category} failure",
        signal=signal,
        stage=(resolved_stage or "").strip(),
        tool=(resolved_tool or "").strip(),
        retryable=bool(getattr(failure, "retryable", False))
        or bool(getattr(failure, "recoverable", False)),
    )


def _policy_for(category: str, signal: str) -> _RecoveryPolicy:
    normalized_signal = signal.casefold()
    if category in {
        FailureCategory.COMPILATION_ERROR.value,
        FailureCategory.BUILD_ERROR.value,
    } and any(
        marker in normalized_signal
        for marker in (
            "no such file",
            "not found",
            "missing dependency",
            "unknown package",
            "cannot open include file",
        )
    ):
        return _RecoveryPolicy(
            root_cause="A required source dependency, header, or library is missing.",
            recommendations=(
                _recommendation(
                    "Install the missing dependency",
                    "Add the library or package named by the first build error "
                    "to the selected firmware environment.",
                    "install_missing_dependency",
                ),
                _recommendation(
                    "Verify dependency configuration",
                    "Confirm include paths, dependency declarations, and "
                    "framework compatibility before rebuilding.",
                    "verify_dependency_configuration",
                ),
            ),
        )
    if "wokwi" in normalized_signal and any(
        marker in normalized_signal
        for marker in ("invalid", "diagram", "project", "lint")
    ):
        return _RecoveryPolicy(
            root_cause="The generated Wokwi project configuration is invalid.",
            recommendations=(
                _recommendation(
                    "Regenerate the Wokwi project",
                    "Create a fresh project using the requested supported board "
                    "and the current firmware artifact.",
                    "regenerate_wokwi_project",
                ),
                _recommendation(
                    "Validate simulation files",
                    "Check diagram.json, wokwi.toml, board parts, and firmware "
                    "paths before starting simulation.",
                    "validate_wokwi_project",
                ),
            ),
        )
    return _POLICIES.get(category, _FALLBACK_POLICY)


def _root_cause(base: str, diagnosis: _Diagnosis) -> str:
    context: list[str] = []
    if diagnosis.stage:
        context.append(f"stage={diagnosis.stage}")
    if diagnosis.tool:
        context.append(f"tool={diagnosis.tool}")
    if not context:
        return base
    return f"{base} ({', '.join(context)})"


def _structured_severity(
    metadata: object,
    category: str,
) -> DebugSeverity:
    if isinstance(metadata, Mapping):
        value = _enum_string(metadata.get("severity", ""))
        if value in _SEVERITY_MAP:
            return _SEVERITY_MAP[value]
        if value in DebugSeverity._value2member_map_:
            return DebugSeverity(value)
    return _CATEGORY_SEVERITY.get(category, DebugSeverity.HIGH)


def _metadata_confidence(metadata: object, *, default: float) -> float:
    if isinstance(metadata, Mapping):
        value = metadata.get("confidence")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and 0.0 <= value <= 1.0
        ):
            return float(value)
    return default


def _raw_failure_text(value: object) -> str:
    if value is None:
        return ""
    raw_output = _text(getattr(value, "raw_output", ""))
    if raw_output:
        return raw_output
    if isinstance(value, BaseException):
        return f"{type(value).__name__}: {value}"
    return _text(value)


def _enum_string(value: object) -> str:
    enum_value = getattr(value, "value", value)
    return _text(enum_value)


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()
