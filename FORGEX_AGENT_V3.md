# Forge Agent V3 Architecture

## Goal

Forge Agent V3 turns ForgeX from a one-shot firmware generator into a continuous embedded coding agent while preserving ForgeX's staged ChangeSet, review, artifact-verification, and hardware-approval boundaries.

## Core loop

```text
User turn
   ↓
Turn router / deterministic controls
   ↓
Context assembler
   ↓
Forge Agent model
   ↓
Tool call
   ↓
Tool policy + capability validation
   ↓
Staged/host tool execution
   ↓
Observation
   └────────────→ Forge Agent model
                    (repeat)
```

A coding turn ends only when the model returns `final=true`, a safety/runtime limit is reached, or a required verification step cannot be completed.

## Context

Context is deliberately tool-first rather than repo-dump-first. Initial context contains:

- the user task
- bounded workspace inventory/revision
- `FORGEX.md` and `AGENTS.md` when present
- active verified project memories
- available skill metadata

The agent then uses list/glob/grep/read tools to acquire source content on demand.

Older tool observations are compacted into a bounded checkpoint so long turns do not grow without limit.

## Overlay editing

For staged ChangeSet turns:

```text
read(file)
   ↓
Does staged version exist?
   ├─ yes → read stage
   └─ no  → read active workspace
```

Writes always target the stage. This means a later model turn sees edits made by an earlier tool call without exposing the active project to direct model mutation.

## Tool surface

### Read/discovery

- `list_files`
- `glob_files`
- `grep_search`
- `read_file`

### Staged editing

- `write_file`
- `edit_file_simple`

### Agent state/context

- `update_plan`
- `load_skill`
- `memory_search`
- `spawn_subagent`

### Verification

- `build_firmware`
- host-controlled command capability where explicitly configured

## Build and repair

Autonomous firmware work runs in an isolated staged workspace. The agent can build that workspace, receive structured build output and bounded compiler diagnostics, inspect relevant source, patch it, and build again in the same reasoning turn.

A required build that fails becomes an observation rather than immediately terminating the workflow, allowing the model to repair the failure within configured turn/tool limits.

## Plans

`update_plan` maintains agent-owned task state independently of the orchestrator's fixed safety graph. UI plan steps can therefore reflect the task the model is actually performing rather than pretending that every run always uses the same stages.

Plan-only mode restricts the tool policy to read/context/plan capabilities and prevents workspace mutation.

## Skills

Skills are lazy-loaded. The agent initially receives only skill names/descriptions and calls `load_skill` when instructions are relevant.

Built-ins:

- PlatformIO repair
- Serial debugging
- Firmware review

Project-specific skills can live under `.forgex/skills/`.

## Subagents

V3 includes one-level bounded specialist child sessions:

- **Explore** — repository investigation
- **Review** — independent diff/source review
- **Verify** — build/static verification support

Child roles do not receive unrestricted mutation capabilities, and nested delegation is intentionally constrained.

## Steering

Only live `queued`/`running` agent turns accept mid-turn steering. Guidance is queued and consumed at the next safe tool boundary.

Completed runs and `awaiting_flash_confirmation` runs are not steerable; a new edit/build request becomes a fresh turn.

## Flash continuation

A successful build stores verified build metadata including firmware path/hash. A later explicit `flash it` request can prepare a hardware approval from that completed run without forcing the user to rebuild first.

The approval remains bound to run, target board, port, PlatformIO environment, artifact identity/hash, and monitor preference before trusted host code performs the flash.

## Serial monitor

Monitor start/stop commands are deterministic hardware actions and bypass firmware generation. This prevents the previous behavior where `open serial monitor` incorrectly launched the generic autonomous build pipeline.

## Compatibility

`FORGEX_ENABLE_AGENT_V3_LOOP=1` is the default for normal desktop composition.

Setting it to `0` selects the prior autonomous workflow. Explicitly injected workflow executors (tests/integrations) are also honored, which keeps the application composition contract stable.
