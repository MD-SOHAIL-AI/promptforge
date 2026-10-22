# ForgeX V2 Architecture

Generated: 2026-06-17

## Product Definition

ForgeX V2 is a desktop Embedded Development Environment:

```text
Electron Shell
  -> Next.js Frontend
  -> ForgeX FastAPI Backend
  -> Agent Layer
  -> Runtime Layer
  -> Embedded Toolchain Layer
```

V2 should preserve V1 backend services and incrementally add product-grade desktop capabilities.

## High-Level Architecture

```text
Electron Main
  - app lifecycle
  - local backend process supervisor
  - native menu/tray
  - secure IPC
  - USB/serial permission prompts
  - filesystem/project dialogs

Electron Renderer / Next.js
  - dashboard
  - workspace
  - board/device managers
  - agent console
  - model router
  - build/flash/serial/sim/logs/settings

FastAPI Backend
  - existing API routes
  - V2 versioned API
  - agent sessions
  - device/model/board/settings APIs
  - durable WebSocket/event stream

Agent Layer
  - planner, hardware, firmware, build, debug, flash, test, monitor, docs, knowledge
  - task graph
  - permissions and approvals
  - tool/result/event ledger

Runtime Layer
  - subprocess manager
  - serial runtime
  - workflow runner
  - event store
  - retry/failure classifier
  - sandbox policies

Embedded Toolchain Layer
  - PlatformIO
  - esptool
  - OpenOCD
  - ST-Link/J-Link/CMSIS-DAP
  - Wokwi/QEMU/Renode
  - vendor SDKs
```

## Desktop Process Model

Use Electron as a supervisor and trust boundary:

- Main process starts/stops FastAPI on a local random or configured port.
- Renderer uses Next.js and talks to backend through localhost HTTP/WebSocket.
- Main process owns native prompts for hardware-sensitive actions.
- Backend remains usable standalone for development and tests.
- Electron IPC should not bypass backend domain validation.

## Backend Evolution

Keep V1:

- `backend/main.py`
- `backend/api/app.py`
- `backend/contracts/*`
- `backend/services/*`
- `backend/tools/*`
- `backend/runtime/*`
- `backend/validation/*`

Add V2 modules:

```text
backend/model_router/
backend/agents/
backend/boards/
backend/devices/
backend/approvals/
backend/events/
backend/settings/
backend/db/
```

## V2 Database

Use SQLite with migrations. Initial tables:

- `settings`
- `providers`
- `models`
- `model_usage`
- `boards`
- `board_plugins`
- `devices`
- `workspaces`
- `projects`
- `agent_sessions`
- `task_graphs`
- `task_nodes`
- `tool_runs`
- `approvals`
- `events`
- `artifacts`
- `logs`

Do not replace `ProjectService` immediately. Wrap it with a V2 project repository that records metadata in SQLite while preserving filesystem project layout.

## API Versioning

Add `/api/v2/*` while keeping V1 routes:

- `/api/v2/workspaces`
- `/api/v2/projects`
- `/api/v2/agents/sessions`
- `/api/v2/agents/{session_id}/events`
- `/api/v2/approvals`
- `/api/v2/models/providers`
- `/api/v2/models/route`
- `/api/v2/boards`
- `/api/v2/devices`
- `/api/v2/builds`
- `/api/v2/flashes`
- `/api/v2/serial`
- `/api/v2/settings`

## Event Architecture

Replace process-local-only progress as the only event source with:

- Durable `events` table.
- In-memory fan-out for live WebSocket clients.
- Replay by sequence.
- Session-scoped and task-scoped event streams.
- Event types for agent thoughts, plans, approvals, tool calls, logs, artifacts, build output, serial lines, and terminal state.

The existing `ExecutionProgressHub` can be retained as a transport adapter over the durable event store.

## Security And Permissions

Hardware actions must require explicit approval:

- Flash firmware.
- Erase flash.
- Change fuses/bootloader/security bits.
- OTA upload.
- Debug attach/halt/reset.
- High-risk shell command.

Approval record:

```text
approval_id
session_id
task_id
action_type
device_id
board_id
artifact_hash
risk_level
requested_by_agent
request_summary
approved_by_user
approved_at
expires_at
one_time_only
```

## Migration Strategy

1. Add docs and architectural boundaries.
2. Add Electron shell that launches existing app.
3. Add V2 SQLite and settings without changing V1 execution.
4. Wrap existing LLM service with model router.
5. Add board/device registries around existing metadata/detector.
6. Add approval service and enforce it in flash route/tool path.
7. Add agent sessions and task graph execution around `WorkflowRunner`.
8. Expand UI to V2 pages.

