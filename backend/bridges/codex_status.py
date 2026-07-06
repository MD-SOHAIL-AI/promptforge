"""Aligned, status-only access to authentication owned by the official Codex CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import DetectedExecutable


SAFE_USER_ENV_KEYS = (
    "PATH", "Path", "PATHEXT", "SystemRoot", "WINDIR", "ComSpec", "TEMP", "TMP",
    "USERPROFILE", "USERNAME", "USERDOMAIN", "LOGONSERVER", "SESSIONNAME", "HOME",
    "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "ProgramData",
    "ProgramFiles", "ProgramFiles(x86)", "CommonProgramFiles", "OneDrive", "SystemDrive",
    "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "OS", "CI", "FORGEX_TEST_MODE",
    "FORGEX_ENABLE_CODEX_SMOKE",
)
FORBIDDEN_ENV_FRAGMENTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE", "CREDENTIAL", "AUTH")
SAFE_VERSION = re.compile(r"^codex(?:-cli)?\s+\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
STATUS_HELP = re.compile(r"\bstatus\b\s+Show login status", re.IGNORECASE)
SIGNED_IN = re.compile(r"\b(?:logged in(?: using chatgpt)?|signed in|authenticated(?: using chatgpt)?|chatgpt)\b", re.IGNORECASE)
SIGNED_OUT = re.compile(r"\b(?:not logged in|login required|not authenticated|signed out)\b", re.IGNORECASE)

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def build_codex_safe_user_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return only Windows user-tool variables; secret-like names are always excluded."""

    values = source if source is not None else os.environ
    result: dict[str, str] = {}
    for name in SAFE_USER_ENV_KEYS:
        value = values.get(name)
        if not isinstance(value, str):
            continue
        upper = name.upper()
        if any(fragment in upper for fragment in FORBIDDEN_ENV_FRAGMENTS):
            continue
        result[name] = value
    return result


# Public camelCase alias used by parity/safety checks and cross-runtime documentation.
buildCodexSafeUserEnv = build_codex_safe_user_env


@dataclass(frozen=True, slots=True)
class CodexAlignedStatus:
    codex_installed: bool = False
    codex_version: str | None = None
    auth_status: str = "unknown"
    auth_classification: str = "CODEX_AUTH_STATUS_UNKNOWN"
    bridge_classification: str = "CODEX_OAUTH_BRIDGE_STATUS_UNKNOWN"
    oauth_bridge_ready: bool = False
    status_command_available: bool = False
    exit_code_category: str = "failed"
    parser_flags: dict[str, bool] | None = None

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "provider_id": "codex_cli_oauth_bridge",
            "codex_installed": self.codex_installed,
            "codex_version": self.codex_version,
            "auth_status": self.auth_status,
            "auth_classification": self.auth_classification,
            "bridge_classification": self.bridge_classification,
            "oauth_bridge_ready": self.oauth_bridge_ready,
            "status_command_available": self.status_command_available,
            "status_runner": "resolved_executable_runner",
            "env_profile": "codex_safe_user_env",
            "safe_env_profile": "codex_safe_user_env",
            "cwd_kind": "neutral_temp",
            "exit_code_category": self.exit_code_category,
            "parser_flags": self.parser_flags or {
                "contains_logged_in_phrase": False,
                "contains_chatgpt_phrase": False,
                "contains_not_logged_in_phrase": False,
                "contains_login_required_phrase": False,
            },
            "tokens_read": False,
            "auth_files_read": False,
            "raw_output_persisted": False,
            "production_routing_enabled": False,
            "qa_only": True,
        }


