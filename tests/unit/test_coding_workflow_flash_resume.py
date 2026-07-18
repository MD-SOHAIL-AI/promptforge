from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from backend.agent_runtime.coding_workflow_flash_service import (
    BUILD_ARTIFACT_NOT_FOUND,
    FLASH_ALREADY_COMPLETED,
    FLASH_CONFIRMATION_REQUIRED,
    FLASH_DEVICE_REQUIRED,
    FLASH_FAILED,
    NOT_AWAITING_FLASH,
    WORKSPACE_NOT_FOUND,
    CodingWorkflowFlashService,
)
from backend.agent_runtime.coding_workflow_service import WORKFLOW_DISABLED
from backend.runtime.result import FlashResult, ResultStatus, VerificationStatus
from backend.tools.board_detector import BoardInfo, BoardType
from backend.tools.build_firmware import BuildArtifact
from backend.tools.flash_firmware import FlashConfig
from tests.unit.test_coding_workflow_apply_resume import ENABLED
from tests.unit.test_coding_workflow_build_resume import StubBuildService, applied_rig


class StubBoardDetector:
    def __init__(self, boards: list[BoardInfo] | None = None) -> None:
        self.boards = boards if boards is not None else [board()]
        self.calls = 0

    def detect_boards(self) -> list[BoardInfo]:
        self.calls += 1
        return list(self.boards)


class StubFlashService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[BuildArtifact, BoardInfo, FlashConfig]] = []

    async def flash(self, artifact: BuildArtifact, detected: BoardInfo, config: FlashConfig) -> FlashResult:
        self.calls.append((artifact, detected, config))
        if self.fail:
            raise RuntimeError("bounded fake flash failure")
        return FlashResult(
            success=True,
            status=ResultStatus.SUCCESS,
            duration_ms=240,
            message="flashed",
            port=config.port,
            board=detected.board_type.value,
            flash_duration_ms=225,
            verification_status=VerificationStatus.PASSED,
            bytes_written=artifact.size_bytes,
            tool="platformio",
            tool_version="test",
        )


def board(*, port: str = "COM7", board_type: BoardType = BoardType.ESP32) -> BoardInfo:
    return BoardInfo(
        board_type=board_type,
        port=port,
        vid=0x1234,
        pid=0x5678,
        manufacturer="Test",
        description="Test board",
        serial_number="TEST-1",
    )


def run(coro):
    return asyncio.run(coro)


