#!/usr/bin/env python3
"""Run and record ForgeX ESP32 production-beta hardware sign-off.

This script is intentionally outside the normal CI gate because it needs a
physical ESP32 DevKit V1-compatible board attached to the operator machine.
It writes a JSON evidence report and exits non-zero unless every requested
build/upload/serial nonce check passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ENVIRONMENT = "esp32dev"
DEFAULT_BUILDS = 20
DEFAULT_FLASH_CYCLES = 10
DEFAULT_BAUD = 115_200
DEFAULT_SERIAL_TIMEOUT = 20.0
REPORT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout_tail: str
    stderr_tail: str
    duration_ms: int


CommandRunner = Callable[[Sequence[str], Path, float | None, dict[str, str]], CommandResult]
SerialNonceReader = Callable[[str, int, str, float], dict[str, Any]]


def bounded_tail(value: str, limit: int = 6000) -> str:
    return value[-limit:]


def run_command(args: Sequence[str], cwd: Path, timeout: float | None, env: dict[str, str]) -> CommandResult:
    started = time.monotonic()
    completed = subprocess.run(
        list(args),
        cwd=str(cwd),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    return CommandResult(
        tuple(args),
        completed.returncode,
        bounded_tail(completed.stdout),
        bounded_tail(completed.stderr),
        max(0, int((time.monotonic() - started) * 1000)),
    )


def read_serial_nonce(port: str, baud: int, nonce: str, timeout_s: float) -> dict[str, Any]:
    try:
        import serial  # type: ignore[import-not-found]
    except ImportError:
        return {
            "success": False,
            "error": "pyserial_not_installed",
            "lines": [],
        }

    deadline = time.monotonic() + timeout_s
    lines: list[str] = []
    try:
        with serial.Serial(port=port, baudrate=baud, timeout=0.5) as connection:
            connection.reset_input_buffer()
            while time.monotonic() < deadline:
                raw = connection.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    lines.append(line)
                if nonce in line:
                    return {
                        "success": True,
                        "matched_nonce": nonce,
                        "lines": lines[-80:],
                    }
    except Exception as exc:  # pragma: no cover - hardware/platform dependent
        return {
            "success": False,
            "error": type(exc).__name__,
            "message": str(exc)[:500],
            "lines": lines[-80:],
        }
    return {
        "success": False,
        "error": "nonce_not_observed",
        "matched_nonce": None,
        "lines": lines[-80:],
    }


def firmware_artifact_path(project_dir: Path, environment: str) -> Path:
    return project_dir / ".pio" / "build" / environment / "firmware.bin"


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_args(pio: str, project_dir: Path, environment: str, nonce: str | None = None) -> list[str]:
    args = [pio, "run", "--project-dir", str(project_dir), "-e", environment]
    if nonce:
        args.extend(["--project-option", f'build_flags=-DFORGEX_HW_SIGNOFF_NONCE="{nonce}"'])
    return args


def upload_args(pio: str, project_dir: Path, environment: str, port: str) -> list[str]:
    return [pio, "run", "--project-dir", str(project_dir), "-e", environment, "-t", "upload", "--upload-port", port]


def validate_inputs(project_dir: Path, builds: int, flash_cycles: int, port: str | None) -> None:
    if not project_dir.is_dir():
        raise ValueError(f"project directory does not exist: {project_dir}")
    if not (project_dir / "platformio.ini").is_file():
        raise ValueError(f"project directory must contain platformio.ini: {project_dir}")
    if builds < 1:
        raise ValueError("--builds must be at least 1")
    if flash_cycles < 0:
        raise ValueError("--flash-cycles cannot be negative")
    if flash_cycles > 0 and not port:
        raise ValueError("--port is required when --flash-cycles is greater than 0")


def run_hardware_signoff(
    *,
    project_dir: Path,
    port: str | None,
    environment: str = DEFAULT_ENVIRONMENT,
    builds: int = DEFAULT_BUILDS,
    flash_cycles: int = DEFAULT_FLASH_CYCLES,
    baud: int = DEFAULT_BAUD,
    serial_timeout: float = DEFAULT_SERIAL_TIMEOUT,
    pio: str = "pio",
    command_runner: CommandRunner = run_command,
    serial_reader: SerialNonceReader = read_serial_nonce,
) -> dict[str, Any]:
    project_dir = project_dir.resolve()
    validate_inputs(project_dir, builds, flash_cycles, port)
    started = datetime.now(timezone.utc)
    env = dict(os.environ)
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "started_at": started.isoformat(),
        "completed_at": None,
        "success": False,
        "target": {
            "board": "ESP32 DevKit V1-compatible",
            "platformio_environment": environment,
            "framework": "Arduino",
            "project_dir": str(project_dir),
            "port": port,
            "baud": baud,
        },
        "requirements": {
            "consecutive_builds": builds,
            "flash_reset_serial_cycles": flash_cycles,
            "serial_nonce_required": flash_cycles > 0,
        },
        "builds": [],
        "flash_cycles": [],
    }

    try:
        for index in range(1, builds + 1):
            result = command_runner(build_args(pio, project_dir, environment), project_dir, None, env)
            artifact = firmware_artifact_path(project_dir, environment)
            entry = {
                "index": index,
                "success": result.returncode == 0,
                "returncode": result.returncode,
                "duration_ms": result.duration_ms,
                "firmware": str(artifact),
                "firmware_sha256": sha256_file(artifact),
                "stdout_tail": result.stdout_tail,
                "stderr_tail": result.stderr_tail,
            }
            report["builds"].append(entry)
            if not entry["success"]:
                return _complete(report, False, "build_failed")

        for index in range(1, flash_cycles + 1):
            assert port is not None
            nonce = f"forgex-hw-{index}-{secrets.token_hex(8)}"
            build = command_runner(build_args(pio, project_dir, environment, nonce), project_dir, None, env)
            if build.returncode != 0:
                report["flash_cycles"].append({
                    "index": index,
                    "success": False,
                    "nonce": nonce,
                    "failure_stage": "nonce_build",
                    "returncode": build.returncode,
                    "stdout_tail": build.stdout_tail,
                    "stderr_tail": build.stderr_tail,
                })
                return _complete(report, False, "nonce_build_failed")

            artifact = firmware_artifact_path(project_dir, environment)
            upload = command_runner(upload_args(pio, project_dir, environment, port), project_dir, None, env)
            serial = serial_reader(port, baud, nonce, serial_timeout) if upload.returncode == 0 else {
                "success": False,
                "error": "upload_failed",
                "lines": [],
            }
            cycle = {
                "index": index,
                "success": upload.returncode == 0 and bool(serial.get("success")),
                "nonce": nonce,
                "firmware_sha256": sha256_file(artifact),
                "upload_returncode": upload.returncode,
                "upload_duration_ms": upload.duration_ms,
                "upload_stdout_tail": upload.stdout_tail,
                "upload_stderr_tail": upload.stderr_tail,
                "serial": serial,
            }
            report["flash_cycles"].append(cycle)
            if not cycle["success"]:
                return _complete(report, False, "flash_or_serial_failed")

        return _complete(report, True, None)
    except subprocess.TimeoutExpired:
        return _complete(report, False, "command_timeout")


def _complete(report: dict[str, Any], success: bool, failure_code: str | None) -> dict[str, Any]:
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    report["success"] = success
    if failure_code:
        report["failure_code"] = failure_code
    return report


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run ForgeX ESP32 hardware production-beta sign-off.")
    parser.add_argument("--project", required=True, type=Path, help="PlatformIO project directory containing platformio.ini")
    parser.add_argument("--port", default=None, help="Serial/upload port, for example COM7")
    parser.add_argument("--environment", default=DEFAULT_ENVIRONMENT)
    parser.add_argument("--builds", type=int, default=DEFAULT_BUILDS)
    parser.add_argument("--flash-cycles", type=int, default=DEFAULT_FLASH_CYCLES)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--serial-timeout", type=float, default=DEFAULT_SERIAL_TIMEOUT)
    parser.add_argument("--pio", default="pio")
    parser.add_argument("--report", type=Path, default=Path("hardware-signoff-reports") / "esp32dev-signoff.json")
    args = parser.parse_args(argv)

    try:
        report = run_hardware_signoff(
            project_dir=args.project,
            port=args.port,
            environment=args.environment,
            builds=args.builds,
            flash_cycles=args.flash_cycles,
            baud=args.baud,
            serial_timeout=args.serial_timeout,
            pio=args.pio,
        )
    except ValueError as exc:
        print(f"HARDWARE_SIGNOFF_INVALID: {exc}", file=sys.stderr)
        return 2

    write_report(report, args.report)
    if report["success"]:
        print(f"HARDWARE_SIGNOFF_PASS: {args.report}")
        return 0
    print(f"HARDWARE_SIGNOFF_FAIL: {report.get('failure_code')} report={args.report}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
