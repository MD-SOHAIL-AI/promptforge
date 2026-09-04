# Phase 2.3.2 - Forge Panel, Cancel, Terminal, Startup Plan

## Current Issues

- The right panel still uses the visible `AI Assistant` identity instead of `Forge`.
- Running workflows cannot be intentionally cancelled from the UI.
- Long user prompts can dominate the Forge panel and push plan/status content out of view.
- The bottom `Terminal` tab is a passive synthesized log view, not an interactive shell.
- Electron opens DevTools automatically in development unless explicitly disabled.

## Cancel Approach

Implement a small process-local execution registry inside the existing `/execute` route module.

- Track the background asyncio workflow task by `task_id`.
- Add `POST /execute/{task_id}/cancel`.
- On cancel, mark the task cancelled, cancel the asyncio task, call the shared subprocess manager cleanup path, emit `WORKFLOW_CANCELLED`, and close progress subscribers as a terminal event.
- Treat `asyncio.CancelledError` separately from ordinary failures so intentional cancellation is not reported as `FAILED`.

This is scoped to current process execution and does not introduce durable job orchestration.

## Terminal Architecture

Implement a backend-managed interactive shell session:

- Add `backend/services/terminal_service.py`.
- Add `backend/api/routes/terminal.py`.
- Start PowerShell on Windows and a normal shell on POSIX.
- Start in the active project path supplied by the frontend.
- Stream shell output into a bounded queue.
- Send command input to the running process stdin.
- Provide restart, clear, read, and terminate endpoints.

The frontend Terminal tab will render a real interactive shell session with command input and output. This avoids fake terminal logs and avoids introducing native Electron dependencies in this stabilization phase.

Risk: this is not full PTY terminal emulation yet. Some commands that require a TTY may behave differently. PowerShell, `pwd`, `dir`, and ordinary command input are expected to work.

## Forge Panel Layout

Update the right panel to:

- Rename visible assistant labels to `Forge`.
- Clamp `User Goal` with internal scrolling and an expand/collapse button.
- Keep `Assistant Plan` visible above execution status and recent output.
- Add explicit Start and Cancel controls.
- Show `CANCELLED` for intentional cancellation.

## DevTools Default

Change Electron startup behavior:

- Default: do not open DevTools.
- Open only when `FORGEX_OPEN_DEVTOOLS` is one of `1`, `true`, or `yes`.

## Files To Modify

- `frontend/components/ide/ai-assistant-panel.tsx`
- `frontend/components/ide/forgex-shell.tsx`
- `frontend/components/ide/bottom-panel.tsx`
- `frontend/hooks/use-promptforge-workspace.ts`
- `frontend/lib/api.ts`
- `frontend/types/index.ts`
- `backend/api/routes/execute.py`
- `backend/api/routes/websocket.py`
- `backend/api/schemas/websocket.py`
- `backend/api/app.py`
- `electron/window-manager.ts`

Files to add:

- `backend/services/terminal_service.py`
- `backend/api/routes/terminal.py`
- `frontend/components/ide/integrated-terminal.tsx`
- focused backend tests if existing fixtures support the new routes
- `docs/phase-2-3-2-forge-terminal-cancel.md`

## Risks

- Cancelling shared subprocesses through the existing manager may stop another active subprocess if concurrent workflows are running. Current desktop UX is single-user/single-active-task, so this is acceptable for stabilization.
- The terminal subprocess must be constrained to explicit workspace/project directories supplied by ForgeX; arbitrary file-system browsing is not being added.
- The first terminal implementation is process-backed interactive PowerShell, not a full terminal emulator.

## Rollback Plan

- Remove the new terminal router/service and frontend terminal component.
- Remove the cancel route and task registry from `execute.py`.
- Revert the Forge panel layout changes.
- Restore the previous DevTools conditional if needed.
