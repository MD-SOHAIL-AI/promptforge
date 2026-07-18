from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from backend.agent_runtime.coding_workflow_monitor_service import (
    MAX_OUTPUT_BYTES,
    MONITOR_ALREADY_COMPLETED,
    MONITOR_CONFIRMATION_REQUIRED,
    MONITOR_FAILED,
    MONITOR_PORT_REQUIRED,
    MONITOR_PRECHECK_FAILED,
    NOT_AWAITING_MONITOR,
    CodingWorkflowMonitorService,
)
from backend.agent_runtime.coding_workflow_service import WORKFLOW_DISABLED
from backend.runtime.result import ObserveResult, ObserveTerminationReason, ResultStatus
from backend.tools.serial_monitor import SerialMonitorConfig
from tests.unit.test_coding_workflow_apply_resume import ENABLED
from tests.unit.test_coding_workflow_flash_resume import StubFlashService, built_rig, run


class StubMonitorFactory:
    def __init__(self, *, output: str = "READY", fail: bool = False) -> None:
        self.output = output
        self.fail = fail
        self.configs: list[SerialMonitorConfig] = []
        self.observe_calls = 0

    def __call__(self, config: SerialMonitorConfig):
        self.configs.append(config)
        return self

    async def observe(self) -> ObserveResult:
        self.observe_calls += 1
        if self.fail:
            raise RuntimeError("bounded fake monitor failure")
        observations = [
            {"timestamp": 1.0, "line": line, "source": "DEVICE", "port": "COM7", "metadata": {}}
            for line in self.output.splitlines()
        ]
        return ObserveResult(
            success=True,
            status=ResultStatus.SUCCESS,
            duration_ms=110,
            message="captured",
            metadata={"observations": observations, "observations_total": len(observations), "observations_dropped": 0},
            lines_captured=len(observations),
            monitoring_duration_ms=100,
            termination_reason=ObserveTerminationReason.TIMEOUT,
            port="COM7",
        )


def flashed_rig(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    monitor: StubMonitorFactory | None = None,
):
    active, provider, store, generated, builder, flasher, _, flash_service = built_rig(
        tmp_path, monkeypatch, flasher=StubFlashService(),
    )
    flashed = run(flash_service.run_flash_for_built_workflow(
        generated.run_id,
        flash_confirmed=True,
        workspace_path=active,
        port="COM7",
        board_id="esp32dev",
    ))
    assert flashed.status == "awaiting_monitor"
    factory = monitor or StubMonitorFactory()
    service = CodingWorkflowMonitorService(store=store, monitor_factory=factory, env=ENABLED)
    return active, provider, store, generated, builder, flasher, factory, service


def test_monitor_requires_enabled_workflow_and_explicit_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, store, generated, _, _, monitor, service = flashed_rig(tmp_path, monkeypatch)
    disabled = CodingWorkflowMonitorService(store=store, monitor_factory=monitor, env={})
    result = run(disabled.run_monitor_for_flashed_workflow(generated.run_id, monitor_confirmed=True))
    assert result.failure_code == WORKFLOW_DISABLED
    refused = run(service.run_monitor_for_flashed_workflow(generated.run_id))
    assert refused.failure_code == MONITOR_CONFIRMATION_REQUIRED
    assert monitor.observe_calls == 0
    assert store.get_run(generated.run_id).status == "awaiting_monitor"


def test_missing_and_not_awaiting_monitor_runs_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, store, generated, _, _, monitor, service = flashed_rig(tmp_path, monkeypatch)
    missing = run(service.run_monitor_for_flashed_workflow("missing-run", monitor_confirmed=True))
    assert missing.failure_code == "CODING_WORKFLOW_RUN_NOT_FOUND"
    store.transition_run(
        generated.run_id,
        expected_statuses=("awaiting_monitor",),
        status="failed",
        generation_status="failed",
        next_action=None,
        failure_code="TEST_FAILURE",
        safe_message="Test failure.",
    )
    rejected = run(service.run_monitor_for_flashed_workflow(generated.run_id, monitor_confirmed=True))
    assert rejected.failure_code == NOT_AWAITING_MONITOR
    assert monitor.observe_calls == 0


