"""Deterministic pre-execution safety validation for embedded requests.

The validator in this module performs no I/O and has no dependency on code
generation.  A typical integration validates all available request context
before generation, build, flash, or execution::

    validator = SafetyValidator()
    result = validator.validate(
        prompt="Drive a 12 V pump from an ESP32",
        planner_output={"force_flash": False},
        firmware_metadata={"peripherals": ["gpio"]},
        hardware_metadata={
            "board": "ESP32",
            "supported_peripherals": ["gpio", "uart", "spi", "i2c"],
        },
    )
    if not result.safe_to_continue:
        print(result.summary())

Validation failures are returned as :class:`SafetyIssue` values; public
validation methods do not raise for malformed or unsafe request metadata.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, unique
from typing import Any, ClassVar, Pattern

__all__ = [
    "SafetyCategory",
    "SafetyIssue",
    "SafetySeverity",
    "SafetyValidationResult",
    "SafetyValidator",
]


@unique
class SafetySeverity(str, Enum):
    """Severity assigned to a deterministic safety finding."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@unique
class SafetyCategory(str, Enum):
    """Embedded-system safety domains recognized by PromptForge."""

    POWER = "POWER"
    VOLTAGE = "VOLTAGE"
    CURRENT = "CURRENT"
    GPIO = "GPIO"
    MOTOR_CONTROL = "MOTOR_CONTROL"
    HIGH_POWER_LOAD = "HIGH_POWER_LOAD"
    RELAY = "RELAY"
    HEATING = "HEATING"
    BATTERY = "BATTERY"
    SERIAL = "SERIAL"
    NETWORK = "NETWORK"
    FLASHING = "FLASHING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class SafetyIssue:
    """One actionable safety finding produced by a validator rule."""

    category: SafetyCategory
    severity: SafetySeverity
    message: str
    recommendation: str
    rule_id: str


@dataclass(frozen=True, slots=True)
class SafetyValidationResult:
    """Immutable aggregate of safety findings.

    ``safe_to_continue`` is false when at least one ``ERROR`` or ``CRITICAL``
    issue exists.  Informational findings and warnings remain visible but do
    not independently block execution.
    """

    safe_to_continue: bool
    issues: tuple[SafetyIssue, ...]
    warnings: tuple[SafetyIssue, ...]
    errors: tuple[SafetyIssue, ...]
    critical_failures: tuple[SafetyIssue, ...]

    @classmethod
    def from_issues(
        cls,
        issues: Iterable[SafetyIssue],
    ) -> SafetyValidationResult:
        """Create a consistently classified result from ``issues``."""

        collected = tuple(issues)
        warnings = tuple(
            issue
            for issue in collected
            if issue.severity is SafetySeverity.WARNING
        )
        errors = tuple(
            issue
            for issue in collected
            if issue.severity is SafetySeverity.ERROR
        )
        critical = tuple(
            issue
            for issue in collected
            if issue.severity is SafetySeverity.CRITICAL
        )
        return cls(
            safe_to_continue=not errors and not critical,
            issues=collected,
            warnings=warnings,
            errors=errors,
            critical_failures=critical,
        )

    def has_errors(self) -> bool:
        """Return whether any error-or-worse blocking finding was found."""

        return bool(self.errors or self.critical_failures)

    def has_critical_failures(self) -> bool:
        """Return whether any critical safety failure was found."""

        return bool(self.critical_failures)

    def summary(self) -> str:
        """Return a stable, human-readable one-line result summary."""

        counts = {severity: 0 for severity in SafetySeverity}
        for issue in self.issues:
            counts[issue.severity] += 1
        state = "SAFE_TO_CONTINUE" if self.safe_to_continue else "BLOCKED"
        return (
            f"{state}: {len(self.issues)} issue(s) "
            f"(info={counts[SafetySeverity.INFO]}, "
            f"warnings={counts[SafetySeverity.WARNING]}, "
            f"errors={counts[SafetySeverity.ERROR]}, "
            f"critical={counts[SafetySeverity.CRITICAL]})"
        )


@dataclass(frozen=True, slots=True)
class _PromptRule:
    rule_id: str
    pattern: Pattern[str]
    category: SafetyCategory
    severity: SafetySeverity
    message: str
    recommendation: str


