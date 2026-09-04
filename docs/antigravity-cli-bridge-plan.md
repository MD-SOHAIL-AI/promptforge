# Antigravity / AGY CLI Bridge Plan

## Goal

Plan future Google Antigravity / AGY CLI bridge support without implementing prompt execution. ForgeX should detect the user-installed AGY tool and report version/auth status only when it can do so safely.

## Active Command Target

Primary command: `agy`

Fallback command: `antigravity`

ForgeX must not run `agy` or `antigravity` without safe arguments because either command may open an interactive TUI.

## Detection

Safe detection uses:

- Windows: `where agy`, then `where antigravity`
- macOS/Linux: `which agy`, then `which antigravity`
- version: `<detected-command> --version`
- optional help: `<detected-command> --help`

All subprocess calls must use argv arrays, `shell=False`, bounded timeouts, and capped stdout/stderr.

## Auth Status

Default installed status is `auth_status: unknown` with low confidence.

ForgeX may only refine auth if a future AGY status command is documented as non-mutating and non-interactive. ForgeX must not trigger login, read Google auth files, read browser cookies, inspect OAuth stores, or dump environment secrets.

## Execution Boundary

This plan does not implement prompt execution, code generation, file editing, diff apply, or model routing through AGY.

AGY bridge capabilities remain disabled until a separate execution phase adds workspace guards, approval flows, process cancellation, output caps, and diff review.

Phase 2.5.3 adds the shared snapshot/diff/review foundation only. AGY execution remains disabled until a later phase wires AGY runs through this review layer.

Phase 2.5.3.1 persists AGY review sessions and snapshot metadata before any AGY execution exists. Pending reviews survive backend restart, expire after 24 hours, and cannot be approved after expiration.

Phase 2.5.4 adds an AGY-only sandbox dry-run prototype behind `FORGEX_ENABLE_AGY_BRIDGE=1`. ForgeX copies the active workspace to a managed sandbox, runs `agy -p <prompt>` only inside that sandbox, diffs sandbox changes, and creates a review session. It does not route normal generation through AGY and does not apply sandbox changes to the active workspace.

Phase 2.5.4.1 adds patch export for AGY sandbox reviews. Users can export or copy a `.patch` generated from the reviewed diff, but ForgeX still does not apply changes to the active workspace.

Phase 2.5.4.2 records patch SHA-256 metadata, verifies whether exported patches changed after export, and lets users open the managed patch folder for inspection. Apply remains disabled.

Phase 2.5.4.3 adds patch history and cleanup controls for exported AGY sandbox review patches. ForgeX indexes patch metadata locally, supports verify/copy/open/delete from Settings, and can clean old or missing patch records. Patch deletion does not delete review sessions and does not modify the active workspace.

Phase 2.5.5 documents the future patch apply preflight system. It does not enable AGY routing and does not apply sandbox-generated patches to the active workspace. Future AGY sandbox patch apply must pass patch integrity verification, approved review checks, workspace identity validation, path containment, drift detection, dry-run simulation, explicit user confirmation, rollback snapshot creation, and audit logging before any active-workspace write is allowed.

Current AGY patch records remain `apply_enabled: false`.

## Gemini CLI Migration

Gemini CLI is not an active ForgeX bridge target for individual-user bridge planning. ForgeX detects Antigravity / AGY instead of the legacy `gemini` CLI.
