# ForgeX Bridge Architecture

## Purpose

Bridge providers allow ForgeX to orchestrate official local AI tools without owning the user's credentials, billing, or subscription. The bridge layer is a local process adapter, not a cloud proxy and not an authentication system.

## Architecture

```text
Forge UI
  ↓
Model Router route selection
  ↓
Bridge Provider Adapter
  ↓
Bridge Process Runner
  ↓
Official local tool
  ↓
Workspace-contained result
```

## Provider Families

API/local providers:

- OpenRouter
- OpenAI API
- Anthropic API
- Gemini API
- Ollama
- LM Studio

Tool bridges:

- Codex Bridge
- Claude Code Bridge
- Antigravity / AGY CLI Bridge

## Bridge Components

Future bridge implementation should be split into these components:

- `BridgeProvider`: provider-like adapter used by the model router.
- `BridgeDetector`: detects installed tools and versions.
- `BridgeAuthChecker`: checks signed-in status without reading credentials.
- `BridgeProcessRunner`: starts, streams, times out, and cancels local tool processes.
- `BridgeWorkspaceGuard`: validates workspace root, allowed paths, and path containment.
- `BridgeDiffService`: detects file changes and generates reviewable diffs.
- `BridgeDiagnosticsStore`: records safe run metadata.

## Phase 2.5.1 Detection Foundation

ForgeX now includes only the safe detection part of this architecture:

- `BridgeDetectionService`
- `CodexDetector`
- `ClaudeCodeDetector`
- `AntigravityCliDetector`

Detection checks PATH for `codex`, `claude`, and `agy`/`antigravity`, then attempts a bounded `--version` call. Prompt execution, streaming, file editing, and diff review remain disabled.

Gemini CLI is deprecated for active bridge planning; ForgeX tracks Google's current individual-user direction through Antigravity / AGY CLI detection.

## Execution Modes

Bridge requests should support:

- `plan`: ask the tool for a plan without modifying files.
- `ask`: ask a question in workspace context.
- `edit`: allow workspace modifications after explicit user approval.

When a bridge tool directly edits files, ForgeX should detect changed files and show a diff before the user accepts the changes into the Forge workflow state.

## Model Router Integration

Bridge adapters should expose provider-like capabilities:

- provider ID
- display name
- provider type: `tool_bridge`
- installed status
- auth status
- supported modes
- streaming support
- cancellation support
- workspace mutation support

The router can then use bridge providers as primary or fallback routes while preserving the existing API provider flow.

## Non-Goals

Bridge architecture must not:

- store subscription credentials
- read browser cookies
- reverse-engineer login state
- proxy calls through ForgeX servers
- hide billing behavior
- run outside the active workspace
- mutate files without user-visible approval
