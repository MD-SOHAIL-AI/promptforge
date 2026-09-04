# ForgeX UI Transformation Plan

## Current UI Structure

ForgeX currently uses the PromptForge V1 workspace layout:

- `frontend/app/page.tsx` renders the main workspace shell.
- `frontend/app/layout.tsx` owns page metadata and global shell metadata.
- `frontend/components/layout/workspace-shell.tsx` composes the existing dashboard/workspace UI.
- `frontend/components/sidebar/workspace-sidebar.tsx` provides the old navigation sidebar.
- `frontend/components/explorer/file-explorer.tsx` renders real project files from the backend.
- `frontend/components/code/code-viewer.tsx` preserves Monaco editor tabs, open-file state, edits, and save flow.
- `frontend/components/console/build-console.tsx` displays existing workflow/build/log output.
- `frontend/components/prompt/prompt-command.tsx` starts the existing prompt execution workflow.
- `frontend/components/inspector/task-inspector.tsx` displays current task and stage state.
- `frontend/hooks/use-promptforge-workspace.ts` is the source of truth for projects, files, editor tabs, logs, workflow execution, WebSocket status, and health.
- `frontend/lib/api.ts` contains the existing API client and must keep using current backend routes.

## Target UI Structure

The new UI will keep the existing hook and API behavior while replacing the visible workspace composition with a ForgeX desktop EDE shell:

```text
Top command bar
Activity bar | Explorer or placeholder panel | Editor workbench + bottom panel | AI assistant + device tools
Status bar
```

The shell will provide:

- ForgeX visible branding and desktop IDE styling.
- VS Code-like activity navigation.
- Real project explorer wired to current file APIs.
- Existing Monaco editor flow for open, edit, save, and close.
- Existing prompt/workflow execution surfaced through an AI assistant panel.
- Existing logs/workflow output surfaced in a bottom panel.
- Device/tool cards as UI-only placeholders where direct backend actions are not already exposed.

## Components To Create

Create `frontend/components/ide/` with:

- `forgex-shell.tsx` - main IDE layout using `usePromptForgeWorkspace`.
- `top-command-bar.tsx` - logo, command input, board selector, health, and primary actions.
- `activity-bar.tsx` - vertical IDE activity navigation.
- `ide-explorer.tsx` - ForgeX wrapper around the existing real file explorer.
- `editor-workbench.tsx` - editor region chrome around the existing Monaco editor.
- `ai-assistant-panel.tsx` - existing prompt execution workflow in assistant form.
- `device-tools-panel.tsx` - board/device/tool UI with disabled placeholders for unavailable direct actions.
- `bottom-panel.tsx` - Problems, Output, Terminal, Debug Console, and Serial Monitor views backed by existing logs.
- `status-bar.tsx` - compact IDE status bar.
- `empty-panel.tsx` - polished placeholder for non-wired activity sections.

## Components To Modify

- `frontend/app/page.tsx` - render the new ForgeX shell.
- `frontend/app/layout.tsx` - rename visible metadata from PromptForge to ForgeX.
- `frontend/app/globals.css` - add ForgeX dark IDE layout styles and theme variables.
- `frontend/components/explorer/file-explorer.tsx` - optionally accept project name/collapse props without breaking old callers.
- `frontend/components/layout/app-header.tsx` and `workspace-sidebar.tsx` - rename visible fallback branding to ForgeX.
- `frontend/hooks/use-promptforge-workspace.ts` - rename visible health error text only.
- `frontend/types/index.ts` - allow backend health `service` field.

## Existing Behavior To Preserve

- Existing backend routes and API paths.
- Browser development mode at `http://localhost:3000`.
- Electron desktop compatibility.
- Project loading and project switching.
- Real file tree loading from backend.
- File open, edit, save, tab close, and tab persistence.
- Prompt execution through the current workflow API.
- WebSocket workflow updates.
- Existing logs and build history display.

## Risks

- The new dense layout can expose sizing issues at narrow viewport widths.
- Existing components may contain old PromptForge branding if old fallback shells are opened directly.
- Direct build/upload/monitor buttons may need to remain disabled because no stable frontend client methods are currently exposed for those actions.
- Monaco editor can be sensitive to parent container sizing; editor containers must use stable `min-height: 0` and flex/grid sizing.

## Rollback Plan

Rollback is straightforward because the backend and API client architecture are unchanged:

1. Change `frontend/app/page.tsx` back to rendering `WorkspaceShell`.
2. Leave new `frontend/components/ide/*` files unused or remove them in a follow-up cleanup.
3. Revert visible metadata/branding changes if needed.
4. No database, backend route, Electron main process, or workflow behavior changes are required to roll back this UI layer.
