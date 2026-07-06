# Phase 2.5.6 - Feature-Flagged Rollback Restore Plan

## Objective

Implement the first real rollback restore write path while keeping patch apply disabled.

Restore is available only when:

```text
FORGEX_ENABLE_ROLLBACK_RESTORE=1
```

Without the flag, the restore API fails closed and the UI shows Restore disabled.

## Restore Flow

```text
Rollback snapshot exists
User runs Restore Preflight
Restore preflight passes
User confirms with RESTORE
ForgeX restores only snapshot-listed files
ForgeX verifies restored hashes
ForgeX writes restore result metadata
ForgeX records audit events
Apply remains disabled
```

## Restore Gates

- feature flag must be enabled
- confirmation must equal `RESTORE`
- restore preflight must return `can_restore=true`
- active workspace root must exist
- active workspace hash must match rollback snapshot metadata
- each path must be relative and contained
- ignored folders must be rejected
- symlink escapes must be rejected
- backup files must exist and match metadata hashes

## Restore Operations

### Modify

Replace the current file with the rollback backup using a temp file in the same target directory and atomic replace where the platform supports it.

### Delete

Restore a previously deleted file from rollback backup and verify the restored hash.

### Create

Remove a file that was created by a future patch apply only when rollback metadata says:

```text
change_type=create
existed_before=false
```

This phase never deletes directories recursively.

## API Plan

Implemented routes:

```text
GET /models/bridges/safety-status
POST /models/bridges/rollback-snapshots/{rollback_id}/restore
GET /models/bridges/rollback-restores
GET /models/bridges/rollback-restores/{restore_id}
```

Restore request:

```json
{
  "workspace_root": "active workspace root",
  "confirmation": "RESTORE"
}
```

## Audit Events

- `rollback_restore_requested`
- `rollback_restore_confirmed`
- `rollback_restore_started`
- `rollback_restore_completed`
- `rollback_restore_failed`
- `rollback_restore_blocked`

Audit logs must not include file contents, patch contents, tokens, cookies, credentials, or raw workspace paths.

## Out Of Scope

- patch apply
- bridge routing
- AGY execution changes
- Codex execution
- Claude execution
- restore without preflight
- restore without explicit confirmation
