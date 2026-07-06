# Phase 2.5.5.1 - Patch Preflight API

## Summary

ForgeX now includes a read-only Patch Preflight API for exported bridge patches.

The API inspects existing ForgeX patch metadata, patch content, review state, active workspace identity, path safety, and current target file hashes. It returns a safety report and does not apply patches, create rollback data, or modify active workspace files.

Apply remains disabled.

## Endpoint

```text
POST /models/bridges/patches/{patch_id}/preflight
```

Request:

```json
{
  "workspace_root": "optional active workspace root if needed"
}
```

Response:

```json
{
  "patch_id": "...",
  "review_id": "...",
  "provider_id": "antigravity_cli_bridge",
  "can_apply": true,
  "apply_enabled": false,
  "integrity_status": "valid",
  "review_status": "approved",
  "workspace_status": "ok",
  "conflicts": [],
  "warnings": [],
  "files_to_create": [],
  "files_to_modify": ["src/main.cpp"],
  "files_to_delete": [],
  "checked_at": "..."
}
```

`can_apply: true` means the read-only checks found no blocking conflict. It does not mean ForgeX can apply the patch in this build. `apply_enabled` is always `false`.

## Implemented Checks

Patch integrity:

- patch record exists
- patch file exists
- current patch SHA-256 matches stored metadata
- modified or missing patch files return blocking conflicts

Review state:

- review exists
- review is approved
- review is not expired
- provider matches patch provider
- review changed-file paths match patch metadata

Workspace identity:

- active workspace root is available from the request or in-memory review
- workspace exists
- workspace is readable and writable
- workspace root hash matches the patch review workspace hash

Path containment:

- rejects absolute paths
- rejects `../` escapes
- rejects paths escaping the workspace after resolution
- rejects symlink escapes
- rejects `.git`, `.pio`, `node_modules`, `dist`, `build`, `.next`, and `.forgex`

Drift detection:

- modified files must still match the review baseline hash
- deleted files must still match the review baseline hash
- missing modified/deleted targets are conflicts
- create targets that already exist are warnings

Patch simulation:

- parses supported `diff --git a/<path> b/<path>` headers
- compares parsed paths against patch metadata
- reports create/modify/delete lists from trusted patch metadata
- rejects unsupported, truncated, binary, large, renamed, or ambiguous patch content

## Conflict Shape

```json
{
  "path": "src/main.cpp",
  "type": "target_changed",
  "severity": "error",
  "message": "File changed after review was created."
}
```

## UI

Bridge Review and Patch History now expose a `Preflight` action.

Clean result:

```text
Preflight passed. Patch appears safe to apply, but Apply is disabled in this build.
```

Conflict result:

```text
Preflight failed. src/main.cpp File changed after review was created.
```

The UI shows a compact report with create/modify/delete counts and conflict summaries. It also shows an `Apply disabled` control. There is no Apply API or active apply button.

## Audit

Preflight records:

- `patch_preflight_started`
- `patch_preflight_completed`
- `patch_preflight_failed`

Audit metadata includes patch ID, review ID, provider ID, conflict count, warning count, `can_apply`, and workspace root hash.

Audit logs do not store full patch content, raw workspace paths, tokens, cookies, raw secrets, or credentials.

## Limitations

- Full patch application is not implemented.
- Rollback records are not created.
- Apply history is not created.
- Persisted reviews may require the caller to provide `workspace_root`, because persisted review records intentionally do not store raw workspace paths.
- Patch parsing is intentionally conservative and supports only straightforward file-level git diff headers.

## Future Requirement

Before any future apply phase, ForgeX still needs feature-gated apply, pre-apply rollback snapshots, atomic write strategy, post-apply verification, rollback execution, and end-to-end QA.
