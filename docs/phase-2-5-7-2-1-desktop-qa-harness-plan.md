# Phase 2.5.7.2.1 Desktop QA Harness Plan

## Goal

Make desktop QA repeatable by providing one command that starts or reuses the backend, starts or reuses the frontend, launches Electron, prints health URLs, and writes safe QA session diagnostics.

## Scope

- Add `npm.cmd run dev:desktop:qa`.
- Add `npm.cmd run qa:create-apply-workspace -- --reset`.
- Add `npm.cmd run qa:safety-scan`.
- Extend bridge safety status with QA-only diagnostic booleans.
- Show a compact QA diagnostics block in Settings -> Models -> Bridge Safety when `FORGEX_QA_MODE=1`.
- Document browser fallback at `http://localhost:3000`.

## Safety Boundaries

- Patch apply remains behind `FORGEX_ENABLE_PATCH_APPLY=1`.
- Rollback restore remains behind `FORGEX_ENABLE_ROLLBACK_RESTORE=1`.
- No bridge routing is added.
- No Codex or Claude execution is added.
- No AGY execution is added outside existing sandbox dry-run mode.
- No auto-build or auto-flash is added.
- No patch safety checks are weakened.

## QA Flow

```text
npm.cmd run qa:create-apply-workspace -- --reset
npm.cmd run qa:safety-scan
npm.cmd run dev:desktop:qa
```

The launch command prints backend and frontend URLs, Electron launch status, renderer URL, feature flag state, and writes `.promptforge/state/qa-session.json`.

If Electron cannot be captured, open the printed frontend URL in a browser for UI QA and record Electron as `BLOCKED` separately.

For CI-like launch diagnostics, `npm.cmd run dev:desktop:qa -- --smoke` verifies readiness, writes the session file, and then stops launcher-owned child processes.
