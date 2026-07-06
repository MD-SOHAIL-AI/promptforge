from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.bridges import BridgeDetectionService, BridgeDiffService, BridgePatchExportService
from backend.bridges.audit_log import BridgeAuditLog
from backend.bridges.review_store import BridgeReviewStore
from backend.model_router import ModelRouterService, ProviderRegistry, ProviderSettingsStorage, UsageTracker
from backend.services.code_generation_service import CodeGenerationService
from backend.services.generation_diagnostics_store import GenerationDiagnosticsStore
from backend.services.project_service import ProjectService


class MissingToolRunner:
    def __call__(self, args: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(args), 1, "", "")


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def review_service(tmp_path: Path) -> BridgeDiffService:
    return BridgeDiffService(
        audit_log=BridgeAuditLog(tmp_path / "bridge-audit.jsonl"),
        store=BridgeReviewStore(
            snapshots_path=tmp_path / "bridge-snapshots.jsonl",
            reviews_path=tmp_path / "bridge-reviews.jsonl",
        ),
    )


def client(tmp_path: Path, bridge_diff_service: BridgeDiffService | None = None) -> TestClient:
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "settings.json"))
    router = ModelRouterService(registry, UsageTracker(tmp_path / "usage.jsonl"), {})
    generation = CodeGenerationService(
        router,
        diagnostics_store=GenerationDiagnosticsStore(tmp_path / "diagnostics"),
    )
    reviews = bridge_diff_service or review_service(tmp_path)
    app = create_app(
        model_router_service=router,
        code_generation_service=generation,
        project_service=ProjectService(tmp_path / "projects"),
        bridge_detection_service=BridgeDetectionService(
            command_runner=MissingToolRunner(),
            platform_name="Linux",
        ),
        bridge_diff_service=reviews,
        bridge_patch_export_service=BridgePatchExportService(
            review_service=reviews,
            patch_directory=tmp_path / "bridge-patches",
            audit_log=BridgeAuditLog(tmp_path / "bridge-audit.jsonl"),
            opener=lambda path: None,
        ),
        version="test-version",
    )
    return TestClient(app, raise_server_exceptions=False)


def create_approved_patch(api: TestClient, workspace: Path) -> tuple[str, str]:
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
    return review_id, patch_id


def test_create_list_detail_and_delete_rollback_snapshot(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    with client(tmp_path) as api:
        review_id, patch_id = create_approved_patch(api, workspace)
        create = api.post(f"/models/bridges/patches/{patch_id}/rollback-snapshot", json={"workspace_root": str(workspace)})
        assert create.status_code == 200
        body = create.json()
        rollback_id = body["rollback_id"]
        assert body["patch_id"] == patch_id
        assert body["review_id"] == review_id
        assert body["status"] == "created"
        assert body["files_backed_up"] == 1
        assert body["restore_enabled"] is False
        assert (workspace / "README.md").read_text(encoding="utf-8") == "old\n"

        listing = api.get("/models/bridges/rollback-snapshots")
        detail = api.get(f"/models/bridges/rollback-snapshots/{rollback_id}")
        delete = api.delete(f"/models/bridges/rollback-snapshots/{rollback_id}")
        after_delete = api.get("/models/bridges/rollback-snapshots")

    assert listing.status_code == 200
    assert listing.json()["count"] == 1
    assert detail.status_code == 200
    assert detail.json()["snapshot"]["rollback_id"] == rollback_id
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True
    assert after_delete.status_code == 200
    assert after_delete.json()["count"] == 0


def test_rollback_snapshot_route_rejects_failed_preflight(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    with client(tmp_path) as api:
        _, patch_id = create_approved_patch(api, workspace)
        write(workspace / "README.md", "changed locally\n")
        response = api.post(f"/models/bridges/patches/{patch_id}/rollback-snapshot", json={"workspace_root": str(workspace)})

    assert response.status_code == 422


def test_rollback_snapshot_cleanup_route(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    with client(tmp_path) as api:
        _, patch_id = create_approved_patch(api, workspace)
        create = api.post(f"/models/bridges/patches/{patch_id}/rollback-snapshot", json={"workspace_root": str(workspace)})
        assert create.status_code == 200
        cleanup = api.post("/models/bridges/rollback-snapshots/cleanup", json={"older_than_days": 30})

    assert cleanup.status_code == 200
    assert cleanup.json()["cleanup"]["removed"] == 0
