# Gemini CLI Bridge Plan - Deprecated

## Goal

This document is historical. Gemini CLI is not an active ForgeX bridge target for individual-user bridge planning. ForgeX detects Google Antigravity / AGY CLI instead.

Current active Google bridge plan:

- `docs/antigravity-cli-bridge-plan.md`
- provider ID: `antigravity_cli_bridge`
- commands: `agy`, fallback `antigravity`

ForgeX should not add new active bridge support around the legacy `gemini` CLI.

## Security Boundary

ForgeX must not:

- store Gemini session tokens
- scrape browser cookies
- reverse-engineer Google OAuth
- read private auth files for token extraction
- proxy Gemini subscription usage through ForgeX servers

## Detection

Historical detection would have:

- use configured executable path if provided
- search PATH for Gemini CLI
- inspect well-known install paths
- run a safe version command if available
- return installed status and version

Detection must not execute prompts or mutate files.

## Auth Status

Gemini CLI auth status is no longer refined by active ForgeX bridge detection. Use Antigravity / AGY CLI planning instead.

If unauthenticated:

- show setup guide
- ask user to sign in through official Gemini tooling
- never collect credentials in ForgeX

## Execution

Historical Gemini CLI bridge execution would have:

- run only in active workspace
- use argv arrays
- prefer prompt files over shell quoting
- filter environment
- enforce timeout
- support cancellation
- stream stdout/stderr
- cap previews
- detect changed files
- show diff review before accepting file edits

## Modes

Suggested modes:

- `ask`
- `plan`
- `edit` if officially supported and safe enough

## Diagnostics

Record:

- run ID
- execution ID
- provider ID
- Gemini CLI version
- workspace root
- mode
- status
- duration
- exit code
- changed files
- capped stdout/stderr

Do not record credentials, cookies, or raw auth files.

## Risks

- Google auth status may be difficult to verify safely
- CLI output format may change
- platform-specific installation differences
- process cancellation complexity
- hidden billing confusion if user does not understand their account is used
- large prompts still require chunking or file-by-file orchestration
