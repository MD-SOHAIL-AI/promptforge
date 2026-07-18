from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from backend.agent_runtime.api_coding_agent_fake import FakeApiCodingAgentProvider
from backend.agent_runtime.api_coding_agent_service import ApiCodingAgentService
from backend.agent_runtime.coding_provider_contracts import CodingRunStatus
from backend.agent_runtime.coding_workflow_build_service import BUILD_FAILED, CodingWorkflowBuildService
from backend.agent_runtime.coding_workflow_flash_service import FLASH_FAILED, CodingWorkflowFlashService
from backend.agent_runtime.coding_workflow_monitor_service import (
    MAX_PERSISTED_PREVIEW_BYTES,
    MONITOR_FAILED,
    CodingWorkflowMonitorService,
)
from backend.agent_runtime.coding_workflow_service import CodingWorkflowApplyService
from backend.agent_runtime.coding_workflow_store import CodingWorkflowStore
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.patch_apply_service import PatchApplyService
from backend.bridges.patch_export_service import BridgePatchExportService
from backend.bridges.patch_preflight_service import PatchPreflightService
from backend.bridges.review_store import BridgeReviewStore
from backend.bridges.rollback_restore_apply_service import RollbackRestoreApplyService
from backend.bridges.rollback_restore_service import RollbackRestorePreflightService
from backend.bridges.rollback_service import RollbackSnapshotService
from backend.bridges.sandbox_service import BridgeSandboxService
from backend.runtime.result import (
    BuildResult,
    FlashResult,
    ObserveResult,
    ObserveTerminationReason,
    ResultStatus,
    VerificationStatus,
)
from backend.tools.board_detector import BoardInfo, BoardType
from backend.tools.build_firmware import BuildArtifact
from backend.tools.flash_firmware import FlashConfig
from backend.tools.serial_monitor import SerialMonitorConfig
from backend.workflow.adapters.coding_agent_adapter import CodingAgentGenerationAdapter


ENABLED_ENV = {
    "FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW": "1",
    "FORGEX_ENABLE_FAKE_API_CODING_AGENT": "1",
}

EXPECTED_SUCCESS_EVENTS = [
    "provider.selected",
    "generation.started",
    "generation.completed",
    "review.created",
    "apply.waiting_for_approval",
    "apply.started",
    "apply.completed",
    "build.waiting_to_start",
    "build.started",
    "build.completed",
    "flash.waiting_for_confirmation",
    "flash.started",
    "flash.completed",
    "monitor.waiting_to_start",
    "monitor.started",
    "monitor.output",
    "monitor.completed",
    "workflow.completed",
]

EXPECTED_SUCCESS_STATUS_HISTORY = [
    "awaiting_apply",
    "applying",
    "awaiting_build",
    "building",
    "awaiting_flash",
    "flashing",
    "awaiting_monitor",
    "monitoring",
    "completed",
]


class CountingFakeProvider(FakeApiCodingAgentProvider):
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.blocked = False

    def generate(self, prompt: str, context: object | None = None) -> str:
        if self.blocked:
            raise AssertionError("coding provider must only run during generation")
        self.prompts.append(prompt)
        return super().generate(prompt, context)


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


class StubBoardDetector:
    def __init__(self, boards: list[BoardInfo] | None = None) -> None:
        self.boards = boards if boards is not None else [stub_board()]
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


class StubMonitorFactory:
    def __init__(self, *, output: str = "booting\nREADY", fail: bool = False) -> None:
        self.output = output
        self.fail = fail
        self.configs: list[SerialMonitorConfig] = []
        self.observe_calls = 0

    def __call__(self, config: SerialMonitorConfig) -> "StubMonitorFactory":
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
            metadata={
                "observations": observations,
                "observations_total": len(observations),
                "observations_dropped": 0,
            },
            lines_captured=len(observations),
            monitoring_duration_ms=100,
            termination_reason=ObserveTerminationReason.TIMEOUT,
            port="COM7",
        )


