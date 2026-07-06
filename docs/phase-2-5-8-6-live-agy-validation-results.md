# Phase 2.5.8.6 Live AGY Validation Results

## Decision

The local AGY installation/authentication and attested-readiness blockers are resolved. Phase 2.5.8.6.2 is **INCOMPLETE / TRUSTED LIVE SMOKE FAILED SAFELY**, not blocked by installation, backend readiness, or attestation.

Final readiness result: **`READY`**. One real AGY process ran in one ForgeX-managed sandbox. The provider run reached `completed` and created a review, but it did not create the expected sandbox artifact. The live harness therefore returned `FAIL` and the positive review/patch path was not proven.

## Installation and readiness

| Check | Result |
| --- | --- |
| AGY installation | Installed; official Google-signed Windows binary |
| AGY version | `1.0.14` |
| Authentication | Operator-attested after the official interactive login flow |
| Ordinary check-only | `BLOCKED_FLAGS_MISSING` |
| Live-flagged pre-auth check-only | `BLOCKED_AGY_NOT_AUTHENTICATED` |
| Final live-flagged check-only | `READY` |
| Throwaway marker | Present and valid |
| Backend | Healthy, loopback-only, generic AGY cutover ready |

The six live flags and authentication attestation were scoped to the QA backend and validation commands. Production defaults were not changed. The official Windows binary lacks usable PE product-version metadata, so the readiness probe now falls back to the fixed, non-shell `agy --version` command and accepts only a bounded version string.

## Live smoke result

| Check | Result |
| --- | --- |
| Harness result | `FAIL` (safe failure) |
| Real AGY execution count | 1 |
| Generic run ID | `bridge-run-d8404bd2a1384104a0df643c90527b31` |
| Review ID | `bridge-review-d20203744f6146e7b6c2df7ed7803e82` |
| Generic run terminal state | `completed` |
| Generic run delta | 1 |
| Sandbox count/delta | 1 |
| Active workspace | Unchanged |
| Active-workspace smoke artifact | Absent |
| Sandbox/review smoke artifact | Absent |
| Review | Created, pending, zero changed files |
| Patch export/integrity/preflight | Not run because the expected reviewed artifact was absent |
| Automatic apply | No |
| Automatic build | No |
| Automatic flash | No |
| Legacy fallback | No |

The harness initially lost its live SSE connection because Python 3.10 raises `asyncio.TimeoutError` for the heartbeat wait while the route caught only the newer timeout class. The catch is now Python-version compatible. Post-run replay verified seven ordered sanitized events, a heartbeat, and exactly one terminal event. Provider execution was not retried, preserving the required single real-process boundary.

## UI, cancellation, and timeout

| Check | Result |
| --- | --- |
| Agent UI | Not required; no live UI validation performed |
| Cancellation | Not run because the normal live harness did not pass |
| Timeout | Not run because the normal live harness did not pass |

No cancellation or timeout outcome is inferred from the normal run.

## Audit and log safety

The managed bridge audit contained 10 metadata-only records. A metadata scan found no absolute workspace path, instruction/prompt field, provider stdout/stderr field, patch/file content field, or credential marker. Public run details and replayed SSE contained only sanitized lifecycle metadata.

`npm.cmd run qa:safety-scan` passed **59 of 59** assertions. Codex, Claude, and OpenCode execution remained disabled. Auto-apply, auto-build, and auto-flash remained disabled.

## Bugs fixed during validation

1. Official Windows AGY version detection now falls back from absent binary metadata to a fixed, bounded, non-shell `--version` probe.
2. Live SSE heartbeat handling now catches `asyncio.TimeoutError`, including Python 3.10 behavior.

Neither change alters provider routing, sandbox ownership, review/apply authority, or production feature-flag defaults.

## Verification

