from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.api.test_antigravity_bridge_routes import make_client, write


def setup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, enabled: bool) -> None:
    monkeypatch.setenv("PROMPTFORGE_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_ENABLE_AGENT_RUNTIME", "1" if enabled else "0")
    monkeypatch.setenv("FORGEX_ENABLE_AGENT_RUNTIME_FAKE_PROVIDER", "1" if enabled else "0")
    monkeypatch.setenv("FORGEX_ENABLE_API_PROVIDERS", "0")


def register(api, tmp_path: Path) -> tuple[str, Path]:
    workspace = tmp_path / "active-project"
    write(workspace / "platformio.ini", "[env:test]\nplatform = native\n")
    write(workspace / "README.md", "active unchanged\n")
    response = api.post("/projects/import", json={"path": str(workspace)})
    assert response.status_code == 200, response.text
    return response.json()["project_id"], workspace


def wait_run(api, run_id: str) -> dict[str, object]:
    for _ in range(300):
        response = api.get(f"/agent-runtime/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()["run"]
        if run["status"] in {"completed", "failed", "cancelled", "blocked", "timed_out"}:
            return run
        time.sleep(0.01)
    raise AssertionError("product agent run did not finish")


def test_product_agent_route_disabled_without_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup(monkeypatch, tmp_path, enabled=False)
    with make_client(tmp_path, enabled=False) as api:
        project_id, _ = register(api, tmp_path)
        response = api.post("/agent-runtime/runs", json={"project_id": project_id, "instruction": "safe"})
        providers = api.get("/agent-runtime/providers")
    assert response.status_code == 403
    assert providers.json()["enabled"] is False


def test_fake_product_route_creates_persistent_review_and_sanitized_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup(monkeypatch, tmp_path, enabled=True)
    with make_client(tmp_path, enabled=False) as api:
        project_id, workspace = register(api, tmp_path)
        before = (workspace / "README.md").read_bytes()
        started = api.post("/agent-runtime/runs", json={"project_id": project_id, "instruction": "Create a safe sandbox file."})
        assert started.status_code == 202, started.text
        run = wait_run(api, started.json()["run"]["run_id"])
        review = api.get(f"/agent-runtime/runs/{run['run_id']}/review")
        events = api.get(f"/agent-runtime/runs/{run['run_id']}/events")
    assert run["classification"] == "TOOL_RUNTIME_PASS"
    assert (run["created_file_count"], run["modified_file_count"], run["deleted_file_count"]) == (1, 0, 0)
    assert run["review_id"] and review.status_code == 200
    assert (workspace / "README.md").read_bytes() == before
    assert not (workspace / "FORGEX_AGENT_RUNTIME_SMOKE.txt").exists()
    serialized = json.dumps({"run": run, "events": events.text}).casefold()
    assert "create a safe sandbox file" not in serialized
    for forbidden in ("api_key", "raw_prompt", "raw_response", "stdout", "stderr"):
        assert forbidden not in events.text.casefold()


def test_non_routeable_provider_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup(monkeypatch, tmp_path, enabled=True)
    with make_client(tmp_path, enabled=False) as api:
        project_id, _ = register(api, tmp_path)
        response = api.post("/agent-runtime/runs", json={"project_id": project_id, "instruction": "safe", "provider_id": "codex_local_cli_paused"})
    assert response.status_code == 422
    assert response.json()["code"] == "PRODUCT_PROVIDER_NOT_ROUTEABLE"