@dataclass
class FullChainRig:
    active: Path
    reviews: BridgeDiffService
    store: CodingWorkflowStore
    provider: CountingFakeProvider
    provider_service: ApiCodingAgentService
    apply_service: CodingWorkflowApplyService


def stub_board(*, port: str = "COM7", board_type: BoardType = BoardType.ESP32) -> BoardInfo:
    return BoardInfo(
        board_type=board_type,
        port=port,
        vid=0x1234,
        pid=0x5678,
        manufacturer="Test",
        description="Test board",
        serial_number="TEST-1",
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def install_no_command_execution_guard(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def forbidden_subprocess(*args: object, **kwargs: object) -> object:
        calls.append(repr(args[0] if args else "subprocess"))
        raise AssertionError("command suggestions, CLI providers, and real tool commands must not execute")

    async def forbidden_async_subprocess(*args: object, **kwargs: object) -> object:
        calls.append(repr(args[0] if args else "async_subprocess"))
        raise AssertionError("async CLI providers and real tool commands must not execute")

    monkeypatch.setattr(subprocess, "run", forbidden_subprocess)
    monkeypatch.setattr(subprocess, "Popen", forbidden_subprocess)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden_async_subprocess)
    return calls


def make_rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FullChainRig:
    monkeypatch.setenv("FORGEX_ENABLE_PATCH_APPLY", "1")
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    monkeypatch.delenv("FORGEX_ENABLE_CODEX_PROVIDER", raising=False)
    monkeypatch.delenv("FORGEX_ENABLE_AGY_BRIDGE", raising=False)
    monkeypatch.delenv("FORGEX_ENABLE_AGY_GENERIC_PROVIDER", raising=False)
    monkeypatch.delenv("FORGEX_ENABLE_AGY_GENERIC_CUTOVER", raising=False)
    monkeypatch.delenv("FORGEX_ENABLE_AGY_SDK_PROVIDER", raising=False)

    state = tmp_path / "state"
    active = tmp_path / "active"
    active.mkdir()
    (active / "README.md").write_text("active workspace\n", encoding="utf-8")

    reviews = BridgeDiffService(store=BridgeReviewStore(
        snapshots_path=state / "snapshots.jsonl",
        reviews_path=state / "reviews.jsonl",
    ))
    provider = CountingFakeProvider()
    provider_service = ApiCodingAgentService(
        sandbox_service=BridgeSandboxService(state / "sandboxes"),
        review_service=reviews,
        provider=provider,
        enabled=True,
    )
    store = CodingWorkflowStore.from_state_directory(state)

    exporter = BridgePatchExportService(review_service=reviews, patch_directory=state / "patches")
    preflight = PatchPreflightService(patch_store=exporter.patch_store, review_service=reviews)
    rollback = RollbackSnapshotService(rollback_directory=state / "rollback", preflight_service=preflight)
    restore_preflight = RollbackRestorePreflightService(rollback_service=rollback)
    restore = RollbackRestoreApplyService(
        restore_directory=state / "restores",
        rollback_service=rollback,
        restore_preflight_service=restore_preflight,
    )
    patch_apply = PatchApplyService(
        apply_directory=state / "applies",
        patch_store=exporter.patch_store,
        review_service=reviews,
        preflight_service=preflight,
        rollback_service=rollback,
        restore_apply_service=restore,
    )
    apply_service = CodingWorkflowApplyService(
        store=store,
        review_service=reviews,
        patch_export_service=exporter,
        patch_apply_service=patch_apply,
        env=ENABLED_ENV,
    )
    return FullChainRig(
        active=active,
        reviews=reviews,
        store=store,
        provider=provider,
        provider_service=provider_service,
        apply_service=apply_service,
    )


def generate_review(rig: FullChainRig) -> Any:
    result = CodingAgentGenerationAdapter(
        fake_service=rig.provider_service,
        store=rig.store,
        env=ENABLED_ENV,
    ).generate_review("basic esp32 blink", rig.active)
    assert result.status is CodingRunStatus.AWAITING_APPLY
    assert result.review_id is not None
    assert result.files_changed == ("platformio.ini", "src/main.cpp")
    assert len(rig.provider.prompts) == 1
    rig.provider.blocked = True

    review = rig.reviews.get_review(result.review_id)
    assert review.artifact_source == "api_coding_agent_fake"
    assert review.artifact_metadata is not None
    assert review.artifact_metadata["real_api_calls"] is False
    assert (rig.active / "README.md").read_text(encoding="utf-8") == "active workspace\n"
    assert not (rig.active / "platformio.ini").exists()
    assert not (rig.active / "src" / "main.cpp").exists()
    assert [event.event_type for event in rig.store.list_events(result.run_id)] == EXPECTED_SUCCESS_EVENTS[:5]
    return result


def apply_review(rig: FullChainRig, run_id: str) -> Any:
    previous = event_ids(rig.store, run_id)
    result = rig.apply_service.approve_and_apply_review(
        run_id,
        workspace_root=rig.active,
        approval_confirmed=True,
        approved_by="user",
    )
    assert result.applied is True
    assert result.status == "awaiting_build"
    assert result.next_action == "run_build"
    assert result.files_changed == ("platformio.ini", "src/main.cpp")
    assert (rig.active / "platformio.ini").is_file()
    assert (rig.active / "src" / "main.cpp").is_file()
    assert_appended_only(rig.store, run_id, previous)
    assert len(rig.provider.prompts) == 1
    return result


def make_build_service(rig: FullChainRig, builder: StubBuildService) -> CodingWorkflowBuildService:
    return CodingWorkflowBuildService(
        store=rig.store,
        build_service=builder,
        patch_apply_service=rig.apply_service._apply,
        env=ENABLED_ENV,
    )


def make_flash_service(
    rig: FullChainRig,
    flasher: StubFlashService,
    detector: StubBoardDetector,
) -> CodingWorkflowFlashService:
    return CodingWorkflowFlashService(
        store=rig.store,
        flash_service=flasher,
        board_detector=detector,
        patch_apply_service=rig.apply_service._apply,
        env=ENABLED_ENV,
    )


def make_monitor_service(rig: FullChainRig, monitor: StubMonitorFactory) -> CodingWorkflowMonitorService:
    return CodingWorkflowMonitorService(store=rig.store, monitor_factory=monitor, env=ENABLED_ENV)


def event_ids(store: CodingWorkflowStore, run_id: str) -> list[str]:
    return [event.event_id for event in store.list_events(run_id)]


def assert_appended_only(store: CodingWorkflowStore, run_id: str, previous_ids: list[str]) -> None:
    current_ids = event_ids(store, run_id)
    assert current_ids[: len(previous_ids)] == previous_ids
    assert len(current_ids) >= len(previous_ids)
    assert_event_sequence_contiguous(store, run_id)


def assert_event_sequence_contiguous(store: CodingWorkflowStore, run_id: str) -> None:
    events = store.list_events(run_id)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert len({event.event_id for event in events}) == len(events)


def event_types(store: CodingWorkflowStore, run_id: str) -> list[str]:
    return [event.event_type for event in store.list_events(run_id)]


def run_status_history(store: CodingWorkflowStore, run_id: str) -> list[str]:
    rows = [
        json.loads(line)
        for line in store.runs_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [row["status"] for row in rows if row["run_id"] == run_id]


def serialized_store(store: CodingWorkflowStore) -> str:
    runs = store.runs_path.read_text(encoding="utf-8") if store.runs_path.exists() else ""
    events = store.events_path.read_text(encoding="utf-8") if store.events_path.exists() else ""
    return runs + events


def assert_store_has_no_raw_sources_or_secrets(store: CodingWorkflowStore) -> None:
    serialized = serialized_store(store)
    forbidden_fragments = [
        "#include <Arduino.h>",
        "pinMode",
        "framework = arduino",
        "raw_prompt",
        "raw_response",
        "source_body",
        "source_code",
        "content",
        "OPENAI_API_KEY",
        "super-secret",
        "C:\\private",
        "pio run",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in serialized


def assert_metadata_is_bounded(metadata: Any) -> None:
    assert isinstance(metadata, dict)
    assert len(json.dumps(metadata, ensure_ascii=True, sort_keys=True).encode("utf-8")) <= 16 * 1024
    for value in metadata.values():
        if isinstance(value, str):
            assert len(value) <= 2_000


def assert_stage_order(types: list[str]) -> None:
    def index(name: str) -> int:
        return types.index(name)

    assert index("apply.started") > index("apply.waiting_for_approval")
    assert index("build.started") > index("apply.completed")
    assert index("flash.started") > index("build.completed")
    assert index("monitor.started") > index("flash.completed")


def assert_failure_persisted(
    rig: FullChainRig,
    run_id: str,
    *,
    failure_code: str,
    event_type: str,
) -> None:
    failed = rig.store.get_run(run_id)
    assert failed.status == "failed"
    assert failed.failure_code == failure_code
    assert failed.safe_message
    assert "RuntimeError" not in failed.safe_message
    assert event_type in event_types(rig.store, run_id)
    failure_events = [event for event in rig.store.list_events(run_id) if event.event_type == event_type]
    assert failure_events[-1].metadata["failure_code"] == failure_code
    assert_metadata_is_bounded(dict(failed.metadata))
    assert_event_sequence_contiguous(rig.store, run_id)
    assert_store_has_no_raw_sources_or_secrets(rig.store)


def test_full_fake_provider_unified_coding_workflow_reaches_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_calls = install_no_command_execution_guard(monkeypatch)
    rig = make_rig(tmp_path, monkeypatch)
    builder = StubBuildService()
    flasher = StubFlashService()
    detector = StubBoardDetector()
    monitor = StubMonitorFactory(
        output="booting\nOPENAI_API_KEY=super-secret\nC:\\private\\firmware.bin\n" + ("x" * 2_000)
    )

    generated = generate_review(rig)
    assert command_calls == []
    assert builder.calls == []
    assert flasher.calls == []
    assert monitor.observe_calls == 0

    applied = apply_review(rig, generated.run_id)
    assert applied.review_id == generated.review_id
    assert command_calls == []
    assert builder.calls == []
    assert flasher.calls == []
    assert monitor.observe_calls == 0

    build_service = make_build_service(rig, builder)
    previous = event_ids(rig.store, generated.run_id)
    built = run(build_service.run_build_for_applied_workflow(
        generated.run_id,
        workspace_root=rig.active,
        build_confirmed=True,
        environment="esp32dev",
    ))
    assert built.succeeded is True
    assert built.status == "awaiting_flash"
    assert built.next_action == "confirm_flash"
    assert built.review_id == generated.review_id
    assert built.artifact_reference == ".pio/build/esp32dev/firmware.bin"
    assert built.environment == "esp32dev"
    assert len(builder.calls) == 1
    assert flasher.calls == []
    assert monitor.observe_calls == 0
    assert len(rig.provider.prompts) == 1
    assert_appended_only(rig.store, generated.run_id, previous)

    flash_service = make_flash_service(rig, flasher, detector)
    previous = event_ids(rig.store, generated.run_id)
    flashed = run(flash_service.run_flash_for_built_workflow(
        generated.run_id,
        flash_confirmed=True,
        workspace_path=rig.active,
        port="COM7",
        board_id="esp32dev",
    ))
    assert flashed.succeeded is True
    assert flashed.status == "awaiting_monitor"
    assert flashed.next_action == "open_monitor"
    assert flashed.review_id == generated.review_id
    assert flashed.port == "COM7"
    assert flashed.board == BoardType.ESP32.value
    assert len(builder.calls) == 1
    assert len(flasher.calls) == 1
    assert detector.calls == 1
    assert monitor.observe_calls == 0
    assert len(rig.provider.prompts) == 1
    assert_appended_only(rig.store, generated.run_id, previous)

    monitor_service = make_monitor_service(rig, monitor)
    previous = event_ids(rig.store, generated.run_id)
    monitored = run(monitor_service.run_monitor_for_flashed_workflow(
        generated.run_id,
        monitor_confirmed=True,
        duration_seconds=3,
        max_output_bytes=256,
    ))
    assert monitored.completed is True
    assert monitored.status == "completed"
    assert monitored.next_action is None
    assert monitored.review_id == generated.review_id
    assert monitored.port == "COM7"
    assert monitored.truncated is True
    assert len(monitored.output_preview.encode("utf-8")) <= 256
    assert "super-secret" not in monitored.output_preview
    assert "[REDACTED]" in monitored.output_preview
    assert "C:\\private" not in monitored.output_preview
    assert len(builder.calls) == 1
    assert len(flasher.calls) == 1
    assert monitor.observe_calls == 1
    assert len(rig.provider.prompts) == 1
    assert command_calls == []
    assert_appended_only(rig.store, generated.run_id, previous)

    final = rig.store.get_run(generated.run_id)
    metadata = dict(final.metadata)
    assert final.status == "completed"
    assert final.next_action is None
    assert final.review_id == generated.review_id
    assert final.files_changed == ("platformio.ini", "src/main.cpp")
    assert metadata["build_status"] == "succeeded"
    assert metadata["artifact_reference"] == ".pio/build/esp32dev/firmware.bin"
    assert len(str(metadata["artifact_reference"])) <= 512
    assert metadata["artifact_size_bytes"] == len(b"fake firmware")
    assert metadata["build_duration_ms"] == 125
    assert metadata["build_warnings_count"] == 1
    assert "firmware_path" not in metadata
    assert metadata["flash_status"] == "succeeded"
    assert metadata["flash_port"] == "COM7"
    assert metadata["flash_board"] == BoardType.ESP32.value
    assert metadata["flash_duration_ms"] == 225
    assert metadata["flash_bytes_written"] == len(b"fake firmware")
    assert metadata["monitor_status"] == "completed"
    assert metadata["monitor_port"] == "COM7"
    assert metadata["monitor_duration_ms"] == 100
    assert metadata["monitor_output_truncated"] is True
    assert len(str(metadata["monitor_output_preview"]).encode("utf-8")) <= MAX_PERSISTED_PREVIEW_BYTES
    assert_metadata_is_bounded(metadata)

    types = event_types(rig.store, generated.run_id)
    assert types == EXPECTED_SUCCESS_EVENTS
    assert_stage_order(types)
    assert run_status_history(rig.store, generated.run_id) == EXPECTED_SUCCESS_STATUS_HISTORY
    assert_event_sequence_contiguous(rig.store, generated.run_id)
    assert_store_has_no_raw_sources_or_secrets(rig.store)


def test_full_chain_stops_when_build_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_calls = install_no_command_execution_guard(monkeypatch)
    rig = make_rig(tmp_path, monkeypatch)
    builder = StubBuildService(fail=True)
    flasher = StubFlashService()
    monitor = StubMonitorFactory()

    generated = generate_review(rig)
    apply_review(rig, generated.run_id)
    result = run(make_build_service(rig, builder).run_build_for_applied_workflow(
        generated.run_id,
        workspace_root=rig.active,
        build_confirmed=True,
        environment="esp32dev",
    ))

    assert result.status == "failed"
    assert result.failure_code == BUILD_FAILED
    assert len(rig.provider.prompts) == 1
    assert len(builder.calls) == 1
    assert flasher.calls == []
    assert monitor.observe_calls == 0
    assert "build.failed" in event_types(rig.store, generated.run_id)
    assert "flash.started" not in event_types(rig.store, generated.run_id)
    assert "monitor.started" not in event_types(rig.store, generated.run_id)
    assert command_calls == []
    assert run_status_history(rig.store, generated.run_id) == [
        "awaiting_apply",
        "applying",
        "awaiting_build",
        "building",
        "failed",
    ]
    assert_failure_persisted(rig, generated.run_id, failure_code=BUILD_FAILED, event_type="build.failed")


def test_full_chain_stops_when_flash_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_calls = install_no_command_execution_guard(monkeypatch)
    rig = make_rig(tmp_path, monkeypatch)
    builder = StubBuildService()
    flasher = StubFlashService(fail=True)
    detector = StubBoardDetector()
    monitor = StubMonitorFactory()

    generated = generate_review(rig)
    apply_review(rig, generated.run_id)
    built = run(make_build_service(rig, builder).run_build_for_applied_workflow(
        generated.run_id,
        workspace_root=rig.active,
        build_confirmed=True,
        environment="esp32dev",
    ))
    assert built.status == "awaiting_flash"
    result = run(make_flash_service(rig, flasher, detector).run_flash_for_built_workflow(
        generated.run_id,
        flash_confirmed=True,
        workspace_path=rig.active,
        port="COM7",
        board_id="esp32dev",
    ))

    assert result.status == "failed"
    assert result.failure_code == FLASH_FAILED
    assert len(rig.provider.prompts) == 1
    assert len(builder.calls) == 1
    assert len(flasher.calls) == 1
    assert detector.calls == 1
    assert monitor.observe_calls == 0
    assert "flash.failed" in event_types(rig.store, generated.run_id)
    assert "monitor.started" not in event_types(rig.store, generated.run_id)
    assert command_calls == []
    assert run_status_history(rig.store, generated.run_id) == [
        "awaiting_apply",
        "applying",
        "awaiting_build",
        "building",
        "awaiting_flash",
        "flashing",
        "failed",
    ]
    assert_failure_persisted(rig, generated.run_id, failure_code=FLASH_FAILED, event_type="flash.failed")


def test_full_chain_persists_monitor_failure_as_terminal_workflow_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command_calls = install_no_command_execution_guard(monkeypatch)
    rig = make_rig(tmp_path, monkeypatch)
    builder = StubBuildService()
    flasher = StubFlashService()
    detector = StubBoardDetector()
    monitor = StubMonitorFactory(fail=True)

    generated = generate_review(rig)
    apply_review(rig, generated.run_id)
    built = run(make_build_service(rig, builder).run_build_for_applied_workflow(
        generated.run_id,
        workspace_root=rig.active,
        build_confirmed=True,
        environment="esp32dev",
    ))
    assert built.status == "awaiting_flash"
    flashed = run(make_flash_service(rig, flasher, detector).run_flash_for_built_workflow(
        generated.run_id,
        flash_confirmed=True,
        workspace_path=rig.active,
        port="COM7",
        board_id="esp32dev",
    ))
    assert flashed.status == "awaiting_monitor"
    result = run(make_monitor_service(rig, monitor).run_monitor_for_flashed_workflow(
        generated.run_id,
        monitor_confirmed=True,
    ))

    assert result.status == "failed"
    assert result.failure_code == MONITOR_FAILED
    assert len(rig.provider.prompts) == 1
    assert len(builder.calls) == 1
    assert len(flasher.calls) == 1
    assert detector.calls == 1
    assert monitor.observe_calls == 1
    types = event_types(rig.store, generated.run_id)
    assert "monitor.failed" in types
    assert "workflow.failed" in types
    assert "workflow.completed" not in types
    assert command_calls == []
    assert run_status_history(rig.store, generated.run_id) == [
        "awaiting_apply",
        "applying",
        "awaiting_build",
        "building",
        "awaiting_flash",
        "flashing",
        "awaiting_monitor",
        "monitoring",
        "failed",
    ]
    assert_failure_persisted(rig, generated.run_id, failure_code=MONITOR_FAILED, event_type="monitor.failed")
