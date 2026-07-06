# Phase 2.5.8.4 AGY Generic Cutover Results

## Compatibility Router

`AGYExecutionRouter` chooses exactly one immutable mode before sandbox creation: `legacy`, `generic`, or `blocked`. Existing URLs, request fields, response fields, polling, cancellation, and review navigation remain valid. `idempotency_key` is an optional backward-compatible request field.

## Feature Matrix

| Existing AGY | Generic routing | AGY provider | Cutover | Result |
| --- | --- | --- | --- | --- |
| Off | Any | Any | Any | Blocked |
| On | Off | Any | Off | Legacy |
| On | On | Off | On | Blocked |
| On | On | On | Off | Legacy |
| On | On | On | On | Generic |
| Partial cutover | Mixed | Mixed | On | Blocked |

Defaults for all new flags are false. Flags are read during application composition and cannot redirect an existing run.

## Exactly-Once and Sandbox Ownership

Generic mode creates one compatibility run ID and one sandbox through `BridgeSandboxService`. The adapter calls `AntigravitySandboxRunner.start_run_in_sandbox` with the same run ID and validated root. The runner retains command, environment, process, timeout, cancellation, diff, review, output, and audit ownership but does not create a second sandbox.

An idempotency key returns the existing run. Persistence is required before provider startup. Generic failure never calls the legacy start path, response delivery never retries execution, and active-workspace content remains unchanged.

## API, State, Event, and Cancellation Compatibility

Canonical queued/validating/preparing map to `pending`; running/collecting map to `running`; completed maps to `review_ready`; timeout maps to `failed_timeout`; cancellation remains `cancelled`; blocked/failed/interrupted map to safe `failed`. Unknown states fail safely.

Polling reads persisted generic state and existing review artifacts; no internal event bus is exposed. Cancellation uses the stored mode: legacy runs call legacy cancellation, generic runs call coordinator cancellation, which delegates through the adapter to the same runner process owner. There is no broad process-name termination or fallback.

## Review, Patch, and Audit Parity

Generic success reuses the existing `BridgeDiffService` review ID and changed-file metadata. Patch export, integrity, preflight, apply history, apply, restore, and rollback remain unchanged and independently gated. Failed/cancelled runs do not become review-ready.

Compatibility audit entries record only mode, provider `agy`, generic run ID where applicable, safe terminal status/failure code, review ID, counts, and workspace hash. Prompts, output, commands, environment, executable paths, workspace/sandbox paths, patch/file content, and credentials are absent.

## Restart

`execution_mode` is persisted in the generic run record. Startup reconciliation marks incomplete generic runs `interrupted` without resuming. The compatibility router reconstructs generic ownership and idempotency lookup from those records. Completed artifact/review associations remain available and are not recreated.

## Fake Parity Matrix

| Case | Legacy | Generic |
| --- | --- | --- |
| Detection | PASS | PASS |
| Successful execution | PASS | PASS |
| Validation failure | PASS | PASS |
| Sandbox rejection | PASS | PASS |
| Process failure | PASS | PASS |
| Timeout | PASS | PASS |
| Cancellation | PASS | PASS |
| Restart interruption | PASS | PASS |
| Review creation | PASS | PASS |
| Artifact safety | PASS | PASS |
| Audit safety | PASS | PASS |
| No active-workspace edits | PASS | PASS |

Automated fake cutover E2E: PASS. It used a temporary `workspace/forgex-apply-test`-named throwaway workspace, one fake process owner, and no apply operation.

## Real AGY Smoke Test

`BLOCKED` — not run. This session did not have explicit operator initiation confirming installed/authenticated AGY plus QA mode, all four execution gates, and the designated throwaway workspace. No real agent command executed.

## Remaining Limitations

The public contract remains AGY-specific. Generic public API/event streaming/provider UI are deferred. Legacy run recovery remains unchanged. Production defaults remain blocked or legacy according to the existing AGY flag.

## Verification

- Focused AGY generic/cutover/compatibility selection: 72 passed, 1,450 deselected.
- Fake cutover E2E/matrix harness: 23 passed.
- Complete backend: 1,515 passed, 7 skipped.
- Frontend typecheck and production build: PASS.
- Electron: 10 passed.
- Safety scan: 34 checks passed.
- Real provider commands executed: none.
