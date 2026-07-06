"""Sanitized command-line adapter for manual AGY scratch project imports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agy_scratch_project_import import (
    AGYScratchImportClassification,
    AGYScratchImportError,
    AGYScratchImportResult,
    AGYScratchProjectImportService,
)
from .audit_log import BridgeAuditLog
from .diff_service import BridgeDiffService
from .review_store import BridgeReviewStore


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[2]
    state_dir = repository_root / ".promptforge" / "state"
    active_workspace = repository_root / "workspace"
    active_workspace.mkdir(parents=True, exist_ok=True)
    reviews = BridgeDiffService(
        audit_log=BridgeAuditLog(state_dir / "bridge-review-audit.jsonl"),
        store=BridgeReviewStore(
            snapshots_path=state_dir / "bridge-snapshots.jsonl",
            reviews_path=state_dir / "bridge-reviews.jsonl",
        ),
    )
    service = AGYScratchProjectImportService(
        repository_root=repository_root,
        active_workspace_root=active_workspace,
        managed_sandbox_root=repository_root / ".promptforge" / "agy-import-sandboxes",
        review_service=reviews,
        status_path=state_dir / "agy-scratch-project-import-status.json",
    )
    try:
        result = service.import_project(args.source)
    except AGYScratchImportError as exc:
        result = exc.to_result()
    except Exception:
        result = AGYScratchImportResult(
            classification=AGYScratchImportClassification.UNKNOWN_SAFE_FAILURE,
            source_inside_scratch_root=False,
            source_name="unknown",
            file_count=0,
            total_bytes=0,
            blocked_files_count=0,
            managed_sandbox_created=False,
            created_file_count=0,
            modified_file_count=0,
            deleted_file_count=0,
            review_created=False,
            review_id=None,
            active_workspace_unchanged=True,
        )
    print(json.dumps(result.to_safe_dict(), ensure_ascii=True, sort_keys=True))
    return 0 if result.classification == AGYScratchImportClassification.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
