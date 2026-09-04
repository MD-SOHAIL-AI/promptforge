"""Deterministic generated-project compatibility validation.

This module validates an in-memory firmware project before build or flash.  It
performs no filesystem access, subprocess execution, network calls, or hardware
communication.  Validation failures are represented as structured issues and
are never raised by the public validation methods.

Example::

    validator = BoardValidator()
    result = validator.validate(
        {
            "target_board": "Arduino Uno",
            "framework": "Arduino",
            "metadata": {
                "firmware_size_bytes": 24_000,
                "ram_usage_bytes": 1_200,
                "gpio_pins": [2, 13],
                "peripherals": ["UART", "PWM"],
            },
        }
    )
    if not result.compatible:
        print(result.summary())
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, unique
from types import MappingProxyType
from typing import Any, ClassVar

__all__ = [
    "BoardValidationCategory",
    "BoardValidationIssue",
    "BoardValidationResult",
    "BoardValidationSeverity",
    "BoardValidator",
]


@unique
class BoardValidationSeverity(str, Enum):
    """Severity assigned to a board compatibility issue."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@unique
class BoardValidationCategory(str, Enum):
    """Board compatibility domain associated with an issue."""

    BOARD = "BOARD"
    FRAMEWORK = "FRAMEWORK"
    FLASH = "FLASH"
    RAM = "RAM"
    GPIO = "GPIO"
    PERIPHERAL = "PERIPHERAL"
    METADATA = "METADATA"


@dataclass(frozen=True, slots=True)
class BoardValidationIssue:
    """One deterministic board compatibility finding."""

    category: BoardValidationCategory
    severity: BoardValidationSeverity
    message: str
    recommendation: str
    rule_id: str


@dataclass(frozen=True, slots=True)
class BoardValidationResult:
    """Immutable aggregate returned by every public validation method."""

    compatible: bool
    issues: tuple[BoardValidationIssue, ...]
    warnings: tuple[BoardValidationIssue, ...]
    errors: tuple[BoardValidationIssue, ...]

    @classmethod
    def from_issues(
        cls,
        issues: Iterable[BoardValidationIssue],
    ) -> BoardValidationResult:
        """Build a consistently classified result from ``issues``."""

        collected = tuple(issues)
        warnings = tuple(
            issue
            for issue in collected
            if issue.severity is BoardValidationSeverity.WARNING
        )
        errors = tuple(
            issue
            for issue in collected
            if issue.severity is BoardValidationSeverity.ERROR
        )
        return cls(
            compatible=not errors,
            issues=collected,
            warnings=warnings,
            errors=errors,
        )

    @property
    def is_valid(self) -> bool:
        """Compatibility alias suitable for generic validation pipelines."""

        return self.compatible

    @property
    def valid(self) -> bool:
        """Short compatibility alias for callers using ``valid`` results."""

        return self.compatible

    def has_errors(self) -> bool:
        """Return whether any blocking incompatibility was found."""

        return bool(self.errors)

    def summary(self) -> str:
        """Return a stable one-line summary."""

        info_count = sum(
            issue.severity is BoardValidationSeverity.INFO
            for issue in self.issues
        )
        state = "COMPATIBLE" if self.compatible else "INCOMPATIBLE"
        return (
            f"{state}: {len(self.issues)} issue(s) "
            f"(info={info_count}, warnings={len(self.warnings)}, "
            f"errors={len(self.errors)})"
        )


@dataclass(frozen=True, slots=True)
class _BoardCapabilities:
    """Normalized immutable board definition used by the rule engine."""

    name: str
    frameworks: frozenset[str]
    flash_bytes: int
    ram_bytes: int
    peripherals: frozenset[str]
    valid_gpio_numbers: frozenset[int] = frozenset()
    valid_gpio_names: frozenset[str] = frozenset()
    reserved_gpio_numbers: frozenset[int] = frozenset()
    gpio_name_pattern: str | None = None


def _as_text(value: object) -> str | None:
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _normalize_identifier(value: object) -> str:
    text = _as_text(value)
    if text is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def _normalize_pin_name(value: object) -> str:
    return _normalize_identifier(value).upper()


