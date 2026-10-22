from dataclasses import dataclass
from enum import Enum

import pytest

from backend.validation.board_validator import (
    BoardValidationCategory,
    BoardValidationSeverity,
    BoardValidator,
)


@pytest.mark.parametrize(
    "board",
    ["ESP32", "ESP32-C3", "ESP32_S3", "Arduino Uno", "STM32"],
)
def test_supported_boards_are_accepted(board: str) -> None:
    assert BoardValidator().validate_board(board).compatible is True


@pytest.mark.parametrize("board", [None, "", "UNKNOWN", {"board": "unknown"}])
def test_missing_or_unknown_board_is_structured_error(board: object) -> None:
    result = BoardValidator().validate_board(board)

    assert result.has_errors() is True
    assert result.errors[0].rule_id == "PF-BOARD-001"


def test_board_not_in_database_is_rejected() -> None:
    result = BoardValidator().validate_board("Raspberry Pi Pico")

    assert result.errors[0].rule_id == "PF-BOARD-002"


@pytest.mark.parametrize(
    ("board", "framework"),
    [
        ("ESP32", "Arduino"),
        ("ESP32", "ESP-IDF"),
        ("Arduino Uno", "Arduino"),
        ("STM32", "Arduino"),
        ("STM32", "STM32Cube"),
    ],
)
def test_supported_framework_combinations(
    board: str,
    framework: str,
) -> None:
    result = BoardValidator().validate_framework(board, framework)

    assert result.compatible is True


@pytest.mark.parametrize(
    ("board", "framework"),
    [
        ("ESP32", "STM32Cube"),
        ("Arduino Uno", "ESP-IDF"),
        ("Arduino Uno", "PlatformIO"),
        ("STM32", "ESP-IDF"),
    ],
)
def test_unsupported_framework_combinations(
    board: str,
    framework: str,
) -> None:
    result = BoardValidator().validate_framework(board, framework)

    assert result.errors[0].rule_id == "PF-BOARD-004"


def test_missing_framework_is_rejected() -> None:
    result = BoardValidator().validate_framework({"target_board": "ESP32"})

    assert result.errors[0].rule_id == "PF-BOARD-003"


def test_flash_and_ram_limits_are_inclusive() -> None:
    validator = BoardValidator()

    result = validator.validate_memory_requirements(
        "Arduino Uno",
        firmware_size=32 * 1024,
        ram_usage=2 * 1024,
    )

    assert result.compatible is True


def test_flash_and_ram_overflow_are_reported_separately() -> None:
    result = BoardValidator().validate_memory_requirements(
        "Arduino Uno",
        firmware_size=32 * 1024 + 1,
        ram_usage=2 * 1024 + 1,
    )

    assert [issue.rule_id for issue in result.errors] == [
        "PF-BOARD-005",
        "PF-BOARD-006",
    ]


@pytest.mark.parametrize("value", [-1, 1.5, True, "1024"])
def test_invalid_memory_metadata_is_an_issue(value: object) -> None:
    result = BoardValidator().validate_memory_requirements(
        "ESP32",
        firmware_size=value,
    )

    assert result.errors[0].rule_id == "PF-BOARD-000"


def test_arduino_uno_gpio_validation() -> None:
    result = BoardValidator().validate_gpio_usage(
        "Arduino Uno",
        [0, 13, 19, "A0", "D13", 50],
    )

    assert len(result.errors) == 1
    assert result.errors[0].rule_id == "PF-BOARD-007"
    assert "50" in result.errors[0].message


def test_esp32_reserved_gpio_is_warning_and_invalid_gpio_is_error() -> None:
    result = BoardValidator().validate_gpio_usage("ESP32", [6, 11, 12, 40])

    assert result.compatible is False
    assert [issue.rule_id for issue in result.warnings] == [
        "PF-BOARD-008",
        "PF-BOARD-008",
    ]
    assert result.errors[0].rule_id == "PF-BOARD-007"


def test_reserved_gpio_warning_does_not_block() -> None:
    result = BoardValidator().validate_gpio_usage("ESP32", [6])

    assert result.compatible is True
    assert result.warnings[0].severity is BoardValidationSeverity.WARNING


def test_stm32_port_pin_names_are_validated() -> None:
    valid = BoardValidator().validate_gpio_usage("STM32", ["PA0", "PK15"])
    invalid = BoardValidator().validate_gpio_usage("STM32", ["PL0", "PA16"])

    assert valid.compatible is True
    assert len(invalid.errors) == 2


