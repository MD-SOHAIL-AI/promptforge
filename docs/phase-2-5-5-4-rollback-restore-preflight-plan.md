# Phase 2.5.5.4 - Rollback Restore Preflight Plan

## Goal

Add a read-only API that checks whether a rollback snapshot can be safely restored later.

This phase does not restore files, apply patches, delete files, or modify the active workspace.

## Scope

- Add restore preflight models.
- Add restore preflight service.
- Verify rollback snapshot metadata.
- Verify backup files exist and still match metadata.
- Check active workspace identity.
- Check path containment.
- Reject ignored/build folders and symlink escapes.
- Detect current workspace drift.
- Add `POST /models/bridges/rollback-snapshots/{rollback_id}/restore-preflight`.
- Add minimal UI action and compact result display.
- Keep Restore disabled.
- Keep Apply disabled.

## Non-Goals

- No patch apply.
- No real restore.
- No active workspace writes.
- No file deletion.
- No bridge routing.
- No AGY/Codex/Claude execution changes.
- No credential, token, cookie, OAuth, or session-file reads.

## API

```text
POST /models/bridges/rollback-snapshots/{rollback_id}/restore-preflight
```

Request:

```json
{
  "workspace_root": "active workspace root"
}
```

Response:

```json
{
  "rollback_id": "...",
  "patch_id": "...",
  "review_id": "...",
  "provider_id": "antigravity_cli_bridge",
  "can_restore": false,
  "restore_enabled": false,
  "workspace_status": "ok",
  "snapshot_status": "valid",
  "conflicts": [],
  "warnings": [],
  "files_to_restore": [],
  "files_to_remove": [],
  "checked_at": "..."
}
```

`restore_enabled` must remain `false` even when `can_restore=true`.

## Conflict Types

- `snapshot_missing`
- `metadata_missing`
- `backup_missing`
- `backup_modified`
- `workspace_missing`
- `workspace_hash_mismatch`
- `path_unsafe`
- `ignored_path`
- `symlink_escape`
- `current_file_changed`
- `current_file_missing`
- `created_file_changed`
- `delete_target_changed`
- `restore_unsupported`
- `unknown`

## Audit

Record:

- `rollback_restore_preflight_started`
- `rollback_restore_preflight_completed`
- `rollback_restore_preflight_failed`

Audit metadata must not include file contents or raw workspace paths.

## Future Work

A later restore phase must add explicit user confirmation, restore execution, post-restore verification, and tests. Apply remains a separate future feature-gated phase.
