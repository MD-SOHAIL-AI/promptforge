"""Feature-flagged AGY sandbox dry-run support."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..audit_log import BridgeAuditLog, hash_workspace_root
from ..diff_service import IGNORED_DIRS, BridgeDiffService
from ..review_models import BridgeAuditEntry, BridgeChangedFile, BridgeWorkspaceSnapshot
from ..run_models import BridgeSandboxRun
from ..sandbox_service import BridgeSandboxError, BridgeSandboxService


AGY_PROVIDER_ID = "antigravity_cli_bridge"
MAX_OUTPUT_PREVIEW = 4_000
DEFAULT_TIMEOUT_SECONDS = 300
MAX_TIMEOUT_SECONDS = 900
DIFF_SETTLE_ATTEMPTS = 3
DIFF_SETTLE_DELAY_SECONDS = 0.1
SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "COOKIE", "CREDENTIAL")
SAFE_ENV_NAMES = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "HOME",
    "USERPROFILE",
    "TMP",
    "TEMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
}


class AntigravityRunnerError(ValueError):
    code = "AGY_SANDBOX_RUN_ERROR"

    def __init__(self, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class AntigravitySandboxRunner:
    def __init__(
        self,
        *,
        sandbox_service: BridgeSandboxService,
        review_service: BridgeDiffService,
        audit_log: BridgeAuditLog | None = None,
        executable_resolver: Callable[[str], str | None] | None = None,
        popen_factory: Callable[..., subprocess.Popen[str]] | None = None,
        env: dict[str, str] | None = None,
        feature_enabled: bool | None = None,
    ) -> None:
        self.sandbox_service = sandbox_service
        self.review_service = review_service
        self.audit_log = audit_log
        self.executable_resolver = executable_resolver or shutil.which
        self.popen_factory = popen_factory or subprocess.Popen
        self.env = env if env is not None else os.environ
        self.feature_enabled = feature_enabled
        self._runs: dict[str, BridgeSandboxRun] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    def is_enabled(self) -> bool:
        if self.feature_enabled is not None:
            return self.feature_enabled
        return self.env.get("FORGEX_ENABLE_AGY_BRIDGE") == "1"

    def start_run(self, *, workspace_root: str | Path, prompt: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> BridgeSandboxRun:
        if not self.is_enabled():
            raise AntigravityRunnerError(
                "AGY sandbox execution is disabled. Enable FORGEX_ENABLE_AGY_BRIDGE=1 for development testing.",
                {"feature_flag": "FORGEX_ENABLE_AGY_BRIDGE"},
            )
        prompt_text = prompt.strip()
        if not prompt_text:
            raise AntigravityRunnerError("Prompt is required for an AGY sandbox run.")
        executable = self._resolve_executable()
        timeout = max(1, min(int(timeout_seconds), MAX_TIMEOUT_SECONDS))
        root = Path(workspace_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise AntigravityRunnerError("Workspace root must be an existing directory.", {"workspace_root": str(root)})
        run = BridgeSandboxRun(
            provider_id=AGY_PROVIDER_ID,
            workspace_root_hash=hash_workspace_root(str(root)),
            sandbox_root="",
        )
        with self._lock:
            self._runs[run.run_id] = run
        self._record_audit("agy_sandbox_run_requested", run)
        worker = threading.Thread(
            target=self._execute_run,
            args=(run.run_id, root, prompt_text, executable, timeout, False),
            daemon=True,
        )
        worker.start()
        return run

    def start_run_in_sandbox(
        self,
        *,
        run_id: str,
        sandbox_root: str | Path,
        prompt: str,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        baseline_root: str | Path | None = None,
    ) -> BridgeSandboxRun:
        """Run in a coordinator-validated existing sandbox without creating a second copy."""

        if not self.is_enabled():
            raise AntigravityRunnerError("AGY sandbox execution is disabled.")
        prompt_text = prompt.strip()
        if not prompt_text:
            raise AntigravityRunnerError("Prompt is required for an AGY sandbox run.")
        executable = self._resolve_executable()
        timeout = max(1, min(int(timeout_seconds), MAX_TIMEOUT_SECONDS))
        root = Path(sandbox_root).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise AntigravityRunnerError("Sandbox root must be an existing directory.")
        run = BridgeSandboxRun(
            provider_id=AGY_PROVIDER_ID,
            workspace_root_hash=hash_workspace_root(str(root)),
            sandbox_root=str(root),
            run_id=run_id,
        )
        with self._lock:
            if run_id in self._runs:
                return self._runs[run_id]
            self._runs[run_id] = run
        self._record_audit("agy_sandbox_run_requested", run)
        worker = threading.Thread(
            target=self._execute_run,
            args=(run.run_id, root, prompt_text, executable, timeout, True, Path(baseline_root).expanduser().resolve() if baseline_root else None),
            daemon=True,
        )
        worker.start()
        return run

    def get_run(self, run_id: str) -> BridgeSandboxRun:
        with self._lock:
            run = self._runs.get(run_id)
        if run is None:
            raise AntigravityRunnerError("AGY sandbox run was not found.", {"run_id": run_id})
        return run

    def list_runs(self) -> tuple[BridgeSandboxRun, ...]:
        with self._lock:
            runs = tuple(self._runs.values())
        return tuple(sorted(runs, key=lambda item: item.started_at, reverse=True))

    def cancel_run(self, run_id: str) -> BridgeSandboxRun:
        run = self.get_run(run_id)
        with self._lock:
            self._cancelled.add(run_id)
            process = self._processes.get(run_id)
        if process is not None and process.poll() is None:
            kill_process_tree(process)
        run.status = "cancelled"
        run.diagnostics.exit_code_classification = "cancelled"
        run.completed_at = datetime.now(timezone.utc)
        run.error_message = "AGY sandbox run was cancelled."
        self._record_audit("agy_run_cancelled", run)
        return run

    def _execute_run(
        self,
        run_id: str,
        workspace_root: Path,
        prompt: str,
        executable: str,
        timeout_seconds: int,
        reuse_sandbox: bool = False,
        baseline_root: Path | None = None,
    ) -> None:
        run = self.get_run(run_id)
        try:
            if reuse_sandbox:
                sandbox_root = workspace_root
            else:
                self.review_service.snapshot_workspace(workspace_root)
                sandbox_root = self.sandbox_service.create_sandbox(run_id=run_id, workspace_root=workspace_root)
            run.sandbox_root = str(sandbox_root)
            run.diagnostics.sandbox_entered = True
            run.diagnostics.working_directory_identity = "sandbox"
            run.diagnostics.sandbox_marker_exists = (sandbox_root / "QA_NOT_REAL_PROJECT.txt").is_file()
            run.diagnostics.instruction_length = len(prompt)
            run.diagnostics.instruction_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            self._record_audit("agy_sandbox_reused" if reuse_sandbox else "agy_sandbox_created", run)
            if reuse_sandbox and baseline_root is not None:
                active_baseline = self.review_service.inspect_workspace(baseline_root)
                copied_baseline = self.review_service.inspect_workspace(sandbox_root)
                if snapshot_fingerprints(active_baseline) != snapshot_fingerprints(copied_baseline):
                    raise AntigravityRunnerError("Trusted AGY workspace baseline verification failed.")
                baseline = BridgeWorkspaceSnapshot(
                    str(sandbox_root),
                    active_baseline.files,
                    workspace_root_hash=hash_workspace_root(str(sandbox_root)),
                )
            else:
                baseline = self.review_service.snapshot_workspace(sandbox_root)
            ignored_before = ignored_file_fingerprints(sandbox_root)
            run.diagnostics.pre_run_file_count = len(baseline.files)
            # Instructions remain runtime-only. They are passed directly to
            # the fixed AGY invocation and are never written into the sandbox.
            # AGY 1.0.14 documents -p as its non-interactive prompt mode.
            # Workspace containment is owned by the managed sandbox cwd.
            args = [executable, "-p", prompt]
            self._validate_args(args)
            run.status = "running"
            self._record_audit("agy_sandbox_started", run)
            process = self._spawn(args, cwd=sandbox_root)
            run.diagnostics.instruction_delivered = True
            with self._lock:
                self._processes[run_id] = process
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            run.exit_code = process.returncode
            run.stdout_preview = None
            run.stderr_preview = None
            run.diagnostics.exit_code_classification = "zero" if process.returncode == 0 else "nonzero"
            run.diagnostics.provider_output_classification = classify_provider_output(stdout, stderr)
            with self._lock:
                cancelled = run_id in self._cancelled
            if cancelled:
                run.status = "cancelled"
                run.diagnostics.exit_code_classification = "cancelled"
                run.error_message = "AGY sandbox run was cancelled."
                self._record_audit("agy_run_cancelled", run)
                return
            if process.returncode != 0:
                run.status = "failed"
                run.error_message = agy_failure_message(stdout, stderr)
                self._record_audit("agy_sandbox_failed", run)
                return
            changed = self._wait_for_changed_files(baseline, sandbox_root)
            post_run = self.review_service.inspect_workspace(sandbox_root)
            ignored_after = ignored_file_fingerprints(sandbox_root)
            run.diagnostics.post_run_file_count = len(post_run.files)
            run.diagnostics.diff_changed_file_count = len(changed)
            run.diagnostics.ignored_changed_file_count = changed_fingerprint_count(ignored_before, ignored_after)
            if not changed:
                run.status = "completed_no_changes"
                run.error_message = "AGY completed but produced no sandbox changes."
                self._record_audit("no_changes_produced", run)
                return
            review = self.review_service.create_review(
                provider_id=AGY_PROVIDER_ID,
                workspace_root=sandbox_root,
                snapshot=baseline,
            )
            run.review_id = review.review_id
            run.changed_file_count = len(review.changed_files)
            run.diagnostics.review_changed_file_count = run.changed_file_count
            run.status = "review_ready"
            self._record_audit("agy_sandbox_completed", run)
            self._record_audit("agy_review_created", run)
        except subprocess.TimeoutExpired:
            with self._lock:
                process = self._processes.get(run_id)
            if process is not None:
                kill_process_tree(process)
                process.communicate()
                run.stdout_preview = None
                run.stderr_preview = None
            run.status = "failed_timeout"
            run.diagnostics.exit_code_classification = "timeout"
            run.error_message = "AGY sandbox run timed out."
            self._record_audit("agy_sandbox_failed", run)
        except (BridgeSandboxError, AntigravityRunnerError, OSError, subprocess.SubprocessError) as exc:
            run.status = "failed"
            run.diagnostics.exit_code_classification = "start_failed"
            run.error_message = str(exc)
            self._record_audit("agy_sandbox_failed", run)
        finally:
            run.completed_at = datetime.now(timezone.utc)
            with self._lock:
                self._processes.pop(run_id, None)
                self._cancelled.discard(run_id)

    def _resolve_executable(self) -> str:
        for command in ("agy", "antigravity"):
            resolved = self.executable_resolver(command)
            if resolved:
                return resolved
        raise AntigravityRunnerError(
            "AGY CLI was not found on PATH. Install Google Antigravity / AGY CLI before running a sandbox test.",
            {"provider_id": AGY_PROVIDER_ID},
        )

    def _spawn(self, args: Sequence[str], *, cwd: Path) -> subprocess.Popen[str]:
        kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "stdin": subprocess.DEVNULL,
            "text": True,
            "shell": False,
            "env": safe_bridge_env(self.env),
        }
        if os.name == "nt" and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        return self.popen_factory(list(args), **kwargs)

    def _validate_args(self, args: Sequence[str]) -> None:
        if len(args) != 3 or args[1] != "-p":
            raise AntigravityRunnerError("AGY sandbox runner refused an interactive command.")
        if any(arg == "--dangerously-skip-permissions" for arg in args):
            raise AntigravityRunnerError("AGY sandbox runner refused an unsafe permission bypass flag.")

    def _wait_for_changed_files(
        self,
        baseline: BridgeWorkspaceSnapshot,
        sandbox_root: Path,
    ) -> tuple[BridgeChangedFile, ...]:
        changed: tuple[BridgeChangedFile, ...] = ()
        for attempt in range(DIFF_SETTLE_ATTEMPTS):
            changed = self.review_service.diff_snapshot(baseline, sandbox_root)
            if changed or attempt == DIFF_SETTLE_ATTEMPTS - 1:
                return changed
            time.sleep(DIFF_SETTLE_DELAY_SECONDS)
        return changed

    def _record_audit(self, event: str, run: BridgeSandboxRun) -> None:
        if self.audit_log is None:
            return
        self.audit_log.record(
            BridgeAuditEntry(
                event=event,
                provider_id=run.provider_id,
                workspace_root_hash=run.workspace_root_hash,
                changed_file_count=run.changed_file_count,
                approved=False,
                review_id=run.review_id or run.run_id,
            )
        )


def cap_output(value: str | None, limit: int = MAX_OUTPUT_PREVIEW) -> str:
    text = value or ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n... [output truncated]\n"


def ignored_file_fingerprints(root: Path) -> dict[str, str]:
    """Hash ignored sandbox files for count-only diagnostics; values are never exposed."""

    fingerprints: dict[str, str] = {}
    for file_path in root.rglob("*"):
        if file_path.is_symlink() or not file_path.is_file():
            continue
        relative = file_path.relative_to(root)
        if not any(part.casefold() in IGNORED_DIRS for part in relative.parts[:-1]):
            continue
        fingerprints[relative.as_posix()] = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return fingerprints


def changed_fingerprint_count(before: dict[str, str], after: dict[str, str]) -> int:
    return sum(1 for name in set(before) | set(after) if before.get(name) != after.get(name))


def snapshot_fingerprints(snapshot: BridgeWorkspaceSnapshot) -> dict[str, tuple[str, int]]:
    return {name: (item.hash, item.size) for name, item in snapshot.files.items()}


def classify_provider_output(stdout: str | None, stderr: str | None) -> str:
    combined = f"{stdout or ''}\n{stderr or ''}".casefold()
    if not combined.strip():
        return "empty"
    if any(marker in combined for marker in ("sign in", "login", "authentication required")):
        return "auth_required"
    if any(marker in combined for marker in ("permission", "approval", "do you trust", "trusted workspace")):
        return "permission_or_trust_required"
    if any(marker in combined for marker in ("unknown flag", "flag provided but not defined", "invalid argument")):
        return "invalid_invocation"
    if any(marker in combined for marker in ("error", "failed", "failure")):
        return "error"
    return "nonempty"


def safe_bridge_env(source: dict[str, str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in source.items():
        upper = key.upper()
        if upper not in SAFE_ENV_NAMES:
            continue
        if any(marker in upper for marker in SECRET_ENV_MARKERS):
            continue
        env[key] = value
    return env


def kill_process_tree(process: subprocess.Popen[str]) -> None:
    pid = process.pid
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=5,
                shell=False,
            )
            if completed.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.killpg(pid, 9)
            return
        except OSError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def agy_failure_message(stdout_preview: str | None, stderr_preview: str | None) -> str:
    combined = f"{stdout_preview or ''}\n{stderr_preview or ''}".casefold()
    if "sign in" in combined or "login" in combined or "auth" in combined:
        return "AGY did not complete. Open AGY manually and complete official Google sign-in first."
    return "AGY sandbox run failed. See capped stdout/stderr previews for details."
