"""Optional pyserial imports shared by hardware-facing backend modules."""

from __future__ import annotations

from typing import Any

try:
    import serial as serial
    from serial.tools import list_ports as list_ports
except ModuleNotFoundError as exc:
    if exc.name != "serial" and not (exc.name or "").startswith("serial."):
        raise
    serial = None
    list_ports = None


PYSERIAL_REQUIRED_MESSAGE = (
    "pyserial is required for serial monitor, board detection, and flash features. "
    "Install it with: pip install pyserial"
)


class PySerialUnavailableError(RuntimeError):
    """Raised when a hardware operation is requested without pyserial."""


def require_pyserial(*components: Any) -> None:
    """Raise an actionable error if an imported pyserial component is absent."""
    if not components or any(component is None for component in components):
        raise PySerialUnavailableError(PYSERIAL_REQUIRED_MESSAGE)
