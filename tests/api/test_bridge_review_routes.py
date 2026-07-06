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


def test_bridge_review_snapshot_diff_approve_and_reject(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "src" / "main.cpp", "old\n")

    with client(tmp_path) as api:
        snapshot_response = api.post(
            "/models/bridges/reviews/snapshot",
            json={"workspace_root": str(workspace)},
        )
        assert snapshot_response.status_code == 200
        snapshot = snapshot_response.json()["snapshot"]
        assert "content" not in str(snapshot)

        write(workspace / "src" / "main.cpp", "new\n")
        write(workspace / "platformio.ini", "[env]\n")

        diff_response = api.post(
            "/models/bridges/reviews/diff",
            json={
                "provider_id": "codex_bridge",
                "workspace_root": str(workspace),
                "snapshot_id": snapshot["snapshot_id"],
            },
        )
        assert diff_response.status_code == 200
        review = diff_response.json()["review"]
        assert review["status"] == "pending"
        assert len(review["changed_files"]) == 2

        detail = api.get(f"/models/bridges/reviews/{review['review_id']}")
        assert detail.status_code == 200

        approved = api.post(f"/models/bridges/reviews/{review['review_id']}/approve")
        assert approved.status_code == 200
        assert approved.json()["review"]["status"] == "approved"

        snapshot_response = api.post(
            "/models/bridges/reviews/snapshot",
            json={"workspace_root": str(workspace)},
        )
        write(workspace / "src" / "main.cpp", "newer\n")
        rejected = api.post(
            "/models/bridges/reviews/diff",
            json={
                "provider_id": "claude_code_bridge",
                "workspace_root": str(workspace),
                "snapshot_id": snapshot_response.json()["snapshot"]["snapshot_id"],
            },
        )
        review_id = rejected.json()["review"]["review_id"]
        rejection = api.post(f"/models/bridges/reviews/{review_id}/reject")
        assert rejection.status_code == 200
        assert rejection.json()["review"]["status"] == "rejected"