def built_rig(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    flasher: StubFlashService | None = None,
    detector: StubBoardDetector | None = None,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    builder = StubBuildService()
    active, provider, store, generated, _, build_service = applied_rig(tmp_path, monkeypatch, builder=builder)
    built = run(build_service.run_build_for_applied_workflow(
        generated.run_id,
        workspace_root=active,
        build_confirmed=True,
        environment="esp32dev",
    ))
    assert built.status == "awaiting_flash"
    flash = flasher or StubFlashService()
    boards = detector or StubBoardDetector()
    service = CodingWorkflowFlashService(
        store=store,
        flash_service=flash,
        board_detector=boards,
        patch_apply_service=build_service._applies,
        env=ENABLED,
    )
    return active, provider, store, generated, builder, flash, boards, service


def test_flash_requires_enabled_workflow_and_explicit_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active, _, store, generated, _, flasher, detector, service = built_rig(tmp_path, monkeypatch)
    disabled = CodingWorkflowFlashService(
        store=store,
        flash_service=flasher,
        board_detector=detector,
        patch_apply_service=service._applies,
        env={},
    )
    result = run(disabled.run_flash_for_built_workflow(
        generated.run_id, flash_confirmed=True, workspace_path=active, port="COM7", board_id="esp32dev",
    ))
    assert result.failure_code == WORKFLOW_DISABLED
    refused = run(service.run_flash_for_built_workflow(
        generated.run_id, workspace_path=active, port="COM7", board_id="esp32dev",
    ))
    assert refused.failure_code == FLASH_CONFIRMATION_REQUIRED
    assert flasher.calls == []
    assert detector.calls == 0


def test_missing_and_not_awaiting_flash_runs_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active, _, store, generated, _, flasher, _, service = built_rig(tmp_path, monkeypatch)
    missing = run(service.run_flash_for_built_workflow(
        "missing-run", flash_confirmed=True, workspace_path=active, port="COM7", board_id="esp32dev",
    ))
    assert missing.failure_code == "CODING_WORKFLOW_RUN_NOT_FOUND"
    store.transition_run(
        generated.run_id,
        expected_statuses=("awaiting_flash",),
        status="failed",
        generation_status="failed",
        next_action=None,
        failure_code="TEST_FAILURE",
        safe_message="Test failure.",
    )
    rejected = run(service.run_flash_for_built_workflow(
        generated.run_id, flash_confirmed=True, workspace_path=active, port="COM7", board_id="esp32dev",
    ))
    assert rejected.failure_code == NOT_AWAITING_FLASH
    assert flasher.calls == []


def test_missing_workspace_device_and_artifact_are_rejected_before_flash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active, _, _, generated, _, flasher, _, service = built_rig(tmp_path / "workspace", monkeypatch)
    missing_workspace = run(service.run_flash_for_built_workflow(
        generated.run_id, flash_confirmed=True, port="COM7", board_id="esp32dev",
    ))
    assert missing_workspace.failure_code == WORKSPACE_NOT_FOUND
    assert flasher.calls == []

    device_root = tmp_path / "device"
    device_root.mkdir()
    active2, _, _, generated2, _, flasher2, _, service2 = built_rig(
        device_root, monkeypatch, detector=StubBoardDetector([]),
    )
    missing_device = run(service2.run_flash_for_built_workflow(
        generated2.run_id, flash_confirmed=True, workspace_path=active2, port="COM7", board_id="esp32dev",
    ))
    assert missing_device.failure_code == FLASH_DEVICE_REQUIRED
    assert flasher2.calls == []

    artifact_root = tmp_path / "artifact"
    artifact_root.mkdir()
    active3, _, store3, generated3, _, flasher3, _, service3 = built_rig(artifact_root, monkeypatch)
    artifact = active3 / str(store3.get_run(generated3.run_id).metadata["artifact_reference"])
    artifact.unlink()
    missing_artifact = run(service3.run_flash_for_built_workflow(
        generated3.run_id, flash_confirmed=True, workspace_path=active3, port="COM7", board_id="esp32dev",
    ))
    assert missing_artifact.failure_code == BUILD_ARTIFACT_NOT_FOUND
    assert flasher3.calls == []


def test_successful_flash_uses_existing_adapter_once_and_stops_before_monitor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active, provider, store, generated, builder, flasher, detector, service = built_rig(tmp_path, monkeypatch)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("provider commands, rebuild, and monitor must not execute")

    monkeypatch.setattr(provider.provider, "generate", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = run(service.run_flash_for_built_workflow(
        generated.run_id,
        flash_confirmed=True,
        workspace_path=active,
        port="COM7",
        board_id="esp32dev",
    ))

    assert result.succeeded is True
    assert result.status == "awaiting_monitor"
    assert result.next_action == "open_monitor"
    assert result.flash_status == "succeeded"
    assert result.port == "COM7"
    assert len(builder.calls) == 1
    assert len(flasher.calls) == 1
    assert detector.calls == 1
    artifact, detected, config = flasher.calls[0]
    assert artifact.path == active / ".pio" / "build" / "esp32dev" / "firmware.bin"
    assert detected.board_type is BoardType.ESP32
    assert config.port == "COM7"
    persisted = store.get_run(generated.run_id)
    assert persisted.status == "awaiting_monitor"
    assert persisted.metadata["flash_status"] == "succeeded"
    assert persisted.metadata["flash_port"] == "COM7"
    assert persisted.metadata["flash_duration_ms"] == 225
    assert [event.event_type for event in store.list_events(generated.run_id)][-3:] == [
        "flash.started", "flash.completed", "monitor.waiting_to_start",
    ]

    repeated = run(service.run_flash_for_built_workflow(
        generated.run_id, flash_confirmed=True, workspace_path=active, port="COM7", board_id="esp32dev",
    ))
    assert repeated.failure_code == FLASH_ALREADY_COMPLETED
    assert len(flasher.calls) == 1


def test_failed_flash_persists_failure_without_monitor_or_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    active, _, store, generated, builder, flasher, _, service = built_rig(
        tmp_path, monkeypatch, flasher=StubFlashService(fail=True),
    )
    result = run(service.run_flash_for_built_workflow(
        generated.run_id, flash_confirmed=True, workspace_path=active, port="COM7", board_id="esp32dev",
    ))
    assert result.failure_code == FLASH_FAILED
    failed = store.get_run(generated.run_id)
    assert failed.status == "failed"
    assert failed.failure_code == FLASH_FAILED
    assert failed.metadata["flash_status"] == "failed"
    assert [event.event_type for event in store.list_events(generated.run_id)][-2:] == ["flash.started", "flash.failed"]
    repeated = run(service.run_flash_for_built_workflow(
        generated.run_id, flash_confirmed=True, workspace_path=active, port="COM7", board_id="esp32dev",
    ))
    assert repeated.failure_code == NOT_AWAITING_FLASH
    assert len(builder.calls) == 1
    assert len(flasher.calls) == 1
