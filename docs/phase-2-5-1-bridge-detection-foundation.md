# Phase 2.5.1 Bridge Detection Foundation

## Summary

ForgeX now has a detection-only foundation for future Tool Bridges. It can tell whether Codex, Claude Code, and Antigravity / AGY CLI appear to be installed, attempt a bounded version check, and display the results in Settings -> Models.

No prompts are sent to bridge tools in this phase. No bridge tool can generate code, edit files, stream output, or participate in Model Router task routes.

## Detection Architecture

```text
BridgeDetectionService
├── CodexDetector       -> codex
├── ClaudeCodeDetector -> claude
└── AntigravityCliDetector -> agy / antigravity
```

Each detector:

- discovers commands with `where` on Windows or `which` on macOS/Linux
- runs `<executable> --version` with a short timeout
- captures stdout/stderr with capped output
- uses subprocess argv arrays with `shell=False`
- returns masked executable paths where possible
- reports conservative auth status

## Auth Limitations

Authentication is intentionally reported as `unknown` when a tool is installed:

```text
Install detected. Open the official tool to confirm login.
```

ForgeX does not read token files, cookies, browser profiles, OAuth stores, or private session databases. Future phases may use official, documented, non-mutating status commands if available.

## Execution Boundary

Bridge execution is disabled:

- `run_prompt: false`
- `stream_prompt: false`
- `edit_files: false`
- `diff_review: false`
- `can_run: false`
- `reason: Prompt execution is not enabled in this phase`

No `/models/bridges/run` route exists.

## API Routes

- `GET /models/bridges` returns all bridge detection cards.
- `POST /models/bridges/refresh` reruns detection and returns all cards.
- `GET /models/bridges/{provider_id}` returns one bridge card or 404.

Provider IDs:

- `codex_bridge`
- `claude_code_bridge`
- `antigravity_cli_bridge`

## Settings UI

Settings -> Models includes a Tool Bridges section with cards for:

- OpenAI Codex
- Claude Code
- Google Antigravity / AGY CLI

Cards show install status, version, executable path, auth status, disabled capabilities, a local docs reference, and the warning that prompt execution is not enabled.

## Privacy Rules

Logs and UI may include provider ID, install status, version, command found/not found status, and safe detection errors. They must not include tokens, cookies, raw auth file contents, secret environment variables, or hidden billing behavior.

## Future Work

The next bridge phase should add only documented auth-status checks first. Prompt execution should be a later phase with explicit user approval, workspace containment, command allowlists, cancellation, output caps, and file-diff review.
