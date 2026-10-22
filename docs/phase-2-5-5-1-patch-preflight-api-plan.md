# Phase 2.5.5.1 - Patch Preflight API Plan

## Goal

Implement a read-only API that answers whether an exported bridge patch appears safe to apply later.

This phase does not implement apply, rollback, bridge routing, AGY routing, Codex execution, Claude execution, or credential/session inspection.

## Scope

- Add read-only preflight models.
- Add a patch preflight service.
- Add `POST /models/bridges/patches/{patch_id}/preflight`.
- Verify patch SHA-256 integrity without rewriting patch metadata.
- Check review state without bypassing approval.
- Check workspace identity when a workspace root is available.
- Reject unsafe or ignored paths.
- Detect workspace drift using review baseline hashes.
- Parse supported `diff --git a/<path> b/<path>` headers.
- Return file create/modify/delete lists, conflicts, and warnings.
- Record preflight audit events without patch content or raw workspace paths.
- Add minimal UI integration with Apply remaining disabled.

## Non-Goals

- No patch apply.
- No active workspace writes.
- No rollback/apply records.
- No bridge prompt execution.
- No AGY/Codex/Claude execution changes.
- No credential, token, cookie, OAuth, or session-file reads.
- No destructive cleanup.

## API

```text
POST /models/bridges/patches/{patch_id}/preflight
```

Request:

```json
{
  "workspace_root": "optional active workspace root"
}
```

Response:

```json
{
  "patch_id": "...",
  "review_id": "...",
  "provider_id": "antigravity_cli_bridge",
  "can_apply": false,
  "apply_enabled": false,
  "integrity_status": "valid",
  "review_status": "approved",
  "workspace_status": "ok",
  "conflicts": [],
  "warnings": [],
  "files_to_create": [],
  "files_to_modify": [],
  "files_to_delete": [],
  "checked_at": "..."
}
```

`apply_enabled` remains `false` even when `can_apply` is `true`.

## Conflict Types

- `workspace_drift`
- `patch_modified`
- `patch_missing`
- `review_missing`
- `review_not_approved`
- `review_expired`
- `provider_mismatch`
- `path_unsafe`
- `target_missing`
- `target_changed`
- `delete_conflict`
- `binary_unsupported`
- `large_file_unsupported`
- `ignored_path`
- `parse_error`
- `unknown`

## Tests

Required coverage:

- valid approved patch passes with `apply_enabled: false`
- modified patch fails integrity
- missing patch fails
- missing review fails
- unapproved review fails
- expired review fails
- provider mismatch fails
- unsafe path fails
- ignored folder path fails
- workspace drift fails
- target missing fails
- delete conflict fails
- parse error fails
- preflight does not modify active workspace
- audit log records preflight events
- route returns preflight report

## Future Work

Phase 2.5.5.2 should improve UI presentation for preflight reports. Safe apply remains a later feature-flagged implementation after rollback planning is implemented and tested.