class SafetyValidator:
    """Pure, deterministic rule engine for embedded firmware safety.

    Args:
        board_database: Supported board names or a mapping from board names to
            metadata.  Supplying it makes tests and deployments independent of
            filesystem state.  When omitted, PromptForge's planner-supported
            board families are used.
        supported_peripherals: Optional global peripheral allow-list.  Board
            metadata or board database entries take precedence when they
            declare their own supported peripherals.
    """

    DEFAULT_BOARD_DATABASE: ClassVar[tuple[str, ...]] = (
        "Arduino Uno",
        "ESP32",
        "ESP32-C3",
        "ESP32-S3",
        "STM32",
    )

    _BOARD_FIELDS: ClassVar[tuple[str, ...]] = (
        "board",
        "board_id",
        "board_name",
        "board_type",
        "name",
        "target_board",
    )
    _PERIPHERAL_FIELDS: ClassVar[tuple[str, ...]] = (
        "peripherals",
        "required_peripherals",
        "used_peripherals",
        "hardware",
        "devices",
    )
    _SUPPORTED_PERIPHERAL_FIELDS: ClassVar[tuple[str, ...]] = (
        "supported_peripherals",
        "peripherals",
        "capabilities",
        "interfaces",
    )

    _PROMPT_RULES: ClassVar[tuple[_PromptRule, ...]] = (
        _PromptRule(
            rule_id="PF-SAFETY-002",
            pattern=re.compile(
                r"\b(?:(?:220|110)\s*v(?:ac)?|mains|ac)\b",
                re.I,
            ),
            category=SafetyCategory.RELAY,
            severity=SafetySeverity.CRITICAL,
            message=(
                "The request involves mains or AC voltage and assumes direct "
                "relay-oriented control."
            ),
            recommendation=(
                "Block execution until a qualified design specifies isolation, "
                "voltage/current ratings, fusing, enclosure, and a certified "
                "mains switching interface."
            ),
        ),
        _PromptRule(
            rule_id="PF-SAFETY-003",
            pattern=re.compile(
                r"\b(?:power\s+(?:the\s+|a\s+)?device|"
                r"drive\s+(?:the\s+|a\s+)?motor|"
                r"connect\s+(?:the\s+|a\s+)?motor)\s+directly\b",
                re.I,
            ),
            category=SafetyCategory.GPIO,
            severity=SafetySeverity.CRITICAL,
            message="The request assumes a GPIO can power a load directly.",
            recommendation=(
                "Use a correctly rated driver stage, external supply, flyback "
                "protection where applicable, and a shared reference only when "
                "the hardware design permits it."
            ),
        ),
        _PromptRule(
            rule_id="PF-SAFETY-004",
            pattern=re.compile(
                r"\b(?:charge\s+(?:a\s+|the\s+)?battery|"
                r"lithium\s+battery|lipo)\b",
                re.I,
            ),
            category=SafetyCategory.BATTERY,
            severity=SafetySeverity.WARNING,
            message="The request includes lithium or battery charging circuitry.",
            recommendation=(
                "Require a chemistry-specific charger, cell protection, thermal "
                "monitoring, current limits, and manufacturer-approved limits."
            ),
        ),
        _PromptRule(
            rule_id="PF-SAFETY-005",
            pattern=re.compile(
                r"\b(?:heater|heating\s+element|"
                r"temperature\s+control(?:led)?\s+oven|reflow)\b",
                re.I,
            ),
            category=SafetyCategory.HEATING,
            severity=SafetySeverity.WARNING,
            message="The request controls a heater or high-temperature system.",
            recommendation=(
                "Add independent over-temperature protection, a fail-off output "
                "state, sensor fault handling, and appropriately rated switching."
            ),
        ),
        _PromptRule(
            rule_id="PF-SAFETY-010",
            pattern=re.compile(
                r"\b(?:open\s+web\s+server|wi[\s-]?fi\s+control|"
                r"remote\s+access)\b",
                re.I,
            ),
            category=SafetyCategory.NETWORK,
            severity=SafetySeverity.INFO,
            message="The firmware exposes control or access over a network.",
            recommendation=(
                "Define authentication, authorization, secure defaults, update "
                "policy, and network exposure boundaries before deployment."
            ),
        ),
    )

    _HIGH_CURRENT_RE: ClassVar[Pattern[str]] = re.compile(
        r"\b(?:motor|servo|pump|fan|solenoid)s?\b",
        re.I,
    )
    _DRIVER_RE: ClassVar[Pattern[str]] = re.compile(
        r"\b(?:relay|driver|mosfet|transistor)s?\b",
        re.I,
    )
    _FORCE_FLASH_RE: ClassVar[Pattern[str]] = re.compile(
        r"\b(?:force[\s_-]*(?:flash|flashing)|"
        r"(?:flash|flashing)[\s_-]*force(?:d)?)\b",
        re.I,
    )
    _VALIDATION_RE: ClassVar[Pattern[str]] = re.compile(
        r"\b(?:validate|validated|validation|verify|verified|verification|"
        r"preflight|pre[\s_-]*flash[\s_-]*check)\b",
        re.I,
    )
    _NO_VALIDATION_RE: ClassVar[Pattern[str]] = re.compile(
        r"\b(?:without|skip|skipping|bypass|bypassing|disable|disabled|no)"
        r"(?:\s+\w+){0,2}\s+(?:validation|verification|preflight|checks?)\b",
        re.I,
    )

    def __init__(
        self,
        board_database: Mapping[str, object] | Iterable[str] | None = None,
        supported_peripherals: Iterable[str] | None = None,
    ) -> None:
        database = (
            self.DEFAULT_BOARD_DATABASE
            if board_database is None
            else board_database
        )
        board_names, board_metadata = _normalize_board_database(database)
        self._board_names = board_names
        self._board_metadata = board_metadata
        self._supported_peripherals = _normalize_name_set(
            supported_peripherals or ()
        )

    def validate_prompt(self, prompt: object) -> SafetyValidationResult:
        """Validate user prompt text against electrical safety rules."""

        if not isinstance(prompt, str) or not prompt.strip():
            return SafetyValidationResult.from_issues(
                (
                    _input_issue(
                        "Prompt must be a non-empty string before safety "
                        "validation can continue."
                    ),
                )
            )

        issues: list[SafetyIssue] = []
        if self._HIGH_CURRENT_RE.search(prompt) and not self._DRIVER_RE.search(
            prompt
        ):
            issues.append(
                SafetyIssue(
                    category=SafetyCategory.MOTOR_CONTROL,
                    severity=SafetySeverity.WARNING,
                    message=(
                        "A high-current or inductive device is requested without "
                        "an explicit relay, driver, MOSFET, or transistor."
                    ),
                    recommendation=(
                        "Specify a correctly rated driver and supply, GPIO input "
                        "protection, and flyback suppression for inductive loads."
                    ),
                    rule_id="PF-SAFETY-001",
                )
            )

        for rule in self._PROMPT_RULES:
            if rule.pattern.search(prompt):
                issues.append(
                    SafetyIssue(
                        category=rule.category,
                        severity=rule.severity,
                        message=rule.message,
                        recommendation=rule.recommendation,
                        rule_id=rule.rule_id,
                    )
                )

        return SafetyValidationResult.from_issues(issues)

    def validate_board(self, board_metadata: object) -> SafetyValidationResult:
        """Validate that board metadata identifies a supported board."""

        board_name = _extract_board_name(board_metadata, self._board_names)
        if board_name is None:
            return SafetyValidationResult.from_issues(
                (
                    SafetyIssue(
                        category=SafetyCategory.UNKNOWN,
                        severity=SafetySeverity.ERROR,
                        message="Board metadata is absent or does not identify a board.",
                        recommendation=(
                            "Select a board from the PromptForge board database "
                            "before generation or hardware execution."
                        ),
                        rule_id="PF-SAFETY-006",
                    ),
                )
            )

        normalized = _normalize_name(board_name)
        if normalized not in self._board_names:
            return SafetyValidationResult.from_issues(
                (
                    SafetyIssue(
                        category=SafetyCategory.UNKNOWN,
                        severity=SafetySeverity.ERROR,
                        message=f"Board {board_name!r} is not supported by PromptForge.",
                        recommendation=(
                            "Use a board present in the configured PromptForge "
                            "board database or add a reviewed board definition."
                        ),
                        rule_id="PF-SAFETY-007",
                    ),
                )
            )

        return SafetyValidationResult.from_issues(())

    def validate_firmware(
        self,
        firmware_metadata: object,
        board_metadata: object | None = None,
    ) -> SafetyValidationResult:
        """Validate flash intent and firmware/peripheral compatibility."""

        if firmware_metadata is None:
            return SafetyValidationResult.from_issues(())
        if not isinstance(firmware_metadata, (str, Mapping)) and not any(
            _get_field(firmware_metadata, field) is not None
            for field in (
                *self._PERIPHERAL_FIELDS,
                "force_flash",
                "force_flashing",
                "action",
                "operation",
                "request",
                "description",
                "execution_steps",
            )
        ):
            return SafetyValidationResult.from_issues(
                (_input_issue("Firmware metadata has an unsupported shape."),)
            )

        issues: list[SafetyIssue] = []
        if _requests_force_flash_without_validation(firmware_metadata):
            issues.append(_unsafe_flash_issue())

        referenced = _extract_name_set(
            firmware_metadata,
            self._PERIPHERAL_FIELDS,
        )
        supported = self._supported_peripherals
        board_name = _extract_board_name(board_metadata, self._board_names)
        normalized_board = _normalize_name(board_name) if board_name else None
        database_metadata = (
            self._board_metadata.get(normalized_board, {})
            if normalized_board
            else {}
        )
        declared = _extract_name_set(
            board_metadata,
            self._SUPPORTED_PERIPHERAL_FIELDS,
        )
        database_supported = _extract_name_set(
            database_metadata,
            self._SUPPORTED_PERIPHERAL_FIELDS,
        )
        if declared:
            supported = declared
        elif database_supported:
            supported = database_supported

        unsupported = tuple(sorted(referenced - supported)) if supported else ()
        if unsupported:
            display = ", ".join(unsupported)
            issues.append(
                SafetyIssue(
                    category=SafetyCategory.UNKNOWN,
                    severity=SafetySeverity.WARNING,
                    message=f"Firmware references unsupported peripherals: {display}.",
                    recommendation=(
                        "Confirm electrical compatibility and add reviewed board "
                        "support for each peripheral before execution."
                    ),
                    rule_id="PF-SAFETY-009",
                )
            )

        return SafetyValidationResult.from_issues(issues)

    def validate(
        self,
        prompt: object | None = None,
        planner_output: object | None = None,
        firmware_metadata: object | None = None,
        hardware_metadata: object | None = None,
        *,
        board_metadata: object | None = None,
    ) -> SafetyValidationResult:
        """Validate all supplied request, plan, firmware, and hardware context.

        ``board_metadata`` is an explicit alias for ``hardware_metadata``.  It
        is useful for callers whose existing contracts use that field name.
        The aggregate result preserves deterministic rule order and de-duplicates
        identical rule findings.
        """

        hardware = (
            board_metadata
            if board_metadata is not None
            else hardware_metadata
        )
        issues: list[SafetyIssue] = []
        if prompt is not None:
            issues.extend(self.validate_prompt(prompt).issues)

        issues.extend(self.validate_board(hardware).issues)

        if planner_output is not None and _requests_force_flash_without_validation(
            planner_output
        ):
            issues.append(_unsafe_flash_issue())

        issues.extend(
            self.validate_firmware(firmware_metadata, hardware).issues
        )
        return SafetyValidationResult.from_issues(_deduplicate_issues(issues))


