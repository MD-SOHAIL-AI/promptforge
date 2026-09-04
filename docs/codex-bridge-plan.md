# Codex Bridge Plan

## Goal

Plan future OpenAI Codex Bridge support without implementing it. The Codex Bridge should invoke official local Codex tooling after the user installs and signs in independently.

## Non-Negotiable Boundary

ForgeX must not:

- ask for ChatGPT credentials
- store ChatGPT session tokens
- scrape browser cookies
- read Codex auth files for token extraction
- proxy Codex subscription access through ForgeX servers

## Detection

Future detection should:

- search configured path first
- search PATH for official Codex executable
- inspect well-known installation locations
- run a safe version command if available
- report installed/not installed/version

Detection must not run prompts or mutate files.

## Auth Status

Auth status should use official Codex status commands if available. If no safe status command exists, ForgeX should show `unknown` and provide setup guidance.

Phase 2.5.2 keeps Codex auth conservative. ForgeX runs `codex --help` as a safe bounded command, but does not run `codex` alone, does not launch login, and does not inspect Codex auth files. Auth remains `unknown` unless a future Codex detector declares a documented non-mutating status command.

If unauthenticated:

- show official setup instructions
- let user open Codex login/setup outside ForgeX
- do not collect credentials in ForgeX

## Execution Plan

Future bridge execution should:

- run only inside active workspace
- use argv array process spawning
- use a temporary prompt file to avoid shell quoting issues
- filter environment variables
- set timeout
- support cancellation
- stream stdout/stderr
- cap output previews
- snapshot files before and after
- parse changed files from workspace diff
- record diagnostics

## Modes

Suggested modes:

- `plan`: ask Codex for a plan only
- `ask`: ask contextual questions
- `edit`: allow workspace edits after user approval

## File Change Review

If Codex edits files directly:

1. snapshot workspace hashes
2. run Codex
3. detect changed files
4. generate diff
5. show review in Forge panel
6. user chooses apply/reject

If files are already modified on disk after the tool exits, reject should require either restoring from backups created before execution or leaving files with clear warning. The safer future implementation should create backups for edit mode.

Phase 2.5.3 adds the shared snapshot/diff/review foundation only. Codex execution remains disabled until a later phase wires Codex runs through this review layer.

Phase 2.5.3.1 persists Codex review sessions and snapshot metadata before any Codex execution exists. Pending reviews survive backend restart, expire after 24 hours, and cannot be approved after expiration.

## Diagnostics

Record:

- run ID
- execution ID
- workspace root
- Codex version
- mode
- status
- timeout/cancel state
- exit code
- changed files
- capped stdout/stderr previews

Do not record credentials or full auth state.

## Risks

- Codex CLI output format may change
- auth status may not be machine-readable
- edit mode may mutate files before ForgeX can approve
- Windows shell quoting issues
- long embedded prompts still need chunking
- process tree cancellation must be robust
