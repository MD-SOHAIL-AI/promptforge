# Phase 2.3.4 Workflow State Semantics

## Previous Wrong Behavior

ForgeX treated an optional hardware failure as a total workflow failure:

```text
Planning success
Generation success
Build success
Flash failed because no board was connected
Workflow failed
```

That hid the important result: firmware generation and PlatformIO build had already succeeded.

## New Status Model

Software work and hardware work are now separated.

Required software stages:

- Planning
- Generation
- Build

Optional hardware stages:

- Board detection
- Flash
- Monitor

The backend now supports a terminal workflow state:

```text
COMPLETED_WITH_PENDING_HARDWARE
```

Frontend stage statuses now include:

```text
blocked
skipped
waiting_for_device
```

## Required vs Optional Stages

Default firmware generation now plans:

```text
Generation -> Build
```

It does not automatically flash or monitor. Flash and monitor remain available through explicit user actions, and explicit flash/monitor requests still fail clearly when hardware is missing.

For compatibility with older or injected plans that still include optional hardware after build, ForgeX normalizes no-device hardware failures into:

```text
Flash: waiting_for_device
Workflow: COMPLETED_WITH_PENDING_HARDWARE
```

## No-Device Handling

If generation and build succeed but no matching board is detected, the user-facing message is:

```text
Firmware generated and built successfully. Connect an ESP32 board to flash.
```

Manual Flash remains strict. If the user explicitly clicks Flash and the requested board is absent, the API returns:

```text
No matching ESP32 device detected. Connect a board and try again.
```

## Frontend Changes

The Forge panel now maps hardware-pending workflow events to non-failure UI states:

- Flash shows `waiting_for_device`
- Overall status shows `BUILD SUCCEEDED`
- Hardware line shows `Waiting for device`

Cancelled workflows still render as cancelled, not failed.

## Test Results

Passed:

- `python -m compileall backend`
- `python -m pytest`
- `npm.cmd --prefix frontend run typecheck`
- `npm.cmd --prefix frontend run build`
- `npm.cmd run build:electron`

Desktop verification:

- ForgeX was relaunched with the updated backend and frontend.
- Frontend is listening on port 3000.
- Backend is listening on port 8000.
- Electron processes are running.