def _normalize_board_database(
    database: Mapping[str, object] | Iterable[str],
) -> tuple[frozenset[str], Mapping[str, object]]:
    if isinstance(database, Mapping):
        normalized = {
            _normalize_name(name): {
                "supported_peripherals": tuple(
                    sorted(
                        _extract_name_set(
                            metadata,
                            SafetyValidator._SUPPORTED_PERIPHERAL_FIELDS,
                            include_nested_metadata=False,
                        )
                    )
                )
            }
            for name, metadata in database.items()
            if isinstance(name, str) and name.strip()
        }
        return frozenset(normalized), normalized

    if isinstance(database, (str, bytes, bytearray)):
        values: Iterable[object] = (database,)
    else:
        try:
            values = tuple(database)
        except TypeError:
            values = ()
    names = frozenset(
        _normalize_name(value)
        for value in values
        if isinstance(value, str) and value.strip()
    )
    return names, {}


def _normalize_name(value: object) -> str:
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str):
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _normalize_name_set(values: Iterable[object]) -> frozenset[str]:
    return frozenset(
        normalized
        for value in values
        if (normalized := _normalize_name(value))
    )


def _get_field(source: object, field_name: str) -> object | None:
    if isinstance(source, Mapping):
        return source.get(field_name)
    try:
        return getattr(source, field_name, None)
    except Exception:
        return None


