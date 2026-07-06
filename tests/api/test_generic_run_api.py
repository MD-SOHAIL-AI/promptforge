from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.api.test_antigravity_bridge_routes import make_client, write


FLAGS = (
    "FORGEX_ENABLE_AGY_BRIDGE",
    "FORGEX_ENABLE_GENERIC_BRIDGE_API",
    "FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING",
    "FORGEX_ENABLE_AGY_GENERIC_PROVIDER",
    "FORGEX_ENABLE_AGY_GENERIC_CUTOVER",
)
FORBIDDEN = {
    "instruction",
    "instruction_hash",
    "stdout_preview",
    "stderr_preview",
    "command",
    "environment",
    "workspace_root",
    "sandbox_root",
    "storage_reference",
    "patch",
}


def configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, missing: str | None = None) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    for name in FLAGS:
        monkeypatch.setenv(name, "0" if name == missing else "1")


def register_throwaway(api, tmp_path: Path) -> tuple[str, Path]:
    workspace = tmp_path / "workspace" / "forgex-apply-test"
    write(workspace / "platformio.ini", "[env:test]\nplatform = native\n")
    write(workspace / "README.md", "base\n")
    response = api.post("/projects/import", json={"path": str(workspace)})
    assert response.status_code == 200, response.text
    return response.json()["project_id"], workspace


def body(project_id: str, **changes):
    value = {
        "provider_id": "agy",
        "project_id": project_id,
        "instruction": "Change README safely in the managed sandbox.",
        "timeout_seconds": 30,
        "idempotency_key": "generic-api-test-001",
    }
    value.update(changes)
    return value


def wait_detail(api, run_id: str):
    for _ in range(200):
        response = api.get(f"/models/bridges/generic/runs/{run_id}")
        assert response.status_code == 200, response.text
        value = response.json()
        if value["status"] in {"completed", "failed", "cancelled", "timed_out", "blocked", "interrupted"}:
            return value
        time.sleep(0.01)
    raise AssertionError("generic run did not reach a terminal state")


def assert_sanitized(value) -> None:
    serialized = json.dumps(value).casefold()
    for key in FORBIDDEN:
        assert f'"{key}"' not in serialized
    assert "change readme safely" not in serialized


def test_generic_api_disabled_by_default_and_safe_provider_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in FLAGS:
        monkeypatch.setenv(name, "0")
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    with make_client(tmp_path, enabled=False) as api:
        response = api.post("/models/bridges/runs", json=body("project-safe-001"))
        providers = api.get("/models/bridges/providers")
        safety = api.get("/models/bridges/safety-status")
    assert response.status_code == 403
    assert response.json() == {
        "code": "generic_execution_disabled",
        "message": "Generic agent execution is disabled by local policy.",
        "details": {},
    }
    assert providers.status_code == 200
    assert [item["provider_id"] for item in providers.json()["providers"]] == ["agy"]
    assert providers.json()["providers"][0]["execution_enabled"] is False
    assert_sanitized(providers.json())
    assert safety.json()["public_generic_run_api_enabled"] is False


@pytest.mark.parametrize("missing", FLAGS)
def test_generic_api_full_feature_gate_matrix_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    configure(monkeypatch, tmp_path, missing=missing)
    with make_client(tmp_path, enabled=missing != "FORGEX_ENABLE_AGY_BRIDGE") as api:
        response = api.post("/models/bridges/runs", json=body("project-safe-001"))
    assert response.status_code == 403
    assert response.json()["code"] == "generic_execution_disabled"


@pytest.mark.parametrize(
    "mutation",
    [
        {"provider_id": "codex"},
        {"provider_id": "claude"},
        {"provider_id": "opencode"},
        {"project_id": "../workspace"},
        {"instruction": "   "},
        {"timeout_seconds": 9},
        {"timeout_seconds": 901},
        {"idempotency_key": "bad key"},
        {"command": "agy"},
        {"arguments": ["--dangerous"]},
        {"environment": {"TOKEN": "secret"}},
        {"working_directory": "C:/workspace"},
        {"auto_apply": True},
    ],
)
def test_generic_start_strict_request_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: dict[str, object]
) -> None:
    configure(monkeypatch, tmp_path)
    with make_client(tmp_path, enabled=True) as api:
        request_body = body("project-safe-001")
        request_body.update(mutation)
        response = api.post("/models/bridges/runs", json=request_body)
    assert response.status_code == 422


