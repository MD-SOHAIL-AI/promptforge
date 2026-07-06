"""Confirmation-gated launcher for authentication owned by the official Codex CLI."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .codex_status import CodexAlignedStatus, CodexStatusService, build_codex_safe_user_env
from .providers.codex import CodexDetector


PROVIDER_ID = "codex_cli_oauth_bridge"


class _InjectedDetectorStatusAdapter:
    """Test compatibility adapter; production always uses CodexStatusService."""

    def __init__(self, detector: Any) -> None:
        self._detector = detector

    def status(self) -> CodexAlignedStatus:
        detected = self._detector.detect()
        available = "codex login status" in detected.checked_commands
        auth_status = (
            "signed_in" if detected.auth_status == "authenticated" and available else
            "signed_out" if detected.auth_status == "unauthenticated" and available else
            "unknown"
        )
        ready = bool(detected.installed and detected.version and auth_status == "signed_in" and available)
        return CodexAlignedStatus(
            codex_installed=detected.installed,
            codex_version=detected.version,
            auth_status=auth_status,
            auth_classification=("CODEX_AUTH_STATUS_SIGNED_IN" if ready else "CODEX_AUTH_STATUS_SIGNED_OUT" if auth_status == "signed_out" else "CODEX_AUTH_STATUS_UNKNOWN"),
            bridge_classification=("CODEX_OAUTH_BRIDGE_READY" if ready else "CODEX_OAUTH_BRIDGE_LOGIN_REQUIRED" if auth_status == "signed_out" else "CODEX_OAUTH_BRIDGE_STATUS_UNKNOWN"),
            oauth_bridge_ready=ready,
            status_command_available=available,
            exit_code_category="success" if available else "failed",
        )

    def resolve_executable(self) -> Any:
        return self._detector.resolve_executable()


@dataclass(frozen=True, slots=True)
class CodexLoginLaunchResult:
    classification: str
    launched: bool = False

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification,
            "provider_id": PROVIDER_ID,
            "launched": self.launched,
            "tokens_read": False,
            "auth_files_read": False,
            "production_routing_enabled": False,
        }


class CodexLoginService:
    """Launches only ``codex login`` and never inspects CLI-owned credentials."""

    def __init__(
        self,
        *,
        detector: CodexDetector | None = None,
        status_service: CodexStatusService | None = None,
        popen_factory: Callable[..., Any] | None = None,
        temp_root: str | Path | None = None,
    ) -> None:
        self._detector = detector or CodexDetector()
        self._status_service = status_service or (
            _InjectedDetectorStatusAdapter(detector) if detector is not None
            else CodexStatusService(temp_root=temp_root)
        )
        self._popen = popen_factory or subprocess.Popen
        self._temp_root = Path(temp_root) if temp_root is not None else Path(tempfile.gettempdir())

    def status(self) -> CodexAlignedStatus:
        return self._status_service.status()

    def status_diagnostics(self) -> dict[str, object]:
        runners = []
        for name in ("qa_status", "backend_status", "aligned_status"):
            status = self.status()
            runners.append({
                "runner_name": name,
                "codex_found": status.codex_installed,
                "codex_version_detected": status.codex_version is not None,
                "auth_status": status.auth_status,
                "exit_code_category": status.exit_code_category,
                "env_profile": "aligned",
                "cwd_kind": "neutral_temp",
                "raw_output_persisted": False,
                "auth_files_read": False,
                "tokens_read": False,
            })
        auth = [str(item["auth_status"]) for item in runners]
        classification = (
            "CODEX_STATUS_DIAGNOSTICS_ALL_SIGNED_OUT" if all(value == "signed_out" for value in auth)
            else "CODEX_STATUS_DIAGNOSTICS_MISMATCH" if len(set(auth)) > 1
            else "CODEX_STATUS_DIAGNOSTICS_PASS" if all(value == "signed_in" for value in auth)
            else "CODEX_STATUS_DIAGNOSTICS_UNKNOWN"
        )
        return {"classification": classification, "runners": runners, "production_routing_enabled": False}

    def launch(self, *, confirm_launch_codex_login: bool) -> CodexLoginLaunchResult:
        if confirm_launch_codex_login is not True:
            return CodexLoginLaunchResult("CODEX_LOGIN_CONFIRMATION_REQUIRED")

        status = self.status()
        if not status.codex_installed:
            return CodexLoginLaunchResult("CODEX_CLI_NOT_FOUND")
        if not status.codex_version:
            return CodexLoginLaunchResult("CODEX_LOGIN_UNKNOWN_SAFE_FAILURE")

        executable = self._status_service.resolve_executable()
        if executable is None:
            return CodexLoginLaunchResult("CODEX_LOGIN_UNKNOWN_SAFE_FAILURE")

        try:
            working_directory = Path(
                tempfile.mkdtemp(prefix="forgex-codex-login-", dir=self._temp_root)
            ).resolve()
        except OSError:
            return CodexLoginLaunchResult("CODEX_LOGIN_UNSAFE_ABORTED")
        if not self._is_safe_login_directory(working_directory):
            return CodexLoginLaunchResult("CODEX_LOGIN_UNSAFE_ABORTED")

        kwargs: dict[str, object] = {
            "cwd": str(working_directory),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "shell": False,
            "env": build_codex_safe_user_env(os.environ),
            "close_fds": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True

        try:
            self._popen(
                [executable.path, *executable.prefix_args, "login"],
                **kwargs,
            )
        except (OSError, subprocess.SubprocessError):
            return CodexLoginLaunchResult("CODEX_LOGIN_COMMAND_FAILED")
        return CodexLoginLaunchResult("CODEX_LOGIN_LAUNCHED", launched=True)

    def _is_safe_login_directory(self, directory: Path) -> bool:
        try:
            temp_root = self._temp_root.resolve()
            home = Path.home().resolve()
            current = Path.cwd().resolve()
            directory.relative_to(temp_root)
        except (OSError, RuntimeError, ValueError):
            return False
        def within(candidate: Path, root: Path) -> bool:
            try:
                candidate.relative_to(root)
                return True
            except ValueError:
                return False

        return (
            directory != temp_root
            and directory != home
            and not within(directory, current)
            and not within(directory, home / "Desktop")
            and not within(directory, home / "OneDrive")
        )


def launch_codex_login(*, confirm_launch_codex_login: bool = False) -> dict[str, object]:
    """Public service function used by non-injected callers."""

    return CodexLoginService().launch(
        confirm_launch_codex_login=confirm_launch_codex_login
    ).to_safe_dict()
