# Phase 2.5.7.3 Packaged Desktop Release-Readiness Results

## Environment

- Date: 2026-06-27
- Platform: Windows (`win32`)
- Package form: repeatable unpacked Electron QA package
- Isolated root: `.promptforge/qa/phase-2-5-7-3/`
- Evidence: `.promptforge/state/phase-2-5-7-3-packaged-qa.json`
- Workspace policy: isolated QA workspace only; no user project was opened or modified

## Packaging Architecture

The repository did not have an installer or unpacked packaging pipeline. The release-readiness harness now builds Next.js standalone output, compiles Electron, stages the installed Electron runtime, standalone renderer, Electron main process, and Python backend into an unpacked QA layout, then runs `ForgeX-QA.exe` with `app.isPackaged=true`.

Packaged Electron owns a production Next server and the Python backend. It does not require `next dev` or the development desktop launcher. The backend still depends on a compatible host Python environment; no Python runtime bundling or installer was added.

Packaged root resolution uses `process.resourcesPath`, not the launch working directory. QA launches use a different working directory to verify that repository `process.cwd()` is not required.

## Isolation

The harness overrides:

- `PROMPTFORGE_ROOT`
- `FORGEX_SETTINGS_PATH`
- `FORGEX_MODEL_ROUTER_SETTINGS_PATH`
- Electron `--user-data-dir`
- backend, frontend, and debugging ports

Reset and package cleanup validate containment below `.promptforge/qa/phase-2-5-7-3/`. Sanitized QA fixtures require `FORGEX_QA_MODE=1`; they contain IDs, relative filenames, hashes, statuses, and normalized messages only.

## Packaged Startup

| Check | Result |
| --- | --- |
| Electron executable starts | PASS |
| Backend health | PASS |
| Production frontend health | PASS |
| Renderer nonblank and ready | PASS |
| Static assets/icons render | PASS |
| Settings and Bridge Safety render | PASS |
| Apply disabled by default | PASS |
| Restore disabled by default | PASS |
| No development frontend required | PASS |
| Launch independent of repository working directory | PASS |
| Launcher-owned processes stop and release ports | PASS |

## Feature Flags

| Apply | Restore | Result |
| --- | --- | --- |
| Off | Off | PASS: apply and restore disabled |
| On | Off | PASS: apply remains blocked |
| Off | On | PASS: apply disabled; restore enabled |
| On | On | PASS: apply and restore enabled, with normal review/preflight gates still required |

Production defaults remained disabled.

## History State Matrix

| State | Result |
| --- | --- |
| Applied | PASS |
| Failed | PASS |
| Failed, rolled back | PASS |
| Failed, rollback failed | PASS |
| Restored | PASS |
| Restore failed | PASS |
| Loading | PASS |
| Empty | PASS |
| Backend/load error with Retry | PASS |

Statuses use distinct labels and tones. Long IDs truncate, failure reasons remain normalized, all actions stay within the Settings pane, and disabled restore actions remain disabled with explanatory text. No raw path or patch content is displayed.

## Detail State Matrix

| State | Result |
| --- | --- |
| Successful apply, not restored | PASS |
| Successful apply, restored | PASS |
| Failed apply | PASS |
| Automatic rollback failed | PASS |
| Restore failed | PASS |
| Restore unavailable | PASS |
| Loading | PASS, live packaged DOM verified |
| Record unavailable | PASS, normalized not-found state and Retry verified |
| Backend error | PASS, renderer remained usable |

Persisted restore results are joined to applies by `rollback_id`, so History and Detail remain consistent after restart. IDs and hashes truncate safely while complete values remain in title text.

## Restart Persistence

| Check | Result |
| --- | --- |
| History persistence after restart | PASS |
| Detail persistence after restart | PASS |
| Restored and restore-failed state persistence | PASS |
| No duplicate records after restart | PASS |

Six canonical apply records and two restore records remained stable across packaged restart. One malformed isolated QA record was ignored without affecting valid history.

## Failure Handling

| Failure | Result |
| --- | --- |
| Backend unavailable | PASS: renderer stayed usable |
| History loading failure | PASS: normalized error and Retry |
| Detail record not found | PASS: normalized error and Retry |
| Malformed persisted QA record | PASS: ignored safely |
| Missing packaged frontend resource | PASS: normalized lifecycle error in automated test |

No unsafe action became enabled during a failure state.

## Log Safety

Packaged Electron, frontend, backend, and harness logs were scanned. No raw repository/workspace path, authorization header, bearer token, cookie, provider credential name, patch content, file content, or session data was found.

## Screenshots

Safe evidence is stored under `docs/images/phase-2-5-7-3/`:

- `packaged-startup.png`
- `bridge-safety-defaults.png`
- `apply-history-state-matrix.png`
- `apply-detail-success.png`
- `apply-detail-failure.png`
- `apply-history-loading.png`
- `apply-history-empty.png`
- `apply-history-error.png`

Transient Detail loading and unavailable states were verified through live packaged DOM text. Their compositor captures timed out, so no misleading screenshots were retained.

## Bugs Found And Fixed

1. Packaged mode had no runnable renderer artifact. Fixed with Next standalone output and an Electron-owned production frontend lifecycle.
2. Packaged QA could otherwise read normal settings/model-router state. Fixed with complete isolated path overrides and Electron user-data isolation.
3. Restore results were transient in Apply History/Detail. Fixed by loading persisted restore history and joining by rollback ID.
4. History load failures appeared as an empty list. Fixed with explicit loading, empty, normalized error, and Retry states.
5. Detail had no independent loading/not-found/error state. Fixed with typed detail loading and Retry handling.
6. History action buttons clipped in the narrow Settings pane. Fixed with a contained single-column action layout.
7. Detail status badges could clip. Fixed with a dedicated wrapping status row.

## Verification

| Command | Result |
| --- | --- |
| `python -m compileall backend` | PASS |
| `python -m pytest` | PASS: 1356 passed, 7 skipped |
| `npm.cmd --prefix frontend run typecheck` | PASS |
| `npm.cmd --prefix frontend run build` | PASS |
| `npm.cmd run build:electron` | PASS |
| `npm.cmd run test:electron` | PASS: 10 passed |
| `npm.cmd run test:qa-packaged` | PASS: 3 passed |
| `npm.cmd run test:ui-state` | PASS: 2 passed |
| `npm.cmd run qa:safety-scan` | PASS |
| `npm.cmd run qa:desktop:packaged -- --reset` | PASS |
| `npm.cmd run qa:desktop:packaged -- --smoke` | PASS |

## Remaining Limitations

- This is an unpacked QA package, not a signed installer.
- The packaged backend uses the compatible host Python installation and installed Python dependencies.
- Code signing, Python bundling, automatic updates, and publishing remain out of scope.
- No binary, rename, chmod, symlink, mode-only, or submodule patch support was added.

## Final Safety Statement

Apply remains feature-flagged.
Restore remains feature-flagged.
Production defaults remain disabled.
No bridge routing was added.
No Codex, Claude, or OpenCode execution was added.
No auto-build or auto-flash was added.
No patch safety checks were weakened.
Packaged QA used isolated state and a throwaway workspace.
