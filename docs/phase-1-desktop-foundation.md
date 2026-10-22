# Phase 1 Desktop Foundation

Generated: 2026-06-17

## What This Adds

Phase 1 adds an Electron desktop shell beside the existing ForgeX V1 web/backend system. It does not rewrite the backend, replace the frontend, add model routing, add autonomous agents, add a board registry, or redesign the UI.

Runtime shape:

```text
Electron main process
  -> starts or reuses FastAPI backend
  -> opens existing Next.js frontend
  -> frontend continues to call existing backend routes
```

## Files Added

```text
electron/
  main.ts
  preload.ts
  backend-manager.ts
  window-manager.ts
  paths.ts
  ipc.ts
  tsconfig.json
```

Root desktop scripts are in `package.json`.

## How To Run Desktop Dev Mode

Install root desktop dependencies:

```powershell
npm.cmd install
```

Install frontend dependencies if needed:

```powershell
cd frontend
npm.cmd install
cd ..
```

Run the desktop development stack:

```powershell
npm.cmd run dev:desktop
```

This starts:

- FastAPI at `http://127.0.0.1:8000`
- Next.js at `http://localhost:3000`
- Electron loading `http://localhost:3000`

## Individual Scripts

```powershell
npm.cmd run dev:backend
npm.cmd run dev:frontend
npm.cmd run build:electron
npm.cmd run dev:electron
```

`dev:electron` compiles `electron/*.ts` to `dist/electron/*.js`, then runs `electron .`.

## Backend Startup

`electron/backend-manager.ts` checks `GET /health` first. If a backend is already healthy on the configured port, Electron reuses it. If no backend is reachable, Electron starts:

```powershell
python -m uvicorn backend.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

The backend port defaults to `8000` and can be overridden with:

```powershell
$env:FORGEX_BACKEND_PORT="8000"
```

Closing Electron stops the backend only when Electron started that backend process. In `dev:desktop`, `concurrently` owns backend/frontend processes and stops them when Electron exits.

## Frontend Connection

The existing frontend remains browser-compatible:

- HTTP calls use `/api/promptforge/*`.
- `frontend/next.config.ts` rewrites those calls to `PROMPTFORGE_API_URL` or `http://127.0.0.1:8000`.
- WebSocket progress still connects to backend port `8000` by default.

No production-only API path is hardcoded.

## Electron Security Defaults

The main window uses:

```ts
nodeIntegration: false
contextIsolation: true
sandbox: false
preload: preloadPath
```

The preload exposes only:

```ts
window.forgexDesktop.getStatus()
```

It does not expose filesystem or shell access.

## Health Check

`GET /health` remains backward-compatible and still returns the existing `healthy/degraded` status contract. It now also includes:

```json
{
  "service": "forgex-backend"
}
```

Electron accepts any successful HTTP response from `/health` as backend readiness.

## Known Limitations

- Packaged production distribution is not complete in Phase 1.
- Electron can load a future static frontend build if present, but the current supported path is development mode with Next.js on port `3000`.
- Python/backend bundling is not implemented.
- No native device permission UI is implemented yet.
- No model router, agent runtime, board registry, device manager, approval system, marketplace, or cloud sync is included.

## Next Phase Notes

Phase 2 can build on this by adding a model router behind the existing backend service boundary. Hardware approval, board registry, and agent runtime should remain separate later phases.