def test_generic_start_idempotent_safe_and_persisted_before_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, tmp_path)
    with make_client(tmp_path, enabled=True) as api:
        project_id, workspace = register_throwaway(api, tmp_path)
        first = api.post("/models/bridges/runs", json=body(project_id))
        second = api.post("/models/bridges/runs", json=body(project_id))
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert first.json()["run_id"] == second.json()["run_id"]
        assert first.json()["idempotent_reuse"] is False
        assert second.json()["idempotent_reuse"] is True
        run_id = first.json()["run_id"]
        assert api.app.state.generic_bridge_coordinator.get_run(run_id).run_id == run_id
        detail = wait_detail(api, run_id)
        listing = api.get("/models/bridges/generic/runs", params={"project_id": project_id, "limit": 10})
        persisted = api.app.state.generic_bridge_run_store.path.read_text(encoding="utf-8")
        calls = api.app.state.bridge_agy_runner.popen_factory.calls
    assert set(first.json()) == {
        "run_id", "provider_id", "status", "created_at", "idempotent_reuse", "event_stream_available"
    }
    assert detail["status"] == "completed"
    assert detail["review_id"]
    assert detail["changed_file_count"] == 1
    assert listing.status_code == 200 and listing.json()["count"] == 1
    assert len(calls) == 1
    assert "Change README safely" not in persisted
    assert workspace.joinpath("README.md").read_text(encoding="utf-8") == "base\n"
    assert_sanitized(first.json())
    assert_sanitized(detail)
    assert_sanitized(listing.json())


def test_generic_idempotency_conflict_does_not_execute_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, tmp_path)
    with make_client(tmp_path, enabled=True) as api:
        project_id, _ = register_throwaway(api, tmp_path)
        first = api.post("/models/bridges/runs", json=body(project_id))
        conflict = api.post(
            "/models/bridges/runs",
            json=body(project_id, instruction="A different runtime-only instruction."),
        )
        wait_detail(api, first.json()["run_id"])
        calls = api.app.state.bridge_agy_runner.popen_factory.calls
    assert conflict.status_code == 409
    assert len(calls) == 1


def test_generic_listing_bounds_filters_not_found_and_safe_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, tmp_path)
    with make_client(tmp_path, enabled=True) as api:
        project_id, _ = register_throwaway(api, tmp_path)
        started = api.post("/models/bridges/runs", json=body(project_id))
        terminal = wait_detail(api, started.json()["run_id"])
        filtered = api.get(
            "/models/bridges/generic/runs",
            params={"provider_id": "agy", "status": terminal["status"], "project_id": project_id, "limit": 1},
        )
        too_large = api.get("/models/bridges/generic/runs", params={"limit": 101})
        missing = api.get("/models/bridges/generic/runs/bridge-run-missing")
        cancel_missing = api.post("/models/bridges/generic/runs/bridge-run-missing/cancel", json={})
        cancel_terminal = api.post(f"/models/bridges/generic/runs/{terminal['run_id']}/cancel", json={})
    assert filtered.status_code == 200 and filtered.json()["count"] == 1
    assert too_large.status_code == 422
    assert missing.status_code == 404 and missing.json()["code"] == "GENERIC_RUN_NOT_FOUND"
    assert cancel_missing.json()["disposition"] == "not_found"
    assert cancel_terminal.json()["disposition"] == "already_terminal"


def test_generic_sse_ordered_replay_resync_and_sanitization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, tmp_path)
    with make_client(tmp_path, enabled=True) as api:
        project_id, _ = register_throwaway(api, tmp_path)
        started = api.post("/models/bridges/runs", json=body(project_id))
        run_id = started.json()["run_id"]
        wait_detail(api, run_id)
        events = api.app.state.generic_bridge_coordinator.run_store.list_events(run_id)
        all_events = api.get(f"/models/bridges/runs/{run_id}/events")
        replay = api.get(
            f"/models/bridges/runs/{run_id}/events",
            headers={"Last-Event-ID": events[-2].event_id},
        )
        resync = api.get(
            f"/models/bridges/runs/{run_id}/events",
            headers={"Last-Event-ID": "event-missed-history"},
        )
    assert all_events.status_code == 200
    assert all_events.headers["content-type"].startswith("text/event-stream")
    sequences = [item.sequence for item in events]
    assert sequences == list(range(1, len(events) + 1))
    assert events[-1].event_id in all_events.text
    assert events[-2].event_id not in replay.text
    assert events[-1].event_id in replay.text
    assert '"event_type":"resync_required"' in resync.text
    assert_sanitized(all_events.text)


def test_generic_remote_origin_and_request_size_are_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, tmp_path)
    with make_client(tmp_path, enabled=True) as api:
        remote = api.post(
            "/models/bridges/runs",
            json=body("project-safe-001"),
            headers={"Origin": "https://remote.example"},
        )
        oversized = api.post(
            "/models/bridges/runs",
            content=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": "40000"},
        )
    assert remote.status_code == 403 and remote.json()["code"] == "REMOTE_ORIGIN_REJECTED"
    assert oversized.status_code == 413 and oversized.json()["code"] == "REQUEST_TOO_LARGE"