def _extract_board_name(
    metadata: object,
    supported_names: frozenset[str],
) -> str | None:
    if isinstance(metadata, Enum):
        metadata = metadata.value
    if isinstance(metadata, str):
        value = metadata.strip()
        if _normalize_name(value) in {"", "unknown", "none", "null"}:
            return None
        return value
    if metadata is None:
        return None
    for field_name in SafetyValidator._BOARD_FIELDS:
        value = _get_field(metadata, field_name)
        if isinstance(value, Enum):
            value = value.value
        if isinstance(value, str) and value.strip():
            candidate = value.strip()
            if _normalize_name(candidate) not in {
                "",
                "unknown",
                "none",
                "null",
            }:
                return candidate

    # Some board databases use the board identifier as the only mapping key.
    if isinstance(metadata, Mapping) and len(metadata) == 1:
        only_key = next(iter(metadata), None)
        if isinstance(only_key, str) and _normalize_name(only_key) in supported_names:
            return only_key
    return None


def _as_names(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return tuple(key for key, enabled in value.items() if bool(enabled))
    if isinstance(value, str) or isinstance(value, Enum):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(
        value,
        (bytes, bytearray),
    ):
        return tuple(value)
    if isinstance(value, (set, frozenset)):
        return tuple(value)
    return ()


def _extract_name_set(
    metadata: object,
    field_names: Iterable[str],
    *,
    include_nested_metadata: bool = True,
) -> frozenset[str]:
    if metadata is None:
        return frozenset()
    for field_name in field_names:
        value = _get_field(metadata, field_name)
        names = _normalize_name_set(_as_names(value))
        if names:
            return names
    if include_nested_metadata:
        nested = _get_field(metadata, "metadata")
        if nested is not None and nested is not metadata:
            return _extract_name_set(
                nested,
                field_names,
                include_nested_metadata=False,
            )
    return frozenset()


def _requests_force_flash_without_validation(metadata: object) -> bool:
    force_fields = ("force_flash", "force_flashing", "force", "forced")
    validation_fields = (
        "validated",
        "validation_complete",
        "preflight_validated",
        "verify_before_flash",
        "validation",
    )

    nested_metadata = _get_field(metadata, "metadata")
    sources = (metadata, nested_metadata) if nested_metadata is not metadata else (metadata,)
    forced = any(
        _get_field(source, field) is True
        for source in sources
        if source is not None
        for field in force_fields
    )
    validated = any(
        _get_field(source, field) is True
        for source in sources
        if source is not None
        for field in validation_fields
    )

    text_parts: list[str] = []
    if isinstance(metadata, str):
        text_parts.append(metadata)
    else:
        for source in sources:
            if source is None:
                continue
            for field in (
                "action",
                "operation",
                "request",
                "description",
                "requirements",
                "execution_steps",
            ):
                value = _get_field(source, field)
                text_parts.extend(str(item) for item in _as_names(value))
    text = " ".join(text_parts)
    forced = forced or bool(SafetyValidator._FORCE_FLASH_RE.search(text))
    explicitly_unvalidated = bool(SafetyValidator._NO_VALIDATION_RE.search(text))
    validated = validated or (
        bool(SafetyValidator._VALIDATION_RE.search(text))
        and not explicitly_unvalidated
    )
    if explicitly_unvalidated:
        validated = False
    return forced and not validated


def _unsafe_flash_issue() -> SafetyIssue:
    return SafetyIssue(
        category=SafetyCategory.FLASHING,
        severity=SafetySeverity.WARNING,
        message="The planner requests forced flashing without prior validation.",
        recommendation=(
            "Require target identity, image compatibility, integrity, power, "
            "and connection checks before permitting a forced flash."
        ),
        rule_id="PF-SAFETY-008",
    )


def _input_issue(message: str) -> SafetyIssue:
    return SafetyIssue(
        category=SafetyCategory.UNKNOWN,
        severity=SafetySeverity.ERROR,
        message=message,
        recommendation="Provide complete, structured metadata and validate again.",
        rule_id="PF-SAFETY-000",
    )


def _deduplicate_issues(
    issues: Iterable[SafetyIssue],
) -> tuple[SafetyIssue, ...]:
    seen: set[tuple[str, str]] = set()
    result: list[SafetyIssue] = []
    for issue in issues:
        key = (issue.rule_id, issue.message)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return tuple(result)
