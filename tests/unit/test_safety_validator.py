from dataclasses import dataclass

import pytest

from backend.validation.safety_validator import (
    SafetyCategory,
    SafetySeverity,
    SafetyValidator,
)


@pytest.mark.parametrize("device", ["motor", "servo", "pump", "fan", "solenoid"])
def test_warns_for_direct_high_current_device_control(device: str) -> None:
    result = SafetyValidator().validate_prompt(f"Control a {device} from GPIO 5")

    assert result.safe_to_continue is True
    assert [issue.rule_id for issue in result.warnings] == ["PF-SAFETY-001"]


@pytest.mark.parametrize("interface", ["relay", "driver", "MOSFET", "transistor"])
def test_high_current_driver_suppresses_direct_control_warning(
    interface: str,
) -> None:
    result = SafetyValidator().validate_prompt(
        f"Control a motor through a {interface}"
    )

    assert all(issue.rule_id != "PF-SAFETY-001" for issue in result.issues)


def test_mains_and_direct_gpio_assumptions_are_critical() -> None:
    result = SafetyValidator().validate_prompt(
        "Connect motor directly to GPIO and switch a 220V AC load"
    )

    assert result.safe_to_continue is False
    assert result.has_errors() is True
    assert result.has_critical_failures() is True
    assert {issue.rule_id for issue in result.critical_failures} == {
        "PF-SAFETY-002",
        "PF-SAFETY-003",
    }


def test_battery_heating_and_network_rules_are_non_blocking() -> None:
    result = SafetyValidator().validate_prompt(
        "Charge a LiPo battery for a reflow heater with WiFi control"
    )

    assert result.safe_to_continue is True
    assert [issue.rule_id for issue in result.issues] == [
        "PF-SAFETY-004",
        "PF-SAFETY-005",
        "PF-SAFETY-010",
    ]
    assert result.issues[-1].severity is SafetySeverity.INFO


def test_missing_and_unsupported_boards_are_errors() -> None:
    validator = SafetyValidator()

    missing = validator.validate_board(None)
    unsupported = validator.validate_board({"board": "Raspberry Pi Pico"})

    assert missing.has_errors() is True
    assert missing.errors[0].rule_id == "PF-SAFETY-006"
    assert unsupported.errors[0].rule_id == "PF-SAFETY-007"


def test_board_database_is_injectable_and_normalized() -> None:
    validator = SafetyValidator({"custom_board": {"interfaces": ["SPI"]}})

    result = validator.validate_board({"target_board": "Custom Board"})

    assert result.safe_to_continue is True


def test_force_flash_requires_validation() -> None:
    validator = SafetyValidator()

    unsafe = validator.validate_firmware({"force_flash": True})
    reviewed = validator.validate_firmware(
        {"force_flash": True, "validated": True}
    )

    assert unsafe.warnings[0].rule_id == "PF-SAFETY-008"
    assert reviewed.issues == ()


def test_nested_planner_metadata_is_validated() -> None:
    result = SafetyValidator().validate(
        planner_output={"metadata": {"force_flash": True}},
        hardware_metadata={"board": "ESP32"},
    )

    assert result.warnings[0].rule_id == "PF-SAFETY-008"


def test_negative_validation_language_takes_precedence() -> None:
    validator = SafetyValidator()

    unsafe = validator.validate_firmware("force flash without validation")
    reviewed = validator.validate_firmware("force flash after validation")

    assert unsafe.warnings[0].rule_id == "PF-SAFETY-008"
    assert reviewed.issues == ()


def test_unsupported_firmware_peripherals_are_reported() -> None:
    validator = SafetyValidator(supported_peripherals=("gpio", "uart"))

    result = validator.validate_firmware(
        {"peripherals": ["GPIO", "CAN", "UART"]},
        {"board": "ESP32"},
    )

    assert result.warnings[0].rule_id == "PF-SAFETY-009"
    assert "can" in result.warnings[0].message


def test_board_specific_peripherals_take_precedence() -> None:
    validator = SafetyValidator(supported_peripherals=("gpio",))

    result = validator.validate_firmware(
        {"peripherals": ["spi"]},
        {"board": "ESP32", "supported_peripherals": ["SPI"]},
    )

    assert result.issues == ()


@dataclass(frozen=True, slots=True)
class FirmwareMetadata:
    peripherals: tuple[str, ...]
    force_flash: bool = False


def test_slotted_metadata_objects_are_supported() -> None:
    validator = SafetyValidator(supported_peripherals=("gpio",))

    result = validator.validate_firmware(FirmwareMetadata(("i2c",)))

    assert result.warnings[0].rule_id == "PF-SAFETY-009"


def test_combined_validation_is_ordered_and_deduplicates_flash_issue() -> None:
    result = SafetyValidator().validate(
        prompt="Open web server for a motor",
        planner_output={"force_flash": True},
        firmware_metadata={"force_flash": True},
        hardware_metadata={"board": "ESP32"},
    )

    assert [issue.rule_id for issue in result.issues] == [
        "PF-SAFETY-001",
        "PF-SAFETY-010",
        "PF-SAFETY-008",
    ]


def test_result_summary_is_deterministic() -> None:
    result = SafetyValidator().validate_prompt("Use mains power")

    assert result.summary() == (
        "BLOCKED: 1 issue(s) (info=0, warnings=0, errors=0, critical=1)"
    )
    assert result.critical_failures[0].category is SafetyCategory.RELAY


def test_malformed_inputs_become_issues_instead_of_exceptions() -> None:
    validator = SafetyValidator()

    assert validator.validate_prompt(None).errors[0].rule_id == "PF-SAFETY-000"
    assert validator.validate_firmware(42).errors[0].rule_id == "PF-SAFETY-000"


def test_compact_mains_voltage_is_detected() -> None:
    result = SafetyValidator().validate_prompt("Switch a 110VAC load")

    assert result.critical_failures[0].rule_id == "PF-SAFETY-002"