def _capabilities(
    name: str,
    *,
    frameworks: Iterable[str],
    flash_bytes: int,
    ram_bytes: int,
    peripherals: Iterable[str],
    valid_gpio_numbers: Iterable[int] = (),
    valid_gpio_names: Iterable[str] = (),
    reserved_gpio_numbers: Iterable[int] = (),
    gpio_name_pattern: str | None = None,
) -> _BoardCapabilities:
    return _BoardCapabilities(
        name=name,
        frameworks=frozenset(_normalize_identifier(item) for item in frameworks),
        flash_bytes=flash_bytes,
        ram_bytes=ram_bytes,
        peripherals=frozenset(
            _normalize_identifier(item) for item in peripherals
        ),
        valid_gpio_numbers=frozenset(valid_gpio_numbers),
        valid_gpio_names=frozenset(
            _normalize_pin_name(item) for item in valid_gpio_names
        ),
        reserved_gpio_numbers=frozenset(reserved_gpio_numbers),
        gpio_name_pattern=gpio_name_pattern,
    )


_STANDARD_PERIPHERALS = ("WiFi", "Bluetooth", "SPI", "I2C", "UART", "ADC", "PWM")
_WIRED_PERIPHERALS = ("SPI", "I2C", "UART", "ADC", "PWM")

_DEFAULT_BOARD_DEFINITIONS: Mapping[str, _BoardCapabilities] = MappingProxyType(
    {
        "arduino uno": _capabilities(
            "Arduino Uno",
            frameworks=("Arduino",),
            flash_bytes=32 * 1024,
            ram_bytes=2 * 1024,
            peripherals=_WIRED_PERIPHERALS,
            valid_gpio_numbers=range(20),
            valid_gpio_names=(
                *(f"D{pin}" for pin in range(14)),
                *(f"A{pin}" for pin in range(6)),
            ),
        ),
        "esp32": _capabilities(
            "ESP32",
            frameworks=("Arduino", "ESP-IDF"),
            flash_bytes=4 * 1024 * 1024,
            ram_bytes=520 * 1024,
            peripherals=_STANDARD_PERIPHERALS,
            valid_gpio_numbers=(
                *range(0, 20),
                *range(21, 24),
                *range(25, 28),
                *range(32, 40),
            ),
            reserved_gpio_numbers=range(6, 12),
        ),
        "esp32 c3": _capabilities(
            "ESP32-C3",
            frameworks=("Arduino", "ESP-IDF"),
            flash_bytes=4 * 1024 * 1024,
            ram_bytes=400 * 1024,
            peripherals=_STANDARD_PERIPHERALS,
            valid_gpio_numbers=range(22),
            reserved_gpio_numbers=range(12, 18),
        ),
        "esp32 s3": _capabilities(
            "ESP32-S3",
            frameworks=("Arduino", "ESP-IDF"),
            flash_bytes=8 * 1024 * 1024,
            ram_bytes=512 * 1024,
            peripherals=_STANDARD_PERIPHERALS,
            valid_gpio_numbers=range(49),
            reserved_gpio_numbers=range(26, 33),
        ),
        "stm32": _capabilities(
            "STM32",
            frameworks=("Arduino", "STM32Cube"),
            flash_bytes=512 * 1024,
            ram_bytes=128 * 1024,
            peripherals=_WIRED_PERIPHERALS,
            gpio_name_pattern=r"P[A-K](?:[0-9]|1[0-5])",
        ),
    }
)

_BOARD_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "arduinouno": "arduino uno",
        "uno": "arduino uno",
        "unor3": "arduino uno",
        "esp32": "esp32",
        "esp32c3": "esp32 c3",
        "espc3": "esp32 c3",
        "esp32s3": "esp32 s3",
        "esps3": "esp32 s3",
        "stm32": "stm32",
    }
)


