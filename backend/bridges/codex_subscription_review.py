"""Create a persistent review for an exact Codex subscription bridge smoke diff."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .audit_log import BridgeAuditLog, hash_workspace_root
from .diff_service import BridgeDiffService
from .review_models import BridgeSnapshotFile, BridgeWorkspaceSnapshot
from .review_store import BridgeReviewStore


MARKER_NAME = "README_FORGEX_CODEX_SANDBOX.txt"
MARKER_CONTENT = (
    "This is a disposable ForgeX Codex subscription bridge sandbox.\n"
    "Codex may only create CODEX_GENERIC_SMOKE.txt for this validation.\n"
)
SMOKE_NAME = "CODEX_GENERIC_SMOKE.txt"
SMOKE_CONTENT = "ForgeX Codex subscription bridge smoke completed."


def create_codex_subscription_review(sandbox_root: Path, state_dir: Path) -> str:
    root = sandbox_root.resolve(strict=True)
    if sandbox_root.is_symlink() or not root.is_dir():
        raise ValueError("codex_review_sandbox_invalid")
    entries = sorted(item.name for item in root.iterdir())
    if entries != [SMOKE_NAME, MARKER_NAME]:
        raise ValueError("codex_review_change_set_invalid")
    marker = root / MARKER_NAME
    smoke = root / SMOKE_NAME
    if marker.is_symlink() or smoke.is_symlink() or not marker.is_file() or not smoke.is_file():
        raise ValueError("codex_review_file_invalid")
    if marker.read_text(encoding="utf-8") != MARKER_CONTENT:
        raise ValueError("codex_review_marker_invalid")
    if smoke.read_text(encoding="utf-8") != SMOKE_CONTENT:
        raise ValueError("codex_review_content_invalid")

    marker_stat = marker.stat()
    marker_hash = hashlib.sha256(marker.read_bytes()).hexdigest()
    baseline = BridgeWorkspaceSnapshot(
        workspace_root=str(root),
        workspace_root_hash=hash_workspace_root(str(root)),
        files={
            MARKER_NAME: BridgeSnapshotFile(
                path=MARKER_NAME,
                hash=marker_hash,
                size=marker_stat.st_size,
                mtime=datetime.fromtimestamp(marker_stat.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
                content=MARKER_CONTENT,
            )
        },
    )
    state_dir.mkdir(parents=True, exist_ok=True)
    service = BridgeDiffService(
        audit_log=BridgeAuditLog(state_dir / "bridge-review-audit.jsonl"),
        store=BridgeReviewStore(
            snapshots_path=state_dir / "bridge-snapshots.jsonl",
            reviews_path=state_dir / "bridge-reviews.jsonl",
        ),
    )
    review = service.create_review(
        provider_id="codex_cli_subscription",
        workspace_root=root,
        snapshot=baseline,
        artifact_source="codex_exec",
        artifact_type="codex_subscription_bridge_diff",
        artifact_metadata={
            "transport": "codex_exec",
            "auth_mode": "official_cli_auth",
            "execution_mode": "cli_auth_bridge",
            "workspace_mode": "external_managed_sandbox",
            "classification": "CODEX_SUBSCRIPTION_BRIDGE_PASS",
            "instruction_type": "codex_subscription_bridge_smoke",
            "expected_filename": SMOKE_NAME,
            "expected_content_hash": hashlib.sha256(SMOKE_CONTENT.encode()).hexdigest(),
            "created_file_count": 1,
            "modified_file_count": 0,
            "deleted_file_count": 0,
            "active_workspace_unchanged": True,
            "auto_apply": False,
            "auto_build": False,
            "auto_flash": False,
        },
    )
    if len(review.changed_files) != 1 or review.changed_files[0].path != SMOKE_NAME or review.changed_files[0].change_type != "created":
        raise ValueError("codex_review_diff_invalid")
    return review.review_id


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--sandbox", required=True)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()
    try:
        create_codex_subscription_review(Path(args.sandbox), Path(args.state_dir))
    except (OSError, ValueError):
        print(json.dumps({"review_created": False}, sort_keys=True))
        return 1
    print(json.dumps({"review_created": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
