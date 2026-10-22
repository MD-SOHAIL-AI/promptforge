# Phase 2.2 Settings Center

## Summary

Phase 2.2 moves Model Router configuration out of the right-side tool panel and into a main Settings Center. The right panel now focuses on active project tools only:

- AI Assistant
- Device Tools

The Settings Center opens from the activity-bar Settings icon, the top-bar Settings button, and the top-bar model indicator. The model indicator opens Settings directly to the Models category.

## Settings Categories

Implemented categories:

- General
- Appearance
- Models
- Editor
- Terminal
- Workspace
- Hardware
- Security
- About

## Backend Settings API

Added non-secret settings routes:

- `GET /settings`
- `GET /settings/schema`
- `PATCH /settings`
- `POST /settings/reset`
- `POST /settings/export`

Settings are persisted in backend-owned app data, not in project folders. The path can be overridden with `FORGEX_SETTINGS_PATH` for tests and development.

## Secret Handling

Model provider API keys remain in the existing Model Router provider settings storage. The general settings export returns only non-secret settings and includes `secrets_included: false`.

## Theme System

Appearance settings now apply CSS variables through `data-theme` on the document root. Implemented themes:

- `forgex-dark`
- `forgex-midnight`
- `light`
- `system`

The shell, activity bar, settings page, right panel, and terminal surface use the theme variables. Monaco internals are only lightly integrated in this phase.

## Model Settings Migration

The existing Phase 2.1 Model Router UI is reused inside Settings -> Models. It still supports:

- Active route summary
- Provider cards
- Provider health checks
- Model refresh
- Task routes
- Usage list
- Masked API keys

## Editor Settings

Safely wired Monaco settings:

- Font size
- Tab size
- Word wrap
- Minimap
- Line numbers

Other editor settings are persisted for later runtime integration.

## Known Limits

- Clear-all provider secrets and clear usage history are shown as disabled security actions because no safe bulk backend endpoints exist yet.
- Some older IDE components still contain hardcoded colors and will need gradual migration to CSS variables.
- Board registry and subscription bridges remain intentionally unimplemented.

## Phase 2.3 Follow-up

Phase 2.3 completed visual QA and stabilization for the Settings Center, right panel, model settings, and active IDE theme surfaces. See `docs/phase-2-3-visual-qa-report.md` for screenshots, fixes, verification commands, and remaining limitations.
