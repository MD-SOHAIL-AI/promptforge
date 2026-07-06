# Phase 2.5.6 - Feature-Flagged Rollback Restore

## Summary

ForgeX now supports rollback restore execution behind:

```text
FORGEX_ENABLE_ROLLBACK_RESTORE=1
```

The default remains disabled. Patch apply remains unimplemented and disabled.

## Feature Flag Behavior

When the flag is missing, restore returns:

```text
Rollback restore is disabled. Enable FORGEX_ENABLE_ROLLBACK_RESTORE=1 for development testing.
```

The Bridge Safety status reports:

```json
{
  "rollback_snapshots_enabled": true,
  "restore_preflight_enabled": true,
  "restore_enabled": false,
  "restore_feature_flag": false,
  "apply_enabled": false
}
```

When enabled, `restore_enabled` and `restore_feature_flag` become `true`. `apply_enabled` remains `false`.

## Restore API

```text
POST /models/bridges/rollback-snapshots/{rollback_id}/restore
GET /models/bridges/rollback-restores
GET /models/bridges/rollback-restores/{restore_id}
```

Restore requires exact confirmation:

```json
{
  "workspace_root": "active workspace root",
  "confirmation": "RESTORE"
}
```

Wrong confirmation fails before any workspace write.

## Safety Guarantees

Restore:

- requires the feature flag
- requires exact `RESTORE` confirmation
- internally runs restore preflight
- refuses to write when restore preflight fails
- restores only files listed in rollback metadata
- rejects unsafe paths, ignored folders, and symlink escapes
- verifies backup hashes before writing
- verifies restored file hashes after writing
- uses temp-file plus replace for file restoration
- removes created-file rollback targets only as single files
- does not delete directories recursively
- writes restore result metadata under managed ForgeX app state

Restore does not:

- apply patches
- run AGY, Codex, or Claude
- run bridge prompts
- read credentials, cookies, or sessions
- log file contents
- log raw workspace paths

## Result Metadata

Restore results are stored under:

```text
.promptforge/state/rollback-restores/<restore_id>/metadata.json
```

Each result records restore ID, rollback ID, patch ID, review ID, provider ID, status, counts, timestamps, and per-file results.

## UI Behavior

Bridge Review and Patch History show Restore disabled unless the backend safety status reports the restore feature flag enabled and restore preflight passed.

When enabled, the UI requires typed confirmation:

```text
RESTORE
```

After restore, the UI reports files restored, files removed, files failed, and that Apply remains disabled.

## Audit

Implemented audit events:

- `rollback_restore_requested`
- `rollback_restore_confirmed`
- `rollback_restore_started`
- `rollback_restore_completed`
- `rollback_restore_failed`
- `rollback_restore_blocked`

Audit metadata contains IDs, counts, and workspace hashes only.

## Remaining Limitations

- Patch apply is still not implemented.
- Restore preflight cannot yet compare against post-apply hashes because apply records do not exist.
- Restore is intended for development testing until patch apply creates richer apply records.
