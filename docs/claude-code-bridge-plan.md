# Claude Code Bridge Plan

## Goal

Plan future Claude Code Bridge support without implementing it. The bridge should invoke official local Claude Code tooling after the user installs and authenticates it independently.

## Security Boundary

ForgeX must not:

- store Claude session tokens
- scrape Claude browser cookies
- read private auth files for token extraction
- reverse-engineer Claude login flows
- proxy subscription usage through ForgeX servers

## Detection

Future detection should:

- support configured executable path
- search PATH for Claude Code
- check well-known install locations
- run a safe version command if available
- report installed/not installed/version

Detection must be read-only and side-effect free.

## Auth Status

Use official status commands if available. If safe auth status cannot be determined, report `unknown` and show setup instructions.

ForgeX should never inspect credential files directly.

Phase 2.5.2 keeps Claude Code auth conservative. ForgeX may run `claude --help`, but it does not run prompts, does not open login, and does not inspect Claude token or session files. Auth remains `unknown` unless a documented non-mutating status command is added later.

## Execution

Future Claude Code bridge execution should:

- run inside active workspace only
- use process argv arrays, not shell strings
- use temporary prompt files for large prompts
- filter environment variables
- enforce timeout
- support cancellation
- stream stdout/stderr
- cap previews
- detect changed files
- require diff review for file edits

## Modes

Suggested modes:

- `plan`
- `ask`
- `edit`

If Claude Code supports native planning or dry-run modes, ForgeX should prefer those before edit mode.

## Diff Review

For edit mode:

- snapshot workspace before execution
- run Claude Code
- detect changed files
- generate diff
- require user approval
- record apply/reject decision

Phase 2.5.3 adds the shared snapshot/diff/review foundation only. Claude Code execution remains disabled until a later phase wires Claude Code runs through this review layer.

Phase 2.5.3.1 persists Claude Code review sessions and snapshot metadata before any Claude Code execution exists. Pending reviews survive backend restart, expire after 24 hours, and cannot be approved after expiration.

## Diagnostics

Record safe metadata:

- run ID
- task type
- project ID
- workspace root
- tool version
- mode
- duration
- status
- exit code
- changed files
- capped stdout/stderr previews

Never record Claude credentials.

## Risks

- CLI interface may change
- auth behavior may change
- changed-file detection may miss generated files ignored by current filters
- large prompts still need chunking
- accidental workspace mutation requires robust backup/review design
