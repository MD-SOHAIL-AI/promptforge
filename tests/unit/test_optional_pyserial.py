from __future__ import annotations

import asyncio
import subprocess
import sys
import textwrap

import pytest

from backend.runtime import serial_runtime
from backend.tools import board_detector, flash_firmware


_MISSING_SERIAL_IMPORT_SCRIPT = textwrap.dedent(
    """
    import importlib
    import importlib.abc
    import sys

    class BlockSerial(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "serial" or fullname.startswith("serial."):
                raise ModuleNotFoundError(
                    f"No module named {fullname!r}", name=fullname
                )
            return None

    for name in tuple(sys.modules):
        if name == "serial" or name.startswith("serial."):
            del sys.modules[name]
    sys.meta_path.insert(0, BlockSerial())

    for name in (
        "backend.api.app",
        "backend.api.routes.devices",
        "backend.api.routes.flash",
        "backend.api.routes.monitor",
        "backend.tools.board_detector",
        "backend.tools.wokwi_simulator",
        "backend.runtime.serial_runtime",
    ):
        importlib.import_module(name)
    """
)


def test_backend_and_hardware_routes_import_without_pyserial() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", _MISSING_SERIAL_IMPORT_SCRIPT],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_board_detection_reports_actionable_error_without_pyserial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(board_detector, "list_ports", None)

    with pytest.raises(board_detector.BoardDependencyError, match="pip install pyserial"):
        board_detector.BoardDetector().detect_boards()


def test_serial_runtime_reports_actionable_error_without_pyserial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serial_runtime, "serial", None)
    monkeypatch.setattr(serial_runtime, "list_ports", None)
    runtime = serial_runtime.SerialRuntime(serial_runtime.SerialConfig(port="COM7"))

    with pytest.raises(serial_runtime.SerialDependencyError, match="pip install pyserial"):
        asyncio.run(runtime.connect())

    assert runtime.state is serial_runtime.SerialConnectionState.DISCONNECTED


def test_flash_port_check_reports_actionable_error_without_pyserial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(flash_firmware, "list_ports", None)

    with pytest.raises(
        flash_firmware.PySerialUnavailableError,
        match="pip install pyserial",
    ):
        flash_firmware._port_is_connected("COM7")
