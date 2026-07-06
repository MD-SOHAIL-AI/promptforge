from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from backend.bridges.audit_log import BridgeAuditLog
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.providers.antigravity_runner import (
    AntigravityRunnerError,
    AntigravitySandboxRunner,
    classify_provider_output,
)
from backend.bridges.review_store import BridgeReviewStore
from backend.bridges.sandbox_service import BridgeSandboxService


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: str = "ok",
        stderr: str = "",
        returncode: int = 0,
        mutate: bool = True,
        mutate_name: str = "README.md",
        timeout: bool = False,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.mutate = mutate
        self.mutate_name = mutate_name
        self.timeout = timeout
        self.pid = 24680
        self.killed = False
        self.cwd: Path | None = None

    def communicate(self, timeout: int | None = None) -> tuple[str, str]:
        if self.timeout and not self.killed:
            import subprocess

            raise subprocess.TimeoutExpired(["agy", "-p"], timeout)
        if self.mutate and self.cwd is not None:
            write(self.cwd / self.mutate_name, "changed by sandbox\n")
        return self.stdout, self.stderr

    def poll(self) -> int | None:
        return None if not self.killed else -9

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


class RecordingPopen:
    def __init__(self, process: FakeProcess) -> None:
        self.process = process
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> FakeProcess:
        self.calls.append((args, kwargs))
        self.process.cwd = Path(kwargs["cwd"])
        return self.process


def runner(tmp_path: Path, popen: RecordingPopen, *, enabled: bool = True) -> AntigravitySandboxRunner:
    review_store = BridgeReviewStore(
        snapshots_path=tmp_path / "state" / "snapshots.jsonl",
        reviews_path=tmp_path / "state" / "reviews.jsonl",
    )
    review_service = BridgeDiffService(
        audit_log=BridgeAuditLog(tmp_path / "state" / "audit.jsonl"),
        store=review_store,
    )
    return AntigravitySandboxRunner(
        sandbox_service=BridgeSandboxService(tmp_path / "state" / "sandboxes"),
        review_service=review_service,
        audit_log=BridgeAuditLog(tmp_path / "state" / "audit.jsonl"),
        executable_resolver=lambda command: "C:/tools/agy.exe" if command == "agy" else None,
        popen_factory=popen,
        feature_enabled=enabled,
    )


def wait_for_run(service: AntigravitySandboxRunner, run_id: str):
    for _ in range(100):
        run = service.get_run(run_id)
        if run.status not in {"pending", "running"}:
            return run
        time.sleep(0.01)
    raise AssertionError("run did not complete")


def test_agy_sandbox_run_disabled_when_feature_flag_missing(tmp_path: Path) -> None:
    write(tmp_path / "workspace" / "README.md", "base\n")
    service = runner(tmp_path, RecordingPopen(FakeProcess()), enabled=False)

    with pytest.raises(AntigravityRunnerError):
        service.start_run(workspace_root=tmp_path / "workspace", prompt="test")


@pytest.mark.parametrize(
    ("output", "classification"),
    [
        ("", "empty"),
        ("authentication required", "auth_required"),
        ("workspace permission required", "permission_or_trust_required"),
        ("unknown flag", "invalid_invocation"),
        ("provider error", "error"),
        ("completed", "nonempty"),
    ],
)
def test_provider_output_is_reduced_to_safe_classification(output: str, classification: str) -> None:
    assert classify_provider_output(output, "") == classification


def test_agy_sandbox_run_requires_installed_agy(tmp_path: Path) -> None:
    write(tmp_path / "workspace" / "README.md", "base\n")
    service = runner(tmp_path, RecordingPopen(FakeProcess()), enabled=True)
    service.executable_resolver = lambda command: None

    with pytest.raises(AntigravityRunnerError):
        service.start_run(workspace_root=tmp_path / "workspace", prompt="test")


def test_agy_command_uses_shell_false_and_never_plain_agy(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "README.md", "base\n")
    popen = RecordingPopen(FakeProcess())
    service = runner(tmp_path, popen)

    run = wait_for_run(service, service.start_run(workspace_root=workspace, prompt="change readme").run_id)
    args, kwargs = popen.calls[0]

    assert run.status == "review_ready"
    assert args[0].endswith("agy.exe")
    assert args[1] == "-p"
    assert len(args) == 3
    assert kwargs["shell"] is False
    assert kwargs["cwd"] == run.sandbox_root
    assert "--dangerously-skip-permissions" not in args


