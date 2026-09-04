# Phase 2.5.2 Safe Bridge Auth Status Refinement

## Summary

ForgeX bridge detection now has an explicit auth/status policy. It can safely refine auth state when a documented, non-mutating status command is available and confirmed by help output. If not, it keeps auth as `unknown` and explains why.

Prompt execution is still disabled. Bridge providers still cannot generate code, edit files, stream prompts, or participate in model routing.

## Allowed Checks

- `where` / `which`
- `<tool> --version`
- `<tool> --help`
- documented non-mutating status commands declared by the detector

All commands use subprocess argv arrays, `shell=False`, timeouts, and capped stdout/stderr.

## Forbidden Checks

ForgeX does not:

- read token files
- read session files
- read browser cookies
- inspect OAuth stores
- trigger login
- open browser auth flows
- run bridge prompts
- modify project files
- route generation through bridges

## Auth May Remain Unknown

Codex, Claude Code, and Antigravity / AGY CLI currently report `unknown` unless a future detector declares a verified safe status command. This is intentional. Unknown is safer than guessing or probing private auth state.

Provider messages:

- Codex: ForgeX cannot safely confirm login without a documented non-mutating status command.
- Claude Code: ForgeX cannot safely confirm authentication in detection-only mode.
- Antigravity / AGY CLI: ForgeX cannot safely confirm authentication in detection-only mode.

## Status Confidence

- `high`: parsed from a safe status command with clear output.
- `medium`: a safe status command ran, but output was not clearly authenticated or unauthenticated.
- `low`: no safe status command was available, or the status check timed out/failed.

## Setup Flow

Users should install and authenticate each official tool outside ForgeX. ForgeX does not collect credentials and does not store subscription tokens.

Setup actions:

- `open_docs`
- `run_official_login_manually`
- `none`

## API

Existing routes now include richer status fields:

- `GET /models/bridges`
- `POST /models/bridges/refresh`
- `GET /models/bridges/{provider_id}`

No run endpoint exists.

## UI

Settings -> Models -> Tool Bridges now shows:

- Installed: Yes/No
- Version
- Auth
- Confidence
- Setup action
- Checked safe commands
- Execution disabled warning
- Credential privacy reminder

## Future Execution Requirements

Before any bridge execution phase, ForgeX must add explicit user approval, workspace containment, command allowlists, environment filtering, cancellation, output caps, and file-diff review. Those capabilities are not part of this phase.
