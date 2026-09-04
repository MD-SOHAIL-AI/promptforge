# Phase 2.5.2 Safe Bridge Auth Status Refinement Plan

## Goal

Improve Tool Bridge auth/status reporting without executing prompts, editing files, reading credentials, or routing generation through bridges.

## Policy Layer

Add `backend/bridges/auth_policy.py` with explicit allowed and forbidden checks.

Allowed:

- PATH executable discovery
- version commands
- help commands
- documented non-mutating status commands
- bounded timeouts
- capped stdout/stderr
- subprocess argv arrays with `shell=False`

Forbidden:

- reading auth config or token files
- reading browser cookies
- reading OAuth session stores
- writing to home directory
- launching login automatically
- sending prompts
- running commands that modify project files

If a command is not clearly safe, ForgeX reports `auth_status: "unknown"`.

## Detection Flow

For each installed bridge:

1. Discover executable.
2. Run bounded `--version`.
3. Run bounded `--help`.
4. Run a status command only when the provider declares it as safe and help output confirms it.
5. Parse only simple authenticated/unauthenticated indicators.
6. Return unknown when output is unclear.

Real Codex, Claude Code, and Antigravity / AGY CLI detectors remain conservative because no status command is currently hard-coded as safe for this phase.

## API Fields

Bridge responses include:

- `auth_status`
- `status_confidence`
- `auth_message`
- `setup_hint`
- `setup_action`
- `safe_status_checked`
- `checked_commands`

Raw status output is not exposed in the API or UI.

## UI

Settings -> Models -> Tool Bridges shows:

- installed status
- version
- auth status
- confidence
- execution disabled
- setup action
- privacy guidance

## Tests

Add coverage for:

- unknown auth when no safe status command exists
- safe status output mapping to authenticated
- safe status output mapping to unauthenticated
- interactive commands not being run
- timeout handling
- output caps
- `shell=False`
- disabled bridge capabilities
- bridge IDs still rejected by model routing

## Next Phase

The next safe step is to verify official documentation for each CLI and add provider-specific status commands only where the command is documented as non-mutating and non-interactive.
