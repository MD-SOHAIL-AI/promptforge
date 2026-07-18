from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.agent_runtime.coding_workflow_store import CodingWorkflowStore
from backend.bridges import BridgeDiffService
from backend.bridges.audit_log import BridgeAuditLog
from backend.bridges.review_store import BridgeReviewStore
from backend.model_router import ModelRouterService, ProviderRegistry, ProviderSettingsStorage, UsageTracker
from backend.model_router.credentials import MemoryCredentialStore
from backend.services.code_generation_service import CodeGenerationService
from backend.services.generation_diagnostics_store import GenerationDiagnosticsStore
from backend.services.project_service import ProjectService
from tests.integration.test_unified_coding_workflow_full_chain import (
    CountingFakeProvider,
    StubBoardDetector,
    StubBuildService,
    StubFlashService,
    StubMonitorFactory,
)


BASE = "/models/coding-workflow"
DIRECT_FAKE = "/models/api-coding-agent/fake/generate-review"


class RouteRig:
    def __init__(
        self,
        api: TestClient,
        *,
        provider: CountingFakeProvider,
        builder: StubBuildService,
        flasher: StubFlashService,
        detector: StubBoardDetector,
        monitor: StubMonitorFactory,
    ) -> None:
        self.api = api
        self.provider = provider
        self.builder = builder
        self.flasher = flasher
        self.detector = detector
        self.monitor = monitor


def configure_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    unified: bool,
    fake: bool,
) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_ENABLE_PATCH_APPLY", "1")
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    if unified:
        monkeypatch.setenv("FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW", "1")
    else:
        monkeypatch.setenv("FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW", "0")
    if fake:
        monkeypatch.setenv("FORGEX_ENABLE_FAKE_API_CODING_AGENT", "1")
    else:
        monkeypatch.setenv("FORGEX_ENABLE_FAKE_API_CODING_AGENT", "0")
    monkeypatch.delenv("FORGEX_ENABLE_CODEX_PROVIDER", raising=False)
    monkeypatch.delenv("FORGEX_ENABLE_AGY_BRIDGE", raising=False)
    monkeypatch.delenv("FORGEX_ENABLE_AGY_GENERIC_PROVIDER", raising=False)
    monkeypatch.delenv("FORGEX_ENABLE_AGY_GENERIC_CUTOVER", raising=False)
    monkeypatch.delenv("FORGEX_ENABLE_AGY_SDK_PROVIDER", raising=False)


def make_route_rig(
    tmp_path: Path,
    *,
    unified: bool = True,
    fake: bool = True,
    monitor_output: str = "booting\nREADY",
) -> RouteRig:
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "settings.json", credential_store=MemoryCredentialStore()))
    router = ModelRouterService(registry, UsageTracker(tmp_path / "usage.jsonl"), {})
    generation = CodeGenerationService(
        router,
        diagnostics_store=GenerationDiagnosticsStore(tmp_path / "diagnostics"),
    )
    review_service = BridgeDiffService(
        audit_log=BridgeAuditLog(tmp_path / "bridge-audit.jsonl"),
        store=BridgeReviewStore(
            snapshots_path=tmp_path / "bridge-snapshots.jsonl",
            reviews_path=tmp_path / "bridge-reviews.jsonl",
        ),
    )
    app = create_app(
        model_router_service=router,
        code_generation_service=generation,
        project_service=ProjectService(tmp_path / "projects"),
        bridge_diff_service=review_service,
        version="test-version",
    )
    provider = CountingFakeProvider()
    production_store = app.state.coding_workflow_store
    app.state.coding_workflow_store = CodingWorkflowStore(
        runs_path=production_store.runs_path,
        events_path=production_store.events_path,
        allow_new_runs=True,
    )  # Explicit legacy-fixture mode; production app is read-only.
    app.state.api_coding_agent_service.provider = provider
    builder = StubBuildService()
    flasher = StubFlashService()
    detector = StubBoardDetector()
    monitor = StubMonitorFactory(output=monitor_output)
    app.state.coding_workflow_build_executor = builder
    app.state.coding_workflow_flash_executor = flasher
    app.state.coding_workflow_board_detector = detector
    app.state.coding_workflow_monitor_factory = monitor
    return RouteRig(
        TestClient(app, raise_server_exceptions=False),
        provider=provider,
        builder=builder,
        flasher=flasher,
        detector=detector,
        monitor=monitor,
    )


