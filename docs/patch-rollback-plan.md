# Patch Rollback Plan

## Purpose

This document defines the future rollback storage and behavior for ForgeX patch apply. It is a planning artifact only. Rollback is not implemented in Phase 2.5.5.

Phase 2.5.5.3 implements rollback snapshot creation only. Restore execution remains unimplemented and disabled.

Phase 2.5.5.4 implements restore preflight only. Restore execution remains unimplemented and disabled.

Phase 2.5.5.5 adds end-to-end QA for rollback snapshot and restore-preflight safety boundaries. It does not implement restore execution.

Phase 2.5.6 implements rollback restore execution behind `FORGEX_ENABLE_ROLLBACK_RESTORE=1`. Patch apply remains unimplemented and disabled.

## Storage Location

Before any future active-workspace apply, ForgeX must create rollback data under:

```text
.promptforge/state/patch-rollback/<apply_id>/
```

Rollback data must be managed by ForgeX and referenced by `apply_id`. The client must not provide arbitrary backup paths.

Current Phase 2.5.5.3 snapshots use `rollback_id` because no apply record exists yet:

```text
.promptforge/state/patch-rollback/<rollback_id>/
```

## Metadata

Rollback metadata should be stored as:

```json
{
  "apply_id": "...",
  "patch_id": "...",
  "review_id": "...",
  "created_at": "...",
  "workspace_root_hash": "...",
  "files_backup": [
    {
      "path": "src/main.cpp",
      "previous_hash": "...",
      "backup_path": "..."
    }
  ]
}
```

All paths inside metadata must be relative to the active workspace root or relative to the rollback directory.

## Backup Requirements

For each patched file, ForgeX must record enough information to reverse the apply:

- modified file: backup previous content and previous hash
- deleted file: backup previous content and previous hash
- created file: record that the file did not previously exist
- directory creation: record newly created parent directories if needed for cleanup

Rollback data must be created before any active workspace write begins. If backup creation fails, apply must not start.

Implemented snapshot creation now:

- requires passing patch preflight
- copies current modified/delete targets into `files/<relative-path>`
- records create targets without backup files when they do not exist
- rejects unsafe paths, ignored paths, symlink escapes, failed preflight, modified patches, and unapproved reviews
- keeps `restore_enabled: false`
- does not apply patches
- does not restore files

## Secret Handling

Rollback must not store secrets unnecessarily. Because rollback may need to preserve previous file contents, it can contain sensitive project files only when those files are directly touched by the patch.

Rules:

- do not back up unrelated ignored folders
- do not back up token, cookie, or credential stores unless the patch directly touches an allowed project file and all path safety gates have passed
- do not log rollback file contents
- do not store raw workspace absolute paths
- keep rollback metadata local

## Restore Behavior

Rollback must support:

- restoring modified files
- restoring deleted files
- removing newly created files
- restoring file hashes to the recorded previous state where possible
- reporting conflicts if the current workspace changed after apply

Before rollback writes, ForgeX should verify that current files still match the post-apply state recorded by the apply operation. If they do not match, rollback must fail safely and report `target_changed` conflicts.

Implemented restore preflight now checks rollback metadata, backup file integrity, active workspace identity, path containment, ignored folders, symlink escapes, and current workspace drift. It returns `can_restore` as a read-only assessment and always returns `restore_enabled: false`.

End-to-end QA now verifies that rollback snapshot creation backs up only touched files into managed ForgeX state, restore preflight detects missing backups, rollback cleanup removes old rollback snapshots only, and neither snapshot nor restore preflight modifies active workspace files.

Implemented feature-flagged restore now:

- requires `FORGEX_ENABLE_ROLLBACK_RESTORE=1`
- requires exact confirmation `RESTORE`
- reruns restore preflight internally
- restores only files listed in rollback metadata
- verifies backup integrity before writing
- verifies restored hashes after writing
- removes created-file rollback targets as single files only
- never deletes directories recursively
- records restore result metadata under `rollback-restores/<restore_id>/metadata.json`
- keeps `apply_enabled: false`

## Partial Apply Handling

If future apply fails after writing some files, ForgeX must either:

- complete rollback automatically using the pre-apply backups, or
- stop with a failed-safe status that identifies written files and preserves rollback availability

The preferred behavior is automatic rollback on partial apply failure, followed by a clear `patch_apply_failed` audit event with rollback status.

## Rollback Response

Successful rollback:

```json
{
  "apply_id": "...",
  "status": "rolled_back",
  "files_restored": 3
}
```

Blocked rollback:

```json
{
  "apply_id": "...",
  "status": "blocked",
  "files_restored": 0,
  "conflicts": [
    {
      "path": "src/main.cpp",
      "type": "target_changed",
      "message": "File changed after the patch was applied."
    }
  ]
}
```

## Rollback Audit

Future rollback events:

- `patch_rollback_requested`
- `patch_rollback_completed`
- `patch_rollback_failed`

Audit metadata may include apply ID, patch ID, review ID, provider ID, file counts, rollback status, and workspace hash.

Audit must not include full file contents, full patch content, tokens, cookies, raw secrets, or full workspace paths.

Phase 2.5.5.3 snapshot audit events:

- `rollback_snapshot_requested`
- `rollback_snapshot_created`
- `rollback_snapshot_failed`
- `rollback_snapshot_deleted`
- `rollback_snapshot_cleanup_started`
- `rollback_snapshot_cleanup_completed`

Phase 2.5.5.4 restore preflight audit events:

- `rollback_restore_preflight_started`
- `rollback_restore_preflight_completed`
- `rollback_restore_preflight_failed`

Phase 2.5.6 restore execution audit events:

- `rollback_restore_requested`
- `rollback_restore_confirmed`
- `rollback_restore_started`
- `rollback_restore_completed`
- `rollback_restore_failed`
- `rollback_restore_blocked`

## Tests

Future rollback tests:

1. rollback restores modified files
2. rollback restores deleted files
3. rollback removes newly created files
4. rollback fails safely if current workspace changed again
5. rollback metadata uses relative paths only
6. rollback is unavailable when backup metadata is missing or corrupted

Current QA coverage:

- rollback snapshot creation after passing patch preflight
- restore preflight after snapshot creation
- missing backup restore-preflight conflict
- rollback cleanup isolation from patch storage
- no active workspace writes during snapshot and restore-preflight safety operations
- feature-flagged restore disabled by default
- exact confirmation requirement
- modify/delete/create restore operations
- result metadata persistence
- restore audit lifecycle
# Phase 2.5.7 Rollback Apply Addendum

Patch apply creates a fresh rollback snapshot immediately before writing. The snapshot is linked to the apply ID and marked restore-enabled when rollback restore support is enabled.

If any apply write fails after mutation, ForgeX attempts automatic rollback from this fresh snapshot. A successful automatic rollback records `failed_rolled_back`; rollback failure records `failed_rollback_failed`.
# Phase 2.5.7.1 Applied Snapshot Restore

Rollback snapshots linked to apply records store post-apply hashes for created and modified files. Restore preflight validates those hashes before allowing manual rollback restore. Delete applies are expected to leave the target absent before restore.

Manual restore remains explicit: restore preflight must pass and the user must type `RESTORE`.
