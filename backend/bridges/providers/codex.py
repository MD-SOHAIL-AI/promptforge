"""Safe status-only bridge to authentication owned by the official Codex CLI."""

from __future__ import annotations

import re
import shutil
import tempfile
import os
from dataclasses import replace
from pathlib import Path

from ..auth_policy import SafeStatusCommand
from ..base import BridgeDetector, DetectedExecutable
from ..models import BridgeDetectionResult


class CodexDetector(BridgeDetector):
    provider_id = "codex_cli_oauth_bridge"
    display_name = "Codex CLI OAuth Bridge"
    command_names = ("codex",)
    auth_mode = "official_codex_cli_oauth"
    execution_mode = "login_helper"
    workspace_mode = "none_for_login"
    production_eligible = False
    qa_only = True
    safe_status_commands = (
        SafeStatusCommand(
            ("login", "status"),
            "Official Codex CLI login status",
            help_args=("login", "--help"),
        ),
    )
    unknown_auth_message = (
        "Codex is installed, but the official CLI did not report a recognizable ChatGPT OAuth state."
    )

    _SAFE_VERSION = re.compile(r"^codex(?:-cli)?\s+\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")

    def _run(self, args, *, timeout):  # type: ignore[no-untyped-def]
        return self._command_runner(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            cwd=tempfile.gettempdir(),
        )

    def _auth_command_runner(self):  # type: ignore[no-untyped-def]
        def run(args, **kwargs):  # type: ignore[no-untyped-def]
            return self._command_runner(args, cwd=tempfile.gettempdir(), **kwargs)

        return run

    def _find_executable(self) -> DetectedExecutable | None:
        if self._is_windows() and self._uses_default_command_runner:
            node = shutil.which("node")
            for entry in os.environ.get("PATH", "").split(os.pathsep):
                if not entry:
                    continue
                native = Path(entry) / "codex.exe"
                if native.is_file():
                    return DetectedExecutable(path=str(native), command_name="codex")
                npm_entry = Path(entry) / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
                if node and npm_entry.is_file():
                    return DetectedExecutable(
                        path=node,
                        command_name="codex",
                        prefix_args=(str(npm_entry),),
                    )
        executable = super()._find_executable()
        if executable is None or not self._is_windows():
            return executable
        launcher = Path(executable.path)
        if launcher.suffix.casefold() not in {".cmd", ".bat"}:
            return executable
        npm_entry = launcher.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        node = shutil.which("node")
        if node and npm_entry.is_file():
            return DetectedExecutable(
                path=node,
                command_name="codex",
                prefix_args=(str(npm_entry),),
            )
        return executable

    def resolve_executable(self) -> DetectedExecutable | None:
        """Resolve a native launcher without exposing its path through the API."""

        return self._find_executable()

    def detect(self) -> BridgeDetectionResult:
        result = super().detect()
        if not result.installed:
            detection = "CODEX_CLI_DETECTION_FAILED" if self._discovery_failed else "CODEX_CLI_NOT_FOUND"
            return replace(
                result,
                executable_path=None,
                detection_classification=detection,
                auth_classification=(
                    "CODEX_OAUTH_STATUS_FAILED"
                    if self._discovery_failed
                    else "CODEX_OAUTH_NOT_INSTALLED"
                ),
                login_command="codex login",
                smoke_classification=(
                    "CODEX_STANDALONE_SMOKE_BLOCKED_DETECTION_FAILED"
                    if self._discovery_failed
                    else "CODEX_STANDALONE_SMOKE_BLOCKED_CLI_NOT_FOUND"
                ),
                reason="QA-only login/status helper; production Codex editing is disabled.",
            )

        status_was_run = "codex login status" in result.checked_commands
        unsupported_version = any("supported sanitized format" in warning for warning in result.warnings)
        if unsupported_version:
            detection = "CODEX_CLI_VERSION_UNSUPPORTED"
        elif result.version is None:
            detection = "CODEX_CLI_DETECTION_FAILED"
        elif not status_was_run:
            detection = "CODEX_CLI_VERSION_UNSUPPORTED"
        else:
            detection = "CODEX_CLI_DETECTED"

        auth_classification = {
            "authenticated": "CODEX_OAUTH_AUTHENTICATED",
            "unauthenticated": "CODEX_OAUTH_NOT_AUTHENTICATED",
            "error": "CODEX_OAUTH_STATUS_FAILED",
        }.get(result.auth_status, "CODEX_OAUTH_STATUS_UNKNOWN")
        smoke_ready = detection == "CODEX_CLI_DETECTED" and result.auth_status == "authenticated"
        return replace(
            result,
            executable_path=None,
            detection_classification=detection,
            auth_classification=auth_classification,
            login_command="codex login",
            smoke_ready=smoke_ready,
            smoke_classification=(
                "CODEX_STANDALONE_SMOKE_READY"
                if smoke_ready
                else "CODEX_STANDALONE_SMOKE_BLOCKED"
            ),
            reason="QA-only login/status helper; production Codex editing is disabled.",
        )

    def _detect_version(self, executable):  # type: ignore[no-untyped-def]
        version, warnings, label = super()._detect_version(executable)
        if version is not None and not self._SAFE_VERSION.fullmatch(version):
            return None, [*warnings, "Version output was not in the supported sanitized format."], label
        return version, warnings, label
