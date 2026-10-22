# Phase 2.5.4.3 - Patch History + Cleanup Controls

## Summary

ForgeX now keeps a local metadata index for exported bridge review patches. Exported patches appear in Settings -> Models -> Bridge Safety, where users can inspect recent patch records, verify integrity, copy patch text, open the managed patch folder, delete exported patch files, or clean up old/missing patch records.

Apply remains disabled.

## Patch Index

Patch metadata is stored under the managed ForgeX patch directory:

```text
<ForgeX state>/bridge-patches/index.jsonl
```

Each record contains:

- `patch_id`
- `review_id`
- `provider_id`
- `created_at`
- relative `patch_path`
- relative `metadata_path`
- `patch_size`
- `patch_sha256`
- `integrity_status`
- changed-file counts and relative paths
- `workspace_root_hash`
- `review_status_at_export`
- `apply_enabled: false`

The index does not store patch content or raw workspace paths.

## API Behavior

Patch history is exposed through:

```text
GET /models/bridges/patches
DELETE /models/bridges/patches/{patch_id}
POST /models/bridges/patches/cleanup
```

The list endpoint supports provider, review, and integrity filters.

Delete removes only the exported patch file and sidecar metadata for a managed patch record. It does not delete review sessions, audit logs, sandboxes, or active workspace files.

Cleanup removes records older than the requested age and, when requested, records whose patch file is missing. The default cleanup request uses 30 days and includes missing records.

## UI Behavior

Bridge Safety now includes a compact Patch History section:

```text
Total patches: X
Valid: Y
Modified: Z
Missing: N
Storage used: 123 KB
```

Recent patch rows include:

```text
View
Verify
Copy
Folder
Delete
```

Delete uses the ForgeX confirmation dialog and explicitly states that the workspace and review history are not modified.

## Audit Behavior

ForgeX records:

- `bridge_patch_history_viewed`
- `bridge_patch_deleted`
- `bridge_patch_cleanup_started`
- `bridge_patch_cleanup_completed`
- `bridge_patch_cleanup_failed`

Audit events include patch IDs, review IDs, provider IDs, patch size, SHA-256, integrity status, and workspace hash where available. They do not include patch content, tokens, cookies, prompts, credentials, or raw workspace paths.

## No-Apply Boundary

This phase does not implement patch apply. Deleting or cleaning patch exports only affects ForgeX-managed exported patch files and metadata. The active workspace remains untouched.

## Remaining Limitations

Patch history is local only. It is not synced, shared, or routed into normal generation. Future apply support requires a separate phase with explicit user approval, path containment checks, pre-apply snapshots, and rollback planning.
