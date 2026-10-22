# Phase 2.3.2 - Forge Panel, Cancel Workflow, Terminal, Startup Fix

## Summary

Phase 2.3.2 stabilized the right-side Forge panel, added intentional workflow cancellation, replaced the passive Terminal tab with an interactive PowerShell-backed terminal session, and stopped Electron DevTools from opening by default.

## Files Created

- `backend/api/routes/terminal.py`
- `backend/services/terminal_service.py`
- `frontend/components/ide/integrated-terminal.tsx`
- `docs/phase-2-3-2-forge-terminal-cancel-plan.md`
- `docs/phase-2-3-2-forge-terminal-cancel.md`

## Files Modified

- `backend/api/app.py`
- `backend/api/progress.py`
- `backend/api/routes/execute.py`
- `backend/api/routes/websocket.py`
- `backend/api/schemas/websocket.py`
- `electron/window-manager.ts`
- `frontend/components/ide/ai-assistant-panel.tsx`
- `frontend/components/ide/bottom-panel.tsx`
- `frontend/components/ide/forgex-shell.tsx`
- `frontend/components/workflow/workflow-timeline.tsx`
- `frontend/hooks/use-promptforge-workspace.ts`
- `frontend/lib/api.ts`
- `frontend/lib/workflow-stage-state.ts`
- `frontend/types/index.ts`
- `tests/api/test_api_routes.py`
- `tests/api/test_websocket.py`

## Forge Rename

Visible right-panel identity was changed from `AI Assistant` to `Forge`.

The TypeScript component name remains `AiAssistantPanel` to avoid unrelated file churn.

## Prompt Layout

The Forge panel now keeps the important workflow areas visible:

- `User Goal` is clamped by default.
- Long goals get `Show more` / `Show less`.
- Expanded goals scroll internally.
- `Assistant Plan`, execution controls, status, and recent output remain accessible.

## Cancel Flow

Added:

- `POST /execute/{task_id}/cancel`
- `WORKFLOW_CANCELLED` websocket/progress event
- `CANCELLED` frontend terminal status
- Forge panel `Cancel` button while workflows are running
- `Cancelling...` state while the cancel request is in flight

Cancellation now cancels the tracked asyncio workflow task and calls the existing subprocess manager cleanup path.

Live verification returned:

```text
start_status: RUNNING
cancel.status: CANCELLED
```

## Integrated Terminal

The Terminal tab now starts a backend-managed interactive shell session in the active project root.

On Windows, the shell is PowerShell.

Verified live commands:

```powershell
pwd
dir
```

The terminal output included the active project path and a PowerShell prompt:

```text
PS C:\Users\diwan\OneDrive\Desktop\promptforge\workspace\projects\...
```

The Output tab remains separate for structured workflow logs, and Serial Monitor remains separate for serial logs.

## DevTools Startup

Electron no longer opens DevTools by default in development.

DevTools now opens only when:

```text
FORGEX_OPEN_DEVTOOLS=1
FORGEX_OPEN_DEVTOOLS=true
FORGEX_OPEN_DEVTOOLS=yes
```

Final dev startup reached:

```text
[forgex-desktop] Renderer did-finish-load
```

No Electron `openDevTools` path was triggered.

## Tests And Verification

Commands run:

```text
python -m compileall backend
python -m pytest
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm run build:electron
npm run dev:desktop
```

Results:

- Backend compile passed.
- Backend tests passed: `1132 passed, 3 skipped`.
- Frontend typecheck passed.
- Frontend production build passed with no warnings.
- Electron build passed.
- Final desktop dev stack is running.
- `http://localhost:3000` returned `200 OK`.
- `http://localhost:8000/health` returned `200 OK`.

## Remaining Limitations

The terminal is an interactive PowerShell-backed subprocess, not full PTY/xterm.js emulation yet. Standard PowerShell commands work, but TTY-specific commands may not behave exactly like a native terminal until a later PTY upgrade.

Cancellation currently uses the process-local active workflow registry and shared subprocess manager cleanup. That fits the current desktop single-active-workflow UX, but a future multi-session runner should cancel subprocesses by session ID rather than using shared cleanup.