class BoardValidator:
    """Pure rule engine for board, framework, memory, GPIO, and peripherals.

    ``board_database`` may contain :class:`_BoardCapabilities` instances or
    mappings with ``frameworks``, ``flash_bytes``, ``ram_bytes``,
    ``peripherals``, and optional GPIO fields.  Invalid injected definitions
    are ignored deterministically rather than causing validation-time errors.
    """

    _BOARD_FIELDS: ClassVar[tuple[str, ...]] = (
        "target_board",
        "board",
        "board_name",
        "board_id",
        "board_type",
        "name",
    )
    _FRAMEWORK_FIELDS: ClassVar[tuple[str, ...]] = (
        "framework",
        "target_framework",
    )
    _GPIO_FIELDS: ClassVar[tuple[str, ...]] = (
        "gpio_usage",
        "gpio_pins",
        "used_gpio",
        "used_pins",
        "pins",
    )
    _PERIPHERAL_FIELDS: ClassVar[tuple[str, ...]] = (
        "peripherals",
        "required_peripherals",
        "used_peripherals",
    )
    _FLASH_FIELDS: ClassVar[tuple[str, ...]] = (
        "firmware_size_bytes",
        "flash_usage_bytes",
        "binary_size_bytes",
        "program_size_bytes",
        "build_size_bytes",
        "size_bytes",
        "estimated_flash_size",
        "firmware_size",
        "flash_size",
        "flash_usage",
        "binary_size",
        "program_size",
    )
    _RAM_FIELDS: ClassVar[tuple[str, ...]] = (
        "ram_usage_bytes",
        "estimated_ram_bytes",
        "estimated_ram_usage",
        "estimated_memory_usage",
        "memory_usage_bytes",
        "ram_usage",
        "ram_size",
    )

    def __init__(
        self,
        board_database: Mapping[str, object] | None = None,
    ) -> None:
        if board_database is None:
            boards = dict(_DEFAULT_BOARD_DEFINITIONS)
        else:
            boards = _normalize_board_database(board_database)
        self._boards: Mapping[str, _BoardCapabilities] = MappingProxyType(boards)

    def validate_board(self, board: object) -> BoardValidationResult:
        """Validate that ``board`` identifies a configured board."""

        board_name = _extract_text(board, self._BOARD_FIELDS)
        if board_name is None or _is_unknown(board_name):
            return BoardValidationResult.from_issues(
                (
                    _issue(
                        BoardValidationCategory.BOARD,
                        "PF-BOARD-001",
                        "Target board metadata is absent or unresolved.",
                        "Select a board from the PromptForge board database.",
                    ),
                )
            )

        if self._resolve_board(board_name) is None:
            return BoardValidationResult.from_issues(
                (
                    _issue(
                        BoardValidationCategory.BOARD,
                        "PF-BOARD-002",
                        f"Board {board_name!r} is not in the PromptForge board database.",
                        "Use a supported board or add a reviewed board definition.",
                    ),
                )
            )
        return BoardValidationResult.from_issues(())

    def validate_framework(
        self,
        board: object,
        framework: object | None = None,
    ) -> BoardValidationResult:
        """Validate framework support for the selected board."""

        capabilities, failure = self._require_board(board)
        if failure is not None:
            return failure
        assert capabilities is not None

        raw_framework = (
            _as_text(framework)
            if framework is not None
            else _extract_text(board, self._FRAMEWORK_FIELDS)
        )
        if raw_framework is None or _is_unknown(raw_framework):
            return BoardValidationResult.from_issues(
                (
                    _issue(
                        BoardValidationCategory.FRAMEWORK,
                        "PF-BOARD-003",
                        "Firmware framework metadata is absent or unresolved.",
                        "Select a framework supported by the target board.",
                    ),
                )
            )

        normalized = _normalize_identifier(raw_framework)
        if normalized not in capabilities.frameworks:
            supported = ", ".join(
                sorted(_display_identifier(item) for item in capabilities.frameworks)
            )
            return BoardValidationResult.from_issues(
                (
                    _issue(
                        BoardValidationCategory.FRAMEWORK,
                        "PF-BOARD-004",
                        (
                            f"Framework {raw_framework!r} is not supported by "
                            f"{capabilities.name}."
                        ),
                        f"Use one of the supported frameworks: {supported}.",
                    ),
                )
            )
        return BoardValidationResult.from_issues(())

    def validate_gpio_usage(
        self,
        board: object,
        gpio_usage: object | None = None,
    ) -> BoardValidationResult:
        """Validate referenced GPIO pins and report reserved-pin warnings."""

        capabilities, failure = self._require_board(board)
        if failure is not None:
            return failure
        assert capabilities is not None

        raw_usage = (
            gpio_usage
            if gpio_usage is not None
            else _extract_value(board, self._GPIO_FIELDS)
        )
        if raw_usage is None:
            return BoardValidationResult.from_issues(())

        pins, malformed = _coerce_pins(raw_usage)
        issues: list[BoardValidationIssue] = []
        if malformed:
            issues.append(
                _issue(
                    BoardValidationCategory.METADATA,
                    "PF-BOARD-000",
                    "GPIO usage metadata contains values that are not valid pin identifiers.",
                    "Provide GPIO pins as integers or board pin names.",
                )
            )

        for pin in pins:
            number, name = _parse_pin(pin)
            if not _pin_is_valid(capabilities, number, name):
                issues.append(
                    _issue(
                        BoardValidationCategory.GPIO,
                        "PF-BOARD-007",
                        f"Pin {pin!r} is not a valid GPIO on {capabilities.name}.",
                        "Use a GPIO exposed and supported by the selected board.",
                    )
                )
            elif number in capabilities.reserved_gpio_numbers:
                issues.append(
                    _issue(
                        BoardValidationCategory.GPIO,
                        "PF-BOARD-008",
                        (
                            f"GPIO {number} is reserved or commonly connected to "
                            f"on-board flash on {capabilities.name}."
                        ),
                        "Select a non-reserved GPIO unless the exact board design proves it safe.",
                        severity=BoardValidationSeverity.WARNING,
                    )
                )
        return BoardValidationResult.from_issues(_deduplicate(issues))

    def validate_peripherals(
        self,
        board: object,
        peripherals: object | None = None,
    ) -> BoardValidationResult:
        """Validate WiFi, Bluetooth, SPI, I2C, UART, ADC, and PWM support."""

        capabilities, failure = self._require_board(board)
        if failure is not None:
            return failure
        assert capabilities is not None

        raw_peripherals = (
            peripherals
            if peripherals is not None
            else _extract_value(board, self._PERIPHERAL_FIELDS)
        )
        if raw_peripherals is None:
            flagged = _extract_peripheral_flags(board)
            if not flagged:
                return BoardValidationResult.from_issues(())
            raw_peripherals = flagged
        names, malformed = _coerce_names(raw_peripherals)
        issues: list[BoardValidationIssue] = []
        if malformed:
            issues.append(
                _issue(
                    BoardValidationCategory.METADATA,
                    "PF-BOARD-000",
                    "Peripheral metadata must contain string identifiers.",
                    "Provide peripherals as a string sequence or enabled-name mapping.",
                )
            )

        unsupported = sorted(
            {
                _normalize_identifier(name)
                for name in names
                if _normalize_identifier(name) not in capabilities.peripherals
            }
        )
        for peripheral in unsupported:
            issues.append(
                _issue(
                    BoardValidationCategory.PERIPHERAL,
                    "PF-BOARD-009",
                    (
                        f"Peripheral {_display_identifier(peripheral)} is not "
                        f"supported by {capabilities.name}."
                    ),
                    "Remove the peripheral or select hardware that provides it.",
                )
            )
        return BoardValidationResult.from_issues(issues)

    def validate_memory_requirements(
        self,
        board: object,
        firmware_size: object | None = None,
        ram_usage: object | None = None,
    ) -> BoardValidationResult:
        """Validate estimated flash and RAM requirements in bytes."""

        capabilities, failure = self._require_board(board)
        if failure is not None:
            return failure
        assert capabilities is not None

        raw_flash = (
            firmware_size
            if firmware_size is not None
            else _extract_value(board, self._FLASH_FIELDS)
        )
        raw_ram = (
            ram_usage
            if ram_usage is not None
            else _extract_value(board, self._RAM_FIELDS)
        )
        flash, flash_invalid = _nonnegative_integer(raw_flash)
        ram, ram_invalid = _nonnegative_integer(raw_ram)
        issues: list[BoardValidationIssue] = []

        if flash_invalid:
            issues.append(
                _invalid_memory_issue("firmware/flash size")
            )
        elif flash is not None and flash > capabilities.flash_bytes:
            issues.append(
                _issue(
                    BoardValidationCategory.FLASH,
                    "PF-BOARD-005",
                    (
                        f"Firmware requires {flash} bytes of flash, exceeding "
                        f"{capabilities.name}'s {capabilities.flash_bytes}-byte limit."
                    ),
                    "Reduce firmware size or select a board with more flash.",
                )
            )

        if ram_invalid:
            issues.append(_invalid_memory_issue("RAM usage"))
        elif ram is not None and ram > capabilities.ram_bytes:
            issues.append(
                _issue(
                    BoardValidationCategory.RAM,
                    "PF-BOARD-006",
                    (
                        f"Firmware requires {ram} bytes of RAM, exceeding "
                        f"{capabilities.name}'s {capabilities.ram_bytes}-byte limit."
                    ),
                    "Reduce static/runtime memory requirements or select a board with more RAM.",
                )
            )
        return BoardValidationResult.from_issues(issues)

    def validate(
        self,
        project: object,
        selected_board: object | None = None,
    ) -> BoardValidationResult:
        """Validate all available project metadata against ``selected_board``.

        When ``selected_board`` is omitted, ``project.target_board`` (or its
        mapping equivalent) is used.  If both are present, a mismatch is a
        blocking issue because building for one target and flashing another is
        unsafe.
        """

        if project is None:
            return BoardValidationResult.from_issues(
                (
                    _issue(
                        BoardValidationCategory.METADATA,
                        "PF-BOARD-000",
                        "Generated project metadata is absent.",
                        "Provide the generated project before board validation.",
                    ),
                )
            )

        project_board = _extract_text(project, self._BOARD_FIELDS)
        selected_name = (
            _extract_text(selected_board, self._BOARD_FIELDS)
            if selected_board is not None
            else project_board
        )
        validation_context = _merge_context(project, selected_board, selected_name)
        issues: list[BoardValidationIssue] = []

        if selected_board is not None and project_board and selected_name:
            project_capabilities = self._resolve_board(project_board)
            selected_capabilities = self._resolve_board(selected_name)
            if (
                project_capabilities is not None
                and selected_capabilities is not None
                and project_capabilities.name != selected_capabilities.name
            ):
                issues.append(
                    _issue(
                        BoardValidationCategory.BOARD,
                        "PF-BOARD-010",
                        (
                            f"Generated project targets {project_capabilities.name}, "
                            f"but {selected_capabilities.name} was selected."
                        ),
                        "Regenerate for the selected board or select the project's target board.",
                    )
                )

        issues.extend(self.validate_board(validation_context).issues)
        if not any(issue.category is BoardValidationCategory.BOARD for issue in issues):
            issues.extend(self.validate_framework(validation_context).issues)
            issues.extend(self.validate_memory_requirements(validation_context).issues)
            issues.extend(self.validate_gpio_usage(validation_context).issues)
            issues.extend(self.validate_peripherals(validation_context).issues)
        return BoardValidationResult.from_issues(_deduplicate(issues))

    def _resolve_board(self, board_name: str) -> _BoardCapabilities | None:
        normalized = _normalize_identifier(board_name)
        key = _BOARD_ALIASES.get(normalized, _normalize_words(board_name))
        capabilities = self._boards.get(key)
        if capabilities is not None:
            return capabilities
        return next(
            (
                item
                for item in self._boards.values()
                if _normalize_identifier(item.name) == normalized
            ),
            None,
        )

    def _require_board(
        self,
        board: object,
    ) -> tuple[_BoardCapabilities | None, BoardValidationResult | None]:
        result = self.validate_board(board)
        if not result.compatible:
            return None, result
        board_name = _extract_text(board, self._BOARD_FIELDS)
        if board_name is None:
            return None, result
        return self._resolve_board(board_name), None


