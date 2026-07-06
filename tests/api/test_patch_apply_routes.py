from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.api.test_rollback_restore_routes import client, write


def create_patch(api: TestClient, workspace: Path) -> str:
    write(workspace / "README.md", "old\n")
    snapshot_response = api.post("/models/bridges/reviews/snapshot", json={"workspace_root": str(workspace)})
    write(workspace / "README.md", "new\n")
    review_response = api.post(
        "/models/bridges/reviews/diff",
        json={
            "provider_id": "antigravity_cli_bridge",
            "workspace_root": str(workspace),
            "snapshot_id": snapshot_response.json()["snapshot"]["snapshot_id"],
        },
    )
    review_id = review_response.json()["review"]["review_id"]
    api.post(f"/models/bridges/reviews/{review_id}/approve")
    patch_id = api.post(f"/models/bridges/reviews/{review_id}/export-patch").json()["patch"]["patch_id"]
    write(workspace / "README.md", "old\n")
    return patch_id


def test_safety_status_reports_patch_apply_flags(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("FORGEX_ENABLE_PATCH_APPLY", raising=False)
    monkeypatch.delenv("FORGEX_ENABLE_ROLLBACK_RESTORE", raising=False)
    with client(tmp_path) as api:
        disabled = api.get("/models/bridges/safety-status").json()
    monkeypatch.setenv("FORGEX_ENABLE_PATCH_APPLY", "1")
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    with client(tmp_path) as api:
        enabled = api.get("/models/bridges/safety-status").json()

    assert disabled["patch_apply_enabled"] is False
    assert disabled["patch_apply_feature_flag"] is False
    assert disabled["rollback_restore_enabled"] is False
    assert disabled["rollback_restore_feature_flag"] is False
    assert disabled["apply_requires_restore"] is True
    assert enabled["patch_apply_enabled"] is True
    assert enabled["patch_apply_feature_flag"] is True
    assert enabled["rollback_restore_enabled"] is True
    assert enabled["rollback_restore_feature_flag"] is True


def test_apply_route_rejects_disabled_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("FORGEX_ENABLE_PATCH_APPLY", raising=False)
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    workspace = tmp_path / "workspace"
    with client(tmp_path) as api:
        patch_id = create_patch(api, workspace)
        response = api.post(
            f"/models/bridges/patches/{patch_id}/apply",
            json={"workspace_root": str(workspace), "confirmation": "APPLY"},
        )

    assert response.status_code == 403
    assert "Patch apply is disabled" in response.json()["message"]
    assert (workspace / "README.md").read_text(encoding="utf-8") == "old\n"


def test_apply_route_applies_and_lists_result(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("FORGEX_ENABLE_PATCH_APPLY", "1")
    monkeypatch.setenv("FORGEX_ENABLE_ROLLBACK_RESTORE", "1")
    workspace = tmp_path / "workspace"
    with client(tmp_path) as api:
        patch_id = create_patch(api, workspace)
        preflight = api.post(f"/models/bridges/patches/{patch_id}/preflight", json={"workspace_root": str(workspace)})
        response = api.post(
            f"/models/bridges/patches/{patch_id}/apply",
            json={"workspace_root": str(workspace), "confirmation": "APPLY"},
        )
        apply_id = response.json()["apply_id"]
        list_response = api.get("/models/bridges/patch-applies")
        detail_response = api.get(f"/models/bridges/patch-applies/{apply_id}")

    assert preflight.json()["apply_enabled"] is True
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "applied"
    assert body["files_modified"] == 1
    assert body["rollback_id"]
    assert body["rollback_available"] is True
    assert (workspace / "README.md").read_text(encoding="utf-8") == "new\n"
    assert list_response.status_code == 200
    assert list_response.json()["count"] == 1
    assert detail_response.status_code == 200
    assert detail_response.json()["apply"]["apply_id"] == apply_id
