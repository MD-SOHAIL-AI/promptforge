"""Retain only validated, host-owned checkpoint effects for review.

An executing stage is never a recovery source: a cancelled worker can still be
writing it. Checkpoints are immutable copies sealed at a committed tool boundary.
"""
from pathlib import Path
import shutil
import uuid

from .checkpoint_files import copy_checkpoint_tree


def retain_checkpoint_draft(service, *, provider_id, workspace_root, checkpoint, authorization_id=None):
    baseline = service.inspect_workspace(workspace_root)
    if baseline.revision != checkpoint["workspace_revision"]:
        raise ValueError("AGENT_RESUME_WORKSPACE_CHANGED")
    source = Path(checkpoint["stage_path"]).absolute()
    if source.parent != service.staging_root:
        raise ValueError("AGENT_RESUME_STAGE_UNOWNED")
    if service.inspect_workspace(source).revision != checkpoint["stage_revision"]:
        raise ValueError("AGENT_RESUME_STAGE_CHANGED")
    if authorization_id:
        from backend.changes.service import _stage_manifest_hash
        for draft in service.list(status="draft"):
            if draft.authorization_id != authorization_id or draft.authorization_source != "sealed_checkpoint_recovery":
                continue
            owned = Path(draft.staging_root).absolute()
            if draft.base_revision == baseline.revision and owned.parent == service.staging_root and not owned.is_symlink():
                if _stage_manifest_hash(owned, draft.changed_files) == draft.staged_manifest_hash:
                    return draft
            raise ValueError("AGENT_RECOVERY_DRAFT_CHANGED")
    sealed = service.create_stage("recovery-seal-" + uuid.uuid4().hex)
    compact = None
    try:
        copy_checkpoint_tree(source, sealed)
        snapshot = service.inspect_workspace(sealed)
        if snapshot.revision != checkpoint["stage_revision"]:
            raise ValueError("AGENT_RESUME_STAGE_CHANGED")
        paths = {path for path, item in snapshot.files.items()
                 if path not in baseline.files or item.hash != baseline.files[path].hash}
        deleted = set(checkpoint.get("deleted_paths", ()))
        if checkpoint.get("stage_mode") == "overlay" and (paths | deleted) - set(checkpoint.get("authorized_paths", ())):
            raise ValueError("AGENT_RECOVERY_UNAUTHORIZED_EFFECT")
        if not paths and not deleted:
            return None
        if len(paths | deleted) > 100 or sum((sealed / path).stat().st_size for path in paths) > 20 * 1024 * 1024:
            raise ValueError("AGENT_RECOVERY_DRAFT_LIMIT")
        compact = service.create_stage("recovery-draft-" + uuid.uuid4().hex)
        for path in sorted(paths):
            target = compact / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sealed / path, target)
        draft = service.create_from_stage(provider_id=provider_id, workspace_root=workspace_root,
            stage_root=compact, baseline=baseline, authorized_paths=paths | deleted,
            deleted_paths=deleted, draft=True, authorization_source="sealed_checkpoint_recovery", authorization_id=authorization_id)
        compact = None  # The ChangeSet now owns this stage.
        return draft
    finally:
        shutil.rmtree(sealed, ignore_errors=True)
        if compact is not None:
            shutil.rmtree(compact, ignore_errors=True)
