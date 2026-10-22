from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_signoff_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "esp32_hardware_signoff.py"
    spec = importlib.util.spec_from_file_location("esp32_hardware_signoff", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "blink"
    root.mkdir()
    (root / "platformio.ini").write_text(
        "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\n",
        encoding="utf-8",
    )
    artifact = root / ".pio" / "build" / "esp32dev"
    artifact.mkdir(parents=True)
    (artifact / "firmware.bin").write_bytes(b"firmware")
    return root


def test_hardware_signoff_requires_platformio_project(tmp_path: Path) -> None:
    signoff = _load_signoff_module()

    with pytest.raises(ValueError, match="platformio.ini"):
        signoff.run_hardware_signoff(project_dir=tmp_path, port=None, builds=1, flash_cycles=0)


def test_hardware_signoff_records_consecutive_builds(tmp_path: Path) -> None:
    signoff = _load_signoff_module()
    project = _project(tmp_path)
    calls: list[tuple[str, ...]] = []

    def runner(args, cwd, timeout, env):
        del cwd, timeout, env
        calls.append(tuple(args))
        return signoff.CommandResult(tuple(args), 0, "build ok", "", 12)

    report = signoff.run_hardware_signoff(
        project_dir=project,
        port=None,
        builds=3,
        flash_cycles=0,
        command_runner=runner,
    )

    assert report["success"] is True
    assert len(report["builds"]) == 3
    assert len(calls) == 3
    assert all(call[:2] == ("pio", "run") for call in calls)
    assert report["builds"][0]["firmware_sha256"]


def test_hardware_signoff_stops_on_first_build_failure(tmp_path: Path) -> None:
    signoff = _load_signoff_module()
    project = _project(tmp_path)
    calls = 0

    def runner(args, cwd, timeout, env):
        nonlocal calls
        del cwd, timeout, env
        calls += 1
        return signoff.CommandResult(tuple(args), 1, "", "compile failed", 12)

    report = signoff.run_hardware_signoff(
        project_dir=project,
        port=None,
        builds=3,
        flash_cycles=0,
        command_runner=runner,
    )

    assert report["success"] is False
    assert report["failure_code"] == "build_failed"
    assert calls == 1


def test_hardware_signoff_records_flash_cycles_with_serial_nonce(tmp_path: Path) -> None:
    signoff = _load_signoff_module()
    project = _project(tmp_path)
    commands: list[tuple[str, ...]] = []
    observed_nonces: list[str] = []

    def runner(args, cwd, timeout, env):
        del cwd, timeout, env
        commands.append(tuple(args))
        return signoff.CommandResult(tuple(args), 0, "ok", "", 10)

    def serial_reader(port, baud, nonce, timeout):
        observed_nonces.append(nonce)
        return {
            "success": True,
            "matched_nonce": nonce,
            "lines": [f"boot {nonce}"],
            "port": port,
            "baud": baud,
            "timeout": timeout,
        }

    report = signoff.run_hardware_signoff(
        project_dir=project,
        port="COM7",
        builds=1,
        flash_cycles=2,
        command_runner=runner,
        serial_reader=serial_reader,
    )

    assert report["success"] is True
    assert len(report["flash_cycles"]) == 2
    assert len(observed_nonces) == 2
    assert all(cycle["serial"]["matched_nonce"] == cycle["nonce"] for cycle in report["flash_cycles"])
    assert any("-t" in command and "upload" in command for command in commands)


def test_hardware_signoff_requires_port_for_flash_cycles(tmp_path: Path) -> None:
    signoff = _load_signoff_module()
    project = _project(tmp_path)

    with pytest.raises(ValueError, match="--port is required"):
        signoff.run_hardware_signoff(project_dir=project, port=None, builds=1, flash_cycles=1)
