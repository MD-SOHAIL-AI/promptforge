from dataclasses import dataclass

import pytest

from backend.hardware.compatibility.compatibility_matrix import CompatibilityMatrix
from backend.hardware.constraints.constraint_engine import (
    ConstraintCategory,
    ConstraintEngine,
    ConstraintSeverity,
    ResourceEstimate,
)


@pytest.fixture
def engine() -> ConstraintEngine:
    return ConstraintEngine()


def test_unknown_board_is_structured_violation(engine: ConstraintEngine) -> None:
    result = engine.validate_constraints("unknown", {})

    assert result[0].rule_id == "PF-CONSTRAINT-001"
    assert result[0].category is ConstraintCategory.BOARD


def test_resource_estimate_normalizes_nested_metadata(
    engine: ConstraintEngine,
) -> None:
    estimate = engine.estimate_resource_usage(
        {
            "metadata": {
                "resources": {
                    "estimated_flash_bytes": 1000,
                    "estimated_ram_bytes": 200,
                    "pins": [2, 4],
                    "peripheral_instances": {"UART": 1, "SPI": 2},
                    "libraries": ["ArduinoJson"],
                }
            }
        }
    )

    assert estimate == ResourceEstimate(
        flash_bytes=1000,
        ram_bytes=200,
        gpio_pins=(2, 4),
        peripheral_instances={"SPI": 2, "UART": 1},
        libraries=("ArduinoJson",),
    )


def test_resource_estimate_aggregates_components(engine: ConstraintEngine) -> None:
    estimate = engine.estimate_resource_usage(
        {
            "flash_bytes": 100,
            "ram_bytes": 20,
            "components": [
                {
                    "flash_bytes": 50,
                    "ram_bytes": 10,
                    "gpio_pins": [2],
                    "peripherals": ["UART"],
                },
                {
                    "flash_bytes": 25,
                    "ram_bytes": 5,
                    "gpio_pins": [3],
                    "peripherals": ["UART"],
                },
            ],
        }
    )

    assert estimate.flash_bytes == 175
    assert estimate.ram_bytes == 35
    assert estimate.gpio_pins == (2, 3)
    assert estimate.peripheral_instances == {"UART": 2}


def test_memory_limits_are_inclusive(engine: ConstraintEngine) -> None:
    assert engine.check_memory_constraints(
        "Arduino Uno", flash_usage=32768, ram_usage=2048
    ) == ()


def test_arduino_memory_overflow(engine: ConstraintEngine) -> None:
    result = engine.check_memory_constraints(
        "Arduino Uno", flash_usage=32769, ram_usage=2049
    )

    assert [item.rule_id for item in result] == [
        "PF-CONSTRAINT-002",
        "PF-CONSTRAINT-003",
    ]


@pytest.mark.parametrize("value", [-1, True, 1.5, "1024"])
def test_invalid_memory_values_are_rejected(
    engine: ConstraintEngine,
    value: object,
) -> None:
    assert engine.check_memory_constraints("ESP32", value, 0)[0].rule_id == (
        "PF-CONSTRAINT-000"
    )


def test_malformed_memory_in_full_plan_is_not_treated_as_zero(
    engine: ConstraintEngine,
) -> None:
    result = engine.validate_constraints("ESP32", {"flash_bytes": "large"})

    assert any(item.rule_id == "PF-CONSTRAINT-000" for item in result)


def test_direct_memory_check_accepts_plan_metadata(engine: ConstraintEngine) -> None:
    result = engine.check_memory_constraints(
        "Arduino Uno",
        {"flash_bytes": 32769, "ram_bytes": 2049},
    )

    assert [item.rule_id for item in result] == [
        "PF-CONSTRAINT-002",
        "PF-CONSTRAINT-003",
    ]


def test_esp32_reserved_and_invalid_gpio(engine: ConstraintEngine) -> None:
    result = engine.check_gpio_constraints("ESP32", [6, 12, 40])

    assert [item.rule_id for item in result] == [
        "PF-CONSTRAINT-005",
        "PF-CONSTRAINT-004",
    ]
    assert result[0].severity is ConstraintSeverity.WARNING


def test_common_numeric_pin_notation_is_normalized(engine: ConstraintEngine) -> None:
    assert engine.check_gpio_constraints("ESP32", ["GPIO12"]) == ()
    assert engine.check_gpio_constraints("Arduino Uno", ["D13"]) == ()


def test_esp32_input_only_pin_cannot_be_planned_as_output(
    engine: ConstraintEngine,
) -> None:
    result = engine.check_gpio_constraints("ESP32", {34: "OUTPUT", 35: "INPUT"})

    assert [item.rule_id for item in result] == ["PF-CONSTRAINT-012"]


def test_direct_gpio_check_accepts_plan_metadata(engine: ConstraintEngine) -> None:
    result = engine.check_gpio_constraints("ESP32", {"gpio_pins": [6]})

    assert result[0].rule_id == "PF-CONSTRAINT-005"