class CodexStatusService:
    """Runs only official, non-mutating Codex status commands in a neutral directory."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner | None = None,
        env: Mapping[str, str] | None = None,
        platform_name: str | None = None,
        temp_root: str | Path | None = None,
        executable: DetectedExecutable | None = None,
    ) -> None:
        self._command_runner = command_runner or subprocess.run
        self._env = env if env is not None else os.environ
        self._platform_name = platform_name or os.name
        self._temp_root = Path(temp_root) if temp_root is not None else Path(tempfile.gettempdir())
        self._executable = executable

    def status(self) -> CodexAlignedStatus:
        safe_env = build_codex_safe_user_env(self._env)
        executable = self._executable or self.resolve_executable(safe_env)
        if executable is None:
            return CodexAlignedStatus(exit_code_category="failed")
        try:
            with tempfile.TemporaryDirectory(prefix="forgex-codex-status-", dir=self._temp_root) as raw_cwd:
                cwd = Path(raw_cwd).resolve()
                if not self._is_neutral_cwd(cwd):
                    return CodexAlignedStatus(
                        auth_classification="CODEX_STATUS_UNSAFE_CWD_ABORTED",
                        bridge_classification="CODEX_STATUS_UNSAFE_CWD_ABORTED",
                        exit_code_category="failed",
                    )
                version_result = self._run(executable, ("--version",), cwd, safe_env)
                version_output = self._bounded_output(version_result)
                installed = not self._missing(version_result)
                version = version_output if version_result.returncode == 0 and SAFE_VERSION.fullmatch(version_output) else None
                if not installed or version is None:
                    return CodexAlignedStatus(codex_installed=installed, exit_code_category=self._category(version_result))

                help_result = self._run(executable, ("login", "--help"), cwd, safe_env)
                supported = help_result.returncode == 0 and bool(STATUS_HELP.search(self._bounded_output(help_result)))
                if not supported:
                    return CodexAlignedStatus(
                        codex_installed=True, codex_version=version,
                        auth_classification="CODEX_AUTH_STATUS_COMMAND_UNAVAILABLE",
                        exit_code_category=self._category(help_result),
                    )

                status_result = self._run(executable, ("login", "status"), cwd, safe_env)
                output = self._bounded_output(status_result)
                auth_status = self._classify_auth(status_result.returncode, output)
                parser_flags = self._parser_flags(output)
                ready = auth_status == "signed_in"
                return CodexAlignedStatus(
                    codex_installed=True,
                    codex_version=version,
                    auth_status=auth_status,
                    auth_classification=(
                        "CODEX_AUTH_STATUS_SIGNED_IN" if ready else
                        "CODEX_AUTH_STATUS_SIGNED_OUT" if auth_status == "signed_out" else
                        "CODEX_AUTH_STATUS_UNKNOWN"
                    ),
                    bridge_classification=(
                        "CODEX_OAUTH_BRIDGE_READY" if ready else
                        "CODEX_OAUTH_BRIDGE_LOGIN_REQUIRED" if auth_status == "signed_out" else
                        "CODEX_OAUTH_BRIDGE_STATUS_UNKNOWN"
                    ),
                    oauth_bridge_ready=ready,
                    status_command_available=True,
                    exit_code_category=self._category(status_result),
                    parser_flags=parser_flags,
                )
        except subprocess.TimeoutExpired:
            return CodexAlignedStatus(
                auth_classification="CODEX_AUTH_STATUS_CHECK_FAILED",
                exit_code_category="timeout",
            )
        except (OSError, subprocess.SubprocessError):
            return CodexAlignedStatus(
                auth_classification="CODEX_AUTH_STATUS_CHECK_FAILED",
                exit_code_category="failed",
            )

    def resolve_executable(self, safe_env: Mapping[str, str] | None = None) -> DetectedExecutable | None:
        env = safe_env if safe_env is not None else build_codex_safe_user_env(self._env)
        path_value = env.get("PATH") or env.get("Path") or ""
        windows = str(self._platform_name).casefold() in {"nt", "windows", "win32"}
        if windows:
            found = shutil.which("codex", path=path_value)
            if found:
                return DetectedExecutable(found, "codex")
            node = shutil.which("node", path=path_value)
            for entry in path_value.split(os.pathsep):
                if not entry:
                    continue
                native = Path(entry) / "codex.exe"
                if native.is_file():
                    return DetectedExecutable(str(native), "codex")
                npm_entry = Path(entry) / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
                if node and npm_entry.is_file():
                    return DetectedExecutable(node, "codex", (str(npm_entry),))
        found = shutil.which("codex", path=path_value)
        return DetectedExecutable(found, "codex") if found else None

    def _run(self, executable: DetectedExecutable, args: Sequence[str], cwd: Path, env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
        suffix = Path(executable.path).suffix.casefold()
        command = [executable.path, *executable.prefix_args, *args]
        if suffix in {".cmd", ".bat"}:
            comspec = env.get("ComSpec") or env.get("COMSPEC") or "cmd.exe"
            quoted = subprocess.list2cmdline(command)
            command = [comspec, "/d", "/s", "/c", quoted]
        elif suffix == ".ps1":
            command = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", *command]
        return self._command_runner(
            command,
            cwd=str(cwd), env=dict(env), capture_output=True, text=True, input="",
            timeout=5.0, shell=False, check=False,
        )

    def _is_neutral_cwd(self, cwd: Path) -> bool:
        try:
            root = self._temp_root.resolve()
            cwd.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return False
        if cwd == root or cwd == Path(cwd.anchor):
            return False
        try:
            home = Path.home().resolve()
            current = Path.cwd().resolve()
        except (OSError, RuntimeError):
            return False
        if cwd in {home, home / "Desktop", home / "OneDrive"}:
            return False
        for candidate in (home / "Desktop", home / "OneDrive", current):
            try:
                cwd.relative_to(candidate)
                return False
            except ValueError:
                pass
        return True

    @staticmethod
    def _bounded_output(result: subprocess.CompletedProcess[str]) -> str:
        combined = " ".join(part for part in (result.stdout or "", result.stderr or "") if part)
        return " ".join(combined.split())[:1000]

    @staticmethod
    def _classify_auth(returncode: int, output: str) -> str:
        if SIGNED_OUT.search(output):
            return "signed_out"
        if returncode == 0 and SIGNED_IN.search(output):
            return "signed_in"
        return "unknown"

    @staticmethod
    def _parser_flags(output: str) -> dict[str, bool]:
        return {
            "contains_logged_in_phrase": bool(re.search(r"\blogged in\b", output, re.IGNORECASE)),
            "contains_chatgpt_phrase": bool(re.search(r"\bchatgpt\b", output, re.IGNORECASE)),
            "contains_not_logged_in_phrase": bool(re.search(r"\bnot logged in\b", output, re.IGNORECASE)),
            "contains_login_required_phrase": bool(re.search(r"\blogin required\b", output, re.IGNORECASE)),
        }

    @staticmethod
    def _category(result: subprocess.CompletedProcess[str]) -> str:
        return "success" if result.returncode == 0 else "nonzero"

    @staticmethod
    def _missing(result: subprocess.CompletedProcess[str]) -> bool:
        return result.returncode == 127


def _main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--safe-json", action="store_true")
    args = parser.parse_args()
    if not args.safe_json:
        return 2
    print(json.dumps(CodexStatusService().status().to_safe_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
