"""Safe auth/status policy for local bridge tools."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from .models import BridgeAuthStatus, BridgeSetupAction, BridgeStatusConfidence


STATUS_TIMEOUT_S = 4.0
MAX_STATUS_OUTPUT = 500

AllowedAuthCheck = Literal["discovery", "version", "help", "documented_status"]

ALLOWED_CHECKS: tuple[str, ...] = (
    "executable discovery",
    "version command",
    "help command",
    "documented non-mutating status command",
    "bounded timeout",
    "capped stdout/stderr",
    "subprocess argv with shell=False",
)

FORBIDDEN_CHECKS: tuple[str, ...] = (
    "reading auth config or token files",
    "reading browser cookies",
    "reading OAuth session stores",
    "writing to home directory",
    "launching login automatically",
    "sending prompts",
    "running commands that modify project files",
)

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class SafeStatusCommand:
    args: tuple[str, ...]
    description: str
    help_args: tuple[str, ...] = ("--help",)

    def label(self, command_name: str) -> str:
        return " ".join((command_name, *self.args))


@dataclass(frozen=True, slots=True)
class BridgeAuthProbeResult:
    auth_status: BridgeAuthStatus = "unknown"
    status_confidence: BridgeStatusConfidence = "low"
    auth_message: str = "Installed, but auth cannot be safely confirmed in detection-only mode."
    setup_hint: str | None = "Run the official tool's login flow manually if authentication is required."
    setup_action: BridgeSetupAction = "run_official_login_manually"
    safe_status_checked: bool = False
    checked_commands: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


class BridgeAuthPolicy:
    """Runs only allowlisted non-mutating checks and parses simple status output."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner,
        timeout_s: float = STATUS_TIMEOUT_S,
        max_output: int = MAX_STATUS_OUTPUT,
    ) -> None:
        self._command_runner = command_runner
        self._timeout_s = timeout_s
        self._max_output = max_output

    def detect_auth_status(
        self,
        *,
        executable: str,
        executable_prefix: Sequence[str] = (),
        command_name: str,
        safe_status_commands: Sequence[SafeStatusCommand],
        unknown_message: str,
    ) -> BridgeAuthProbeResult:
        checked: list[str] = []
        warnings: list[str] = []
        help_args = safe_status_commands[0].help_args if safe_status_commands else ("--help",)
        help_label = " ".join((command_name, *help_args))
        checked.append(help_label)
        try:
            help_result = self._run([executable, *executable_prefix, *help_args], probe_args=help_args)
        except subprocess.TimeoutExpired:
            return BridgeAuthProbeResult(
                auth_message=unknown_message,
                safe_status_checked=True,
                checked_commands=tuple(checked),
                warnings=("Auth help command timed out.",),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return BridgeAuthProbeResult(
                auth_message=unknown_message,
                safe_status_checked=True,
                checked_commands=tuple(checked),
                warnings=(f"Auth help command failed: {type(exc).__name__}.",),
            )

        help_output, help_truncated = self._safe_output(help_result)
        if help_truncated:
            warnings.append("Auth help output was truncated.")

        for status_command in safe_status_commands:
            if not help_mentions_command(help_output, status_command.args):
                continue
            label = status_command.label(command_name)
            checked.append(label)
            try:
                status_result = self._run(
                    [executable, *executable_prefix, *status_command.args],
                    probe_args=status_command.args,
                )
            except subprocess.TimeoutExpired:
                warnings.append("Auth status command timed out.")
                return BridgeAuthProbeResult(
                    auth_message="Safe status command timed out; auth remains unknown.",
                    safe_status_checked=True,
                    checked_commands=tuple(checked),
                    warnings=tuple(warnings),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                warnings.append(f"Auth status command failed: {type(exc).__name__}.")
                return BridgeAuthProbeResult(
                    auth_message="Safe status command failed; auth remains unknown.",
                    safe_status_checked=True,
                    checked_commands=tuple(checked),
                    warnings=tuple(warnings),
                )

            status_output, status_truncated = self._safe_output(status_result)
            if status_truncated:
                warnings.append("Auth status output was truncated.")
            parsed = parse_auth_status(status_output)
            if parsed == "authenticated":
                return BridgeAuthProbeResult(
                    auth_status="authenticated",
                    status_confidence="high",
                    auth_message="Detected via safe non-mutating status command.",
                    setup_hint=None,
                    setup_action="none",
                    safe_status_checked=True,
                    checked_commands=tuple(checked),
                    warnings=tuple(warnings),
                )
            if parsed == "unauthenticated":
                return BridgeAuthProbeResult(
                    auth_status="unauthenticated",
                    status_confidence="high",
                    auth_message="Safe status command reports no authenticated local session.",
                    setup_hint="Run the official tool's login command manually.",
                    setup_action="run_official_login_manually",
                    safe_status_checked=True,
                    checked_commands=tuple(checked),
                    warnings=tuple(warnings),
                )
            if status_result.returncode != 0:
                warnings.append("Safe status command exited without a recognized auth state.")
                return BridgeAuthProbeResult(
                    auth_status="error",
                    auth_message="Safe status command failed; no credential output was retained.",
                    status_confidence="high",
                    setup_hint="Run the official tool's login command manually.",
                    setup_action="run_official_login_manually",
                    safe_status_checked=True,
                    checked_commands=tuple(checked),
                    warnings=tuple(warnings),
                )
            return BridgeAuthProbeResult(
                auth_message="Safe status command did not report a recognizable auth state.",
                status_confidence="medium",
                setup_hint="Use the official tool to confirm login state.",
                setup_action="run_official_login_manually",
                safe_status_checked=True,
                checked_commands=tuple(checked),
                warnings=tuple(warnings),
            )

        return BridgeAuthProbeResult(
            auth_message=unknown_message,
            safe_status_checked=True,
            checked_commands=tuple(checked),
            warnings=tuple(warnings),
        )

    def _run(
        self,
        args: Sequence[str],
        *,
        probe_args: Sequence[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        policy_args = [args[0], *(probe_args if probe_args is not None else args[1:])]
        if not is_allowed_probe_args(policy_args):
            raise ValueError("Unsafe bridge auth probe command")
        return self._command_runner(
            list(args),
            capture_output=True,
            text=True,
            timeout=self._timeout_s,
            shell=False,
        )

    def _safe_output(self, result: subprocess.CompletedProcess[str]) -> tuple[str, bool]:
        output = normalize_command_output(result.stdout, result.stderr)
        if len(output) <= self._max_output:
            return output, False
        return output[: self._max_output].rstrip(), True


def is_allowed_probe_args(args: Sequence[str]) -> bool:
    if len(args) < 2:
        return False
    command_args = tuple(args[1:])
    if command_args in {("--version",), ("--help",), ("-h",)}:
        return True
    if command_args in {("login", "--help"), ("login", "status")}:
        return True
    return not any(_looks_interactive_or_mutating(arg) for arg in command_args)


def parse_auth_status(output: str) -> BridgeAuthStatus:
    text = output.casefold()
    # The OAuth bridge must not treat API-key or agent-identity sessions as a
    # ChatGPT OAuth session. Keep those modes unknown without retaining output.
    if "logged in using an api key" in text or "logged in using agent identity" in text:
        return "unknown"
    unauthenticated_patterns = (
        r"\bnot\s+authenticated\b",
        r"\bnot\s+logged\s+in\b",
        r"\bnot\s+signed\s+in\b",
        r"\bunauthenticated\b",
        r"\blogin\s+required\b",
        r"\bauthentication\s+required\b",
        r"\bsign\s+in\s+required\b",
        r"\blogged\s+out\b",
    )
    authenticated_patterns = (
        r"\bauthenticated\b",
        r"\blogged\s+in\b",
        r"\bsigned\s+in\b",
        r"\blogin:\s*true\b",
        r"\bauthenticated:\s*true\b",
        r"\baccount:\s*\S+",
    )
    if any(re.search(pattern, text) for pattern in unauthenticated_patterns):
        return "unauthenticated"
    if any(re.search(pattern, text) for pattern in authenticated_patterns):
        return "authenticated"
    return "unknown"


def help_mentions_command(help_output: str, command_args: Sequence[str]) -> bool:
    if not command_args:
        return False
    normalized = help_output.casefold()
    phrase = " ".join(command_args).casefold()
    if phrase in normalized:
        return True
    return all(re.search(rf"\b{re.escape(arg.casefold())}\b", normalized) for arg in command_args)


def normalize_command_output(stdout: str | None, stderr: str | None) -> str:
    combined = "\n".join(part.strip() for part in (stdout or "", stderr or "") if part and part.strip())
    return " ".join(combined.split())


def _looks_interactive_or_mutating(arg: str) -> bool:
    return arg.casefold() in {
        "login",
        "logout",
        "init",
        "run",
        "generate",
        "edit",
        "apply",
        "write",
        "create",
        "delete",
        "remove",
    }
