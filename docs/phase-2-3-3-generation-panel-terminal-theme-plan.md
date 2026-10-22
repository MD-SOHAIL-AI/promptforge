# Phase 2.3.3 Stabilization Plan

## Scope

Fix IDE stability issues found during manual testing without adding subscription bridges, agents, board registry, cloud sync, marketplace, or unrelated architecture.

## Build Workflow

Root cause: the workflow build invocation still passed non-generated workspace state through the `GeneratedProject` adapter. External/open-folder workspaces can have a valid `platformio.ini` on disk without a managed generated-project model, which produced the internal error `project must be a GeneratedProject`.

Plan:

- Keep `GeneratedProject` validation for managed generation.
- Add a workspace-root build resolver that parses on-disk `platformio.ini`.
- Resolve build input in this order:
  1. generated project object and its materialized root
  2. execution context project path
  3. active workspace root metadata
  4. clear missing-`platformio.ini` error
- Add tests for external PlatformIO roots, generic folders, and build-only external workflows.

## Forge Panel

Plan:

- Keep the composer pinned at the bottom of the Forge panel.
- Clamp long User Goal text with internal scroll and Show more / Show less.
- Ensure all cards use `min-width: 0`, `min-height: 0`, and scoped overflow.
- Keep Start and Cancel visible together, with Cancel enabled only while running.

## Bottom Panel And Terminal

Plan:

- Keep the editor as the priority flex child.
- Default terminal height near 240px.
- Clamp terminal resizing between 120px and 50% of the viewport.
- Add maximize/restore and collapse controls.
- Replace the fake terminal transcript/input UI with xterm.js.
- Use the existing backend terminal session API as the process-backed stream.
- Keep Output tab separate from Terminal tab.

## Theme

Plan:

- Use the existing ForgeX CSS variables for terminal, bottom panel, Forge panel, and shell chrome.
- Improve light theme border and text contrast.
- Add xterm global CSS and map xterm colors to ForgeX variables.

## Startup

Plan:

- Confirm DevTools are gated by `FORGEX_OPEN_DEVTOOLS`.
- Run desktop startup verification and clean up launched dev processes.

## Verification

Run:

- `python -m compileall backend`
- `python -m pytest`
- `npm --prefix frontend run typecheck`
- `npm --prefix frontend run build`
- `npm run build:electron`
- `npm run dev:desktop`
