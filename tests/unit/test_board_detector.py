from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from backend.tools.board_detector import (
    BoardDetectionError,
    BoardDetector,
    BoardInfo,
    BoardType,
)


def port(
    device: str | None,
    *,
    vid: int | None = None,
    pid: int | None = None,
    manufacturer: str | None = None,
    description: str | None = None,
    serial_number: str | None = None,
    product: str | None = None,
    interface: str | None = None,
    hwid: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        device=device,
        vid=vid,
        pid=pid,
        manufacturer=manufacturer,
        description=description,
        serial_number=serial_number,
        product=product,
        interface=interface,
        hwid=hwid,
    )


def install_ports(monkeypatch: pytest.MonkeyPatch, *ports: object) -> None:
    monkeypatch.setattr(
        "backend.tools.board_detector.list_ports.comports",
        lambda: list(ports),
    )


@pytest.mark.parametrize(
    ("port_info", "expected"),
    [
        (port("COM1", vid=0x10C4, pid=0xEA60), BoardType.ESP32),
        (port("COM2", vid=0x1A86, pid=0x55D4), BoardType.ESP32_S3),
        (
            port(
                "COM3",
                vid=0x303A,
                pid=0x1001,
                product="ESP32-C3 USB JTAG/serial debug unit",
            ),
            BoardType.ESP32_C3,
        ),
        (
            port(
                "COM4",
                vid=0x303A,
                pid=0x1001,
                description="Espressif ESP32-S3",
            ),
            BoardType.ESP32_S3,
        ),
        (port("COM5", vid=0x0483, pid=0x5740), BoardType.STM32),
        (port("COM6", vid=0x0483, pid=0x374B), BoardType.STM32),
        (port("COM7", vid=0x2341, pid=0x0043), BoardType.ARDUINO_UNO),
        (port("COM8", vid=0x2A03, pid=0x0001), BoardType.ARDUINO_UNO),
        (port("COM9", vid=0x0403, pid=0x6001), BoardType.ARDUINO_UNO),
    ],
)
def test_classifies_supported_vid_pid_devices(
    port_info: SimpleNamespace,
    expected: BoardType,
) -> None:
    assert BoardDetector.classify_port(port_info) is expected


def test_text_hint_disambiguates_shared_ch340_as_arduino_uno() -> None:
    port_info = port(
        "/dev/ttyUSB0",
        vid=0x1A86,
        pid=0x7523,
        description="Arduino Uno R3 CH340",
    )

    assert BoardDetector.classify_port(port_info) is BoardType.ARDUINO_UNO


def test_text_hint_disambiguates_bridge_as_esp32_c3() -> None:
    port_info = port(
        "/dev/ttyUSB0",
        vid=0x10C4,
        pid=0xEA60,
        interface="ESP32_C3 console",
    )

    assert BoardDetector.classify_port(port_info) is BoardType.ESP32_C3


def test_detect_returns_complete_immutable_board_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_ports(
        monkeypatch,
        port(
            "COM7",
            vid=0x2341,
            pid=0x0043,
            manufacturer="Arduino LLC",
            description="Arduino Uno",
            serial_number="ABC123",
        ),
    )

    boards = BoardDetector().detect_boards()

    assert boards == [
        BoardInfo(
            board_type=BoardType.ARDUINO_UNO,
            port="COM7",
            vid=0x2341,
            pid=0x0043,
            manufacturer="Arduino LLC",
            description="Arduino Uno",
            serial_number="ABC123",
        )
    ]
    with pytest.raises(FrozenInstanceError):
        boards[0].port = "COM8"  # type: ignore[misc]


def test_no_connected_boards_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_ports(monkeypatch)

    assert BoardDetector().detect_boards() == []


def test_missing_vid_pid_is_preserved_and_classified_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_ports(
        monkeypatch,
        port(
            "/dev/ttyS0",
            description="Generic UART",
            manufacturer="  ",
        ),
    )

    board = BoardDetector().detect_boards()[0]

    assert board.board_type is BoardType.UNKNOWN
    assert board.vid is None
    assert board.pid is None
    assert board.manufacturer is None
    assert board.description == "Generic UART"


