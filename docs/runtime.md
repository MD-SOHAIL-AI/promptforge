# ForgeX Runtime

This document describes the current V4 desktop runtime. Older phase reports in
`docs/phase-*` and the root V1 analysis files are historical evidence, not the
current release contract.

## Desktop process model

- `npm run dev:desktop` starts or reuses the Next.js frontend, then launches Electron.
- Electron owns the FastAPI backend lifecycle in desktop mode.
- The backend requires `FORGEX_LOCAL_TOKEN` when `FORGEX_DESKTOP=1`; Electron generates and passes that token.
- `/health` stays public for process identity checks. Other local API/WebSocket routes require the desktop token boundary.

## Agent runtime

- Forge Agent V3 is the default runtime loop.
- Desktop release mode rejects `FORGEX_ENABLE_AGENT_V3_LOOP=0`.
- Models act as planners. ForgeX owns file changes, builds, serial, and flash operations.
- Hardware flashing remains explicit and approval-bound.

## Release checks

- `npm run verify:beta` runs the non-destructive software gate.
- `npm run scan:release -- <zip-or-staging-dir>` scans a strict release artifact.
- `npm run scan:source-release` checks the local development checkout while ignoring disposable generated artifacts.
- `npm run signoff:esp32 -- --project <platformio-project> --port COM7` records physical ESP32 build/flash/serial evidence.
