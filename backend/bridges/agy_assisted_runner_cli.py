"""Sanitized CLI adapter for the guarded AGY assisted scratch runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agy_assisted_runner import AGYAssistedRunner, SUPPORTED_TEMPLATE
from .agy_scratch_project_import import AGYScratchProjectImportService
from .audit_log import BridgeAuditLog
from .diff_service import BridgeDiffService
from .review_store import BridgeReviewStore


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--template", required=True)
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
    importer = AGYScratchProjectImportService(
        repository_root=repository_root,
        active_workspace_root=active_workspace,
        managed_sandbox_root=repository_root / ".promptforge" / "agy-import-sandboxes",
        review_service=reviews,
        status_path=state_dir / "agy-scratch-project-import-status.json",
    )
    runner = AGYAssistedRunner(
        repository_root=repository_root,
        active_workspace_root=active_workspace,
        import_service=importer,
        feature_enabled=True,
        status_path=state_dir / "agy-assisted-runner-status.json",
    )
    result = runner.run(args.template)
    print(json.dumps(result.to_safe_dict(), ensure_ascii=True, sort_keys=True))
    return 0 if result.classification == "AGY_ASSISTED_IMPORT_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
