# Phase 2.2 Settings Center Plan

## Current UI Issue

Phase 2.1 put Model Router configuration in the right-side tool panel beside AI Assistant and Device Tools. That panel is useful for active project operations, but it is too narrow for provider cards, route editing, usage history, and future non-secret settings. The Settings activity icon also exists without opening a real settings surface.

## Target Settings Center

Add a main workbench Settings page opened from the activity bar Settings icon and the top-bar model indicator. The right panel keeps only AI Assistant and Device Tools. Settings categories:

- General
- Appearance
- Models
- Editor
- Terminal
- Workspace
- Hardware
- Security
- About

## Backend Storage Plan

Add backend-owned non-secret settings storage under user app data:

- `%APPDATA%/ForgeX/settings/settings.json` on Windows
- `~/.forgex/settings/settings.json` fallback
- `FORGEX_SETTINGS_PATH` override for tests/development

Model provider API keys remain in the existing Model Router secret storage. Settings export returns non-secret settings only.

## Backend API Plan

Add:

- `GET /settings`
- `GET /settings/schema`
- `PATCH /settings`
- `POST /settings/reset`
- `POST /settings/export`

Settings defaults and schema live in `backend/settings/`. Unknown keys are rejected with a clear API error.

## Frontend Component Plan

Create `frontend/components/settings/` with:

- `settings-page.tsx`
- `settings-sidebar.tsx`
- category components for General, Appearance, Models, Editor, Terminal, Workspace, Hardware, Security, About

Reuse the existing Model Router UI through `settings/model-settings.tsx` instead of duplicating provider logic.

Add a small settings hook/API client for loading, patching, resetting, exporting, and applying appearance settings.

## Theme Plan

Use CSS variables and `document.documentElement.dataset.theme`:

- `forgex-dark`
- `forgex-midnight`
- `light`
- `system`

Theme changes apply without restart where the shell uses variables. Monaco internals remain lightly integrated for this phase.

## Files To Modify

- `backend/api/app.py`
- `backend/api/routes/settings.py`
- `backend/settings/*`
- `frontend/lib/api.ts`
- `frontend/lib/theme.ts`
- `frontend/hooks/use-forgex-settings.ts`
- `frontend/types/index.ts`
- `frontend/components/ide/activity-bar.tsx`
- `frontend/components/ide/top-command-bar.tsx`
- `frontend/components/ide/forgex-shell.tsx`
- `frontend/app/globals.css`
- `frontend/components/settings/*`

## Risks

- Theme variables may not cover every legacy hardcoded color yet.
- Settings API outages must not crash the shell.
- Moving Models out of the right panel must not break top-bar route status or model refresh.
- Settings stored in app data must remain separate from provider secrets.

## Rollback Plan

The change is isolated behind the Settings page and `/settings` API. If needed, revert the Settings Center wiring in `forgex-shell.tsx`, restore the Models right-panel tab, and remove the settings route/service while leaving Model Router storage untouched.