def make_workspace(tmp_path: Path) -> Path:
    active = tmp_path / "active"
    active.mkdir()
    (active / "README.md").write_text("active workspace\n", encoding="utf-8")
    return active


def generate(api: TestClient, active: Path) -> dict[str, Any]:
    response = api.post(
        f"{BASE}/fake/generate-review",
        json={"prompt": "basic esp32 blink", "workspace_path": str(active)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def apply(api: TestClient, run_id: str, active: Path) -> dict[str, Any]:
    response = api.post(
        f"{BASE}/{run_id}/approve-apply",
        json={"approval_confirmed": True, "workspace_path": str(active), "approved_by": "user"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def build(api: TestClient, run_id: str, active: Path) -> dict[str, Any]:
    response = api.post(
        f"{BASE}/{run_id}/build",
        json={"build_confirmed": True, "workspace_path": str(active), "environment": "esp32dev"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def flash(api: TestClient, run_id: str, active: Path) -> dict[str, Any]:
    response = api.post(
        f"{BASE}/{run_id}/flash",
        json={
            "flash_confirmed": True,
            "workspace_path": str(active),
            "port": "COM7",
            "board_id": "esp32dev",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def monitor(api: TestClient, run_id: str) -> dict[str, Any]:
    response = api.post(
        f"{BASE}/{run_id}/monitor",
        json={"monitor_confirmed": True, "duration_seconds": 3, "max_output_bytes": 128},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_write_routes_are_feature_gated_and_read_routes_are_safe_when_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=False, fake=True)
    rig = make_route_rig(tmp_path)
    active = make_workspace(tmp_path)

    write_requests = [
        rig.api.post(f"{BASE}/fake/generate-review", json={"prompt": "blink", "workspace_path": str(active)}),
        rig.api.post(f"{BASE}/missing-run/approve-apply", json={"approval_confirmed": True, "workspace_path": str(active)}),
        rig.api.post(f"{BASE}/missing-run/build", json={"build_confirmed": True, "workspace_path": str(active)}),
        rig.api.post(f"{BASE}/missing-run/flash", json={"flash_confirmed": True, "workspace_path": str(active), "port": "COM7", "board_id": "esp32dev"}),
        rig.api.post(f"{BASE}/missing-run/monitor", json={"monitor_confirmed": True}),
    ]
    assert {response.status_code for response in write_requests} == {403}
    assert {response.json()["code"] for response in write_requests} == {"UNIFIED_CODING_WORKFLOW_DISABLED"}

    assert rig.api.get(BASE).json() == {"runs": [], "count": 0}
    missing = rig.api.get(f"{BASE}/missing-run")
    assert missing.status_code == 404
    assert missing.json()["code"] == "CODING_WORKFLOW_RUN_NOT_FOUND"

    configure_env(monkeypatch, tmp_path / "fake-disabled", unified=True, fake=False)
    rig2 = make_route_rig(tmp_path / "fake-disabled")
    active2 = make_workspace(tmp_path / "fake-disabled")
    response = rig2.api.post(
        f"{BASE}/fake/generate-review",
        json={"prompt": "blink", "workspace_path": str(active2)},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "FAKE_API_CODING_AGENT_DISABLED"


def test_generate_route_creates_awaiting_apply_without_changing_workspace_and_direct_fake_still_works(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=True)
    rig = make_route_rig(tmp_path)
    active = make_workspace(tmp_path)

    payload = generate(rig.api, active)
    direct = rig.api.post(
        DIRECT_FAKE,
        json={"prompt": "basic esp32 blink", "workspace_path": str(active)},
    )

    assert payload["status"] == "awaiting_apply"
    assert payload["generation_status"] == "review_created"
    assert payload["review_id"]
    assert payload["files_changed"] == ["platformio.ini", "src/main.cpp"]
    assert payload["next_action"] == "await_user_approval"
    assert [event["event_type"] for event in payload["events"]] == [
        "provider.selected",
        "generation.started",
        "generation.completed",
        "review.created",
        "apply.waiting_for_approval",
    ]
    assert (active / "README.md").read_text(encoding="utf-8") == "active workspace\n"
    assert not (active / "platformio.ini").exists()
    assert not (active / "src" / "main.cpp").exists()
    assert len(rig.provider.prompts) == 2
    assert direct.status_code == 200
    assert direct.json()["status"] == "review_created"
    assert direct.json()["files"] == ["platformio.ini", "src/main.cpp"]


def test_routes_drive_full_chain_one_gate_at_a_time_and_read_safe_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=True)
    rig = make_route_rig(
        tmp_path,
        monitor_output="booting\nOPENAI_API_KEY=super-secret\nC:\\private\\firmware.bin\n" + ("x" * 2_000),
    )
    active = make_workspace(tmp_path)

    generated = generate(rig.api, active)
    run_id = generated["run_id"]
    rig.provider.blocked = True

    refused_apply = rig.api.post(
        f"{BASE}/{run_id}/approve-apply",
        json={"workspace_path": str(active)},
    )
    assert refused_apply.status_code == 403
    assert refused_apply.json()["code"] == "CODING_WORKFLOW_APPROVAL_REQUIRED"
    assert rig.builder.calls == []

    applied = apply(rig.api, run_id, active)
    assert applied["status"] == "awaiting_build"
    assert applied["next_action"] == "run_build"
    assert (active / "platformio.ini").is_file()
    assert (active / "src" / "main.cpp").is_file()
    assert rig.builder.calls == []
    assert rig.flasher.calls == []
    assert rig.monitor.observe_calls == 0

    refused_build = rig.api.post(f"{BASE}/{run_id}/build", json={"workspace_path": str(active)})
    assert refused_build.status_code == 403
    assert refused_build.json()["code"] == "CODING_WORKFLOW_BUILD_CONFIRMATION_REQUIRED"
    built = build(rig.api, run_id, active)
    assert built["status"] == "awaiting_flash"
    assert built["next_action"] == "confirm_flash"
    assert built["build_status"] == "succeeded"
    assert len(rig.builder.calls) == 1
    assert rig.flasher.calls == []

    refused_flash = rig.api.post(f"{BASE}/{run_id}/flash", json={"workspace_path": str(active), "port": "COM7"})
    assert refused_flash.status_code == 403
    assert refused_flash.json()["code"] == "CODING_WORKFLOW_FLASH_CONFIRMATION_REQUIRED"
    flashed = flash(rig.api, run_id, active)
    assert flashed["status"] == "awaiting_monitor"
    assert flashed["next_action"] == "open_monitor"
    assert flashed["flash_status"] == "succeeded"
    assert len(rig.flasher.calls) == 1
    assert rig.monitor.observe_calls == 0

    refused_monitor = rig.api.post(f"{BASE}/{run_id}/monitor", json={})
    assert refused_monitor.status_code == 403
    assert refused_monitor.json()["code"] == "CODING_WORKFLOW_MONITOR_CONFIRMATION_REQUIRED"
    completed = monitor(rig.api, run_id)
    assert completed["status"] == "completed"
    assert completed["next_action"] is None
    assert completed["monitor_status"] == "completed"
    assert completed["truncated"] is True
    assert len(completed["output_preview"].encode("utf-8")) <= 128
    assert "super-secret" not in completed["output_preview"]
    assert "[REDACTED]" in completed["output_preview"]
    assert "C:\\private" not in completed["output_preview"]
    assert rig.monitor.observe_calls == 1
    assert len(rig.provider.prompts) == 1

    run_response = rig.api.get(f"{BASE}/{run_id}")
    events_response = rig.api.get(f"{BASE}/{run_id}/events")
    list_response = rig.api.get(BASE)
    assert run_response.status_code == 200
    assert run_response.json()["run"]["status"] == "completed"
    assert run_response.json()["run"]["next_action"] is None
    events = events_response.json()["events"]
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert events[-1]["event_type"] == "workflow.completed"
    assert list_response.json()["count"] == 1
    assert list_response.json()["runs"][0]["run_id"] == run_id

    serialized = json.dumps(
        {
            "run": run_response.json(),
            "events": events_response.json(),
            "list": list_response.json(),
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    for forbidden in [
        "#include <Arduino.h>",
        "pinMode",
        "framework = arduino",
        "OPENAI_API_KEY",
        "super-secret",
        "C:\\private",
        "source_body",
        "source_code",
        "raw_response",
    ]:
        assert forbidden not in serialized


def test_wrong_stage_repeated_and_missing_run_calls_return_stable_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=True)
    rig = make_route_rig(tmp_path)
    active = make_workspace(tmp_path)

    missing = rig.api.post(
        f"{BASE}/missing-run/approve-apply",
        json={"approval_confirmed": True, "workspace_path": str(active)},
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "CODING_WORKFLOW_RUN_NOT_FOUND"

    generated = generate(rig.api, active)
    run_id = generated["run_id"]
    wrong_stage = rig.api.post(
        f"{BASE}/{run_id}/build",
        json={"build_confirmed": True, "workspace_path": str(active)},
    )
    assert wrong_stage.status_code == 409
    assert wrong_stage.json()["code"] == "CODING_WORKFLOW_NOT_AWAITING_BUILD"

    apply(rig.api, run_id, active)
    repeated_apply = rig.api.post(
        f"{BASE}/{run_id}/approve-apply",
        json={"approval_confirmed": True, "workspace_path": str(active)},
    )
    assert repeated_apply.status_code == 409
    assert repeated_apply.json()["code"] == "CODING_WORKFLOW_ALREADY_APPLIED"

    build(rig.api, run_id, active)
    repeated_build = rig.api.post(
        f"{BASE}/{run_id}/build",
        json={"build_confirmed": True, "workspace_path": str(active)},
    )
    assert repeated_build.status_code == 409
    assert repeated_build.json()["code"] == "CODING_WORKFLOW_BUILD_ALREADY_COMPLETED"

    flash(rig.api, run_id, active)
    repeated_flash = rig.api.post(
        f"{BASE}/{run_id}/flash",
        json={"flash_confirmed": True, "workspace_path": str(active), "port": "COM7", "board_id": "esp32dev"},
    )
    assert repeated_flash.status_code == 409
    assert repeated_flash.json()["code"] == "CODING_WORKFLOW_FLASH_ALREADY_COMPLETED"

    monitor(rig.api, run_id)
    repeated_monitor = rig.api.post(f"{BASE}/{run_id}/monitor", json={"monitor_confirmed": True})
    assert repeated_monitor.status_code == 409
    assert repeated_monitor.json()["code"] == "CODING_WORKFLOW_MONITOR_ALREADY_COMPLETED"

    active2 = tmp_path / "second-active"
    active2.mkdir()
    (active2 / "README.md").write_text("active workspace\n", encoding="utf-8")
    generated2 = generate(rig.api, active2)
    apply(rig.api, generated2["run_id"], active2)
    build(rig.api, generated2["run_id"], active2)
    no_device = rig.api.post(
        f"{BASE}/{generated2['run_id']}/flash",
        json={"flash_confirmed": True, "workspace_path": str(active2), "port": "COM7"},
    )
    assert no_device.status_code == 422
    assert no_device.json()["code"] == "CODING_WORKFLOW_FLASH_DEVICE_REQUIRED"


def test_cancel_allowed_from_waiting_state_and_cancelled_run_cannot_continue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=True)
    rig = make_route_rig(tmp_path)
    active = make_workspace(tmp_path)
    generated = generate(rig.api, active)
    run_id = generated["run_id"]

    cancelled = rig.api.post(
        f"{BASE}/{run_id}/cancel",
        json={"cancel_confirmed": True, "reason": "user changed their mind"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["next_action"] is None

    apply_after_cancel = rig.api.post(
        f"{BASE}/{run_id}/approve-apply",
        json={"approval_confirmed": True, "workspace_path": str(active)},
    )
    assert apply_after_cancel.status_code == 409
    assert apply_after_cancel.json()["code"] == "CODING_WORKFLOW_NOT_AWAITING_APPLY"
    events = rig.api.get(f"{BASE}/{run_id}/events").json()["events"]
    assert [event["event_type"] for event in events][-2:] == ["cancel.started", "workflow.cancelled"]


def test_duplicate_operation_request_is_rejected_while_lock_is_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=True)
    rig = make_route_rig(tmp_path)
    active = make_workspace(tmp_path)
    generated = generate(rig.api, active)
    run_id = generated["run_id"]
    store = rig.api.app.state.coding_workflow_store

    store.acquire_operation_lock(run_id, "apply")
    duplicate = rig.api.post(
        f"{BASE}/{run_id}/approve-apply",
        json={"approval_confirmed": True, "workspace_path": str(active)},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "CODING_WORKFLOW_OPERATION_IN_PROGRESS"
    store.release_operation_lock(run_id, "apply")

    applied = apply(rig.api, run_id, active)
    assert applied["status"] == "awaiting_build"
    assert store.is_operation_locked(run_id) is False


def test_duplicate_build_flash_and_monitor_requests_are_rejected_while_lock_is_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=True)
    rig = make_route_rig(tmp_path)
    active = make_workspace(tmp_path)
    generated = generate(rig.api, active)
    run_id = generated["run_id"]
    store = rig.api.app.state.coding_workflow_store
    apply(rig.api, run_id, active)

    store.acquire_operation_lock(run_id, "build")
    duplicate_build = rig.api.post(
        f"{BASE}/{run_id}/build",
        json={"build_confirmed": True, "workspace_path": str(active), "environment": "esp32dev"},
    )
    assert duplicate_build.status_code == 409
    assert duplicate_build.json()["code"] == "CODING_WORKFLOW_OPERATION_IN_PROGRESS"
    store.release_operation_lock(run_id, "build")

    build(rig.api, run_id, active)
    store.acquire_operation_lock(run_id, "flash")
    duplicate_flash = rig.api.post(
        f"{BASE}/{run_id}/flash",
        json={"flash_confirmed": True, "workspace_path": str(active), "port": "COM7", "board_id": "esp32dev"},
    )
    assert duplicate_flash.status_code == 409
    assert duplicate_flash.json()["code"] == "CODING_WORKFLOW_OPERATION_IN_PROGRESS"
    store.release_operation_lock(run_id, "flash")

    flash(rig.api, run_id, active)
    store.acquire_operation_lock(run_id, "monitor")
    duplicate_monitor = rig.api.post(f"{BASE}/{run_id}/monitor", json={"monitor_confirmed": True})
    assert duplicate_monitor.status_code == 409
    assert duplicate_monitor.json()["code"] == "CODING_WORKFLOW_OPERATION_IN_PROGRESS"
    store.release_operation_lock(run_id, "monitor")


def test_operation_lock_clears_after_failed_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=True, fake=True)
    rig = make_route_rig(tmp_path)
    active = make_workspace(tmp_path)
    generated = generate(rig.api, active)
    apply(rig.api, generated["run_id"], active)
    rig.builder.fail = True

    failed = rig.api.post(
        f"{BASE}/{generated['run_id']}/build",
        json={"build_confirmed": True, "workspace_path": str(active), "environment": "esp32dev"},
    )

    assert failed.status_code == 422
    assert failed.json()["code"] == "CODING_WORKFLOW_BUILD_FAILED"
    assert rig.api.app.state.coding_workflow_store.is_operation_locked(generated["run_id"]) is False
