# Phase 2.5.4.3 - Patch History + Cleanup Controls Plan

## Goal

Add a safe management layer for exported bridge review patches without applying patches or modifying the active workspace.

## Scope

- Add a patch metadata index under ForgeX managed app state.
- List exported patches through `/models/bridges/patches`.
- Delete exported patch files and metadata through explicit user action.
- Clean up old or missing patch records.
- Show compact Patch History in Settings -> Models -> Bridge Safety.
- Record audit events for history, delete, and cleanup.

## Non-Goals

- No patch apply.
- No active workspace mutation.
- No bridge routing.
- No Codex or Claude execution.
- No credential, token, session, or cookie reads.
- No bridge prompt execution outside the existing AGY sandbox feature flag.

## Backend Design

`BridgePatchStore` stores metadata-only records in:

```text
<ForgeX state>/bridge-patches/index.jsonl
```

Index records include patch IDs, review IDs, provider IDs, patch size, SHA-256, integrity status, changed-file counts, relative changed-file paths, workspace hash, and `apply_enabled: false`.

The index does not store patch content.

## API Plan

```text
GET /models/bridges/patches
GET /models/bridges/patches?provider_id=antigravity_cli_bridge
GET /models/bridges/patches?review_id=...
GET /models/bridges/patches?integrity_status=valid
DELETE /models/bridges/patches/{patch_id}
POST /models/bridges/patches/cleanup
```

Delete and cleanup only operate on files inside the managed patch directory.

## UI Plan

Settings -> Models -> Bridge Safety shows:

- Total patches
- Valid / modified / missing counts
- Storage used
- Recent patch rows with View, Verify, Copy, Open Folder, and Delete
- Cleanup button for old or missing exported patches

Delete requires an explicit danger confirmation.

## Audit Events

- `bridge_patch_history_viewed`
- `bridge_patch_deleted`
- `bridge_patch_cleanup_started`
- `bridge_patch_cleanup_completed`
- `bridge_patch_cleanup_failed`

Audit metadata excludes patch content and raw workspace paths.

## Verification

Run backend tests, frontend typecheck/build, and Electron build/tests. Manual verification should confirm patch deletion does not delete review sessions or change active workspace files.
