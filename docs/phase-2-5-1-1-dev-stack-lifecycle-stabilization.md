# Phase 2.5.1.1 Dev Stack Lifecycle Stabilization

## Root Cause

`npm run dev:desktop` launched a standalone backend process before Electron started. If an old ForgeX backend was already listening on `127.0.0.1:8000`, that standalone backend failed with an address-in-use error and `concurrently` stopped the whole dev stack before Electron could reuse the healthy backend.

## Backend Lifecycle Behavior

Electron now owns the desktop backend lifecycle:

1. Probe `http://127.0.0.1:${FORGEX_BACKEND_PORT}/health`.
2. Reuse the process only when the health response has `service: "forgex-backend"` and `status: "healthy"`.
3. Reject any reachable service that is not ForgeX.
4. Reject an existing ForgeX backend that responds but is not healthy.
5. Spawn a new backend only when no service is reachable.
6. Track the spawned PID in backend status.
7. Stop only the backend process spawned by the current Electron run.

## Port Conflict Behavior

If an unrelated process owns the backend port, ForgeX reports:

```text
Port 8000 is already in use by another process.
Close that process or configure a different ForgeX backend port.
```

If health probing times out, ForgeX reports that the port may be occupied by an unresponsive service instead of waiting silently.

## Process Cleanup Behavior

Electron sends `SIGTERM` only to the child backend process it spawned. It does not kill unrelated Python processes and does not kill an existing backend it chose to reuse.

## Port Configuration

The backend port can be configured with:

```text
FORGEX_BACKEND_PORT=8000
```

`PROMPTFORGE_BACKEND_PORT` is still accepted for compatibility. The frontend dev proxy reads the same port when `PROMPTFORGE_API_URL` is not explicitly set.

## Dev Scripts

- `npm run dev:desktop` starts the frontend and Electron. Electron starts or reuses the backend.
- `npm run dev:backend` starts only the backend on the configured port.
- `npm run dev:cleanup` inspects listeners on ports `8000` and `3000` without killing anything by default.

To inspect manually on Windows:

```powershell
netstat -ano | findstr :8000
netstat -ano | findstr :3000
```

To run the cleanup helper:

```powershell
npm run dev:cleanup
```

To opt into explicit per-PID kill prompts:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/dev-cleanup.ps1 -Kill
```

## Tests Run

- `npm run test:electron`
- `npm run build:electron`
- Full backend/frontend verification is recorded in the implementation handoff.

## Remaining Limitations

The cleanup helper only reports process IDs and optional explicit kills. It does not infer process ownership beyond the port listener and process name, by design.
