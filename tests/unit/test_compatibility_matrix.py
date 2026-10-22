import json
from enum import Enum
from pathlib import Path

import pytest

from backend.hardware.compatibility.compatibility_matrix import (
    Board,
    CompatibilityMatrix,
    CompatibilityMetadataError,
    Framework,
    Library,
    Peripheral,
)


@pytest.fixture
def matrix() -> CompatibilityMatrix:
    return CompatibilityMatrix()


def test_loads_all_board_metadata_in_stable_order(
    matrix: CompatibilityMatrix,
) -> None:
    assert [board.board_name for board in matrix.boards] == [
        "Arduino Uno Rev3",
        "ESP32 DevKit V1",
        "STM32F103C8T6 Blue Pill",
    ]
    assert all(isinstance(board, Board) for board in matrix.boards)


@pytest.mark.parametrize(
    "alias",
    ["esp32", "ESP32 DevKit V1", "ESP32-DevKit-V1"],
)
def test_board_aliases_are_normalized(
    matrix: CompatibilityMatrix,
    alias: str,
) -> None:
    assert matrix.get_board(alias).board_name == "ESP32 DevKit V1"  # type: ignore[union-attr]


def test_unknown_board_returns_empty_compatibility(matrix: CompatibilityMatrix) -> None:
    assert matrix.get_board("missing") is None
    assert matrix.is_framework_supported("missing", "Arduino") is False
    assert matrix.is_peripheral_supported("missing", "SPI") is False
    assert matrix.is_library_supported("missing", "ArduinoJson") is False
    assert matrix.get_supported_frameworks("missing") == ()


@pytest.mark.parametrize(
    ("board", "framework", "supported"),
    [
        ("esp32", "Arduino", True),
        ("esp32", "ESP-IDF", True),
        ("arduino_uno", "Arduino", True),
        ("arduino_uno", "ESP-IDF", False),
        ("stm32", "STM32Cube", True),
        ("stm32", "Arduino", True),
    ],
)
def test_framework_compatibility(
    matrix: CompatibilityMatrix,
    board: str,
    framework: str,
    supported: bool,
) -> None:
    assert matrix.is_framework_supported(board, framework) is supported


def test_framework_objects_and_enums_are_supported(
    matrix: CompatibilityMatrix,
) -> None:
    class FrameworkName(str, Enum):
        ARDUINO = "Arduino"

    assert matrix.is_framework_supported("esp32", Framework("Arduino"))
    assert matrix.is_framework_supported("esp32", FrameworkName.ARDUINO)


@pytest.mark.parametrize(
    ("board", "peripheral", "supported"),
    [
        ("esp32", "WiFi", True),
        ("esp32", "Bluetooth", True),
        ("esp32", "TWAI", True),
        ("arduino uno", "SPI", True),
        ("arduino uno", "WiFi", False),
        ("stm32", "CAN", True),
        ("stm32", "Bluetooth", False),
    ],
)
def test_peripheral_compatibility(
    matrix: CompatibilityMatrix,
    board: str,
    peripheral: str,
    supported: bool,
) -> None:
    assert matrix.is_peripheral_supported(board, peripheral) is supported


def test_peripheral_value_object_is_supported(matrix: CompatibilityMatrix) -> None:
    assert matrix.is_peripheral_supported("esp32", Peripheral("I2C"))


def test_library_compatibility_is_framework_aware(
    matrix: CompatibilityMatrix,
) -> None:
    assert matrix.is_library_supported("esp32", "ArduinoJson")
    assert matrix.is_library_supported(
        "esp32", "bblanchon/ArduinoJson @ ^7.0.0", "Arduino"
    )
    assert not matrix.is_library_supported(
        "esp32", "ArduinoJson", "ESP-IDF"
    )
    assert not matrix.is_library_supported("arduino_uno", "WiFiManager")


def test_library_value_object_is_supported(matrix: CompatibilityMatrix) -> None:
    assert matrix.is_library_supported("stm32", Library("Servo"), Framework("Arduino"))


def test_get_supported_frameworks_uses_metadata_order(
    matrix: CompatibilityMatrix,
) -> None:
    assert matrix.get_supported_frameworks("esp32") == ("Arduino", "ESP-IDF")
    assert matrix.get_supported_frameworks("arduino uno") == ("Arduino",)


def test_get_compatible_boards_without_filters_returns_all(
    matrix: CompatibilityMatrix,
) -> None:
    assert matrix.get_compatible_boards() == (
        "Arduino Uno Rev3",
        "ESP32 DevKit V1",
        "STM32F103C8T6 Blue Pill",
    )


def test_get_compatible_boards_combines_all_filters(
    matrix: CompatibilityMatrix,
) -> None:
    assert matrix.get_compatible_boards(
        framework="Arduino",
        peripheral=("SPI", "I2C"),
        library=("Servo", "ArduinoJson"),
    ) == (
        "Arduino Uno Rev3",
        "STM32F103C8T6 Blue Pill",
    )
    assert matrix.get_compatible_boards(
        framework="Arduino",
        peripheral="WiFi",
        library="WiFiManager",
    ) == ("ESP32 DevKit V1",)


def test_from_metadata_is_filesystem_independent() -> None:
    matrix = CompatibilityMatrix.from_metadata(
        {
            "custom": {
                "board_name": "Custom Board",
                "frameworks": ["Framework X"],
                "peripherals": ["UART"],
                "libraries": {"Framework X": ["Library X"]},
            }
        }
    )

    assert matrix.is_framework_supported("custom", "framework-x")
    assert matrix.is_peripheral_supported("Custom Board", "uart")
    assert matrix.is_library_supported("custom", "Library X", "Framework X")


def test_sequence_library_policy_applies_to_all_frameworks() -> None:
    matrix = CompatibilityMatrix.from_metadata(
        {
            "custom": {
                "board_name": "Custom Board",
                "frameworks": ["A", "B"],
                "peripherals": ["UART"],
                "libraries": ["Shared Library"],
            }
        }
    )

    assert matrix.is_library_supported("custom", "Shared Library", "A")
    assert matrix.is_library_supported("custom", "Shared Library", "B")


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"x": []},
        {"x": {"board_name": "X", "frameworks": [], "peripherals": ["UART"]}},
        {
            "x": {
                "board_name": "X",
                "frameworks": ["A", "a"],
                "peripherals": ["UART"],
            }
        },
        {
            "x": {
                "board_name": "X",
                "frameworks": ["A"],
                "peripherals": ["UART"],
                "libraries": {"B": []},
            }
        },
    ],
)
def test_invalid_metadata_fails_fast(metadata: dict[str, object]) -> None:
    with pytest.raises(CompatibilityMetadataError):
        CompatibilityMatrix.from_metadata(metadata)  # type: ignore[arg-type]


def test_invalid_json_file_fails_fast(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")

    with pytest.raises(CompatibilityMetadataError):
        CompatibilityMatrix(tmp_path)


def test_metadata_files_are_valid_json() -> None:
    boards = Path("backend/hardware/boards")

    for path in boards.glob("*.json"):
        assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
