from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.api.test_rollback_restore_routes import client, create_rollback_snapshot, write


def test_bridge_safety_status_reports_restore_flag_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("FORGEX_ENABLE_ROLLBACK_RESTORE", raising=False)
    with client(tmp_path) as api:
        response = api.get("/models/bridges/safety-status")

    assert response.status_code == 200
    body = response.json()
    assert body["rollback_snapshots_enabled"] is True
    assert body["restore_preflight_enabled"] is True
    assert body["restore_enabled"] is False
    assert body["restore_feature_flag"] is False
    assert body["apply_enabled"] is False


def test_restore_route_rejects_when_feature_flag_disabled(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("FORGEX_ENABLE_ROLLBACK_RESTORE", raising=False)
    workspace = tmp_path / "workspace"
    with client(tmp_path) as api:
        rollback_id = create_rollback_snapshot(api, workspace)
        response = restore(api, rollback_id, workspace)

    assert response.status_code == 403
    assert "Rollback restore is disabled" in response.json()["message"]
    assert (workspace / "README.md").read_text(encoding="utf-8") == "old\n"


def test_restore_route_requires_exact_confirmation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    workspace = tmp_path / "workspace"
    with client(tmp_path) as api:
        rollback_id = create_rollback_snapshot(api, workspace)
        response = api.post(
            f"/models/bridges/rollback-snapshots/{rollback_id}/restore",
            json={"workspace_root": str(workspace), "confirmation": "restore"},
        )

    assert response.status_code == 422
    assert "confirmation RESTORE" in response.json()["message"]
    assert (workspace / "README.md").read_text(encoding="utf-8") == "old\n"


def test_restore_route_restores_and_persists_result(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    workspace = tmp_path / "workspace"
    with client(tmp_path) as api:
        rollback_id = create_rollback_snapshot(api, workspace)
        write(workspace / "unrelated.txt", "keep\n")
        status_response = api.get("/models/bridges/safety-status")
        response = restore(api, rollback_id, workspace)
        list_response = api.get("/models/bridges/rollback-restores")
        restore_id = response.json()["restore_id"]
        detail_response = api.get(f"/models/bridges/rollback-restores/{restore_id}")

    assert status_response.json()["restore_enabled"] is True
    assert response.status_code == 200
    body = response.json()
    assert body["rollback_id"] == rollback_id
    assert body["status"] == "restored"
    assert body["files_restored"] == 1
    assert body["files_removed"] == 0
    assert body["files_failed"] == 0
    assert body["restore_enabled"] is True
    assert body["apply_enabled"] is False
    assert (workspace / "README.md").read_text(encoding="utf-8") == "old\n"
    assert (workspace / "unrelated.txt").read_text(encoding="utf-8") == "keep\n"
    assert list_response.status_code == 200
    assert list_response.json()["count"] == 1
    assert detail_response.status_code == 200
    assert detail_response.json()["restore"]["restore_id"] == restore_id


def test_restore_route_rejects_failed_preflight(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    workspace = tmp_path / "workspace"
    with client(tmp_path) as api:
        rollback_id = create_rollback_snapshot(api, workspace)
        backup = next((tmp_path / "patch-rollback" / rollback_id / "files").rglob("README.md"))
        backup.unlink()
        response = restore(api, rollback_id, workspace)

    assert response.status_code == 422
    assert "preflight failed" in response.json()["message"]
    assert (workspace / "README.md").read_text(encoding="utf-8") == "old\n"


def restore(api: TestClient, rollback_id: str, workspace: Path):
    return api.post(
        f"/models/bridges/rollback-snapshots/{rollback_id}/restore",
        json={"workspace_root": str(workspace), "confirmation": "RESTORE"},
    )
