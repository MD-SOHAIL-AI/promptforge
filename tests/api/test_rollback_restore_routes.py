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


def client(tmp_path: Path) -> TestClient:
    registry = ProviderRegistry(ProviderSettingsStorage(tmp_path / "settings.json"))
    router = ModelRouterService(registry, UsageTracker(tmp_path / "usage.jsonl"), {})
    generation = CodeGenerationService(router, diagnostics_store=GenerationDiagnosticsStore(tmp_path / "diagnostics"))
    reviews = BridgeDiffService(
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
        bridge_detection_service=BridgeDetectionService(command_runner=MissingToolRunner(), platform_name="Linux"),
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


def create_rollback_snapshot(api: TestClient, workspace: Path) -> str:
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
    return api.post(f"/models/bridges/patches/{patch_id}/rollback-snapshot", json={"workspace_root": str(workspace)}).json()["rollback_id"]


def test_restore_preflight_route_returns_read_only_report(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    with client(tmp_path) as api:
        rollback_id = create_rollback_snapshot(api, workspace)
        response = api.post(f"/models/bridges/rollback-snapshots/{rollback_id}/restore-preflight", json={"workspace_root": str(workspace)})

    assert response.status_code == 200
    body = response.json()
    assert body["rollback_id"] == rollback_id
    assert body["can_restore"] is True
    assert body["restore_enabled"] is False
    assert body["workspace_status"] == "ok"
    assert body["snapshot_status"] == "valid"
    assert body["files_to_restore"] == ["README.md"]
    assert (workspace / "README.md").read_text(encoding="utf-8") == "old\n"


def test_restore_preflight_route_reports_missing_backup(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    with client(tmp_path) as api:
        rollback_id = create_rollback_snapshot(api, workspace)
        backup = next((tmp_path / "patch-rollback" / rollback_id / "files").rglob("README.md"))
        backup.unlink()
        response = api.post(f"/models/bridges/rollback-snapshots/{rollback_id}/restore-preflight", json={"workspace_root": str(workspace)})

    assert response.status_code == 200
    body = response.json()
    assert body["can_restore"] is False
    assert any(item["type"] == "backup_missing" for item in body["conflicts"])


def test_restore_preflight_route_missing_snapshot_returns_404(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        response = api.post("/models/bridges/rollback-snapshots/missing/restore-preflight", json={"workspace_root": str(tmp_path)})

    assert response.status_code == 404