@pytest.mark.parametrize(
    "peripheral",
    ["WiFi", "Bluetooth", "SPI", "I2C", "UART", "ADC", "PWM"],
)
def test_esp32_supports_requested_peripherals(peripheral: str) -> None:
    assert BoardValidator().validate_peripherals("ESP32", [peripheral]).compatible


def test_uno_rejects_wireless_and_accepts_wired_peripherals() -> None:
    result = BoardValidator().validate_peripherals(
        "Arduino Uno",
        ["WiFi", "Bluetooth", "SPI", "I2C", "UART", "ADC", "PWM"],
    )

    assert [issue.rule_id for issue in result.errors] == [
        "PF-BOARD-009",
        "PF-BOARD-009",
    ]
    assert {issue.message.split()[1] for issue in result.errors} == {
        "Bluetooth",
        "WiFi",
    }


def test_project_metadata_is_validated_end_to_end() -> None:
    result = BoardValidator().validate(
        {
            "target_board": "Arduino Uno",
            "framework": "Arduino",
            "metadata": {
                "firmware_size_bytes": 33_000,
                "estimated_ram_usage": 3_000,
                "gpio_usage": {"13": "OUTPUT", "50": "OUTPUT"},
                "required_peripherals": ["UART", "WiFi"],
            },
        }
    )

    assert [issue.rule_id for issue in result.issues] == [
        "PF-BOARD-005",
        "PF-BOARD-006",
        "PF-BOARD-007",
        "PF-BOARD-009",
    ]


def test_build_size_and_nested_memory_aliases_are_supported() -> None:
    result = BoardValidator().validate(
        {
            "target_board": "Arduino Uno",
            "framework": "Arduino",
            "metadata": {
                "build_size_bytes": 32 * 1024,
                "memory": {"ram_usage_bytes": 2 * 1024},
            },
        }
    )

    assert result.compatible is True


def test_boolean_peripheral_flags_are_supported() -> None:
    result = BoardValidator().validate(
        {
            "target_board": "Arduino Uno",
            "framework": "Arduino",
            "metadata": {"wifi": True, "spi": True},
        }
    )

    assert len(result.errors) == 1
    assert "WiFi" in result.errors[0].message


def test_project_and_selected_board_mismatch_is_rejected() -> None:
    result = BoardValidator().validate(
        {"target_board": "ESP32", "framework": "Arduino"},
        selected_board="Arduino Uno",
    )

    assert result.errors[0].rule_id == "PF-BOARD-010"


class BoardName(str, Enum):
    ESP32 = "ESP32"


@dataclass(frozen=True, slots=True)
class ProjectMetadata:
    target_board: BoardName
    framework: str
    metadata: dict[str, object]


def test_slotted_objects_and_string_enums_are_supported() -> None:
    project = ProjectMetadata(
        BoardName.ESP32,
        "ESP-IDF",
        {"gpio_pins": [12], "peripherals": ["WiFi"]},
    )

    assert BoardValidator().validate(project).compatible is True


def test_custom_board_database_is_injectable() -> None:
    validator = BoardValidator(
        {
            "Custom Board": {
                "frameworks": ["Arduino"],
                "flash_bytes": 4096,
                "ram_bytes": 1024,
                "peripherals": ["UART"],
                "valid_gpio_numbers": [0, 1],
            }
        }
    )

    result = validator.validate(
        {
            "target_board": "custom-board",
            "framework": "Arduino",
            "metadata": {
                "firmware_size": 4096,
                "ram_usage": 1024,
                "pins": [1],
                "peripherals": ["UART"],
            },
        }
    )

    assert result.compatible is True


def test_malformed_gpio_and_peripheral_metadata_do_not_raise() -> None:
    validator = BoardValidator()

    gpio = validator.validate_gpio_usage("ESP32", [object(), True])
    peripherals = validator.validate_peripherals("ESP32", ["SPI", object()])

    assert gpio.errors[0].category is BoardValidationCategory.METADATA
    assert peripherals.errors[0].category is BoardValidationCategory.METADATA


def test_result_summary_is_deterministic() -> None:
    result = BoardValidator().validate_gpio_usage("ESP32", [6, 50])

    assert result.summary() == (
        "INCOMPATIBLE: 2 issue(s) (info=0, warnings=1, errors=1)"
    )
    assert result.is_valid is False
    assert result.valid is False
