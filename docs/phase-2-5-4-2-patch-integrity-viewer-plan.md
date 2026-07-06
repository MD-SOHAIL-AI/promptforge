# Phase 2.5.4.2 Patch Integrity Viewer Plan

## Goal

Improve sandbox review patch export with integrity metadata and safer inspection support.

## Metadata

Each exported patch records:

- patch ID
- review ID
- provider ID
- patch size
- patch SHA-256
- changed file count
- created, modified, and deleted file lists
- workspace root hash
- review status at export
- apply enabled: `false`
- integrity status

Metadata is stored next to the patch under:

```text
.promptforge/state/bridge-patches/<review_id>.metadata.json
```

## Integrity

Verification compares the current patch file hash to the recorded SHA-256.

Statuses:

- `valid`
- `missing`
- `modified`
- `unknown`

Modified or missing patches can still be viewed, but ForgeX shows a warning.

## Viewer/Open Folder

ForgeX can open the managed patch directory for a review after the patch exists. The operation resolves the patch path from the review ID and managed patch directory only; it does not accept arbitrary user paths.

## API

- `GET /models/bridges/reviews/{review_id}/patch-metadata`
- `POST /models/bridges/reviews/{review_id}/verify-patch`
- `POST /models/bridges/reviews/{review_id}/open-patch-folder`

Existing export and patch download routes remain.

## Non-Goals

No patch apply, active workspace mutation, bridge routing, Codex execution, Claude execution, token reading, or cookie reading.
