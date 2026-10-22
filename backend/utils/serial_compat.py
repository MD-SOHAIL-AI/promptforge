"""Graceful pyserial compatibility boundary.

ForgeX declares pyserial as a runtime dependency, but importing the whole backend
should not crash when optional hardware dependencies are unavailable (for example
in a source-review, CI, or cloud environment with no serial stack installed).
Hardware operations still fail explicitly when invoked.
"""
from __future__ import annotations

from types import SimpleNamespace

SERIAL_AVAILABLE = True
SERIAL_IMPORT_ERROR: Exception | None = None

try:  # pragma: no cover - exercised when pyserial is installed
    import serial as serial  # type: ignore[no-redef]
    from serial.tools import list_ports as list_ports  # type: ignore[no-redef]
except ModuleNotFoundError as exc:  # pragma: no cover - covered through consumers in minimal environments
    SERIAL_AVAILABLE = False
    SERIAL_IMPORT_ERROR = exc

    class _SerialException(OSError):
        pass

    class _MissingSerial:
        SerialException = _SerialException
        EIGHTBITS = 8
        PARITY_NONE = "N"
        STOPBITS_ONE = 1

        class Serial:  # noqa: D101 - mirrors pyserial constructor surface
            def __init__(self, *args: object, **kwargs: object) -> None:
                del args, kwargs
                raise _SerialException("pyserial_not_installed")

    class _MissingListPorts:
        @staticmethod
        def comports() -> list[object]:
            raise _SerialException("pyserial_not_installed")

    serial = _MissingSerial()
    serial.tools = SimpleNamespace(list_ports=_MissingListPorts())  # type: ignore[attr-defined]
    list_ports = serial.tools.list_ports  # type: ignore[attr-defined]