def test_timeout_kills_spawned_process(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "README.md", "base\n")
    process = FakeProcess(timeout=True)
    service = runner(tmp_path, RecordingPopen(process))

    run = wait_for_run(service, service.start_run(workspace_root=workspace, prompt="timeout", timeout_seconds=1).run_id)

    assert run.status == "failed_timeout"
    assert process.killed is True


def test_run_output_is_not_persisted_and_active_workspace_is_unchanged(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "README.md", "base\n")
    process = FakeProcess(stdout="x" * 5000)
    service = runner(tmp_path, RecordingPopen(process))

    run = wait_for_run(service, service.start_run(workspace_root=workspace, prompt="change").run_id)

    assert run.stdout_preview is None
    assert run.stderr_preview is None
    assert run.changed_file_count == 1
    assert run.diagnostics.sandbox_entered is True
    assert run.diagnostics.working_directory_identity == "sandbox"
    assert run.diagnostics.instruction_delivered is True
    assert run.diagnostics.instruction_length == len("change")
    assert len(run.diagnostics.instruction_sha256) == 64
    assert run.diagnostics.exit_code_classification == "zero"
    assert run.diagnostics.provider_output_classification == "nonempty"
    assert (workspace / "README.md").read_text(encoding="utf-8") == "base\n"


def test_zero_change_run_fails_closed_without_review_authority(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    write(sandbox / "README.md", "base\n")
    service = runner(tmp_path, RecordingPopen(FakeProcess(mutate=False)))

    run = wait_for_run(
        service,
        service.start_run_in_sandbox(run_id="bridge-run-zero", sandbox_root=sandbox, prompt="no-op").run_id,
    )

    assert run.status == "completed_no_changes"
    assert run.review_id is None
    assert run.changed_file_count == 0
    assert run.error_message == "AGY completed but produced no sandbox changes."
    assert run.diagnostics.pre_run_file_count == 1
    assert run.diagnostics.post_run_file_count == 1
    assert run.diagnostics.diff_changed_file_count == 0
    assert run.diagnostics.review_changed_file_count == 0


def test_reused_sandbox_positive_artifact_is_diffed_after_process_exit(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    write(sandbox / "QA_NOT_REAL_PROJECT.txt", "marker\n")
    process = FakeProcess(mutate_name="AGY_GENERIC_SMOKE.txt")
    service = runner(tmp_path, RecordingPopen(process))
    order: list[str] = []
    original_diff = service.review_service.diff_snapshot

    def observed_diff(*args: Any, **kwargs: Any):
        order.append("diff")
        assert process.cwd == sandbox
        assert (sandbox / "AGY_GENERIC_SMOKE.txt").exists()
        if len(order) == 1:
            return ()
        return original_diff(*args, **kwargs)

    service.review_service.diff_snapshot = observed_diff  # type: ignore[method-assign]
    run = wait_for_run(
        service,
        service.start_run_in_sandbox(run_id="bridge-run-positive", sandbox_root=sandbox, prompt="create").run_id,
    )
    review = service.review_service.get_review(run.review_id or "")

    assert order == ["diff", "diff", "diff"]
    assert run.status == "review_ready"
    assert run.changed_file_count == 1
    assert run.diagnostics.diff_changed_file_count == 1
    assert run.diagnostics.review_changed_file_count == 1
    assert [(item.path, item.change_type) for item in review.changed_files] == [("AGY_GENERIC_SMOKE.txt", "created")]


def test_approval_does_not_apply_sandbox_changes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "README.md", "base\n")
    service = runner(tmp_path, RecordingPopen(FakeProcess()))

    run = wait_for_run(service, service.start_run(workspace_root=workspace, prompt="change").run_id)
    review, _ = service.review_service.approve_review(run.review_id or "")

    assert review.status == "approved"
    assert (workspace / "README.md").read_text(encoding="utf-8") == "base\n"


def test_audit_log_records_run_lifecycle(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "README.md", "base\n")
    service = runner(tmp_path, RecordingPopen(FakeProcess()))

    wait_for_run(service, service.start_run(workspace_root=workspace, prompt="change").run_id)
    entries = BridgeAuditLog(tmp_path / "state" / "audit.jsonl").list_entries(100)
    events = [entry["event"] for entry in entries]

    assert "agy_sandbox_run_requested" in events
    assert "agy_sandbox_created" in events
    assert "agy_sandbox_started" in events
    assert "agy_sandbox_completed" in events
    assert "agy_review_created" in events
    assert "base" not in str(entries)
