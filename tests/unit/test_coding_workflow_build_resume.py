from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from backend.agent_runtime.coding_workflow_build_service import (
    BUILD_ALREADY_COMPLETED,
    BUILD_CONFIRMATION_REQUIRED,
    BUILD_FAILED,
    BUILD_PRECHECK_FAILED,
    NOT_AWAITING_BUILD,
    WORKSPACE_NOT_FOUND,
    CodingWorkflowBuildService,
)
from backend.agent_runtime.coding_workflow_service import WORKFLOW_DISABLED
from backend.runtime.result import BuildResult, ResultStatus
from tests.unit.test_coding_workflow_apply_resume import ENABLED, make_rig


class StubBuildService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[Path, str | None]] = []

    async def build(self, project: str | Path, *, environment: str | None = None) -> BuildResult:
        root = Path(project)
        self.calls.append((root, environment))
        if self.fail:
            raise RuntimeError("bounded fake build failure")
        selected = environment or "esp32dev"
        artifact = root / ".pio" / "build" / selected / "firmware.bin"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"fake firmware")
        return BuildResult(
            success=True,
            status=ResultStatus.SUCCESS,
            duration_ms=125,
            message="built",
            metadata={"artifact_environment": selected, "artifact_type": "bin"},
            firmware_path=str(artifact),
            build_size_bytes=artifact.stat().st_size,
            platform="platformio",
            board="esp32dev",
            warnings_count=1,
        )


def applied_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, builder: StubBuildService | None = None):
    active, provider, _, store, generated, apply_resume = make_rig(tmp_path, monkeypatch)
    applied = apply_resume.approve_and_apply_review(
        generated.run_id,
        workspace_root=active,
        approval_confirmed=True,
    )
    assert applied.status == "awaiting_build"
    build = builder or StubBuildService()
    service = CodingWorkflowBuildService(
        store=store,
        build_service=build,
        patch_apply_service=apply_resume._apply,
        env=ENABLED,
    )
    return active, provider, store, generated, build, service


def run(coro):
    return asyncio.run(coro)


def test_build_requires_enabled_workflow_and_explicit_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active, _, store, generated, builder, service = applied_rig(tmp_path, monkeypatch)
    disabled = CodingWorkflowBuildService(
        store=store,
        build_service=builder,
        patch_apply_service=service._applies,
        env={},
    )
    result = run(disabled.run_build_for_applied_workflow(generated.run_id, workspace_root=active, build_confirmed=True))
    assert result.failure_code == WORKFLOW_DISABLED
    refused = run(service.run_build_for_applied_workflow(generated.run_id, workspace_root=active))
    assert refused.failure_code == BUILD_CONFIRMATION_REQUIRED
    assert builder.calls == []
    assert store.get_run(generated.run_id).status == "awaiting_build"


def test_missing_and_not_awaiting_build_runs_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active, _, store, generated, builder, service = applied_rig(tmp_path, monkeypatch)
    missing = run(service.run_build_for_applied_workflow("missing-run", workspace_root=active, build_confirmed=True))
    assert missing.failure_code == "CODING_WORKFLOW_RUN_NOT_FOUND"
    store.transition_run(
        generated.run_id,
        expected_statuses=("awaiting_build",),
        status="failed",
        generation_status="failed",
        next_action=None,
        failure_code="TEST_FAILURE",
        safe_message="Test failure.",
    )
    rejected = run(service.run_build_for_applied_workflow(generated.run_id, workspace_root=active, build_confirmed=True))
    assert rejected.failure_code == NOT_AWAITING_BUILD
    assert builder.calls == []


def test_successful_build_uses_canonical_result_adapter_and_stops_before_flash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active, provider, store, generated, builder, service = applied_rig(tmp_path, monkeypatch)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider suggestions, flash, and monitor must not execute")

    monkeypatch.setattr(provider.provider, "generate", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = run(service.run_build_for_applied_workflow(
        generated.run_id,
        workspace_root=active,
        build_confirmed=True,
        environment="esp32dev",
    ))

    assert result.succeeded is True
    assert result.status == "awaiting_flash"
    assert result.next_action == "confirm_flash"
    assert result.artifact_reference == ".pio/build/esp32dev/firmware.bin"
    assert result.environment == "esp32dev"
    assert builder.calls == [(active.resolve(), "esp32dev")]
    persisted = store.get_run(generated.run_id)
    assert persisted.status == "awaiting_flash"
    assert persisted.metadata["build_status"] == "succeeded"
    assert persisted.metadata["artifact_reference"] == result.artifact_reference
    assert persisted.metadata["build_board"] == "esp32dev"
    assert persisted.metadata["build_duration_ms"] == 125
    assert "firmware_path" not in persisted.metadata
    assert [event.event_type for event in store.list_events(generated.run_id)][-3:] == [
        "build.started",
        "build.completed",
        "flash.waiting_for_confirmation",
    ]

    repeated = run(service.run_build_for_applied_workflow(generated.run_id, workspace_root=active, build_confirmed=True))
    assert repeated.failure_code == BUILD_ALREADY_COMPLETED
    assert len(builder.calls) == 1


def test_failed_build_persists_failure_without_flash_or_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    builder = StubBuildService(fail=True)
    active, _, store, generated, _, service = applied_rig(tmp_path, monkeypatch, builder=builder)
    result = run(service.run_build_for_applied_workflow(generated.run_id, workspace_root=active, build_confirmed=True))
    assert result.failure_code == BUILD_FAILED
    failed = store.get_run(generated.run_id)
    assert failed.status == "failed"
    assert failed.failure_code == BUILD_FAILED
    assert failed.metadata["build_status"] == "failed"
    assert [event.event_type for event in store.list_events(generated.run_id)][-2:] == ["build.started", "build.failed"]
    repeated = run(service.run_build_for_applied_workflow(generated.run_id, workspace_root=active, build_confirmed=True))
    assert repeated.failure_code == NOT_AWAITING_BUILD
    assert len(builder.calls) == 1


def test_missing_workspace_and_applied_file_drift_fail_before_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active, _, store, generated, builder, service = applied_rig(tmp_path, monkeypatch)
    missing = run(service.run_build_for_applied_workflow(generated.run_id, build_confirmed=True))
    assert missing.failure_code == WORKSPACE_NOT_FOUND
    assert builder.calls == []

    drift_root = tmp_path / "drift"
    drift_root.mkdir()
    active2, _, store2, generated2, builder2, service2 = applied_rig(drift_root, monkeypatch)
    (active2 / "src" / "main.cpp").write_text("drifted\n", encoding="utf-8")
    drifted = run(service2.run_build_for_applied_workflow(generated2.run_id, workspace_root=active2, build_confirmed=True))
    assert drifted.failure_code == BUILD_PRECHECK_FAILED
    assert store2.get_run(generated2.run_id).status == "failed"
    assert builder2.calls == []
