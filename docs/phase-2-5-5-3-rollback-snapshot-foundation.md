# Phase 2.5.5.3 - Rollback Snapshot Foundation

## Summary

ForgeX now supports creating rollback snapshots for exported bridge patches after read-only preflight passes.

The snapshot system copies current versions of touched active-workspace files into ForgeX app state and records relative-path metadata. It does not apply patches, restore files, or modify active workspace files.

Apply remains disabled. Restore remains disabled.

## What Gets Backed Up

Create file:

- if the target does not exist, records `existed_before=false`
- no backup file is copied

Modify file:

- backs up the current target file
- records previous hash, backup path, size, and mtime

Delete file:

- backs up the current target file before a future delete
- records previous hash, backup path, size, and mtime

Ignored/build folders, unsafe paths, and symlink escapes are rejected before backup.

## Storage

Rollback snapshots are stored under:

```text
.promptforge/state/patch-rollback/<rollback_id>/
```

Metadata:

```text
.promptforge/state/patch-rollback/<rollback_id>/metadata.json
```

Backups:

```text
.promptforge/state/patch-rollback/<rollback_id>/files/src/main.cpp
```

Metadata stores relative paths and workspace hashes. It does not store raw workspace roots.

## API Behavior

Create:

```text
POST /models/bridges/patches/{patch_id}/rollback-snapshot
```

List:

```text
GET /models/bridges/rollback-snapshots
```

Inspect:

```text
GET /models/bridges/rollback-snapshots/{rollback_id}
```

Delete:

```text
DELETE /models/bridges/rollback-snapshots/{rollback_id}
```

Cleanup:

```text
POST /models/bridges/rollback-snapshots/cleanup
```

Delete and cleanup remove only ForgeX-managed rollback snapshot directories. They do not modify the active workspace.

## UI

Bridge Review and Patch History can create a rollback snapshot after preflight passes.

The button is enabled only when:

- preflight result has `can_apply=true`
- `apply_enabled=false`
- an active workspace root exists

After creation, the UI shows:

```text
Rollback snapshot:
- files backed up: 2
- restore: disabled
```

No Restore button is shown. Apply remains disabled.

## Audit

ForgeX records:

- `rollback_snapshot_requested`
- `rollback_snapshot_created`
- `rollback_snapshot_failed`
- `rollback_snapshot_deleted`
- `rollback_snapshot_cleanup_started`
- `rollback_snapshot_cleanup_completed`

Audit metadata includes IDs, file counts, and workspace hash. It does not include file contents or raw workspace paths.

## Safety Boundary

This phase does not implement patch apply or restore. It only prepares rollback data under ForgeX state for a later feature-gated apply flow.

No bridge prompt execution, AGY routing, Codex execution, Claude execution, credential reads, token reads, cookie reads, or session-file reads were added.
