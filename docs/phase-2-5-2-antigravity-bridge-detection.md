# Phase 2.5.2 Antigravity Bridge Detection

## Summary

ForgeX replaces the active Gemini CLI bridge detector with Google Antigravity / AGY CLI detection.

Active Tool Bridges:

- OpenAI Codex
- Claude Code
- Google Antigravity / AGY CLI

Gemini CLI is deprecated for ForgeX bridge planning and is not returned as an active bridge card.

## Detection Behavior

Provider ID: `antigravity_cli_bridge`

Display name: `Google Antigravity / AGY CLI`

Command discovery:

- `agy`
- `antigravity`

Version check:

- `agy --version` when `agy` is found
- `antigravity --version` when only the fallback command is found

ForgeX never runs `agy` or `antigravity` without safe arguments.

## Auth Behavior

Installed AGY defaults to:

```text
auth_status: unknown
status_confidence: low
auth_message: AGY CLI is installed, but ForgeX cannot safely confirm authentication in detection-only mode.
```

ForgeX does not launch login, trigger OAuth, read Google auth files, read browser cookies, or parse session stores.

## Execution Boundary

AGY capabilities remain:

```json
{
  "detect": true,
  "run_prompt": false,
  "stream_prompt": false,
  "edit_files": false,
  "diff_review": false
}
```

No bridge run endpoint exists, and bridge IDs are not valid model-router generation providers.