def _normalize_board_database(
    database: Mapping[str, object],
) -> dict[str, _BoardCapabilities]:
    if not isinstance(database, Mapping):
        return {}
    normalized: dict[str, _BoardCapabilities] = {}
    for raw_name, raw_definition in database.items():
        name = _as_text(raw_name)
        if name is None:
            continue
        if isinstance(raw_definition, _BoardCapabilities):
            normalized[_normalize_words(name)] = raw_definition
            continue
        if not isinstance(raw_definition, Mapping):
            continue
        definition = _definition_from_mapping(name, raw_definition)
        if definition is not None:
            normalized[_normalize_words(name)] = definition
    return normalized


def _definition_from_mapping(
    name: str,
    data: Mapping[str, object],
) -> _BoardCapabilities | None:
    frameworks, frameworks_invalid = _coerce_names(data.get("frameworks", ()))
    peripherals, peripherals_invalid = _coerce_names(data.get("peripherals", ()))
    flash, flash_invalid = _nonnegative_integer(
        data.get(
            "flash_bytes",
            data.get("flash_size_bytes", data.get("flash_size")),
        )
    )
    ram, ram_invalid = _nonnegative_integer(
        data.get("ram_bytes", data.get("ram_size_bytes", data.get("ram_size")))
    )
    if (
        frameworks_invalid
        or peripherals_invalid
        or flash_invalid
        or ram_invalid
        or flash is None
        or ram is None
    ):
        return None

    valid_numbers, numbers_invalid = _coerce_integer_set(
        data.get("valid_gpio_numbers", ())
    )
    reserved_numbers, reserved_invalid = _coerce_integer_set(
        data.get("reserved_gpio_numbers", ())
    )
    valid_names, names_invalid = _coerce_names(data.get("valid_gpio_names", ()))
    pattern = data.get("gpio_name_pattern")
    if pattern is not None and not isinstance(pattern, str):
        return None
    if numbers_invalid or reserved_invalid or names_invalid:
        return None
    return _capabilities(
        name,
        frameworks=frameworks,
        flash_bytes=flash,
        ram_bytes=ram,
        peripherals=peripherals,
        valid_gpio_numbers=valid_numbers,
        valid_gpio_names=valid_names,
        reserved_gpio_numbers=reserved_numbers,
        gpio_name_pattern=pattern,
    )


