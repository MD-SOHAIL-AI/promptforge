# Patch Apply API Plan

## Purpose

This document defines future patch preflight, apply, rollback, and apply-history APIs. It is a planning artifact only. These routes must not be implemented as active workspace mutation in Phase 2.5.5.

## Planned Routes

```text
POST /models/bridges/patches/{patch_id}/preflight
POST /models/bridges/patches/{patch_id}/apply
POST /models/bridges/patches/{patch_id}/rollback
GET /models/bridges/patch-applies/{apply_id}
GET /models/bridges/patch-applies
```

Until the safe-apply implementation phase, apply and rollback routes should either not exist or return a disabled feature-gate response. They must not write to the active workspace.

## Implemented In Phase 2.5.5.1

`POST /models/bridges/patches/{patch_id}/preflight` is implemented as a read-only API.

It verifies patch integrity, review approval/expiry/provider state, workspace identity, path containment, ignored folder policy, drift conflicts, and supported patch headers. It returns a safety report and never writes active workspace files.

Apply and rollback routes remain unimplemented. `apply_enabled` remains `false`.

## Implemented In Phase 2.5.5.3

Rollback snapshot APIs are implemented for backup preparation only:

```text
POST /models/bridges/patches/{patch_id}/rollback-snapshot
GET /models/bridges/rollback-snapshots
GET /models/bridges/rollback-snapshots/{rollback_id}
DELETE /models/bridges/rollback-snapshots/{rollback_id}
POST /models/bridges/rollback-snapshots/cleanup
```

These APIs create and manage backup snapshots under ForgeX app state. They do not apply patches, restore files, or create apply records.

Create response includes `restore_enabled: false`.

## Implemented In Phase 2.5.5.4

Rollback restore preflight is implemented as a read-only route:

```text
POST /models/bridges/rollback-snapshots/{rollback_id}/restore-preflight
```

It verifies rollback metadata, backup file integrity, active workspace identity, path containment, ignored-folder policy, symlink escapes, and drift. It does not restore files and does not apply patches.

Responses always include `restore_enabled: false`.

## Implemented In Phase 2.5.6

Rollback restore execution is implemented behind `FORGEX_ENABLE_ROLLBACK_RESTORE=1`.

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

Restore requires a passing restore preflight and exact confirmation. The route restores only files listed in rollback metadata and records metadata-only restore results under managed ForgeX state.

Patch apply routes remain unimplemented. `apply_enabled` remains `false`.

## Preflight Request

```json
{
  "workspace_id": "...",
  "require_review_approval": true,
  "include_file_lists": true
}
```

The server should derive patch location, review ID, provider ID, workspace metadata, and changed-file lists from trusted ForgeX state, not from client-supplied paths.

## Preflight Response

```json
{
  "patch_id": "...",
  "can_apply": false,
  "integrity_status": "valid",
  "review_status": "approved",
  "conflicts": [],
  "warnings": [],
  "files_to_create": [],
  "files_to_modify": [],
  "files_to_delete": []
}
```

Additional future fields may include:

```json
{
  "review_id": "...",
  "provider_id": "antigravity_cli_bridge",
  "workspace_status": "matched",
  "dry_run_status": "passed",
  "requires_delete_confirmation": false,
  "preflight_id": "..."
}
```

## Conflict Shape

```json
{
  "path": "src/main.cpp",
  "type": "target_changed",
  "message": "File changed after review was created."
}
```

Allowed conflict types:

- `clean`
- `workspace_drift`
- `patch_modified`
- `path_unsafe`
- `target_missing`
- `target_changed`
- `delete_conflict`
- `binary_unsupported`
- `large_file_unsupported`
- `unknown`

## Apply Request

```json
{
  "preflight_id": "...",
  "confirm_active_workspace_write": true,
  "confirm_delete_files": false
}
```

Future apply must require a recent successful preflight. The server must re-run critical checks at apply time because workspace state can change between preflight and confirmation.

## Apply Response

```json
{
  "apply_id": "...",
  "status": "applied",
  "files_changed": 3,
  "rollback_available": true
}
```

Failure response should include conflict and rollback status:

```json
{
  "apply_id": "...",
  "status": "failed",
  "files_changed": 1,
  "rollback_available": true,
  "rollback_status": "completed",
  "conflicts": [
    {
      "path": "src/main.cpp",
      "type": "unknown",
      "message": "Apply failed after writing one file; rollback completed."
    }
  ]
}
```

## Rollback Request

```json
{
  "confirm_rollback": true
}
```

Rollback must target a known `apply_id`; it must not accept arbitrary paths or backup locations from the client.

## Rollback Response

```json
{
  "apply_id": "...",
  "status": "rolled_back",
  "files_restored": 3
}
```

Rollback may fail closed if the workspace changed after apply:

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

## Apply History

`GET /models/bridges/patch-applies` should return metadata-only records:

```json
{
  "items": [
    {
      "apply_id": "...",
      "patch_id": "...",
      "review_id": "...",
      "provider_id": "antigravity_cli_bridge",
      "status": "applied",
      "created_at": "...",
      "files_changed": 3,
      "rollback_available": true
    }
  ]
}
```

Apply history must not store full patch content, raw workspace paths, tokens, cookies, raw secrets, or full private file contents.

## Audit Events

Future routes should emit:

- `patch_preflight_started`
- `patch_preflight_completed`
- `patch_preflight_failed`
- `patch_apply_requested`
- `patch_apply_confirmed`
- `patch_apply_started`
- `patch_apply_completed`
- `patch_apply_failed`
- `patch_rollback_requested`
- `patch_rollback_completed`
- `patch_rollback_failed`

## Test Categories

Preflight tests:

- valid patch passes preflight
- modified patch fails integrity
- missing patch fails
- expired review fails
- unapproved review fails
- unsafe path fails
- workspace drift fails
- ignored folder write fails

Apply tests:

- clean patch applies
- active workspace snapshot created
- created files are created
- modified files are modified
- deleted files are deleted only after explicit confirmation
- partial apply rolls back or fails safely

Rollback tests:

- rollback restores modified files
- rollback restores deleted files
- rollback removes newly created files
- rollback fails safely if current workspace changed again
# Phase 2.5.7 Apply API Addendum

New routes:

```text
POST /models/bridges/patches/{patch_id}/apply
GET /models/bridges/patch-applies
GET /models/bridges/patch-applies/{apply_id}
```

Apply request:

```json
{
  "workspace_root": "active workspace root",
  "confirmation": "APPLY"
}
```

The apply route returns a `PatchApplyResult` with `apply_id`, `patch_id`, `review_id`, `rollback_id`, status, file counts, rollback availability, and per-file results. It does not return patch content or file content.

`GET /models/bridges/safety-status` now reports patch apply and rollback restore flags:

```json
{
  "patch_apply_enabled": false,
  "patch_apply_feature_flag": false,
  "rollback_restore_enabled": false,
  "rollback_restore_feature_flag": false,
  "apply_requires_restore": true
}
```

No generic bridge apply route is added.
