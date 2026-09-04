# Phase 2.5.8.5.1 Packaged Agent Readiness Results

## Decision

Phase 2.5.8.5.1 is complete. Packaged readiness, generic API/SSE validation, renderer DOM verification, screenshot capture, automated tests, and the safety scan pass. Live AGY is honestly **BLOCKED: operator did not authorize**; real provider executions remain **0**.

## Root cause

`AGYExecutionRouter.__init__` synchronously restored generic history during FastAPI composition. Invalid retained `generic-bridge-runs.json` metadata raised `BridgeDomainError` before application construction and before `/health` could respond. The previous harness discarded probe exceptions and reported only `Backend readiness timed out`, obscuring the process/composition failure.

The defect was reproduced with corrupt isolated metadata. Composition and lifespan reconciliation now catch this domain failure, mark generic history unavailable, keep `/health` and Bridge Safety reachable, and leave store-dependent generic routes fail-closed with sanitized errors. No startup timeout was increased.

## Packaged readiness

Evidence: `.promptforge/state/phase-2-5-8-5-1-packaged-readiness.json`

| Check | Result |
| --- | --- |
| Backend health | PASS |
| Frontend health | PASS |
| Electron renderer | PASS |
| Generic providers, AGY only | PASS |
| Generic runs list/detail | PASS |
| Generic start disabled by default | PASS: HTTP 403 `generic_execution_disabled` |
| Invalid SSE run | PASS: safe 404 |
| Bridge Safety | PASS |
| Agent UI packaged DOM | PASS: 16 states |
| Restart persistence | PASS |
| Packaged log safety | PASS |

The harness records roles, loopback ports/health URLs, Electron PID, sanitized package/resource identities, startup phase, safe health-failure reason, and bounded sanitized backend previews. It never records a full command, environment dump, prompt, patch/file content, credential, cookie, token, or raw filesystem root.

## SSE

Packaged fake-run validation passed connection, ordered terminal replay, `Last-Event-ID`, deterministic QA heartbeat, terminal close, invalid-run failure, `resync_required`, and polling/event deduplication coverage. No provider executed.

## Visual QA

The in-app browser surface was unavailable, so the previously proven packaged-renderer CDP fallback was used. It produced 19 safe PNGs under `docs/images/phase-2-5-8-5/`: packaged startup plus 18 Agent captures covering disabled/default, AGY-only provider, disabled reason, ready, submitting, queued, validating, preparing sandbox, running, collecting artifacts, completed/review-ready, blocked, failed, cancelled, timed out, interrupted, resync required, and backend unavailable.

Representative files:

- `agent-disabled-default.png`
- `agent-running.png`
- `agent-completed-review-ready.png`
- `agent-failed.png`
- `agent-resync-required.png`
- `agent-backend-unavailable.png`

Visual inspection found no raw paths, instructions, patch/file content, credentials, tokens, or personal data. The packaged DOM verified all 16 fixture states.

## Live AGY

- Result: **BLOCKED: operator did not authorize**
- Evidence: `.promptforge/state/phase-2-5-8-5-1-live-agy-smoke.json`
- Real provider executions: **0**

The command was invoked without `--confirm-real-agy` to verify the guard and refused before checking or executing a provider. The confirmed command was not run.

## Verification

| Command | Result |
| --- | --- |
| `python -m pytest tests -k "generic and (api or sse or packaged or agent)"` | 32 passed, 1537 deselected |
| New readiness/API files plus generic API | 47 passed |
| `python -m compileall backend` | PASS |
| `python -m pytest` | 1562 passed, 7 skipped |
| `npm.cmd --prefix frontend run typecheck` | PASS |
| `npm.cmd --prefix frontend run build` | PASS |
| `npm.cmd run build:electron` | PASS |
| `npm.cmd run test:electron` | 10 passed |
| `npm.cmd run test:qa-packaged` | 3 passed |
| `npm.cmd run test:agent-ui` | 8 passed |
| `npm.cmd run qa:safety-scan` | 50 passed, 0 failed |
| `npm.cmd run qa:desktop:packaged -- --reset` | PASS |
| `npm.cmd run qa:desktop:packaged -- --smoke` | PASS |
| guarded live command without confirmation | BLOCKED as required, 0 executions |

## Files fixed

Backend composition/lifecycle and diagnostics were fixed in `backend/api/app.py`, `backend/bridges/agy_execution_router.py`, `backend/api/routes/generic_runs.py`, `backend/api/routes/models.py`, and `backend/api/schemas/generic_runs.py`. Packaged diagnostics/capture and live guarding were updated in `scripts/qa-packaged-desktop.mjs`, `scripts/qa-agy-generic-live.mjs`, and `scripts/safety-scan-bridge-apply.mjs`. Agent QA state rendering was updated in the generic Agent hook, panel, API client, and frontend types. Regression coverage was added under `tests/integration/`.

## Limitations and next phase

- This remains an unpacked QA package using host Python, not a signed installer with bundled Python.
- AGY authentication and real output were not tested because the operator did not authorize a real execution.
- QA visual fixtures exist only under `FORGEX_QA_MODE=1` and are inert.
- AGY remains the only generic provider. Codex, Claude, and OpenCode remain disabled.

Recommended next phase: **Phase 2.5.8.6 — Operator-Guided Live AGY Validation**.
