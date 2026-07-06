"""Persist a Bridge Review only for an exact Codex OAuth sandbox smoke pass."""

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


MARKER_NAME = "README_FORGEX_CODEX_OAUTH_SMOKE_SANDBOX.txt"
MARKER_CONTENT = "This is a disposable ForgeX Codex OAuth smoke sandbox.\nDo not use as active workspace.\n"
SMOKE_NAME = "CODEX_OAUTH_BRIDGE_SMOKE.txt"
SMOKE_CONTENT = "ForgeX Codex OAuth bridge smoke completed."
PROMPT_VARIANT = "strict_single_line_v2"


def _normalized_smoke_content(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    text = raw.decode("utf-8")
    return text.replace("\r\n", "\n").rstrip("\n")


def _normalization_applied(raw: bytes) -> str:
    return "eof_newline_or_bom_or_crlf_only" if raw.startswith(b"\xef\xbb\xbf") or b"\r\n" in raw or raw.endswith(b"\n") else "none"


def create_codex_oauth_smoke_review(
    sandbox_root: Path,
    review_service: BridgeDiffService,
) -> str:
    root = sandbox_root.resolve(strict=True)
    if sandbox_root.is_symlink() or not root.is_dir():
        raise ValueError("codex_oauth_review_sandbox_invalid")
    entries = sorted(item.name for item in root.iterdir() if item.name != ".git")
    if entries != [SMOKE_NAME, MARKER_NAME]:
        raise ValueError("codex_oauth_review_change_set_invalid")
    marker, smoke = root / MARKER_NAME, root / SMOKE_NAME
    if any(item.is_symlink() or not item.is_file() for item in (marker, smoke)):
        raise ValueError("codex_oauth_review_file_invalid")
    if marker.read_text(encoding="utf-8") != MARKER_CONTENT:
        raise ValueError("codex_oauth_review_marker_invalid")
    smoke_bytes = smoke.read_bytes()
    if _normalized_smoke_content(smoke_bytes) != SMOKE_CONTENT:
        raise ValueError("codex_oauth_review_content_invalid")

    stat = marker.stat()
    baseline = BridgeWorkspaceSnapshot(
        workspace_root=str(root),
        workspace_root_hash=hash_workspace_root(str(root)),
        files={
            MARKER_NAME: BridgeSnapshotFile(
                path=MARKER_NAME,
                hash=hashlib.sha256(marker.read_bytes()).hexdigest(),
                size=stat.st_size,
                mtime=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat().replace("+00:00", "Z"),
                content=MARKER_CONTENT,
            )
        },
    )
    review = review_service.create_review(
        provider_id="codex_cli_oauth_bridge",
        workspace_root=root,
        snapshot=baseline,
        artifact_source="codex_exec",
        artifact_type="codex_oauth_bridge_smoke_diff",
        artifact_metadata={
            "provider_kind": "local_cli", "transport": "codex_exec",
            "auth_mode": "official_codex_cli_oauth", "execution_mode": "sandboxed_oauth_smoke",
            "workspace_mode": "external_disposable_sandbox", "classification": "CODEX_OAUTH_SMOKE_PASS",
            "instruction_type": "codex_oauth_bridge_smoke", "expected_filename": SMOKE_NAME,
            "expected_content_hash": hashlib.sha256(SMOKE_CONTENT.encode()).hexdigest(),
            "content_validation_mode": "normalized_single_line", "normalization_applied": _normalization_applied(smoke_bytes),
            "prompt_variant": PROMPT_VARIANT,
            "sandbox_kind": "external_disposable_oauth_smoke", "created_file_count": 1,
            "modified_file_count": 0, "deleted_file_count": 0, "active_workspace_unchanged": True,
            "auto_apply": False, "auto_build": False, "auto_flash": False,
            "production_routing_enabled": False, "qa_only": True,
        },
    )
    if len(review.changed_files) != 1 or review.changed_files[0].path != SMOKE_NAME or review.changed_files[0].change_type != "created":
        raise ValueError("codex_oauth_review_diff_invalid")
    return review.review_id


def _service(state_dir: Path) -> BridgeDiffService:
    state_dir.mkdir(parents=True, exist_ok=True)
    return BridgeDiffService(
        audit_log=BridgeAuditLog(state_dir / "bridge-review-audit.jsonl"),
        store=BridgeReviewStore(
            snapshots_path=state_dir / "bridge-snapshots.jsonl",
            reviews_path=state_dir / "bridge-reviews.jsonl",
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--sandbox", required=True)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()
    try:
        review_id = create_codex_oauth_smoke_review(Path(args.sandbox), _service(Path(args.state_dir)))
    except (OSError, ValueError):
        print(json.dumps({"review_created": False}, sort_keys=True))
        return 1
    print(json.dumps({"review_created": True, "review_id": review_id}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