def test_bridge_review_rejects_missing_workspace(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        response = api.post(
            "/models/bridges/reviews/snapshot",
            json={"workspace_root": str(tmp_path / "missing")},
        )

    assert response.status_code == 422


def test_bridge_review_routes_use_persisted_store(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "src" / "main.cpp", "old\n")

    with client(tmp_path) as api:
        snapshot_response = api.post(
            "/models/bridges/reviews/snapshot",
            json={"workspace_root": str(workspace)},
        )
        write(workspace / "src" / "main.cpp", "new\n")
        review_response = api.post(
            "/models/bridges/reviews/diff",
            json={
                "provider_id": "codex_bridge",
                "workspace_root": str(workspace),
                "snapshot_id": snapshot_response.json()["snapshot"]["snapshot_id"],
            },
        )
        review_id = review_response.json()["review"]["review_id"]

    with client(tmp_path) as api:
        detail = api.get(f"/models/bridges/reviews/{review_id}")
        assert detail.status_code == 200
        assert detail.json()["review"]["status"] == "pending"

        listing = api.get("/models/bridges/reviews?status=pending")
        assert listing.status_code == 200
        assert listing.json()["counts"]["pending"] == 1

        approved = api.post(f"/models/bridges/reviews/{review_id}/approve")
        assert approved.status_code == 200
        assert approved.json()["review"]["status"] == "approved"

    with client(tmp_path) as api:
        detail = api.get(f"/models/bridges/reviews/{review_id}")
        assert detail.status_code == 200
        assert detail.json()["review"]["status"] == "approved"


def test_bridge_review_cleanup_removes_expired_only(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "src" / "main.cpp", "old\n")
    service = review_service(tmp_path)
    snapshot = service.snapshot_workspace(workspace)
    write(workspace / "src" / "main.cpp", "new\n")
    expired = service.create_review(provider_id="codex_bridge", workspace_root=workspace, snapshot=snapshot)
    service._sessions[expired.review_id] = type(expired)(
        review_id=expired.review_id,
        provider_id=expired.provider_id,
        workspace_root=expired.workspace_root,
        workspace_root_hash=expired.workspace_root_hash,
        status="expired",
        created_at=expired.created_at,
        expires_at=expired.expires_at,
        changed_files=expired.changed_files,
        summary=expired.summary,
        decision=None,
    )
    service.store.save_review(service._sessions[expired.review_id])  # type: ignore[union-attr]
    snapshot_2 = service.snapshot_workspace(workspace)
    write(workspace / "src" / "main.cpp", "newer\n")
    pending = service.create_review(provider_id="codex_bridge", workspace_root=workspace, snapshot=snapshot_2)

    with client(tmp_path) as api:
        cleanup = api.post("/models/bridges/reviews/cleanup")

    assert cleanup.status_code == 200
    assert cleanup.json()["cleanup"]["removed"] == 1
    restored = review_service(tmp_path)
    assert restored.get_review(pending.review_id).status == "pending"


def test_bridge_review_patch_export_and_download(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "README.md", "old\n")

    with client(tmp_path) as api:
        snapshot_response = api.post(
            "/models/bridges/reviews/snapshot",
            json={"workspace_root": str(workspace)},
        )
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

        export_response = api.post(f"/models/bridges/reviews/{review_id}/export-patch")
        metadata_response = api.get(f"/models/bridges/reviews/{review_id}/patch-metadata")
        verify_response = api.post(f"/models/bridges/reviews/{review_id}/verify-patch")
        opened_response = api.post(f"/models/bridges/reviews/{review_id}/open-patch-folder")
        patch_response = api.get(f"/models/bridges/reviews/{review_id}/patch")

    assert export_response.status_code == 200
    patch = export_response.json()["patch"]
    assert patch["review_id"] == review_id
    assert patch["patch_path"] == f"{review_id}.patch"
    assert patch["patch_size"] > 0
    assert patch["patch_sha256"]
    assert patch["integrity_status"] == "valid"
    assert patch["apply_enabled"] is False
    assert metadata_response.status_code == 200
    assert metadata_response.json()["patch"]["patch_sha256"] == patch["patch_sha256"]
    assert verify_response.status_code == 200
    assert verify_response.json()["patch"]["integrity_status"] == "valid"
    assert opened_response.status_code == 200
    assert opened_response.json()["opened"] is True
    assert patch_response.status_code == 200
    assert "diff --git a/README.md b/README.md" in patch_response.text
    assert str(workspace) not in patch_response.text


def test_bridge_patch_history_delete_and_cleanup_routes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "README.md", "old\n")

    with client(tmp_path) as api:
        snapshot_response = api.post(
            "/models/bridges/reviews/snapshot",
            json={"workspace_root": str(workspace)},
        )
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
        export_response = api.post(f"/models/bridges/reviews/{review_id}/export-patch")
        patch_id = export_response.json()["patch"]["patch_id"]

        history = api.get("/models/bridges/patches?provider_id=antigravity_cli_bridge")
        assert history.status_code == 200
        body = history.json()
        assert body["count"] == 1
        assert body["patches"][0]["patch_id"] == patch_id
        assert "diff --git" not in str(body)

        delete_response = api.delete(f"/models/bridges/patches/{patch_id}")
        assert delete_response.status_code == 200
        assert delete_response.json()["deleted"] is True

        review_detail = api.get(f"/models/bridges/reviews/{review_id}")
        assert review_detail.status_code == 200
        assert review_detail.json()["review"]["review_id"] == review_id
        assert (workspace / "README.md").read_text(encoding="utf-8") == "new\n"

        cleanup = api.post("/models/bridges/patches/cleanup", json={"older_than_days": 30, "include_missing": True})
        assert cleanup.status_code == 200
        assert cleanup.json()["cleanup"]["removed"] == 0


def test_bridge_patch_cleanup_removes_missing_record(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    write(workspace / "README.md", "old\n")

    with client(tmp_path) as api:
        snapshot_response = api.post(
            "/models/bridges/reviews/snapshot",
            json={"workspace_root": str(workspace)},
        )
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
        patch_id = api.post(f"/models/bridges/reviews/{review_id}/export-patch").json()["patch"]["patch_id"]
        (tmp_path / "bridge-patches" / patch_id).unlink()

        cleanup = api.post("/models/bridges/patches/cleanup", json={"older_than_days": 30, "include_missing": True})
        history = api.get("/models/bridges/patches")

    assert cleanup.status_code == 200
    assert cleanup.json()["cleanup"]["removed"] == 1
    assert history.status_code == 200
    assert history.json()["count"] == 0


def test_bridge_review_patch_export_missing_review_returns_404(tmp_path: Path) -> None:
    with client(tmp_path) as api:
        response = api.post("/models/bridges/reviews/missing/export-patch")

    assert response.status_code == 404