def test_missing_port_and_invalid_monitor_limits_fail_precheck(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, store, generated, _, _, monitor, service = flashed_rig(tmp_path / "port", monkeypatch)
    metadata = dict(store.get_run(generated.run_id).metadata)
    metadata.pop("flash_port")
    store.transition_run(
        generated.run_id,
        expected_statuses=("awaiting_monitor",),
        status="awaiting_monitor",
        generation_status="review_created",
        next_action="open_monitor",
        metadata=metadata,
    )
    missing_port = run(service.run_monitor_for_flashed_workflow(generated.run_id, monitor_confirmed=True))
    assert missing_port.failure_code == MONITOR_PORT_REQUIRED
    assert monitor.observe_calls == 0

    _, _, _, generated2, _, _, monitor2, service2 = flashed_rig(tmp_path / "baud", monkeypatch)
    invalid = run(service2.run_monitor_for_flashed_workflow(
        generated2.run_id, monitor_confirmed=True, baud_rate=0,
    ))
    assert invalid.failure_code == MONITOR_PRECHECK_FAILED
    assert monitor2.observe_calls == 0


def test_successful_monitor_is_bounded_sanitized_and_completes_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_output = "booting\nOPENAI_API_KEY=super-secret\nC:\\private\\firmware.bin\n" + ("x" * 20_000)
    active, provider, store, generated, builder, flasher, monitor, service = flashed_rig(
        tmp_path, monkeypatch, monitor=StubMonitorFactory(output=raw_output),
    )

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider commands, build, flash, and downstream stages must not rerun")

    monkeypatch.setattr(provider.provider, "generate", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = run(service.run_monitor_for_flashed_workflow(
        generated.run_id,
        monitor_confirmed=True,
        duration_seconds=3,
        max_output_bytes=128,
    ))

    assert result.completed is True
    assert result.status == "completed"
    assert result.next_action is None
    assert result.monitor_status == "completed"
    assert result.port == "COM7"
    assert result.truncated is True
    assert len(result.output_preview.encode("utf-8")) <= 128
    assert "super-secret" not in result.output_preview
    assert "[REDACTED]" in result.output_preview
    assert "C:\\private" not in result.output_preview
    assert len(builder.calls) == 1
    assert len(flasher.calls) == 1
    assert monitor.observe_calls == 1
    assert monitor.configs[0].timeout_s == 3
    assert monitor.configs[0].max_line_length == 128
    persisted = store.get_run(generated.run_id)
    assert persisted.status == "completed"
    assert persisted.next_action is None
    assert persisted.metadata["monitor_status"] == "completed"
    assert persisted.metadata["monitor_output_truncated"] is True
    assert len(str(persisted.metadata["monitor_output_preview"]).encode("utf-8")) <= 1024
    serialized = store.runs_path.read_text(encoding="utf-8") + store.events_path.read_text(encoding="utf-8")
    assert "super-secret" not in serialized
    assert [event.event_type for event in store.list_events(generated.run_id)][-4:] == [
        "monitor.started", "monitor.output", "monitor.completed", "workflow.completed",
    ]

    repeated = run(service.run_monitor_for_flashed_workflow(generated.run_id, monitor_confirmed=True))
    assert repeated.failure_code == MONITOR_ALREADY_COMPLETED
    assert monitor.observe_calls == 1


def test_failed_monitor_persists_terminal_failure_without_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, store, generated, builder, flasher, monitor, service = flashed_rig(
        tmp_path, monkeypatch, monitor=StubMonitorFactory(fail=True),
    )
    result = run(service.run_monitor_for_flashed_workflow(generated.run_id, monitor_confirmed=True))
    assert result.failure_code == MONITOR_FAILED
    failed = store.get_run(generated.run_id)
    assert failed.status == "failed"
    assert failed.failure_code == MONITOR_FAILED
    assert failed.metadata["monitor_status"] == "failed"
    assert [event.event_type for event in store.list_events(generated.run_id)][-2:] == [
        "monitor.failed", "workflow.failed",
    ]
    repeated = run(service.run_monitor_for_flashed_workflow(generated.run_id, monitor_confirmed=True))
    assert repeated.failure_code == NOT_AWAITING_MONITOR
    assert len(builder.calls) == 1
    assert len(flasher.calls) == 1
    assert monitor.observe_calls == 1


def test_monitor_rejects_output_limit_above_global_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, _, _, generated, _, _, monitor, service = flashed_rig(tmp_path, monkeypatch)
    result = run(service.run_monitor_for_flashed_workflow(
        generated.run_id, monitor_confirmed=True, max_output_bytes=MAX_OUTPUT_BYTES + 1,
    ))
    assert result.failure_code == MONITOR_PRECHECK_FAILED
    assert monitor.observe_calls == 0
