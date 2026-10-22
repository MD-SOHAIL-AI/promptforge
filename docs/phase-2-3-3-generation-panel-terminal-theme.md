# Phase 2.3.3 Stabilization Report

## Build Workflow Failure

The failure was caused by the workflow build invocation using the old generated-project-only adapter for every build. Build-only external projects and generic open folders after generation can be valid PlatformIO workspaces without needing the build stage to receive a `GeneratedProject`.

The workflow now resolves build roots from:

1. `GeneratedProject` plus its materialized path
2. `ExecutionContext.project_path`
3. `metadata.active_workspace.rootPath`
4. a user-facing error when `platformio.ini` is missing

The replacement error is:

```text
Build requires platformio.ini. Generate or initialize a PlatformIO project first.
```

## Forge Panel Layout

The Forge panel keeps its footer composer pinned and makes the main card area scroll. Long User Goal content is capped by default, can scroll internally, and can be expanded or collapsed. Cards now use `min-width: 0` to avoid narrow-panel overflow.

Start and Cancel remain visible together. Cancel remains disabled outside an active workflow.

## Editor And Bottom Panel

The editor area remains the primary flex child. The bottom panel defaults to 240px, resizes down to 120px, caps at 50% of viewport height, collapses, and has a maximize/restore action.

## Terminal Architecture

Chosen architecture:

```text
FastAPI terminal service
PowerShell subprocess on Windows
terminal session REST stream
xterm.js frontend terminal emulator
```

This avoids adding a new Electron PTY bridge in this stabilization pass while replacing the fake textarea/input terminal with a real terminal emulator surface. The backend no longer injects synthetic startup text or a fake `PS>` prompt; shell output is rendered directly by xterm.

Current terminal support:

- PowerShell on Windows
- active project directory as cwd
- continuous terminal surface
- direct typing inside xterm
- clear, restart, and resize calls
- Output tab remains separate

Limitation: the backend still uses subprocess pipes rather than a true PTY. Interactive command behavior is substantially better in xterm, but full PTY semantics such as advanced console apps are not guaranteed.

## Theme Fixes

Light theme variables were adjusted for stronger text and border contrast. xterm styling is imported globally and its colors are mapped to ForgeX CSS variables.

## DevTools

Electron DevTools remain off by default and are gated by:

```text
FORGEX_OPEN_DEVTOOLS=1
```

The desktop verification command launched and was then stopped after the verification timeout. No ForgeX Electron processes or listeners on ports 3000/8000 remained afterward.

## Tests And Verification

Passed:

- `python -m compileall backend`
- `python -m pytest`
- `npm.cmd --prefix frontend run typecheck`
- `npm.cmd --prefix frontend run build`
- `npm.cmd run build:electron`

Desktop startup:

- `npm.cmd run dev:desktop` launched and ran until the 60s verification timeout.
- Dev process cleanup was performed afterward.

## Manual QA Notes

The new automated coverage includes:

- external PlatformIO build without `GeneratedProject`
- empty open-folder generation followed by build
- missing `platformio.ini` clear error
- advanced ESP32 prompt path generating required workspace files
- successful PlatformIO build result preserving workflow success
