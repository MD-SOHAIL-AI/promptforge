"""Connected embedded-board discovery for PromptForge AI.

The detector enumerates USB serial devices through pyserial, classifies known
VID/PID combinations, and returns immutable metadata snapshots. It does not
open serial ports or participate in build, flash, retry, or session workflows.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum, unique
from typing import Iterable, Optional

from serial.tools import list_ports

__all__ = [
    "BoardDetectionError",
    "BoardDetector",
    "BoardInfo",
    "BoardType",
]

logger = logging.getLogger(__name__)


@unique
class BoardType(str, Enum):
    """Embedded board families recognized by PromptForge."""

    ESP32 = "ESP32"
    ESP32_S3 = "ESP32_S3"
    ESP32_C3 = "ESP32_C3"
    STM32 = "STM32"
    ARDUINO_UNO = "ARDUINO_UNO"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class BoardInfo:
    """Immutable metadata for one detected serial-connected device."""

    board_type: BoardType
    port: str
    vid: Optional[int]
    pid: Optional[int]
    manufacturer: Optional[str]
    description: Optional[str]
    serial_number: Optional[str]


class BoardDetectionError(RuntimeError):
    """Serial-port enumeration failed before devices could be classified."""


# USB identities that deterministically identify a supported board family or
# ESP32 variant. These mappings take priority over all text heuristics.
_EXACT_BOARD_IDS: dict[tuple[int, int], BoardType] = {
    # Official Arduino/Genuino Uno USB interfaces.
    (0x2341, 0x0001): BoardType.ARDUINO_UNO,
    (0x2341, 0x0043): BoardType.ARDUINO_UNO,
    (0x2A03, 0x0001): BoardType.ARDUINO_UNO,
    (0x2A03, 0x0043): BoardType.ARDUINO_UNO,
    # STM32 virtual COM and ST-LINK virtual COM interfaces.
    (0x0483, 0x5740): BoardType.STM32,
    (0x0483, 0x3748): BoardType.STM32,
    (0x0483, 0x374B): BoardType.STM32,
    # WCH CH9102-family identities used by supported ESP32 variants.
    (0x1A86, 0x55D3): BoardType.ESP32_C3,
    (0x1A86, 0x55D4): BoardType.ESP32_S3,
}

# Shared USB-UART/native USB interfaces used by supported boards. These do not
# identify a specific ESP32 variant and are therefore generic fallbacks.
_ESP32_INTERFACE_IDS: dict[tuple[int, int], BoardType] = {
    (0x303A, 0x1001): BoardType.ESP32,     # Espressif USB Serial/JTAG
    (0x10C4, 0xEA60): BoardType.ESP32,     # Silicon Labs CP210x
    (0x1A86, 0x7523): BoardType.ESP32,     # WCH CH340
}

# FT232R is the interface used by older Uno revisions. It remains a fallback
# only; explicit ESP32/STM32 text hints take precedence for shared adapters.
_ARDUINO_INTERFACE_IDS = {
    (0x0403, 0x6001),
}

_ESP32_C3_RE = re.compile(r"\besp32[\s_-]*c3\b|\besp[\s_-]*c3\b", re.IGNORECASE)
_ESP32_S3_RE = re.compile(r"\besp32[\s_-]*s3\b|\besp[\s_-]*s3\b", re.IGNORECASE)
_ESP32_RE = re.compile(r"\besp32\b|\bespressif\b", re.IGNORECASE)
_ARDUINO_UNO_RE = re.compile(r"\b(arduino|genuino)[\s_-]*uno\b|\buno[\s_-]*r3\b", re.IGNORECASE)
_STM32_RE = re.compile(r"\bstm32\b|\bst[\s_-]*link\b|\bstlink\b", re.IGNORECASE)

_TEXT_RULES: tuple[tuple[re.Pattern[str], BoardType], ...] = (
    (_ESP32_C3_RE, BoardType.ESP32_C3),
    (_ESP32_S3_RE, BoardType.ESP32_S3),
    (_ARDUINO_UNO_RE, BoardType.ARDUINO_UNO),
    (_STM32_RE, BoardType.STM32),
    (_ESP32_RE, BoardType.ESP32),
)


class BoardDetector:
    """Enumerate and classify serial-connected embedded boards."""

    def detect_boards(self) -> list[BoardInfo]:
        """Return detected boards in deterministic port order.

        An empty list means enumeration completed and no serial devices were
        present. Enumeration failures raise ``BoardDetectionError`` so callers
        can distinguish host permission/configuration problems from no boards.
        """
        try:
            ports = list(list_ports.comports())
        except PermissionError as exc:
            raise BoardDetectionError(
                f"Permission denied while enumerating serial ports: {exc}"
            ) from exc
        except OSError as exc:
            raise BoardDetectionError(
                f"Operating-system error while enumerating serial ports: {exc}"
            ) from exc
        except Exception as exc:
            raise BoardDetectionError(
                f"Unable to enumerate serial ports: {exc}"
            ) from exc

        boards = [
            board
            for board in (self._to_board_info(port) for port in ports)
            if board is not None
        ]
        return self._deduplicate(boards)

    def detect(self) -> list[BoardInfo]:
        """Compatibility-friendly shorthand for ``detect_boards``."""
        return self.detect_boards()

    @classmethod
    def classify_port(cls, port_info: object) -> BoardType:
        """Classify one pyserial ``ListPortInfo``-compatible object."""
        vid = _normalize_usb_id(getattr(port_info, "vid", None))
        pid = _normalize_usb_id(getattr(port_info, "pid", None))
        identity = (vid, pid) if vid is not None and pid is not None else None

        if identity in _EXACT_BOARD_IDS:
            return _EXACT_BOARD_IDS[identity]

        for field in ("product", "description", "manufacturer"):
            board_type = _classify_text(getattr(port_info, field, None))
            if board_type is not None:
                return board_type

        # Interface and HWID are less reliable than human-readable USB
        # metadata, but retain useful variant hints when those fields are absent.
        for field in ("interface", "hwid"):
            board_type = _classify_text(getattr(port_info, field, None))
            if board_type is not None:
                return board_type

        if identity in _ESP32_INTERFACE_IDS:
            return _ESP32_INTERFACE_IDS[identity]
        if identity in _ARDUINO_INTERFACE_IDS:
            return BoardType.ARDUINO_UNO
        return BoardType.UNKNOWN

    @classmethod
    def _to_board_info(cls, port_info: object) -> Optional[BoardInfo]:
        port = _normalize_text(getattr(port_info, "device", None))
        if port is None:
            logger.warning("Ignoring serial-port entry without a device name")
            return None

        return BoardInfo(
            board_type=cls.classify_port(port_info),
            port=port,
            vid=_normalize_usb_id(getattr(port_info, "vid", None)),
            pid=_normalize_usb_id(getattr(port_info, "pid", None)),
            manufacturer=_normalize_text(
                getattr(port_info, "manufacturer", None)
            ),
            description=_normalize_text(
                getattr(port_info, "description", None)
            ),
            serial_number=_normalize_text(
                getattr(port_info, "serial_number", None)
            ),
        )

    @staticmethod
    def _deduplicate(boards: Iterable[BoardInfo]) -> list[BoardInfo]:
        unique: dict[tuple[object, ...], BoardInfo] = {}
        for board in sorted(boards, key=lambda item: item.port.casefold()):
            identity = _device_identity(board)
            current = unique.get(identity)
            if current is None or _metadata_score(board) > _metadata_score(current):
                unique[identity] = board

        return sorted(unique.values(), key=lambda item: item.port.casefold())


def _normalize_usb_id(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return normalized if 0 <= normalized <= 0xFFFF else None


def _normalize_text(value: object) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _classify_text(value: object) -> Optional[BoardType]:
    text = _normalize_text(value)
    if text is None:
        return None
    for pattern, board_type in _TEXT_RULES:
        if pattern.search(text):
            return board_type
    return None


def _device_identity(board: BoardInfo) -> tuple[object, ...]:
    if board.vid is not None and board.pid is not None and board.serial_number:
        return (
            "usb",
            board.vid,
            board.pid,
            board.serial_number.casefold(),
        )
    return ("port", board.port.casefold())


def _metadata_score(board: BoardInfo) -> int:
    return (
        int(board.board_type is not BoardType.UNKNOWN) * 4
        + int(board.vid is not None)
        + int(board.pid is not None)
        + int(board.manufacturer is not None)
        + int(board.description is not None)
        + int(board.serial_number is not None)
    )
