# Phase 2.5.7.2 Desktop Manual QA Polish Plan

## Goal

Validate the feature-flagged patch apply and rollback restore path against a throwaway workspace in desktop mode, then keep Apply History and Apply Detail readable without changing bridge execution behavior.

## Scope

- Use only `workspace/forgex-apply-test/` as the manual QA workspace.
- Keep patch apply behind `FORGEX_ENABLE_PATCH_APPLY=1`.
- Keep rollback restore behind `FORGEX_ENABLE_ROLLBACK_RESTORE=1`.
- Inspect Apply History and Apply Detail for compact counts, long ID handling, disabled states, and rollback linkage.
- Record every required manual case as `PASS`, `FAIL`, `BLOCKED`, or `NOT TESTED`.
- Run automated backend/frontend verification after UI or documentation changes.

## Out Of Scope

- Bridge routing.
- Codex or Claude execution.
- AGY execution outside existing sandbox dry-run mode.
- Auto-build or auto-flash after apply.
- Binary, rename, chmod, mode-only, or submodule patch support.
- Any weakening of path, symlink, ignored-folder, preflight, confirmation, or integrity checks.

## QA Workspace

```text
workspace/forgex-apply-test/
  README.md
  platformio.ini
  src/main.cpp
  src/delete_me.cpp
```

## Desktop Command

```powershell
$env:FORGEX_ENABLE_AGY_BRIDGE="1"
$env:FORGEX_ENABLE_ROLLBACK_RESTORE="1"
$env:FORGEX_ENABLE_PATCH_APPLY="1"
npm.cmd run dev:desktop
```

## Required Evidence

- Apply disabled by default.
- Apply blocked when rollback restore flag is missing.
- Modify/create/delete apply and rollback behavior.
- Drift and corrupt patch blocking.
- Apply history record and detail readability.
- Audit safety review for raw paths, patch content, file content, tokens, cookies, sessions, and Google credentials.

## Fallback If Desktop Access Is Blocked

If this session cannot control or inspect the Electron desktop window, document the blocker explicitly and rely only on automated throwaway-workspace tests as non-desktop evidence. Do not mark manual desktop cases as passed unless they were actually exercised in desktop mode.