def _merge_context(
    project: object,
    selected_board: object | None,
    selected_name: str | None,
) -> Mapping[str, object]:
    context: dict[str, object] = {}
    for field_names in (
        BoardValidator._FRAMEWORK_FIELDS,
        BoardValidator._GPIO_FIELDS,
        BoardValidator._PERIPHERAL_FIELDS,
        BoardValidator._FLASH_FIELDS,
        BoardValidator._RAM_FIELDS,
    ):
        for field in field_names:
            value = _extract_value(project, (field,))
            if value is not None:
                context[field] = value
                break

    if not any(field in context for field in BoardValidator._PERIPHERAL_FIELDS):
        flagged = _extract_peripheral_flags(project)
        if flagged:
            context["peripherals"] = flagged

    if selected_board is not None:
        for field_names in (
            BoardValidator._GPIO_FIELDS,
            BoardValidator._PERIPHERAL_FIELDS,
            BoardValidator._FLASH_FIELDS,
            BoardValidator._RAM_FIELDS,
        ):
            for field in field_names:
                value = _extract_value(selected_board, (field,))
                if value is not None:
                    context[field] = value
                    break
    if selected_name is not None:
        context["target_board"] = selected_name
    return MappingProxyType(context)


def _extract_text(source: object, fields: Iterable[str]) -> str | None:
    if isinstance(source, (str, Enum)):
        return _as_text(source)
    value = _extract_value(source, fields)
    return _as_text(value)


