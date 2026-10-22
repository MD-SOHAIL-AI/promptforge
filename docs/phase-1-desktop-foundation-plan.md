# Phase 1 Desktop Foundation Plan

Generated: 2026-06-17

## Current Structure

ForgeX V1 is currently a web frontend plus Python backend:

```text
backend/
  api/app.py              FastAPI app factory and dependency composition
  api/routes/*            existing HTTP and WebSocket routes
  main.py                 canonical workflow composition
frontend/
  app/*                   Next.js app router
  components/*            workspace UI
  hooks/*                 frontend workspace state
  lib/api.ts              browser fetch client using /api/promptforge rewrites
  lib/websocket.ts        direct WebSocket client to backend port 8000
  next.config.ts          rewrites /api/promptforge/* to PROMPTFORGE_API_URL or 127.0.0.1:8000
```

There is no root `package.json`; root `package-lock.json` is effectively empty. Frontend scripts are scoped inside `frontend/package.json`.

Existing backend routes that must remain stable:

- `GET /health`
- `POST /execute`
- `GET /projects`
- `GET /projects/{project_id}`
- `DELETE /projects/{project_id}`
- `GET /projects/{project_id}/files`
- `GET /files/content`
- `POST /files`
- `PUT /files`
- `DELETE /files`
- `POST /build`
- `POST /flash`
- `POST /monitor/start`
- `POST /monitor/stop`
- `GET /monitor/status`
- `GET /build-history`
- `GET /logs`
- `WS /ws/execution/{task_id}`

## Proposed Electron Structure

Add Electron beside the existing codebase:

```text
electron/
  main.ts                 Electron app entry; starts backend and window
  preload.ts              minimal safe renderer bridge
  backend-manager.ts      FastAPI child process lifecycle and health polling
  window-manager.ts       BrowserWindow creation and frontend loading
  paths.ts                repo/app/backend/frontend/data path helpers
  ipc.ts                  minimal desktop status/health IPC
  tsconfig.json           Electron TypeScript build config
```

Compiled Electron output will go to:

```text
dist/electron/
```

The Electron renderer will continue to load the existing Next.js frontend. In development it loads `http://localhost:3000`. The frontend continues to use existing HTTP rewrites and direct WebSocket behavior.

## Scripts To Add

Create a root `package.json` with scripts:

- `dev:backend`: starts FastAPI on `127.0.0.1:8000`.
- `dev:frontend`: starts Next.js from `frontend/`.
- `build:electron`: compiles TypeScript in `electron/`.
- `dev:electron`: compiles Electron and launches `electron .`.
- `dev:desktop`: runs backend, frontend, waits for both, then launches Electron.

Root dev dependencies:

- `electron`
- `concurrently`
- `wait-on`
- `typescript`
- `@types/node`

## Files To Modify

Planned changes:

- Add `package.json`.
- Update root `package-lock.json` via `npm install` when dependencies are available.
- Add `electron/*`.
- Add `docs/phase-1-desktop-foundation.md`.
- Add `service` field to the health schema/response if done without breaking existing health consumers.

No planned changes:

- No backend workflow rewrite.
- No frontend redesign.
- No model router.
- No agent runtime.
- No board registry.
- No marketplace/cloud sync.

## Runtime Behavior

Desktop development flow:

```text
npm run dev:desktop
  -> FastAPI backend starts on 127.0.0.1:8000
  -> Next.js frontend starts on localhost:3000
  -> wait-on verifies frontend and backend health
  -> Electron opens BrowserWindow at localhost:3000
```

If Electron starts a backend itself through `backend-manager.ts`, it owns that child process and stops it during app shutdown. In `dev:desktop`, the concurrently-managed backend is preferred and Electron should reuse it rather than launch a duplicate.

## Risks

- Electron dependencies require a root Node install.
- `wait-on` package versions may require newer Node versions; pin compatible versions if needed.
- Python executable may differ across environments (`python` vs `py`).
- Uvicorn import path must run from repository root.
- WebSocket URL in frontend defaults to backend port 8000 and should continue to work in dev.
- Packaged production mode will need more work to bundle Python/backend and static frontend assets.

## Rollback Plan

Rollback is straightforward because Electron is additive:

1. Remove `electron/`.
2. Remove root `package.json`.
3. Revert root `package-lock.json`.
4. Revert health schema/route additions if necessary.
5. Keep existing `frontend/package.json`, Next.js frontend, FastAPI backend, and routes untouched.

