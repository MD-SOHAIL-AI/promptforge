# Phase 2.5.4.2 Patch Integrity Viewer

## Summary

Patch exports now include SHA-256 integrity metadata and verification support. Users can inspect patch metadata, verify whether the patch changed after export, and open the managed patch folder.

Apply remains disabled.

## Patch Metadata

ForgeX writes a metadata sidecar:

```text
.promptforge/state/bridge-patches/<review_id>.metadata.json
```

Metadata includes provider ID, review ID, patch size, patch SHA-256, changed file counts, relative changed-file lists, workspace root hash, review status at export, and `apply_enabled: false`.

## Integrity Verification

`POST /models/bridges/reviews/{review_id}/verify-patch` compares the current patch SHA-256 to the recorded hash.

Statuses:

- `valid`: patch matches the recorded export hash
- `modified`: patch exists but no longer matches
- `missing`: patch file is gone
- `unknown`: metadata is unavailable

Modified and missing states are surfaced as warnings but do not block viewing.

## UI

`BridgeReviewPanel` shows:

- patch SHA-256
- patch size
- changed-file count
- integrity status
- apply enabled: No

Buttons:

- Export Patch
- Copy Patch
- Verify Patch
- Open Patch Folder

## Open Folder Safety

The open-folder action resolves the patch location from ForgeX managed patch state. It does not accept arbitrary paths and does not execute patch files.

## Audit

Audit events:

- `bridge_patch_metadata_created`
- `bridge_patch_verified`
- `bridge_patch_integrity_failed`
- `bridge_patch_folder_opened`

Audit metadata includes patch size, patch SHA-256, and integrity status. Full patch content is not logged.

## Safety Boundary

Patch export, verification, copy, and folder opening do not modify the active workspace. Applying changes remains a future phase with separate requirements.
