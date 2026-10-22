# Phase 2.5.8.4 AGY Generic Cutover Plan

## Objective

Route the existing AGY sandbox API through the generic coordinator only when all explicit cutover gates are enabled, while preserving legacy behavior and public response contracts.

## Existing Route

The frontend calls `POST /models/bridges/antigravity/sandbox-run`, polls `GET /models/bridges/runs/{run_id}`, cancels through the existing run cancellation route, and navigates to the returned review ID. Public statuses are `pending`, `running`, `review_ready`, `completed`, `failed`, `failed_timeout`, and `cancelled`. Legacy run/process lookup is in-memory and review IDs are created by `BridgeDiffService`.

## Plan

1. Add one compatibility router at the existing route/service boundary.
2. Evaluate immutable feature flags before sandbox or process work.
3. Preserve legacy calls when cutover is off and fail closed for partial cutover.
4. Add a runner entry point that reuses a coordinator-validated sandbox.
5. Translate generic records back to legacy run/status/review responses.
6. Persist `execution_mode=generic` and use it for lookup, cancellation, and restart reconstruction.
7. Add idempotency, routing/terminal audit entries, fake E2E parity, safety scanning, and documentation.

## Non-Goals

- No public generic endpoint or provider-selection UI.
- No automatic fallback or dual execution.
- No Codex, Claude Code, or OpenCode execution.
- No automatic apply, build, flash, or restore.
- No real AGY smoke test without every QA/operator gate.
