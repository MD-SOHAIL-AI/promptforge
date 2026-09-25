# Forge Agent Unified Runtime

## Purpose

ForgeX uses one model-driven Forge Agent experience with capability profiles instead of separate chat, inspect, edit, build, and simulation brains. Existing staging, ChangeSets, rollback, build repair, hardware approval, artifact hashing, steering, memory, skills, and bounded subagents remain the safety foundation.

## Runtime model

```text
User turn
  -> deterministic intent/capability selection
  -> session + turn context
  -> Forge model/tool loop
  -> permission boundary
  -> trusted tools
  -> observations back to the model
  -> completion / ChangeSet / approval
```

The implementation intentionally keeps the existing autonomous firmware executor underneath build/repair/simulation turns because it already owns the trusted PlatformIO and hardware-safe workflow. Read-only Ask/Inspect and staged Plan/Edit now use the same provider/tool-plan semantics and capability model rather than hardcoded conversational responses.

## Capability profiles

| Profile | Read/search | Plan | Edit | Build | Simulation | Physical hardware |
| --- | --- | --- | --- | --- | --- | --- |
| Ask / Inspect | yes | no | no | no | no | no |
| Plan | yes | yes | no | no | no | no |
| Edit | yes | yes | staged only | no unless requested | no | no |
| Build only | yes | no | no | yes | no | no |
| Build + Repair | yes | yes | staged only | yes | available | no |
| Simulate | yes | no | no | required | required | no |
| Flash | uses verified run artifact | n/a | n/a | rebuild verification | n/a | explicit one-time approval |

`run_command` remains denied. ForgeX exposes trusted embedded operations rather than arbitrary shell access.

## Read-only intelligence

Ask and Inspect no longer return a mostly hardcoded response. They route through the selected model with read-only repository, skill, and verified-memory tools. The same model can therefore answer project-aware questions while the permission profile prevents writes, builds, flashing, or monitoring.

## Strict build semantics

A plain build request routes to the trusted autonomous build executor with a read + build-only policy. Write tools are not exposed. A repair request separately receives edit + build permissions, so `build` cannot silently become `build and repair`.

## Plan integrity

Plan mode persists a structured `AgentPlanRecord` containing:

- immutable plan ID
- monotonic revision
- SHA-256 of canonical plan steps
- source run ID
- status (`ready`, `approved`, `superseded`)

The Execute Plan UI submits the exact plan ID, revision, and hash. The backend rejects stale or tampered bindings with `AGENT_PLAN_BINDING_MISMATCH` and injects the exact approved steps into execution context.

## Embedded project context

`embedded_context.py` derives a deterministic local project graph from PlatformIO and firmware source:

- PlatformIO environments
- target board
- framework
- common GPIO symbol assignments
- I2C, SPI, UART, PWM, and ADC usage
- likely duplicate GPIO assignments

This graph is injected into model context so Forge reasons about firmware and hardware together instead of acting as a generic code editor.

## Simulation

Simulation is a first-class trusted tool for autonomous simulation turns:

1. Forge must first produce a successful verified build.
2. `run_simulation` invokes the existing bounded Wokwi backend.
3. Only supported ESP32 families are mapped to Wokwi board types.
4. Serial output and structured simulation status are returned as observations.
5. Temporary Wokwi workspaces created for the agent run are cleaned after observation.
6. Simulation never grants physical flash/serial permissions.

A missing Wokwi CLI/token or unsupported board is surfaced as a safe simulation failure rather than bypassed.

## Event truth

The agent now emits factual model/tool lifecycle events such as:

- agent loop/turn started and completed
- assistant summary
- tool pending/started/completed
- build started/completed/failed
- simulation started/completed/failed
- plan updates
- ChangeSet/review/apply state
- flash approval state

The Nexus UI no longer invents pseudo-reasoning fallback sentences. It prefers the actual model turn summary and otherwise displays factual activity.

## Workspace safety

The redesign preserves the existing guarantees:

- isolated managed staging
- path traversal and symlink/reparse protection
- sensitive-file restrictions
- exact ChangeSet diffing
- risk assessment and review
- rollback/undo support
- bounded model turns/tool calls/runtime
- orphan stage cleanup when no ChangeSet survives

Every provider-selected V3 tool is validated against its capability policy before trusted host execution, including build and simulation.

## Hardware safety

Physical flash remains outside ordinary model tool permissions. Flash approval is bound to run/project/artifact/environment/board/port state. Forge rebuilds immediately before flash and rejects an artifact hash mismatch.

## Persistence and recovery

Restored agent runs now retain richer state including plans, changed-file lists, provider diagnostics, and simulation results. Non-terminal runs restored after a crash are marked interrupted rather than implicitly resumed as successful.

## Deliberately not added

The redesign does not add:

- arbitrary shell execution
- plugin marketplace infrastructure
- recursive multi-agent swarms
- multiple competing planners
- a second chat model path
- synthetic chain-of-thought display
- generic computer-control tools
- a new flash path bypassing approval

These are intentionally excluded unless future ForgeX embedded benchmarks demonstrate a concrete need.
