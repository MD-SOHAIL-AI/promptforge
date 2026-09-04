"""Deterministic hardware planning constraints backed by board metadata.

The engine validates structured planner/generator requirements before code is
generated or built. It performs no hardware access, subprocess execution, or
filesystem mutation. Board capabilities and limits come exclusively from the
compatibility layer's normalized JSON metadata.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum, unique
from types import MappingProxyType
from typing import Any

from ..compatibility.compatibility_matrix import Board, CompatibilityMatrix

__all__ = [
    "Constraint",
    "ConstraintCategory",
    "ConstraintEngine",
    "ConstraintSeverity",
    "ConstraintViolation",
    "HardwareRequirements",
    "ResourceEstimate",
]


@unique
class ConstraintSeverity(str, Enum):
    """Severity of a hardware planning constraint violation."""

    WARNING = "WARNING"
    ERROR = "ERROR"


@unique
class ConstraintCategory(str, Enum):
    """Hardware resource domain constrained by a rule."""

    BOARD = "BOARD"
    FRAMEWORK = "FRAMEWORK"
    GPIO = "GPIO"
    FLASH = "FLASH"
    RAM = "RAM"
    PERIPHERAL = "PERIPHERAL"
    LIBRARY = "LIBRARY"
    METADATA = "METADATA"


@dataclass(frozen=True, slots=True)
class Constraint:
    """One immutable hardware constraint definition."""

    rule_id: str
    category: ConstraintCategory
    description: str
    severity: ConstraintSeverity = ConstraintSeverity.ERROR
    limit: int | str | tuple[int | str, ...] | None = None


@dataclass(frozen=True, slots=True)
class ConstraintViolation:
    """One failed hardware constraint with actionable context."""

    constraint: Constraint
    message: str
    actual: object = None
    recommendation: str = "Revise the hardware plan to satisfy this constraint."

    @property
    def rule_id(self) -> str:
        return self.constraint.rule_id

    @property
    def category(self) -> ConstraintCategory:
        return self.constraint.category

    @property
    def severity(self) -> ConstraintSeverity:
        return self.constraint.severity

    @property
    def limit(self) -> object:
        return self.constraint.limit


@dataclass(frozen=True, slots=True)
class ResourceEstimate:
    """Normalized resource requirements extracted from a hardware plan."""

    flash_bytes: int
    ram_bytes: int
    gpio_pins: tuple[int | str, ...]
    peripheral_instances: Mapping[str, int]
    libraries: tuple[str, ...]

    @property
    def gpio_count(self) -> int:
        return len({_normalize_pin(pin) for pin in self.gpio_pins})


@dataclass(frozen=True, slots=True)
class HardwareRequirements:
    """Typed domain input accepted by hardware constraint validation.

    Control-plane metadata must never be passed through this contract.  The
    only compatibility path is :meth:`from_mapping`, which reads an explicit
    hardware-requirements object rather than recursively interpreting arbitrary
    plan or project metadata.
    """

    framework: str | None = None
    flash_bytes: int = 0
    ram_bytes: int = 0
    gpio_pins: tuple[int | str, ...] = ()
    peripheral_instances: Mapping[str, int] = MappingProxyType({})
    libraries: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.framework is not None and (not isinstance(self.framework, str) or not self.framework.strip()):
            raise ValueError("hardware framework must be a non-empty string")
        if isinstance(self.flash_bytes, bool) or not isinstance(self.flash_bytes, int) or self.flash_bytes < 0:
            raise ValueError("hardware flash_bytes must be a non-negative integer")
        if isinstance(self.ram_bytes, bool) or not isinstance(self.ram_bytes, int) or self.ram_bytes < 0:
            raise ValueError("hardware ram_bytes must be a non-negative integer")
        pins, malformed_pins = _coerce_pins(self.gpio_pins)
        if malformed_pins:
            raise ValueError("hardware gpio_pins are malformed")
        peripherals, malformed_peripherals = _extract_peripheral_counts(
            {"peripheral_instances": self.peripheral_instances},
            ("peripheral_instances",),
        )
        if malformed_peripherals:
            raise ValueError("hardware peripheral_instances are malformed")
        libraries, malformed_libraries = _coerce_names(self.libraries)
        if malformed_libraries:
            raise ValueError("hardware libraries are malformed")
        object.__setattr__(self, "gpio_pins", tuple(pins))
        object.__setattr__(self, "peripheral_instances", MappingProxyType(dict(sorted(peripherals.items()))))
        object.__setattr__(self, "libraries", tuple(dict.fromkeys(libraries)))

    @classmethod
    def from_mapping(
        cls,
        value: object,
        *,
        framework: str | None = None,
    ) -> HardwareRequirements:
        if value is None:
            return cls(framework=framework)
        if isinstance(value, HardwareRequirements):
            return value
        if not isinstance(value, Mapping):
            raise ValueError("hardware_requirements must be a mapping")
        resources = value.get("resources")
        source = resources if isinstance(resources, Mapping) else value
        flash = source.get("flash_bytes", source.get("estimated_flash_bytes", 0))
        ram = source.get("ram_bytes", source.get("estimated_ram_bytes", 0))
        pins = source.get("gpio_pins", source.get("pins", ()))
        peripherals = source.get("peripheral_instances", source.get("peripherals", {}))
        libraries = source.get("libraries", source.get("dependencies", ()))
        resolved_framework = value.get("framework", framework)
        return cls(
            framework=resolved_framework if isinstance(resolved_framework, str) else framework,
            flash_bytes=flash,  # type: ignore[arg-type]
            ram_bytes=ram,  # type: ignore[arg-type]
            gpio_pins=tuple(pins) if isinstance(pins, Sequence) and not isinstance(pins, (str, bytes, bytearray)) else (pins,),
            peripheral_instances=peripherals if isinstance(peripherals, Mapping) else {str(item): 1 for item in peripherals} if isinstance(peripherals, Sequence) and not isinstance(peripherals, (str, bytes, bytearray)) else peripherals,  # type: ignore[arg-type]
            libraries=tuple(libraries) if isinstance(libraries, Sequence) and not isinstance(libraries, (str, bytes, bytearray)) else (libraries,),
        )

    def resource_estimate(self) -> ResourceEstimate:
        return ResourceEstimate(
            flash_bytes=self.flash_bytes,
            ram_bytes=self.ram_bytes,
            gpio_pins=self.gpio_pins,
            peripheral_instances=self.peripheral_instances,
            libraries=self.libraries,
        )


class ConstraintEngine:
    """Validate planner requirements against metadata-defined board limits."""

    _FLASH_FIELDS = (
        "flash_bytes",
        "firmware_size_bytes",
        "estimated_flash_bytes",
        "estimated_flash_size",
        "flash_usage",
        "program_size",
    )
    _RAM_FIELDS = (
        "ram_bytes",
        "ram_usage_bytes",
        "estimated_ram_bytes",
        "estimated_ram_usage",
        "memory_usage_bytes",
        "ram_usage",
    )
    _GPIO_FIELDS = ("gpio_pins", "gpio_usage", "used_gpio", "used_pins", "pins")
    _PERIPHERAL_FIELDS = (
        "peripheral_instances",
        "peripherals",
        "required_peripherals",
        "used_peripherals",
    )
    _LIBRARY_FIELDS = ("libraries", "dependencies", "lib_deps")

    def __init__(self, compatibility: CompatibilityMatrix | None = None) -> None:
        self._compatibility = compatibility or CompatibilityMatrix()

    def estimate_resource_usage(self, requirements: object) -> ResourceEstimate:
        """Normalize explicit resource estimates from planner metadata.

        Nested ``metadata``, ``resources``, and ``resource_usage`` mappings are
        supported. Component sequences may declare their own flash/RAM values;
        these are added to top-level estimates. Invalid or negative values are
        ignored here and reported by :meth:`validate_constraints`.
        """

        if isinstance(requirements, HardwareRequirements):
            return requirements.resource_estimate()
        flash = _first_nonnegative_integer(requirements, self._FLASH_FIELDS) or 0
        ram = _first_nonnegative_integer(requirements, self._RAM_FIELDS) or 0
        pins = _extract_pins(requirements, self._GPIO_FIELDS)[0]
        peripherals = _extract_peripheral_counts(requirements, self._PERIPHERAL_FIELDS)[0]
        libraries = _extract_names(requirements, self._LIBRARY_FIELDS)[0]

        components = _extract_value(requirements, ("components", "modules", "features"))
        if isinstance(components, Sequence) and not isinstance(
            components, (str, bytes, bytearray)
        ):
            for component in components:
                flash += _first_nonnegative_integer(component, self._FLASH_FIELDS) or 0
                ram += _first_nonnegative_integer(component, self._RAM_FIELDS) or 0
                component_pins, _ = _extract_pins(component, self._GPIO_FIELDS)
                pins += component_pins
                component_peripherals, _ = _extract_peripheral_counts(
                    component, self._PERIPHERAL_FIELDS
                )
                for name, count in component_peripherals.items():
                    peripherals[name] = peripherals.get(name, 0) + count
                component_libraries, _ = _extract_names(
                    component, self._LIBRARY_FIELDS
                )
                libraries += component_libraries

        return ResourceEstimate(
            flash_bytes=flash,
            ram_bytes=ram,
            gpio_pins=tuple(pins),
            peripheral_instances=MappingProxyType(dict(sorted(peripherals.items()))),
            libraries=tuple(dict.fromkeys(libraries)),
        )

    def check_gpio_constraints(
        self,
        board: object,
        gpio_usage: object,
    ) -> tuple[ConstraintViolation, ...]:
        """Validate GPIO identifiers, duplicates, count, and reservations."""

        resolved = self._compatibility.get_board(board)  # type: ignore[arg-type]
        if resolved is None:
            return (_unknown_board_violation(board),)
        nested_usage = _extract_value(gpio_usage, self._GPIO_FIELDS)
        raw_usage = nested_usage if nested_usage is not None else gpio_usage
        pins, malformed = _coerce_pins(raw_usage)
        violations: list[ConstraintViolation] = []
        if malformed:
            violations.append(_invalid_metadata_violation("GPIO usage"))

        valid = {_normalize_pin(pin) for pin in resolved.valid_gpio}
        reserved = {_normalize_pin(pin) for pin in resolved.reserved_gpio}
        input_only = {_normalize_pin(pin) for pin in resolved.input_only_gpio}
        output_pins = _extract_output_pins(raw_usage)
        seen: set[str] = set()
        for pin in pins:
            normalized = _normalize_pin(pin)
            if normalized in seen:
                violations.append(
                    _violation(
                        "PF-CONSTRAINT-006",
                        ConstraintCategory.GPIO,
                        f"GPIO {pin!r} is assigned more than once.",
                        "Assign each physical pin to one planned function.",
                        actual=pin,
                    )
                )
            seen.add(normalized)
            if valid and normalized not in valid:
                violations.append(
                    _violation(
                        "PF-CONSTRAINT-004",
                        ConstraintCategory.GPIO,
                        f"GPIO {pin!r} is not valid for {resolved.board_name}.",
                        "Select a GPIO declared by the target board metadata.",
                        actual=pin,
                        limit=resolved.valid_gpio,
                    )
                )
            elif normalized in reserved:
                violations.append(
                    _violation(
                        "PF-CONSTRAINT-005",
                        ConstraintCategory.GPIO,
                        f"GPIO {pin!r} is reserved on {resolved.board_name}.",
                        "Use a non-reserved pin unless the exact board design is reviewed.",
                        actual=pin,
                        limit=resolved.reserved_gpio,
                        severity=ConstraintSeverity.WARNING,
                    )
                )
            if normalized in input_only and normalized in output_pins:
                violations.append(
                    _violation(
                        "PF-CONSTRAINT-012",
                        ConstraintCategory.GPIO,
                        f"GPIO {pin!r} is input-only on {resolved.board_name}.",
                        "Use the pin only as an input or select an output-capable GPIO.",
                        actual=pin,
                        limit=resolved.input_only_gpio,
                    )
                )

        available_gpio_count = (
            len(valid) if valid else resolved.gpio_count
        )
        if available_gpio_count is not None and len(seen) > available_gpio_count:
            violations.append(
                _violation(
                    "PF-CONSTRAINT-007",
                    ConstraintCategory.GPIO,
                    (
                        f"Plan requires {len(seen)} unique GPIOs, exceeding "
                        f"{resolved.board_name}'s {available_gpio_count}-GPIO limit."
                    ),
                    "Reduce GPIO demand or select a board with more GPIOs.",
                    actual=len(seen),
                    limit=available_gpio_count,
                )
            )
        return _deduplicate(violations)

    def check_memory_constraints(
        self,
        board: object,
        flash_usage: object = 0,
        ram_usage: object = 0,
    ) -> tuple[ConstraintViolation, ...]:
        """Validate non-negative byte estimates against flash and RAM limits."""

        resolved = self._compatibility.get_board(board)  # type: ignore[arg-type]
        if resolved is None:
            return (_unknown_board_violation(board),)
        nested_flash = _extract_value(flash_usage, self._FLASH_FIELDS)
        nested_ram = _extract_value(flash_usage, self._RAM_FIELDS)
        if nested_flash is not None:
            flash_usage = nested_flash
        if nested_ram is not None:
            ram_usage = nested_ram
        violations: list[ConstraintViolation] = []
        flash = _strict_nonnegative_integer(flash_usage)
        ram = _strict_nonnegative_integer(ram_usage)
        if flash is None:
            violations.append(_invalid_metadata_violation("Flash usage"))
        elif resolved.flash_size is not None and flash > resolved.flash_size:
            violations.append(
                _violation(
                    "PF-CONSTRAINT-002",
                    ConstraintCategory.FLASH,
                    (
                        f"Plan requires {flash} flash bytes, exceeding "
                        f"{resolved.board_name}'s {resolved.flash_size}-byte limit."
                    ),
                    "Reduce firmware features or select a board with more flash.",
                    actual=flash,
                    limit=resolved.flash_size,
                )
            )
        if ram is None:
            violations.append(_invalid_metadata_violation("RAM usage"))
        elif resolved.ram_size is not None and ram > resolved.ram_size:
            violations.append(
                _violation(
                    "PF-CONSTRAINT-003",
                    ConstraintCategory.RAM,
                    (
                        f"Plan requires {ram} RAM bytes, exceeding "
                        f"{resolved.board_name}'s {resolved.ram_size}-byte limit."
                    ),
                    "Reduce buffers and static allocation or select a board with more RAM.",
                    actual=ram,
                    limit=resolved.ram_size,
                )
            )
        return tuple(violations)

    def validate_constraints(
        self,
        board: object,
        requirements: object,
        *,
        framework: object | None = None,
    ) -> tuple[ConstraintViolation, ...]:
        """Validate all structured planning constraints for a board."""

        resolved = self._compatibility.get_board(board)  # type: ignore[arg-type]
        if resolved is None:
            return (_unknown_board_violation(board),)
        violations: list[ConstraintViolation] = []

        selected_framework = framework or (
            requirements.framework
            if isinstance(requirements, HardwareRequirements)
            else _extract_value(requirements, ("framework", "target_framework"))
        )
        if selected_framework is not None and not self._compatibility.is_framework_supported(
            resolved, selected_framework  # type: ignore[arg-type]
        ):
            violations.append(
                _violation(
                    "PF-CONSTRAINT-008",
                    ConstraintCategory.FRAMEWORK,
                    f"Framework {selected_framework!r} is unsupported by {resolved.board_name}.",
                    "Select a framework declared in the board metadata.",
                    actual=selected_framework,
                    limit=tuple(item.name for item in resolved.frameworks),
                )
            )

        estimate = self.estimate_resource_usage(requirements)
        if _has_invalid_numeric_value(requirements, self._FLASH_FIELDS):
            violations.append(_invalid_metadata_violation("Flash usage"))
        if _has_invalid_numeric_value(requirements, self._RAM_FIELDS):
            violations.append(_invalid_metadata_violation("RAM usage"))
        violations.extend(
            self.check_memory_constraints(
                resolved, estimate.flash_bytes, estimate.ram_bytes
            )
        )
        violations.extend(self.check_gpio_constraints(resolved, estimate.gpio_pins))

        _, malformed_pins = _extract_pins(requirements, self._GPIO_FIELDS)
        _, malformed_peripherals = _extract_peripheral_counts(
            requirements, self._PERIPHERAL_FIELDS
        )
        _, malformed_libraries = _extract_names(requirements, self._LIBRARY_FIELDS)
        if malformed_pins:
            violations.append(_invalid_metadata_violation("GPIO usage"))
        if malformed_peripherals:
            violations.append(_invalid_metadata_violation("Peripheral requirements"))
        if malformed_libraries:
            violations.append(_invalid_metadata_violation("Library requirements"))

        for peripheral, count in estimate.peripheral_instances.items():
            if not self._compatibility.is_peripheral_supported(resolved, peripheral):
                violations.append(
                    _violation(
                        "PF-CONSTRAINT-009",
                        ConstraintCategory.PERIPHERAL,
                        f"Peripheral {peripheral} is unsupported by {resolved.board_name}.",
                        "Remove the peripheral or select compatible hardware.",
                        actual=peripheral,
                    )
                )
                continue
            limit = resolved.peripheral_limits.get(_normalize_name(peripheral))
            if limit is not None and count > limit:
                violations.append(
                    _violation(
                        "PF-CONSTRAINT-010",
                        ConstraintCategory.PERIPHERAL,
                        (
                            f"Plan requires {count} {peripheral} instances, exceeding "
                            f"{resolved.board_name}'s limit of {limit}."
                        ),
                        "Reduce peripheral instances or select a board with more controllers.",
                        actual=count,
                        limit=limit,
                    )
                )

        for library in estimate.libraries:
            if not self._compatibility.is_library_supported(
                resolved,
                library,
                selected_framework,  # type: ignore[arg-type]
            ):
                violations.append(
                    _violation(
                        "PF-CONSTRAINT-011",
                        ConstraintCategory.LIBRARY,
                        f"Library {library!r} is unsupported by {resolved.board_name}.",
                        "Use a library approved for the selected board and framework.",
                        actual=library,
                    )
                )
        return _deduplicate(violations)


def _extract_value(source: object, fields: Iterable[str]) -> object | None:
    return _extract_value_recursive(source, tuple(fields), active=set())


def _extract_value_recursive(
    source: object,
    fields: tuple[str, ...],
    *,
    active: set[int],
) -> object | None:
    if source is None:
        return None
    identity = id(source)
    if identity in active:
        return None
    active.add(identity)
    try:
        for field in fields:
            value = _get_field(source, field)
            if value is not None:
                return value
        for container_name in (
            "metadata",
            "resources",
            "resource_usage",
            "requirements",
        ):
            nested = _get_field(source, container_name)
            if nested is None or nested is source:
                continue
            value = _extract_value_recursive(nested, fields, active=active)
            if value is not None:
                return value
        return None
    finally:
        active.remove(identity)


def _get_field(source: object, field: str) -> object | None:
    if isinstance(source, Mapping):
        return source.get(field)
    try:
        return getattr(source, field, None)
    except Exception:
        return None


def _first_nonnegative_integer(source: object, fields: Iterable[str]) -> int | None:
    value = _extract_value(source, fields)
    return _strict_nonnegative_integer(value)


def _strict_nonnegative_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _has_invalid_numeric_value(source: object, fields: Iterable[str]) -> bool:
    value = _extract_value(source, fields)
    if value is not None and _strict_nonnegative_integer(value) is None:
        return True
    components = _extract_value(source, ("components", "modules", "features"))
    if isinstance(components, Sequence) and not isinstance(
        components, (str, bytes, bytearray)
    ):
        return any(
            _has_invalid_numeric_value(component, fields)
            for component in components
        )
    return False


def _extract_pins(
    source: object,
    fields: Iterable[str],
) -> tuple[tuple[int | str, ...], bool]:
    value = _extract_value(source, fields)
    if value is None:
        return (), False
    return _coerce_pins(value)


def _coerce_pins(value: object) -> tuple[tuple[int | str, ...], bool]:
    if value is None:
        return (), False
    if isinstance(value, Mapping):
        entries = tuple(key for key, enabled in value.items() if bool(enabled))
    elif isinstance(value, (int, str, Enum)) and not isinstance(value, bool):
        entries = (value,)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        entries = tuple(value)
    else:
        return (), True
    pins: list[int | str] = []
    malformed = False
    for entry in entries:
        if isinstance(entry, Enum):
            entry = entry.value
        if isinstance(entry, bool) or not isinstance(entry, (int, str)):
            malformed = True
        elif isinstance(entry, str) and not entry.strip():
            malformed = True
        else:
            pins.append(entry.strip() if isinstance(entry, str) else entry)
    return tuple(pins), malformed


def _extract_peripheral_counts(
    source: object,
    fields: Iterable[str],
) -> tuple[dict[str, int], bool]:
    value = _extract_value(source, fields)
    if value is None:
        flags = _boolean_capabilities(source)
        return ({name: 1 for name in flags}, False)
    counts: dict[str, int] = {}
    malformed = False
    if isinstance(value, Mapping):
        entries = tuple(value.items())
        for raw_name, raw_count in entries:
            name = _text(raw_name)
            if name is None:
                malformed = True
                continue
            if raw_count is True:
                count = 1
            elif raw_count is False:
                continue
            elif isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count >= 0:
                count = raw_count
            else:
                malformed = True
                continue
            normalized = _display_name(name)
            counts[normalized] = counts.get(normalized, 0) + count
        return counts, malformed
    names, malformed = _coerce_names(value)
    for name in names:
        display = _display_name(name)
        counts[display] = counts.get(display, 0) + 1
    return counts, malformed


def _extract_names(
    source: object,
    fields: Iterable[str],
) -> tuple[tuple[str, ...], bool]:
    value = _extract_value(source, fields)
    if value is None:
        return (), False
    return _coerce_names(value)


def _coerce_names(value: object) -> tuple[tuple[str, ...], bool]:
    if isinstance(value, (str, Enum)):
        entries = (value,)
    elif isinstance(value, Mapping):
        entries = tuple(key for key, enabled in value.items() if bool(enabled))
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        entries = tuple(value)
    else:
        return (), True
    names: list[str] = []
    malformed = False
    for entry in entries:
        name = _text(entry)
        if name is None:
            malformed = True
        else:
            names.append(name)
    return tuple(names), malformed


def _boolean_capabilities(source: object) -> tuple[str, ...]:
    result: list[str] = []
    candidates = (source, _get_field(source, "metadata"))
    legacy_fields = {
        "wifi_required": "WiFi",
        "bluetooth_required": "Bluetooth",
        "adc_required": "ADC",
        "i2c_required": "I2C",
        "i2s_required": "I2S",
        "pwm_required": "PWM",
        "spi_required": "SPI",
        "uart_required": "UART",
        "usb_required": "USB",
        "can_required": "CAN",
    }
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        for key, display in legacy_fields.items():
            if candidate.get(key) is True:
                result.append(display)
    return tuple(dict.fromkeys(result))


def _text(value: object) -> str | None:
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _display_name(value: str) -> str:
    normalized = _normalize_name(value)
    acronyms = {
        "adc": "ADC",
        "bluetooth": "Bluetooth",
        "can": "CAN",
        "i2c": "I2C",
        "i2s": "I2S",
        "pwm": "PWM",
        "spi": "SPI",
        "twai": "TWAI",
        "uart": "UART",
        "usart": "USART",
        "usb": "USB",
        "wifi": "WiFi",
    }
    return acronyms.get(normalized, value.strip())


def _normalize_name(value: object) -> str:
    text = _text(value)
    return re.sub(r"[^a-z0-9]+", "", text.casefold()) if text else ""


def _normalize_pin(value: int | str) -> str:
    if isinstance(value, int):
        return str(value)
    normalized = re.sub(r"[^a-z0-9]+", "", value.casefold())
    numeric = re.fullmatch(r"(?:gpio|d)([0-9]+)", normalized)
    return numeric.group(1) if numeric else normalized


def _extract_output_pins(value: object) -> frozenset[str]:
    if not isinstance(value, Mapping):
        return frozenset()
    result: set[str] = set()
    for pin, mode in value.items():
        if isinstance(pin, bool) or not isinstance(pin, (int, str)):
            continue
        if isinstance(mode, str) and re.search(
            r"\b(?:output|pwm|dac|drive|write)\b", mode, re.I
        ):
            result.add(_normalize_pin(pin))
        elif isinstance(mode, Mapping) and any(
            mode.get(field) is True
            for field in ("output", "pwm", "drive", "write")
        ):
            result.add(_normalize_pin(pin))
    return frozenset(result)


def _unknown_board_violation(board: object) -> ConstraintViolation:
    return _violation(
        "PF-CONSTRAINT-001",
        ConstraintCategory.BOARD,
        f"Board {board!r} is not present in the compatibility matrix.",
        "Select a board defined by PromptForge board metadata.",
        actual=board,
    )


def _invalid_metadata_violation(label: str) -> ConstraintViolation:
    return _violation(
        "PF-CONSTRAINT-000",
        ConstraintCategory.METADATA,
        f"{label} metadata is malformed.",
        "Provide deterministic non-negative counts and valid identifiers.",
    )


def _violation(
    rule_id: str,
    category: ConstraintCategory,
    message: str,
    recommendation: str,
    *,
    actual: object = None,
    limit: int | str | tuple[int | str, ...] | None = None,
    severity: ConstraintSeverity = ConstraintSeverity.ERROR,
) -> ConstraintViolation:
    return ConstraintViolation(
        constraint=Constraint(
            rule_id=rule_id,
            category=category,
            description=message,
            severity=severity,
            limit=limit,
        ),
        message=message,
        actual=actual,
        recommendation=recommendation,
    )


def _deduplicate(
    violations: Iterable[ConstraintViolation],
) -> tuple[ConstraintViolation, ...]:
    seen: set[tuple[str, str]] = set()
    result: list[ConstraintViolation] = []
    for violation in violations:
        key = (violation.rule_id, violation.message)
        if key not in seen:
            seen.add(key)
            result.append(violation)
    return tuple(result)
