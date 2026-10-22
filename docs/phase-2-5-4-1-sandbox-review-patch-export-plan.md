# Phase 2.5.4.1 Sandbox Review Patch Export Plan

## Goal

Allow reviewed sandbox diffs to be exported as `.patch` files without applying changes to the active workspace.

## Flow

```text
AGY sandbox run
sandbox diff generated
review session created
user exports patch
ForgeX writes patch under app state
user may copy patch manually
active workspace remains unchanged
```

## Storage

Patch files are stored under ForgeX app state:

```text
.promptforge/state/bridge-patches/<review_id>.patch
```

API responses expose the patch filename, size, file count, creation timestamp, and patch download URL.

## Patch Rules

- Use relative paths only.
- Use `diff --git a/<path> b/<path>` headers.
- Do not include raw workspace absolute paths.
- Mark unsupported binary/large file previews with comments.
- Cap huge patch output.

## API

- `POST /models/bridges/reviews/{review_id}/export-patch`
- `GET /models/bridges/reviews/{review_id}/patch`

## Non-Goals

This phase does not add apply-to-active-workspace, AGY routing, Codex execution, Claude execution, token reads, cookie reads, or credential storage.
