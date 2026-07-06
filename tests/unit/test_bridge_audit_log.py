from __future__ import annotations

from pathlib import Path

from backend.bridges.audit_log import BridgeAuditLog, hash_workspace_root
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.review_models import BridgeAuditEntry
from backend.bridges.review_store import BridgeReviewStore


def test_audit_log_does_not_store_workspace_path_or_secrets(tmp_path: Path) -> None:
    log_path = tmp_path / "audit.jsonl"
    workspace = str(tmp_path / "secret-workspace")
    log = BridgeAuditLog(log_path)

    log.record(
        BridgeAuditEntry(
            event="bridge_review_created",
            provider_id="codex_bridge",
            workspace_root_hash=hash_workspace_root(workspace),
            changed_file_count=1,
            approved=False,
            review_id="review-1",
        )
    )

    raw = log_path.read_text(encoding="utf-8")
    assert workspace not in raw
    assert "secret-workspace" not in raw
    assert "token" not in raw.casefold()
    assert log.list_entries()[0]["workspace_root_hash"] == hash_workspace_root(workspace)


def test_audit_log_records_review_lifecycle(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "main.cpp").write_text("old\n", encoding="utf-8")
    log = BridgeAuditLog(tmp_path / "audit.jsonl")
    service = BridgeDiffService(
        audit_log=log,
        store=BridgeReviewStore(
            snapshots_path=tmp_path / "snapshots.jsonl",
            reviews_path=tmp_path / "reviews.jsonl",
        ),
    )

    snapshot = service.snapshot_workspace(workspace)
    (workspace / "src" / "main.cpp").write_text("new\n", encoding="utf-8")
    review = service.create_review(provider_id="codex_bridge", workspace_root=workspace, snapshot=snapshot)
    service.approve_review(review.review_id)

    events = [entry["event"] for entry in log.list_entries()]

    assert "snapshot_created" in events
    assert "diff_generated" in events
    assert "review_created" in events
    assert "review_approved" in events