def test_duplicate_gpio_assignment_is_rejected(engine: ConstraintEngine) -> None:
    result = engine.check_gpio_constraints("Arduino Uno", [2, "2", 3])

    assert any(item.rule_id == "PF-CONSTRAINT-006" for item in result)


def test_uno_analog_pin_names_are_valid(engine: ConstraintEngine) -> None:
    assert engine.check_gpio_constraints("Arduino Uno", ["A0", "A5", 13]) == ()


def test_uno_all_declared_digital_and_analog_pins_fit(engine: ConstraintEngine) -> None:
    pins = [*range(14), *(f"A{index}" for index in range(6))]

    assert engine.check_gpio_constraints("Arduino Uno", pins) == ()


def test_stm32_pin_constraints_are_metadata_driven(engine: ConstraintEngine) -> None:
    result = engine.check_gpio_constraints("STM32", ["PA0", "PA13", "PD0"])

    assert [item.rule_id for item in result] == [
        "PF-CONSTRAINT-005",
        "PF-CONSTRAINT-004",
    ]


def test_uno_rejects_wifi_and_bluetooth(engine: ConstraintEngine) -> None:
    result = engine.validate_constraints(
        "Arduino Uno", {"peripherals": ["WiFi", "Bluetooth", "UART"]}
    )

    unsupported = [item for item in result if item.rule_id == "PF-CONSTRAINT-009"]
    assert {item.actual for item in unsupported} == {"WiFi", "Bluetooth"}


@pytest.mark.parametrize(
    ("board", "peripheral", "count", "limit"),
    [
        ("ESP32", "UART", 4, 3),
        ("Arduino Uno", "I2C", 2, 1),
        ("STM32", "CAN", 2, 1),
        ("STM32", "SPI", 3, 2),
    ],
)
def test_peripheral_instance_limits(
    engine: ConstraintEngine,
    board: str,
    peripheral: str,
    count: int,
    limit: int,
) -> None:
    result = engine.validate_constraints(
        board, {"peripheral_instances": {peripheral: count}}
    )

    violation = next(item for item in result if item.rule_id == "PF-CONSTRAINT-010")
    assert violation.actual == count
    assert violation.limit == limit


def test_peripheral_limit_boundary_is_valid(engine: ConstraintEngine) -> None:
    assert engine.validate_constraints(
        "STM32", {"peripheral_instances": {"USART": 3}}
    ) == ()


def test_framework_and_library_constraints(engine: ConstraintEngine) -> None:
    result = engine.validate_constraints(
        "Arduino Uno",
        {"framework": "ESP-IDF", "libraries": ["WiFiManager"]},
    )

    assert {item.rule_id for item in result} == {
        "PF-CONSTRAINT-008",
        "PF-CONSTRAINT-011",
    }


def test_supported_framework_and_library_are_accepted(
    engine: ConstraintEngine,
) -> None:
    assert engine.validate_constraints(
        "ESP32",
        {
            "framework": "Arduino",
            "libraries": ["bblanchon/ArduinoJson @ ^7"],
        },
    ) == ()


def test_boolean_required_peripheral_flags(engine: ConstraintEngine) -> None:
    result = engine.validate_constraints(
        "Arduino Uno", {"wifi_required": True, "bluetooth_required": False}
    )

    assert result[0].rule_id == "PF-CONSTRAINT-009"
    assert result[0].actual == "WiFi"


def test_malformed_gpio_peripheral_and_library_metadata(
    engine: ConstraintEngine,
) -> None:
    result = engine.validate_constraints(
        "ESP32",
        {
            "gpio_pins": [object()],
            "peripherals": [object()],
            "libraries": [object()],
        },
    )

    assert sum(item.rule_id == "PF-CONSTRAINT-000" for item in result) == 3


@dataclass(frozen=True, slots=True)
class PlanResources:
    flash_bytes: int
    ram_bytes: int
    gpio_pins: tuple[int, ...]
    peripherals: tuple[str, ...]


def test_slotted_resource_objects_are_supported(engine: ConstraintEngine) -> None:
    assert engine.validate_constraints(
        "ESP32", PlanResources(1024, 512, (2, 4), ("UART",))
    ) == ()


def test_custom_metadata_controls_constraints() -> None:
    matrix = CompatibilityMatrix.from_metadata(
        {
            "custom": {
                "board_name": "Custom",
                "frameworks": ["F"],
                "peripherals": ["UART"],
                "libraries": {"F": []},
                "flash_size": 100,
                "ram_size": 50,
                "gpio_count": 2,
                "valid_gpio": [1, 2],
                "reserved_gpio": [2],
                "peripheral_limits": {"UART": 1},
            }
        }
    )
    result = ConstraintEngine(matrix).validate_constraints(
        "custom",
        {
            "flash_bytes": 101,
            "ram_bytes": 51,
            "gpio_pins": [2, 3],
            "peripheral_instances": {"UART": 2},
        },
    )

    assert {item.rule_id for item in result} == {
        "PF-CONSTRAINT-002",
        "PF-CONSTRAINT-003",
        "PF-CONSTRAINT-004",
        "PF-CONSTRAINT-005",
        "PF-CONSTRAINT-010",
    }