| Command | Result |
| --- | --- |
| `npm.cmd run test:agy-live` | 11 passed |
| Focused generic API/live tests | 32 passed |
| `python -m compileall backend` | PASS |
| `python -m pytest` | 1570 passed, 7 skipped |
| `npm.cmd --prefix frontend run typecheck` | PASS |
| `npm.cmd --prefix frontend run build` | PASS; 4 static pages generated |
| `npm.cmd run build:electron` | PASS |
| `npm.cmd run test:electron` | 10 passed |
| `npm.cmd run qa:safety-scan` | 59 passed, 0 failed |

## Remaining limitation

The installation and authentication blockers are cleared, but the successful artifact path remains unproven because AGY exited successfully without creating the requested sandbox file. A future explicitly authorized run may retest after confirming a safe official AGY tool-permission configuration. It must not use `--dangerously-skip-permissions`, and it must retain the same sandbox, review, no-apply, no-build, and no-flash gates.

## Phase 2.5.8.6.1 follow-up

The positive-artifact follow-up reproduced the zero-change behavior and narrowed it to AGY's per-folder project trust/write approval in a unique non-interactive sandbox. Experimental new-project and explicit-workspace modes did not provide a safe headless solution. ForgeX now records sanitized execution diagnostics, performs bounded post-exit diff settling, does not persist provider output, and maps zero changes to `no_changes_produced` without review authority.

Readiness remains `READY`, but no live run created the expected artifact. Phase 2.5.8.6.1 is incomplete pending an official scoped workspace-trust mechanism. Full results are recorded in `phase-2-5-8-6-1-agy-positive-artifact-fix-results.md`.

## Phase 2.5.8.6.2 scoped-trust update

ForgeX now supports an explicit QA-only stable AGY workspace that the operator can trust once. The root is managed, marked, contained, protected-root checked, symlink checked, reset from the active throwaway workspace before each run, and locked through review creation. Unsafe global permission bypass remains prohibited.

Bootstrap preparation passed and executed no provider. Structural verification passed but correctly reported that operator trust attestation is required. The current shell did not detect an installed/authenticated AGY command, so no confirmed real run was attempted. Positive artifact, run/review identifiers, and patch pipeline results therefore remain pending; active-workspace safety and all automated trusted-mode tests passed.

## July 2026 trusted-workspace validation

| Check | Latest result |
| --- | --- |
| AGY detection/version | Installed; `1.0.14` |
| QA backend live gates | Enabled; loopback health and Bridge Safety ready |
| Trusted workspace prepare | PASS (`PREPARED`); no provider execution |
| Trusted workspace verify | Structural PASS; operator attested per run |
| Attested check-only | `READY`; real execution count 0 |
| Real smoke | Safe failure: `no_changes_produced` |
| Real execution count | 1 |
| Run ID | `bridge-run-d46552c3ef6e489f98d6850c971288c7` |
| Review ID | None |
| Changed files | 0 changed; 0 created; 0 modified; 0 deleted |
| Expected created filename | Not created: `AGY_GENERIC_SMOKE.txt` |
| Active workspace | PASS; unchanged by relative hash comparison |
| Patch export / integrity / preflight | Not run; no review exists |
| Automatic apply / build / flash | No / no / no |
| Legacy fallback | No |
| Safety scan | 74 passed, 0 failed |

Backend compile passed. The exact full backend command reported 1 failed, 1,586 passed, and 9 skipped because a local timeout override is 300 seconds instead of the documented 180-second default. A process-only default override produced 1,587 passed and 9 skipped. Frontend typecheck passed, frontend production build passed with 4 static pages, Electron build passed, and Electron tests reported 10 passed, 0 failed, 0 skipped.

The active and managed workspaces had zero relative content differences after the run. The smoke artifact was absent from both, and `platformio.ini` was unchanged. No Codex, Claude, or OpenCode execution was enabled. Phase 2.5.8.6.2 remains incomplete until a real trusted AGY run creates the expected one-file review and the read-only patch export, integrity, and preflight path can be validated.
