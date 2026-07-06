# Phase 2.5.4 AGY Sandbox Dry-Run Plan

## Goal

Add the first AGY-only bridge execution prototype while keeping the active workspace protected.

## Feature Flag

AGY sandbox execution is disabled by default.

```text
FORGEX_ENABLE_AGY_BRIDGE=1
```

Without the flag, ForgeX reports that AGY sandbox execution is disabled and does not start runs.

## Flow

```text
active workspace snapshot
sandbox copy under ForgeX app state
AGY non-interactive run inside sandbox
sandbox diff
persistent review session
approve/reject review state only
```

## Sandbox Rules

Sandboxes are created under:

```text
.promptforge/state/bridge-sandboxes/<run_id>
```

The copy ignores:

- `.git`
- `.pio`
- `node_modules`
- `dist`
- `build`
- `.next`
- `.forgex`

Symlinks are skipped. Active workspace files are never modified.

## AGY Command Rules

Allowed execution form:

```text
agy -p <prompt>
```

ForgeX never runs plain `agy`, never uses shell strings, and never passes `--dangerously-skip-permissions`.

The prompt is written to:

```text
sandbox/.forgex/agy-prompt.txt
```

ForgeX reads that file and passes the prompt as one argv value to avoid shell quoting.

## API

- `GET /models/bridges/antigravity/sandbox-status`
- `POST /models/bridges/antigravity/sandbox-run`
- `GET /models/bridges/runs`
- `GET /models/bridges/runs/{run_id}`
- `POST /models/bridges/runs/{run_id}/cancel`

No generic bridge execution route is added.

## Non-Goals

This phase does not add Codex execution, Claude Code execution, bridge routing, active-workspace apply, token storage, cookie reading, or AGY auth/session inspection.