def _extract_value(source: object, fields: Iterable[str]) -> object | None:
    if source is None:
        return None
    for field in fields:
        value = _get_field(source, field)
        if value is not None:
            return value
    nested = _get_field(source, "metadata")
    if nested is not None and nested is not source:
        for field in fields:
            value = _get_field(nested, field)
            if value is not None:
                return value
        for container_name in ("memory", "memory_requirements", "resources"):
            container = _get_field(nested, container_name)
            if container is None:
                continue
            for field in fields:
                value = _get_field(container, field)
                if value is not None:
                    return value
    for container_name in ("memory", "memory_requirements", "resources"):
        container = _get_field(source, container_name)
        if container is None or container is source:
            continue
        for field in fields:
            value = _get_field(container, field)
            if value is not None:
                return value
    return None


def _extract_peripheral_flags(source: object) -> tuple[str, ...]:
    candidates = (source, _get_field(source, "metadata"))
    found: list[str] = []
    for candidate in candidates:
        if candidate is None:
            continue
        for peripheral in _STANDARD_PERIPHERALS:
            field = _normalize_identifier(peripheral)
            value = _get_field_case_insensitive(candidate, field)
            if value is True and peripheral not in found:
                found.append(peripheral)
    return tuple(found)


def _get_field(source: object, field: str) -> object | None:
    if isinstance(source, Mapping):
        return source.get(field)
    try:
        return getattr(source, field, None)
    except Exception:
        return None


def _get_field_case_insensitive(source: object, field: str) -> object | None:
    if isinstance(source, Mapping):
        normalized = _normalize_identifier(field)
        for key, value in source.items():
            if _normalize_identifier(key) == normalized:
                return value
        return None
    return _get_field(source, field)


