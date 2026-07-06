"""Shared safe detector implementation for command-line tool bridges."""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .auth_policy import BridgeAuthPolicy, SafeStatusCommand
from .models import BridgeDetectionResult


logger = logging.getLogger(__name__)

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

VERSION_TIMEOUT_S = 4.0
DISCOVERY_TIMEOUT_S = 3.0
MAX_VERSION_OUTPUT = 200


@dataclass(frozen=True, slots=True)
class DetectedExecutable:
    path: str
    command_name: str
    prefix_args: tuple[str, ...] = ()


class BridgeDetector:
    provider_id: str
    display_name: str
    command_names: tuple[str, ...]
    safe_status_commands: tuple[SafeStatusCommand, ...] = ()
    unknown_auth_message = "Installed, but auth cannot be safely confirmed in detection-only mode."
    provider_kind = "local_cli"
    auth_mode = "official_cli_managed"
    execution_mode = "detection_only"
    workspace_mode = "none"
    production_eligible = False
    qa_only = True

    def __init__(
        self,
        *,
        command_runner: CommandRunner | None = None,
        platform_name: str | None = None,
    ) -> None:
        self._uses_default_command_runner = command_runner is None
        self._command_runner = command_runner or subprocess.run
        self._platform_name = platform_name
        self._discovery_failed = False

    def detect(self) -> BridgeDetectionResult:
        executable = self._find_executable()
        if executable is None:
            logger.info("Bridge detection: %s not installed", self.provider_id)
            return BridgeDetectionResult(
                provider_id=self.provider_id,
                display_name=self.display_name,
                installed=False,
                version=None,
                executable_path=None,
                auth_status="not_installed",
                auth_message="Tool executable was not found on PATH.",
                status_confidence="high",
                setup_hint="Install the official tool and run its login flow manually.",
                setup_action="open_docs",
                safe_status_checked=False,
                checked_commands=(self._command_label(self.command_names[0], "discovery"),),
                **self._identity_fields(
                    detection_classification="CLI_NOT_FOUND",
                    auth_classification="CLI_AUTH_NOT_INSTALLED",
                    executable_found=False,
                    version_detected=False,
                ),
            )

        version, warnings, version_command = self._detect_version(executable)
        auth_policy = BridgeAuthPolicy(command_runner=self._auth_command_runner())
        auth_result = auth_policy.detect_auth_status(
            executable=executable.path,
            executable_prefix=executable.prefix_args,
            command_name=executable.command_name,
            safe_status_commands=self.safe_status_commands,
            unknown_message=self.unknown_auth_message,
        )
        logger.info(
            "Bridge detection: %s installed=%s version=%s auth_status=%s confidence=%s",
            self.provider_id,
            True,
            version or "unknown",
            auth_result.auth_status,
            auth_result.status_confidence,
        )
        return BridgeDetectionResult(
            provider_id=self.provider_id,
            display_name=self.display_name,
            installed=True,
            version=version,
            executable_path=mask_executable_path(executable.path),
            auth_status=auth_result.auth_status,
            auth_message=auth_result.auth_message,
            status_confidence=auth_result.status_confidence,
            setup_hint=auth_result.setup_hint,
            setup_action=auth_result.setup_action,
            safe_status_checked=auth_result.safe_status_checked,
            checked_commands=(version_command, *auth_result.checked_commands),
            warnings=tuple((*warnings, *auth_result.warnings)),
            **self._identity_fields(
                detection_classification="CLI_DETECTED" if version else "CLI_DETECTION_FAILED",
                auth_classification=f"CLI_AUTH_{auth_result.auth_status.upper()}",
                executable_found=True,
                version_detected=version is not None,
            ),
        )

    def _identity_fields(self, **overrides: object) -> dict[str, object]:
        fields: dict[str, object] = {
            "provider_kind": self.provider_kind,
            "auth_mode": self.auth_mode,
            "execution_mode": self.execution_mode,
            "workspace_mode": self.workspace_mode,
            "production_eligible": self.production_eligible,
            "qa_only": self.qa_only,
        }
        fields.update(overrides)
        return fields

    def _find_executable(self) -> DetectedExecutable | None:
        self._discovery_failed = False
        discovery_command = "where.exe" if self._is_windows() else "which"
        for command_name in self.command_names:
            try:
                completed = self._run(
                    [discovery_command, command_name],
                    timeout=DISCOVERY_TIMEOUT_S,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                self._discovery_failed = True
                logger.info(
                    "Bridge discovery error for %s using %s: %s",
                    self.provider_id,
                    command_name,
                    type(exc).__name__,
                )
                continue
            if completed.returncode != 0:
                continue
            path = first_non_empty_line(completed.stdout)
            if path:
                return DetectedExecutable(path=path, command_name=command_name)
        return None

    def _detect_version(self, executable: DetectedExecutable) -> tuple[str | None, list[str], str]:
        command_label = self._command_label(executable.command_name, "--version")
        try:
            completed = self._run(
                [executable.path, *executable.prefix_args, "--version"],
                timeout=VERSION_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return None, ["Version detection timed out."], command_label
        except (OSError, subprocess.SubprocessError) as exc:
            return None, [f"Version detection failed: {type(exc).__name__}."], command_label
        output = normalize_version_output(completed.stdout, completed.stderr)
        warnings: list[str] = []
        if len(output) > MAX_VERSION_OUTPUT:
            output = output[:MAX_VERSION_OUTPUT].rstrip()
            warnings.append("Version output was truncated.")
        if completed.returncode != 0:
            warnings.append("Version command failed.")
            return None, warnings, command_label
        return output or None, warnings, command_label

    def _run(
        self,
        args: Sequence[str],
        *,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        return self._command_runner(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )

    def _auth_command_runner(self) -> CommandRunner:
        return self._command_runner

    def _is_windows(self) -> bool:
        return (self._platform_name or platform.system()).casefold().startswith("win")

    def _command_label(self, command_name: str, *args: str) -> str:
        return " ".join((command_name, *args))


def first_non_empty_line(value: str | None) -> str | None:
    if not value:
        return None
    for line in value.splitlines():
        text = line.strip()
        if text:
            return text
    return None


def normalize_version_output(stdout: str | None, stderr: str | None) -> str:
    combined = "\n".join(part.strip() for part in (stdout or "", stderr or "") if part and part.strip())
    return " ".join(combined.split())


def mask_executable_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    try:
        home = str(Path.home()).replace("\\", "/").rstrip("/")
    except RuntimeError:
        home = ""
    if home and normalized.casefold().startswith(home.casefold() + "/"):
        return "~/" + normalized[len(home) + 1 :]
    userprofile = os.getenv("USERPROFILE", "").replace("\\", "/").rstrip("/")
    if userprofile and normalized.casefold().startswith(userprofile.casefold() + "/"):
        return "%USERPROFILE%/" + normalized[len(userprofile) + 1 :]
    return normalized
