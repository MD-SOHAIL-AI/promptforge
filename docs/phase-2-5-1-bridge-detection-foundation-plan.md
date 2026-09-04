# Phase 2.5.1 Bridge Detection Foundation Plan

## Goal

Add safe local detection for future Tool Bridges without executing prompts, modifying files, storing subscription credentials, or inspecting private authentication state.

## Scope

- Detect whether the official CLI command is available on PATH.
- Detect a version through a bounded `--version` command.
- Return conservative authentication status.
- Expose detection results through read-only `/models/bridges` API routes.
- Show read-only Tool Bridge cards in Settings -> Models.
- Keep bridge providers out of generation routing.

## Bridge Commands

- OpenAI Codex: `codex`
- Claude Code: `claude`
- Google Antigravity / AGY CLI: `agy`, fallback `antigravity`

Discovery uses `where` on Windows and `which` on macOS/Linux with subprocess argv arrays and `shell=False`.

## Safety Boundary

This phase does not add prompt execution, streaming, file editing, diff review, billing behavior, OAuth handling, token storage, cookie scraping, or auth-file inspection.

All bridge capabilities report:

```json
{
  "detect": true,
  "run_prompt": false,
  "stream_prompt": false,
  "edit_files": false,
  "diff_review": false
}
```

## API

- `GET /models/bridges`
- `POST /models/bridges/refresh`
- `GET /models/bridges/{provider_id}`

No run endpoint is introduced.

## Tests

- Unit tests cover missing tools, installed tools, version timeouts, capped output, `shell=False`, unknown auth, and disabled execution capabilities.
- API tests cover all three bridge cards and per-provider details.
- Model router tests assert bridge IDs are not valid generation providers.

## Next Phase

The next phase can refine official safe auth status checks if each tool documents a non-mutating status command. Prompt execution should remain separate and require explicit workspace guards, command allowlists, output caps, cancellation, and user approval.
