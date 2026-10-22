# Phase 2.5.4.1 Sandbox Review Patch Export

## Summary

Bridge review panels can now export reviewed sandbox diffs as `.patch` files. Exporting or copying a patch does not apply changes to the active workspace.

## Patch Export Behavior

ForgeX loads the persisted review, validates that it has changed files, and writes a patch to:

```text
.promptforge/state/bridge-patches/<review_id>.patch
```

The patch uses relative paths and `diff --git` headers:

```diff
diff --git a/src/main.cpp b/src/main.cpp
--- a/src/main.cpp
+++ b/src/main.cpp
@@ ...
```

Unsupported binary or large file previews are represented with comments instead of raw file content.

## API

- `POST /models/bridges/reviews/{review_id}/export-patch`
- `GET /models/bridges/reviews/{review_id}/patch`

## UI

`BridgeReviewPanel` now includes:

- `Export Patch`
- `Copy Patch`

The panel warns that patch export does not apply changes and that patches should be reviewed before manual use.

## Audit

Audit events:

- `bridge_patch_exported`
- `bridge_patch_viewed`
- `bridge_patch_copied`

Audit records store review ID, provider ID, changed-file count, workspace hash, and patch size metadata. They do not store patch content.

## Safety Boundary

Apply remains disabled. Active workspace files are not modified by patch export, patch view, patch copy, approve, or reject.
