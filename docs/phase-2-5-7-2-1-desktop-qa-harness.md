# Phase 2.5.7.2.1 Desktop QA Harness

## Commands

Create or reset the throwaway workspace:

```powershell
npm.cmd run qa:create-apply-workspace -- --reset
```

Run the static safety helper:

```powershell
npm.cmd run qa:safety-scan
```

Launch desktop QA:

```powershell
npm.cmd run dev:desktop:qa
```

For launch diagnostics without leaving a desktop session running:

```powershell
npm.cmd run dev:desktop:qa -- --smoke
```

The QA launcher sets missing flags to development QA defaults:

```text
FORGEX_ENABLE_AGY_BRIDGE=1
FORGEX_ENABLE_ROLLBACK_RESTORE=1
FORGEX_ENABLE_PATCH_APPLY=1
FORGEX_QA_MODE=1
```

## Expected Launch Output

```text
Backend ready: http://localhost:8000/health
Frontend ready: http://localhost:3000
Electron launched
QA session written: .promptforge/state/qa-session.json
Frontend URL: http://localhost:3000
```

## QA Session File

The launcher writes:

```text
.promptforge/state/qa-session.json
```

It contains only timestamps, local URLs, Electron launch status, and feature flag booleans. It does not store secrets, full environment variables, patch content, file content, or workspace paths.

## Browser Fallback

If the Electron window cannot be captured or controlled, open:

```text
http://localhost:3000
```

Use the browser to inspect the same Settings -> Models -> Bridge Safety UI and record Electron as `BLOCKED` separately in the QA checklist.

## QA Mode Panel

When `FORGEX_QA_MODE=1`, Bridge Safety shows:

- QA Mode
- AGY sandbox
- Rollback restore
- Patch apply
- Bridge routing
- Codex execution
- Claude execution
- Auto-build after apply
- Auto-flash after apply

The routing, Codex, Claude, auto-build, and auto-flash rows must remain disabled.

## Recording Results

Use `docs/phase-2-5-7-2-qa-checklist-template.md` and record every case as `PASS`, `FAIL`, `BLOCKED`, or `NOT TESTED`.

Screenshots should be captured manually by the operator. Do not paste secrets, file contents, patch contents, or raw credential paths into the checklist.
