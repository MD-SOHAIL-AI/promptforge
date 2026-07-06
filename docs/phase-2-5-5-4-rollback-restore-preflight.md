# Phase 2.5.5.4 - Rollback Restore Preflight

## Summary

ForgeX now has a read-only Rollback Restore Preflight API. It checks whether an existing rollback snapshot appears safe to restore later.

Restore remains disabled. Apply remains disabled.

## Endpoint

```text
POST /models/bridges/rollback-snapshots/{rollback_id}/restore-preflight
```

The route reads rollback metadata, backup files, and current workspace file hashes. It does not write, delete, restore, or apply files.

## Implemented Checks

Snapshot checks:

- rollback snapshot directory exists
- metadata exists and is readable
- backup files exist for modify/delete entries
- backup file hashes still match metadata

Workspace checks:

- active workspace root exists
- workspace hash matches rollback metadata when available
- all target paths are relative and contained
- ignored/build folders are rejected
- symlink escapes are rejected

Drift checks:

- modified restore targets must still match the snapshot-time hash
- delete restore targets may be missing or unchanged
- changed delete targets are conflicts
- created-file rollback targets are reported without deleting anything

## UI

Bridge Review and Patch History expose:

```text
Restore Preflight
```

Result examples:

```text
Restore preflight passed.
Snapshot appears restorable, but Restore is disabled in this build.
```

```text
Restore preflight blocked.
Backup file missing: src/main.cpp
```

The UI shows:

```text
Restore disabled
Apply disabled
```

No active Restore button exists.

## Audit Events

- `rollback_restore_preflight_started`
- `rollback_restore_preflight_completed`
- `rollback_restore_preflight_failed`

Audit metadata includes rollback ID, patch ID, review ID, conflict count, warning count, `can_restore`, and workspace hash. It does not include file contents or raw workspace paths.

## Safety Boundary

This phase does not implement real restore, patch apply, active workspace writes, file deletion, bridge routing, AGY routing, Codex execution, Claude execution, credential reads, token reads, cookie reads, or session-file reads.

`restore_enabled` remains `false` in every response.
