# ForgeX Desktop One-Command Startup Validation

## Goal

The normal development workflow is self-contained after first-time dependency installation:

```bash
npm run dev:desktop
```

No manual `.venv` activation and no separate `npm run dev:backend` are required.

## Root cause / existing behavior

ForgeX already had the correct lifecycle ownership: `scripts/dev-desktop.mjs` starts/reuses Next.js and launches Electron; Electron `BackendManager` owns FastAPI startup/reuse and cleanup. The remaining weaknesses were lifecycle hardening rather than a missing backend launch:

- a present but broken repository `.venv` could be hidden by falling back to a global Python;
- synchronous Uvicorn spawn failures were not normalized into backend state;
- readiness failure could leave an owned backend process alive until the desktop error window exited;
- backend ownership was not cleared immediately when the child exited unexpectedly;
- Windows launcher shutdown killed only the wrapper process and could leave descendant Next/Electron processes behind;
- packaged-path resolution was host-path-style dependent in cross-platform tests;
- setup docs still told developers to activate the virtual environment manually.

## Python resolution order

Windows:

1. `FORGEX_PYTHON`
2. `<repo>/.venv/Scripts/python.exe`
3. `<repo>/venv/Scripts/python.exe`
4. `python`
5. `py`

Linux/macOS:

1. `FORGEX_PYTHON`
2. `<repo>/.venv/bin/python`
3. `<repo>/venv/bin/python`
4. `python3`
5. `python`

Candidates are checked for executability and for `fastapi` + `uvicorn`. If a repository venv exists but is broken/missing dependencies, ForgeX fails with the detected interpreter and exact install command instead of silently using unrelated global Python.

## Backend lifecycle

- Electron probes the configured backend port before spawning.
- A compatible healthy ForgeX backend can be reused when it satisfies the current runtime/source/auth contract.
- Non-ForgeX, stale, unhealthy, timed-out, or incompatible services are rejected with actionable messages.
- When Electron starts Uvicorn, it records ownership and waits for `/health` before reporting readiness.
- Only Electron-owned backend processes are stopped on normal Electron shutdown.
- Reused/external backend processes are not killed by `BackendManager.stop()`.
- Failed readiness now terminates an owned backend before returning the startup error.
- Unexpected child exit/error clears backend ownership.
- Windows `dev:desktop` shutdown terminates launcher-owned process trees to reduce stale Next/Electron descendants.

## Files changed

- `electron/python-resolver.ts`
- `electron/python-resolver.test.mjs`
- `electron/backend-manager.ts`
- `electron/backend-manager.test.mjs`
- `electron/paths.ts`
- `scripts/dev-desktop.mjs`
- `.env.example`
- `README.md`
- `docs/phase-2-5-1-1-dev-stack-lifecycle-stabilization.md`
- `FORGEX_DESKTOP_STARTUP_VALIDATION.md`

## Validation

Executed in the delivery environment:

- Backend regression: **1384 passed**
- Focused Electron lifecycle tests: **28 passed**
- Focused frontend/UI-state tests: **26 passed**
- Changed Electron TypeScript transpile/syntax validation: **PASS**
- `node --check scripts/dev-desktop.mjs`: **PASS**
- Python `compileall` for backend/scripts/tests: **PASS**

A normal `npm run test:electron` / frontend `typecheck` requires the ZIP's npm dependencies to be installed. The delivery environment's npm dependency restore timed out, so those full dependency-backed npm commands must be rerun after `npm ci` and `npm --prefix frontend ci` on the target machine.

## First-time Windows setup

```bash
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci
npm --prefix frontend ci
```

## Normal development

```bash
npm run dev:desktop
```