def _normalize_words(value: object) -> str:
    text = _as_text(value)
    if text is None:
        return ""
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _is_unknown(value: str) -> bool:
    return _normalize_identifier(value) in {"", "unknown", "none", "null"}


def _display_identifier(value: str) -> str:
    names = {
        "arduino": "Arduino",
        "espidf": "ESP-IDF",
        "stm32cube": "STM32Cube",
        "wifi": "WiFi",
        "bluetooth": "Bluetooth",
        "spi": "SPI",
        "i2c": "I2C",
        "uart": "UART",
        "adc": "ADC",
        "pwm": "PWM",
    }
    return names.get(value, value)


def _coerce_names(value: object) -> tuple[tuple[str, ...], bool]:
    if value is None:
        return (), False
    if isinstance(value, (str, Enum)):
        text = _as_text(value)
        return ((text,) if text else ()), text is None
    if isinstance(value, Mapping):
        items = tuple(key for key, enabled in value.items() if bool(enabled))
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        items = tuple(value)
    else:
        return (), True
    names: list[str] = []
    malformed = False
    for item in items:
        text = _as_text(item)
        if text is None:
            malformed = True
        else:
            names.append(text)
    return tuple(names), malformed


def _coerce_pins(value: object) -> tuple[tuple[object, ...], bool]:
    if isinstance(value, Mapping):
        items = tuple(key for key, enabled in value.items() if bool(enabled))
    elif isinstance(value, (str, int, Enum)) and not isinstance(value, bool):
        items = (value,)
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        items = tuple(value)
    else:
        return (), True
    valid: list[object] = []
    malformed = False
    for item in items:
        if isinstance(item, Enum):
            item = item.value
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            malformed = True
        elif isinstance(item, str) and not item.strip():
            malformed = True
        else:
            valid.append(item)
    return tuple(valid), malformed


def _parse_pin(pin: object) -> tuple[int | None, str | None]:
    if isinstance(pin, int) and not isinstance(pin, bool):
        return pin, None
    text = _as_text(pin)
    if text is None:
        return None, None
    normalized = _normalize_pin_name(text)
    numeric_match = re.fullmatch(r"(?:GPIO|D)?([0-9]+)", normalized)
    if numeric_match:
        return int(numeric_match.group(1)), normalized
    return None, normalized


def _pin_is_valid(
    board: _BoardCapabilities,
    number: int | None,
    name: str | None,
) -> bool:
    if number is not None and number in board.valid_gpio_numbers:
        return True
    if name is not None and name in board.valid_gpio_names:
        return True
    if name is not None and board.gpio_name_pattern is not None:
        try:
            return re.fullmatch(board.gpio_name_pattern, name, re.I) is not None
        except re.error:
            return False
    return False


def _nonnegative_integer(value: object) -> tuple[int | None, bool]:
    if value is None:
        return None, False
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None, True
    return value, False


def _coerce_integer_set(value: object) -> tuple[tuple[int, ...], bool]:
    if value is None:
        return (), False
    if isinstance(value, int) and not isinstance(value, bool):
        items = (value,)
    elif isinstance(value, Iterable) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        items = tuple(value)
    else:
        return (), True
    if any(isinstance(item, bool) or not isinstance(item, int) for item in items):
        return (), True
    return tuple(items), False


def _invalid_memory_issue(label: str) -> BoardValidationIssue:
    return _issue(
        BoardValidationCategory.METADATA,
        "PF-BOARD-000",
        f"{label.capitalize()} must be a non-negative integer byte count.",
        "Provide deterministic memory estimates in bytes.",
    )


def _issue(
    category: BoardValidationCategory,
    rule_id: str,
    message: str,
    recommendation: str,
    *,
    severity: BoardValidationSeverity = BoardValidationSeverity.ERROR,
) -> BoardValidationIssue:
    return BoardValidationIssue(
        category=category,
        severity=severity,
        message=message,
        recommendation=recommendation,
        rule_id=rule_id,
    )


def _deduplicate(
    issues: Iterable[BoardValidationIssue],
) -> tuple[BoardValidationIssue, ...]:
    seen: set[tuple[str, str]] = set()
    result: list[BoardValidationIssue] = []
    for issue in issues:
        key = (issue.rule_id, issue.message)
        if key not in seen:
            seen.add(key)
            result.append(issue)
    return tuple(result)