def test_missing_vid_pid_can_still_use_specific_product_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_ports(
        monkeypatch,
        port("/dev/ttyACM0", product="STM32 Virtual COM Port"),
    )

    assert BoardDetector().detect_boards()[0].board_type is BoardType.STM32


def test_unsupported_hardware_is_returned_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_ports(
        monkeypatch,
        port(
            "COM12",
            vid=0x239A,
            pid=0xCAFE,
            manufacturer="Adafruit",
            description="Feather M4 Express",
            serial_number="M4-001",
        ),
    )

    board = BoardDetector().detect_boards()[0]

    assert board.board_type is BoardType.UNKNOWN
    assert board.port == "COM12"
    assert board.vid == 0x239A
    assert board.pid == 0xCAFE


def test_duplicate_usb_identity_is_returned_once_with_richer_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_ports(
        monkeypatch,
        port(
            "COM8",
            vid=0x2341,
            pid=0x0043,
            serial_number="same-device",
        ),
        port(
            "COM9",
            vid=0x2341,
            pid=0x0043,
            manufacturer="Arduino LLC",
            description="Arduino Uno",
            serial_number="SAME-DEVICE",
        ),
    )

    boards = BoardDetector().detect_boards()

    assert len(boards) == 1
    assert boards[0].port == "COM9"
    assert boards[0].manufacturer == "Arduino LLC"


def test_same_vid_pid_without_serial_remains_two_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_ports(
        monkeypatch,
        port("COM4", vid=0x10C4, pid=0xEA60),
        port("COM5", vid=0x10C4, pid=0xEA60),
    )

    assert [board.port for board in BoardDetector().detect_boards()] == [
        "COM4",
        "COM5",
    ]


def test_duplicate_port_without_serial_is_returned_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_ports(
        monkeypatch,
        port("COM4", description="first"),
        port("com4", description="second", manufacturer="Vendor"),
    )

    boards = BoardDetector().detect_boards()

    assert len(boards) == 1
    assert boards[0].manufacturer == "Vendor"


def test_results_are_sorted_by_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_ports(
        monkeypatch,
        port("COM9"),
        port("COM2"),
        port("COM5"),
    )

    assert [board.port for board in BoardDetector().detect_boards()] == [
        "COM2",
        "COM5",
        "COM9",
    ]


def test_entry_without_device_name_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    install_ports(monkeypatch, port(None), port("COM3"))

    boards = BoardDetector().detect_boards()

    assert [board.port for board in boards] == ["COM3"]
    assert "without a device name" in caplog.text


def test_permission_failure_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied() -> list[object]:
        raise PermissionError("access denied")

    monkeypatch.setattr(
        "backend.tools.board_detector.list_ports.comports",
        denied,
    )

    with pytest.raises(BoardDetectionError, match="Permission denied") as exc_info:
        BoardDetector().detect_boards()

    assert isinstance(exc_info.value.__cause__, PermissionError)


def test_os_enumeration_failure_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed() -> list[object]:
        raise OSError("registry unavailable")

    monkeypatch.setattr(
        "backend.tools.board_detector.list_ports.comports",
        failed,
    )

    with pytest.raises(BoardDetectionError, match="Operating-system error"):
        BoardDetector().detect_boards()


def test_generic_enumeration_failure_is_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed() -> list[object]:
        raise RuntimeError("backend failed")

    monkeypatch.setattr(
        "backend.tools.board_detector.list_ports.comports",
        failed,
    )

    with pytest.raises(BoardDetectionError, match="Unable to enumerate"):
        BoardDetector().detect_boards()


def test_detect_alias_delegates_to_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_ports(monkeypatch, port("COM1", vid=0x10C4, pid=0xEA60))

    boards = BoardDetector().detect()

    assert len(boards) == 1
    assert boards[0].board_type is BoardType.ESP32
