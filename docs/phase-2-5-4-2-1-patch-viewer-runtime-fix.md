# Phase 2.5.4.2.1 - Patch Viewer Runtime Fix

## Root Cause

The patch integrity/viewer UI could surface raw browser or API failure values into React state and the IDE error boundary. When an event-like value reached rendering or an error boundary without normalization, the UI displayed `Runtime Error [object Event]` and the shell recovered poorly.

The highest-risk paths were patch export, copy, verify, and open-folder actions because they interact with browser clipboard APIs, backend patch routes, and OS folder-opening behavior.

## Fix Summary

This hotfix adds a shared frontend error normalizer and uses it across the patch viewer, settings panel, workspace hook, API client, and IDE error boundary.

Errors are now converted to user-readable strings before rendering:

- `Error` -> message
- `string` -> string
- `Response` -> HTTP status message
- `Event` / `ProgressEvent` -> browser event type
- objects with `message`, `detail`, or `error` -> that text
- unknown values -> safe fallback text

Patch action handlers now catch failures and show inline messages instead of throwing from React event handlers.

## Backend Route Safety

The backend patch folder opener now converts OS opener failures into a clean patch export error response. It does not accept arbitrary paths and still only opens patch files managed by ForgeX patch export storage.

## UI Recovery

The IDE error boundary now renders `ForgeX runtime error` with a normalized error message instead of directly rendering unknown thrown values. The patch review panel also handles missing metadata with:

```text
No patch exported yet. Export a patch to view integrity metadata.
```

## Safety Boundary

This phase does not add patch apply, bridge routing, Codex execution, Claude execution, or any new active workspace mutation path. Patch export, copy, verify, and open-folder remain inspection-only.

## Verification

Required verification for this hotfix:

```bash
python -m compileall backend
python -m pytest
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm run build:electron
npm run test:electron
```

Runtime verification should confirm ForgeX opens without the `[object Event]` overlay, Settings -> Models renders normally, and patch viewer action failures show inline readable errors.

## Remaining Limitations

Patch application remains intentionally disabled. Browser-level runtime QA depends on a working local desktop/browser test environment; failures in the test harness should be recorded separately from ForgeX runtime behavior.
