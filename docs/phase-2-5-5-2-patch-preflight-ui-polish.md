# Phase 2.5.5.2 - Patch Preflight UI Polish

## Summary

ForgeX now renders read-only patch preflight results as a compact safety report in Bridge Review and Patch History.

Apply remains disabled.

## UI Components

New reusable component:

```text
frontend/components/ide/patch-preflight-report.tsx
```

It displays:

- safety status
- patch integrity
- review status
- workspace status
- files to create
- files to modify
- files to delete
- conflicts
- warnings

The report is compact and expandable. It handles missing or partial result fields defensively.

New status helper:

```text
frontend/lib/patch-preflight-status.ts
```

It maps backend preflight results into readable UI statuses:

- Safe candidate
- Blocked
- Warning
- Needs active workspace
- Patch modified
- Approval required
- Unknown

## Bridge Review Behavior

Bridge Review shows a `Preflight` action after a patch is exported.

If no patch exists:

```text
Export a patch before running preflight.
```

If no active workspace root is available:

```text
Open the target workspace before running preflight.
```

Loading state:

```text
Running preflight...
```

Clean result:

```text
Preflight passed. Patch appears safe to apply, but Apply is disabled in this build.
```

Blocked result shows the first readable conflict and the expandable report.

## Patch History Behavior

Patch History rows include a `Preflight` action. The action is disabled until an active ForgeX workspace is open.

After preflight, each row shows:

```text
Preflight: Blocked - src/main.cpp: File changed after review was created.
```

or:

```text
Preflight: Safe candidate - Patch appears safe to apply later.
```

The selected row expands to show the full safety report.

## Conflict Labels

The UI maps backend conflict codes to readable labels, including:

- Patch file was modified after export.
- Patch file is missing.
- Review session was not found.
- Review is not approved yet.
- Review expired. Create a new sandbox review.
- Patch provider does not match review provider.
- Patch contains unsafe path.
- Patch targets ignored/build folder.
- Workspace changed since review.
- Target file changed after review.
- Target file is missing.
- Delete target changed or is unsafe.
- Patch format could not be parsed.

## Workspace Root Safety

The UI uses only the active ForgeX workspace root from `activeProject.project_path`.

It does not persist raw workspace paths for preflight, does not prompt for arbitrary paths, and does not send user-entered paths.

## Output Panel Messages

Preflight emits short output panel messages:

```text
[preflight] Started patch preflight
[preflight] Patch integrity: valid
[preflight] Result: blocked, 1 conflict
```

These messages do not include patch content or credentials.

## No-Apply Boundary

This phase does not implement apply, rollback, apply history, bridge routing, AGY routing, Codex execution, Claude execution, or credential/session reads.

`apply_enabled` remains `false`. The UI displays `Apply disabled` and does not expose an active apply button.

## Limitations

There is no frontend unit test runner configured. UI behavior is covered by TypeScript, production build, Electron build/tests, and manual QA.

The report is a presentation layer over the existing read-only API. It does not perform patch application or full hunk application simulation in the browser.
