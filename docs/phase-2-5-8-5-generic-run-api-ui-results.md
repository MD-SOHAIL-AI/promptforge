# Phase 2.5.8.5 Generic Run API and Agent UI Results

## Completion

Phase 2.5.8.5 is **complete** after Phase 2.5.8.5.1 packaged closure. The public API and AGY generic execution remain disabled by default. Automated coverage and visual fixtures are inert. Real provider executions: **0**.

## API and safety behavior

- AGY is the only accepted and listed generic provider.
- Start accepts only `provider_id`, `project_id`, runtime-only `instruction`, bounded timeout, and idempotency key.
- Unknown fields are rejected; raw instructions and provider output are not persisted or exposed.
- Execution requires all five disabled-by-default gates.
- Generic list/detail/events remain sanitized; cancellation uses persisted ownership.
- Codex, Claude, OpenCode, auto-apply, auto-build, and auto-flash remain disabled.

## Packaged closure

Packaged desktop QA: **PASS** for `--reset` and `--smoke`.

Backend health, frontend health, Electron renderer, Bridge Safety, AGY-only providers, generic list/detail, safely disabled start, invalid SSE failure, replay, heartbeat, terminal close, resync, restart persistence, and log safety passed. Evidence is stored in `.promptforge/state/phase-2-5-8-5-1-packaged-readiness.json`.

The timeout root cause was synchronous generic-history restoration in `AGYExecutionRouter.__init__`: corrupt retained generic metadata could raise before FastAPI and `/health` existed. The old probe loop discarded the underlying reason. Corrupt history now preserves backend health and Bridge Safety while store-dependent routes fail closed, and the harness records bounded sanitized diagnostics without increasing timeouts.

## Visual QA

Screenshots: **PASS**.

The in-app browser was unavailable, so the established packaged Electron/CDP fallback captured 19 safe PNGs under `docs/images/phase-2-5-8-5/`. Sixteen inert QA states were DOM-verified: disabled, ready, submitting, queued, validating, preparing sandbox, running, collecting artifacts, completed, blocked, failed, cancelled, timed out, interrupted, resync required, and backend unavailable.

## Live AGY smoke

Live AGY smoke: **BLOCKED: operator did not authorize**. The guard was verified without `--confirm-real-agy` and refused before provider checks or execution. Evidence is stored in `.promptforge/state/phase-2-5-8-5-1-live-agy-smoke.json`.

## Verification

| Command | Result |
| --- | --- |
| Exact focused pytest selector | 32 passed, 1537 deselected |
| New readiness/API files plus generic API | 47 passed |
| Full backend | 1562 passed, 7 skipped |
| Python compileall | PASS |
| Frontend typecheck/build | PASS |
| Agent UI state tests | 8 passed |
| Electron tests | 10 passed |
| Packaged helper tests | 3 passed |
| Safety scan | 50 passed, 0 failed |
| Packaged reset and smoke | PASS / PASS |

## Remaining limitations

- Live AGY remains untested until an operator explicitly authorizes one real run with every gate and attestation.
- The QA package is unpacked and uses host Python; signing and Python bundling remain out of scope.
- AGY is the only generic provider.

Recommended next phase: **Phase 2.5.8.6 — Operator-Guided Live AGY Validation**.

## Phase 2.5.8.6 readiness update

The non-executing live readiness probe is implemented and tested. The final result is `BLOCKED_AGY_NOT_INSTALLED`; authentication is not attested and no live-gated backend was used. No provider process, generic run, sandbox, review, apply, build, or flash occurred. Phase 2.5.8.6 must be rerun after local AGY installation and authentication.
