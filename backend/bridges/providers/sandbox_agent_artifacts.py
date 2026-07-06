"""Shared review artifact creation for managed sandbox agent providers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..diff_service import BridgeDiffService
from ..generic.models import BridgeArtifactReference, BridgeArtifactType
from ..generic.validation import utc_now
from ..review_models import BridgeWorkspaceSnapshot


def create_review_artifact(
    *,
    provider_id: str,
    run_id: str,
    sandbox_root: Path,
    managed_root: Path,
    baseline: BridgeWorkspaceSnapshot,
    review_service: BridgeDiffService,
    artifact_source: str,
) -> BridgeArtifactReference | None:
    if not review_service.diff_snapshot(baseline, sandbox_root):
        return None
    review = review_service.create_review(
        provider_id=provider_id,
        workspace_root=sandbox_root,
        snapshot=baseline,
        artifact_source=artifact_source,
        artifact_type="workspace_diff",
    )
    relative = sandbox_root.resolve().relative_to(managed_root.resolve()).as_posix()
    digest = hashlib.sha256(review.review_id.encode("utf-8")).hexdigest()
    return BridgeArtifactReference(
        artifact_id=f"artifact-{run_id}",
        run_id=run_id,
        artifact_type=BridgeArtifactType.REVIEW,
        created_at=utc_now(),
        content_hash=digest,
        size_bytes=0,
        storage_reference=relative,
        review_required=True,
        review_id=review.review_id,
        pipeline_artifact_id=review.review_id,
    )
