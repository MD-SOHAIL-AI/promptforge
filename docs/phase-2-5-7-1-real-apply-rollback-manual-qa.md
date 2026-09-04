# Phase 2.5.7.1 Real Apply/Rollback Manual QA

Use a throwaway workspace only.

Create:

```text
forgex-apply-test/
  README.md
  platformio.ini
  src/main.cpp
```

## Test 1 - Apply Disabled By Default

1. Start ForgeX without `FORGEX_ENABLE_PATCH_APPLY` and without `FORGEX_ENABLE_ROLLBACK_RESTORE`.
2. Open or import `forgex-apply-test/`.
3. Confirm Apply is disabled.
4. Confirm Restore is disabled.

## Test 2 - Apply Requires Both Flags

1. Enable only `FORGEX_ENABLE_PATCH_APPLY=1`.
2. Confirm Apply remains blocked because rollback restore support is missing.
3. Enable both flags.
4. Confirm Apply can appear only after approved review, valid patch integrity, active workspace, and clean preflight.

## Test 3 - Modify File Apply

1. Create a patch that modifies `README.md`.
2. Approve the review.
3. Export the patch.
4. Verify patch integrity.
5. Run preflight.
6. Apply with exact `APPLY`.
7. Confirm `README.md` changed.
8. Confirm a rollback snapshot exists.
9. Confirm an apply history record exists.

## Test 4 - Rollback After Modify

1. Open the apply detail.
2. Run Restore Preflight on the apply rollback snapshot.
3. Restore with exact `RESTORE`.
4. Confirm `README.md` returns to previous content.
5. Confirm apply history remains.
6. Confirm restore history remains.

## Test 5 - Create File Apply And Rollback

1. Apply a patch that creates `src/generated_test.cpp`.
2. Confirm the file exists.
3. Run restore preflight from apply detail.
4. Restore with exact `RESTORE`.
5. Confirm `src/generated_test.cpp` is removed.
6. Confirm `src/` and existing files were not recursively deleted.

## Test 6 - Delete File Apply And Rollback

1. Add a disposable file such as `src/delete_me.cpp`.
2. Apply a patch that deletes it.
3. Confirm the file is deleted.
4. Restore from the apply rollback snapshot.
5. Confirm the file is restored.

## Test 7 - Drift Blocked

1. Export a patch.
2. Modify the target file manually before apply.
3. Run apply.
4. Confirm apply is blocked.
5. Confirm no workspace files changed.

## Test 8 - Corrupt Patch Blocked

1. Export a patch.
2. Modify the exported patch file manually.
3. Run apply.
4. Confirm modified integrity blocks apply.
5. Confirm no workspace files changed.

## Test 9 - Audit Safety

Inspect bridge audit logs and confirm they do not contain raw workspace absolute paths, patch content, file content, tokens, cookies, session data, or Google credentials.

## Safety Scan

Confirm no bridge routing, no Codex execution, no Claude execution, no AGY execution outside sandbox, no auto-build after apply, no auto-flash after apply, and disabled-by-default apply/restore flags.

Manual QA status for this repository update: documented for desktop execution. Automated e2e coverage was added for real apply and rollback restore flows.

## Phase 2.5.7.2.1 QA Harness

Before running the manual cases, reset the throwaway workspace:

```powershell
npm.cmd run qa:create-apply-workspace -- --reset
```

Launch the QA desktop flow:

```powershell
npm.cmd run dev:desktop:qa
```

The launcher prints backend and frontend health URLs and writes `.promptforge/state/qa-session.json`. If Electron cannot be captured, use `http://localhost:3000` for UI QA and record Electron as `BLOCKED` separately.

Use `docs/phase-2-5-7-2-qa-checklist-template.md` to record `PASS`, `FAIL`, `BLOCKED`, or `NOT TESTED` for every case.
