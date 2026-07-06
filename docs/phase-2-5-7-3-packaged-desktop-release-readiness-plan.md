# Phase 2.5.7.3 Packaged Desktop Release-Readiness Plan

## Packaging Architecture Findings

- The repository has no installer, `electron-builder`, or unpacked Windows packaging command. `build:electron` only compiles Electron TypeScript into `dist/electron`.
- Development mode is selected through `app.isPackaged === false`. It loads `FORGEX_FRONTEND_URL` or `http://localhost:3000` and reuses or starts the Python backend.
- Packaged mode currently resolves its application root as `path.dirname(process.resourcesPath)`. It looks for `frontend/out/index.html` or `dist/frontend/index.html` beside the executable.
- The Next.js frontend is not configured for static export. Its API calls use `/api/promptforge`, which depends on Next rewrites, so a `file://` renderer cannot currently reach the backend.
- The practical packaged renderer path is therefore a production Next standalone server, started and owned by Electron. This is a production server, not the development frontend.
- The Python backend is launched with `python -m uvicorn` from the packaged application root. A QA unpacked package must stage `backend/` beside the executable and still depends on a compatible host Python environment.
- `PROMPTFORGE_ROOT` controls managed workspace, bridge, apply, rollback, and restore state. General settings and model-router state use separate `%APPDATA%` defaults unless `FORGEX_SETTINGS_PATH` and `FORGEX_MODEL_ROUTER_SETTINGS_PATH` are set.
- Electron desktop data uses `app.getPath("userData")`. Packaged QA must also pass an isolated `--user-data-dir`.
- Apply History is persisted under `<PROMPTFORGE_ROOT>/.promptforge/state/patch-applies`. Restore results are persisted separately under `rollback-restores` and must be joined by `rollback_id` in the UI to survive restart.
- The packaged root and frontend resolution are intentionally independent of the repository working directory once the unpacked layout is staged.

## Implementation Scope

1. Configure Next.js standalone output and add an Electron-owned production frontend lifecycle.
2. Add a repeatable Windows unpacked QA package builder/launcher using the installed Electron runtime.
3. Isolate QA workspace, bridge state, settings, model-router data, Electron data, ports, and logs under `.promptforge/qa/phase-2-5-7-3/`.
4. Seed sanitized canonical apply/restore fixtures only in QA mode and isolated state.
5. Join persisted restore results into Apply History and Apply Detail; add explicit loading, empty, unavailable, and normalized error states.
6. Validate production-default flags, the four flag combinations, restart persistence, malformed fixture handling, backend failure behavior, logs, and screenshots.
7. Add focused Electron/backend tests and run the existing verification suite.

## Safety Boundaries

- Production apply and restore defaults remain disabled.
- QA fixtures require `FORGEX_QA_MODE=1` and are written only below the contained packaged QA root.
- Reset and cleanup refuse targets outside `.promptforge/qa/phase-2-5-7-3/`.
- The harness tracks and stops only processes it starts.
- Fixture metadata contains sanitized IDs, relative filenames, hashes, and normalized messages only. It contains no patch text, file content, raw workspace path, credential, or audit record.
- No bridge routing, agent execution, automatic apply, automatic build, or automatic flash is introduced.

## Verification Strategy

- Unit-test frontend/backend lifecycle ownership and packaged path resolution without requiring a real window.
- Test malformed apply/restore records through existing service readers.
- Run the unpacked package with isolated ports and state, confirm backend and renderer health, inspect the renderer through QA-only remote debugging, restart, and compare persisted IDs/counts.
- Capture only sanitized Settings and Apply History/Detail surfaces.
