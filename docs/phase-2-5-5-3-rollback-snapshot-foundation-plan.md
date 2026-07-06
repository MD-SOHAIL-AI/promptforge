# Phase 2.5.5.3 - Rollback Snapshot Foundation Plan

## Goal

Implement rollback snapshot creation for future safe patch apply without implementing patch apply or restore.

This phase prepares backup data in ForgeX app state after a patch passes read-only preflight. It does not write to active workspace files.

## Scope

- Add rollback snapshot metadata models.
- Add rollback snapshot service.
- Create rollback snapshots only after passing preflight.
- Back up current versions of touched active-workspace files into ForgeX app state.
- Record create targets that do not exist without copying a file.
- List, inspect, delete, and clean up rollback snapshot records.
- Surface snapshot creation in the preflight UI.
- Keep Restore disabled.
- Keep Apply disabled.

## Non-Goals

- No patch apply.
- No rollback restore.
- No active workspace writes.
- No bridge routing.
- No AGY/Codex/Claude execution changes.
- No credential, token, cookie, OAuth, or session-file reads.

## Storage

Rollback snapshots are stored under ForgeX state:

```text
.promptforge/state/patch-rollback/<rollback_id>/
```

Metadata:

```text
.promptforge/state/patch-rollback/<rollback_id>/metadata.json
```

Backups:

```text
.promptforge/state/patch-rollback/<rollback_id>/files/<relative-path>
```

Metadata uses relative paths and workspace root hashes only.

## API

```text
POST /models/bridges/patches/{patch_id}/rollback-snapshot
GET /models/bridges/rollback-snapshots
GET /models/bridges/rollback-snapshots/{rollback_id}
DELETE /models/bridges/rollback-snapshots/{rollback_id}
POST /models/bridges/rollback-snapshots/cleanup
```

Create request:

```json
{
  "workspace_root": "active workspace root"
}
```

Create response:

```json
{
  "rollback_id": "...",
  "patch_id": "...",
  "review_id": "...",
  "status": "created",
  "files_backed_up": 2,
  "total_bytes": 12345,
  "restore_enabled": false
}
```

## Safety Rules

Snapshot creation must reject:

- failed preflight
- modified or missing patch
- missing, unapproved, expired, or mismatched review
- unsafe paths
- ignored/build folder paths
- symlink escapes
- missing active workspace
- workspace drift

Snapshot creation must not modify active workspace files.

## Audit Events

- `rollback_snapshot_requested`
- `rollback_snapshot_created`
- `rollback_snapshot_failed`
- `rollback_snapshot_deleted`
- `rollback_snapshot_cleanup_started`
- `rollback_snapshot_cleanup_completed`

Audit metadata must not include file contents or raw workspace paths.

## Future Work

Future phases still need restore execution, apply records, feature-gated apply, post-apply verification, partial-apply rollback behavior, and end-to-end QA.
