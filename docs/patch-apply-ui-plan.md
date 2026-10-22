# Patch Apply UI Plan

## Purpose

This document defines the future ForgeX UI for patch preflight, apply, and rollback. It is a design plan only. Current apply controls must remain disabled.

## Surfaces

Future controls belong in:

- `BridgeReviewPanel`
- Patch History in Settings -> Models -> Bridge Safety

Planned actions:

```text
Preflight Check
Apply Patch
Rollback
```

## Current Phase Behavior

Apply remains disabled. The UI may mention that active-workspace apply requires a future preflight implementation, but it must not expose a working apply path.

## Phase 2.5.5.2 Read-Only Safety Report

ForgeX now presents read-only preflight results with a reusable safety report component in Bridge Review and Patch History.

The report shows:

- safety status
- patch integrity
- review status
- workspace status
- files to create
- files to modify
- files to delete
- conflicts
- warnings

Patch History rows can run preflight against the active ForgeX workspace root. If no active workspace is open, preflight is disabled and the UI explains that the target workspace must be opened first.

The UI maps conflict codes into readable labels such as "Patch file was modified after export", "Review is not approved yet", "Patch targets ignored/build folder", and "Target file changed after review".

Even when preflight returns a safe candidate, the UI shows `Apply disabled` and does not expose a working Apply action.

## Future User Flow

```text
1. User clicks Preflight.
2. ForgeX shows safety report.
3. If clean, Apply button becomes available.
4. User confirms dangerous action.
5. ForgeX applies patch.
6. ForgeX shows changed files.
7. Rollback button appears.
```

## Preflight Report

The preflight report should show:

- patch integrity status
- review approval status
- workspace identity status
- drift status
- dry-run status
- conflicts
- warnings
- files to create
- files to modify
- files to delete

The report should use relative paths only.

## Apply Confirmation

Required warning text:

```text
Applying a patch will modify your active workspace.
ForgeX will create a rollback snapshot before applying.
Review all changes carefully.
```

The confirmation should require a deliberate action, not a passive modal close. If the preflight includes deletes, the UI should require an additional explicit delete confirmation.

## Conflict Display

Conflict rows should include:

- relative path
- conflict type
- short explanation
- blocking or warning severity

Supported conflict types:

- `workspace_drift`
- `patch_modified`
- `path_unsafe`
- `target_missing`
- `target_changed`
- `delete_conflict`
- `binary_unsupported`
- `large_file_unsupported`
- `unknown`

`clean` files do not need a conflict row.

## Apply Result

After a future successful apply, the UI should show:

- apply ID
- patch ID
- files changed
- created files
- modified files
- deleted files
- rollback availability

The UI should avoid showing full patch content in audit-like summaries. Detailed patch viewing should continue to use the existing patch viewer/copy flow.

## Rollback Flow

Rollback appears only after a successful apply record with rollback data.

Rollback confirmation should explain that ForgeX will restore backed-up prior file states and remove files created by the patch. If files changed after apply, rollback must show a blocking conflict report rather than overwriting silently.

## Disabled States

Apply should be disabled when:

- preflight has not run
- preflight failed
- patch integrity is not valid
- review is not approved
- review expired
- workspace identity does not match
- conflicts exist
- rollback snapshot cannot be created
- feature flag is off in the future implementation phase

Rollback should be disabled when:

- no apply record exists
- rollback data is missing
- rollback already completed
- rollback preflight reports blocking conflicts

## Audit Visibility

The UI may show audit status labels such as preflight started, completed, failed, apply completed, and rollback completed. It must not display tokens, cookies, raw secrets, full workspace paths, or unbounded patch content in audit summaries.
# Phase 2.5.7 Apply UI Addendum

The review panel and model settings patch history show `Apply disabled` unless:

```text
FORGEX_ENABLE_PATCH_APPLY=1
FORGEX_ENABLE_ROLLBACK_RESTORE=1
preflight can_apply=true
review approved
patch integrity valid
active workspace exists
```

When enabled, the UI opens a confirmation prompt:

```text
Apply patch to active workspace?

This will modify files in your active workspace.
ForgeX will create a rollback snapshot before applying.
Only files listed in the preflight report will be touched.
You can use rollback restore if something goes wrong.

Type APPLY to continue.
```

After apply, the UI shows created, modified, deleted, failed counts, and the rollback snapshot ID. Apply does not trigger build or flash.
# Phase 2.5.7.1 Apply History UI

Settings -> Models -> Bridge Safety includes a compact Patch Apply History section with total applies, applied count, failed count, failed rolled back count, and rollback available count.

Recent rows expose Details, Open rollback snapshot, Restore Preflight, and Restore Snapshot. Restore Snapshot remains disabled unless rollback restore is feature-flagged and restore preflight passes. The confirmation remains exact `RESTORE`.

The detail view does not display raw workspace paths, patch content, or file content.

# Phase 2.5.7.2 Desktop QA Polish

Apply History now includes compact helper text that records are local and feature-flagged and that no build or flash is triggered after apply. The empty state explains that apply is available only after preflight passes and feature flags are enabled.

Apply Detail includes a readable disabled-state explanation for rollback restore actions when the snapshot is missing, rollback restore is disabled, restore preflight has not run, or restore preflight is blocked.

The desktop manual QA pass was attempted against `workspace/forgex-apply-test/`, but interactive Electron inspection was blocked in this session. Manual desktop cases must not be considered passed until a human can operate the desktop window.

# Phase 2.5.7.2.1 QA Harness UI

When `FORGEX_QA_MODE=1`, Settings -> Models -> Bridge Safety shows a compact QA Diagnostics block with feature flag and safety-disablement state. It is informational only and does not add apply, restore, routing, build, or flash behavior.

The QA launcher prints `http://localhost:3000` as a browser fallback. If Electron cannot be captured, the operator may use that URL for UI inspection and must record Electron as `BLOCKED` separately.

# Phase 2.5.7.3 Packaged State UI

Apply History now loads persisted rollback restore results and maps the canonical state model to readable labels: Applied, Restored, Restore failed, Failed, Failed rolled back, and Failed rollback failed.

History and Detail expose explicit loading, empty, unavailable, and retryable error states. Detail loads from its typed API when selected, preserves full IDs in title text, truncates long values visually, and never enables restore from a failed load state.

History actions remain in a contained wrapping row so the Settings pane does not clip rollback or restore controls in packaged desktop layouts.

# Phase 2.5.8.1 Generic Bridge UI Boundary

No generic run UI or prompt route is added. Existing Bridge Safety diagnostics may report that generic contracts are available while routing and AGY generic adaptation remain disabled. Codex, Claude Code, OpenCode, auto-apply, auto-build, and auto-flash remain disabled. Artifact references cannot enable Apply controls; the existing review, integrity, preflight, feature-flag, and rollback gates remain authoritative.
