# ForgeX V1 Repository Analysis

Generated: 2026-06-17

This analysis treats the repository as the source of truth. Runtime code is not changed. The current product is still named PromptForge in the codebase; this document uses ForgeX for the V2 target name.

## Executive Summary

ForgeX V1 is a browser-delivered embedded development workflow with a FastAPI backend, a Next.js frontend, deterministic planning, LLM-backed code generation, validation, PlatformIO build/flash tools, serial monitoring, Wokwi simulation support, SQLite persistence primitives, runtime logs, metrics, and WebSocket progress streaming.

The strongest V1 architecture is the backend: contracts are immutable, tests are broad, execution is broken into planner, coordinator, workflow runner, adapters, services, tools, validators, and runtime infrastructure. The weakest V1 architecture is product packaging and repository hygiene: there is no Electron desktop shell, no durable model router, no formal board plugin system, no multi-agent runtime, and tracked generated workspace/vendor artifacts create noise and risk.

## Repository Tree

```text
promptforge/
  README.md
  requirements.txt
  pyproject.toml
  package-lock.json
  .env.example
  .promptforge/                 tracked dev logs; should become ignored runtime state
  backend/
    main.py                     canonical workflow composition
    agent/                      planner, coordinator, debugger
    api/                        FastAPI app, routes, schemas, WebSocket progress
    contracts/                  immutable task, plan, generated project, execution context
    core/                       env/config placeholders
    hardware/                   board JSON, compatibility matrix, constraint engine
    models/                     serializable runtime/execution/firmware/tool models
    observability/              metrics and structured runtime logs
    runtime/                    engine, session, retry, subprocess, serial, result, failure classifier
    services/                   LLM, code generation, project, PlatformIO, serial, artifact services
    state/                      SQLite persistence manager
    tools/                      build, flash, detect board, serial monitor, Wokwi, tool registry
    utils/                      paths, logger, serialization helpers
    validation/                 safety, firmware, board validators
    workflow/                   workflow runner, generate handler, stage adapters
    workspace/                  workspace manager
  frontend/
    app/                        Next.js app router
    components/                 editor, explorer, console, layout, prompt, sidebar, inspector, ui
    hooks/                      workspace orchestration hook
    lib/                        API client, WebSocket client, stage state, utilities
    types/                      frontend API/domain types
  docs/                         existing V1 API/workflow notes; several placeholders are empty
  examples/                     usage snippets for backend contracts/services/workflow
  tests/                        API, integration, unit coverage
  workspace/                    tracked generated projects, build artifacts, logs, PlatformIO libdeps
```

## Architecture Diagram

```text
Next.js browser UI
  | HTTP rewrite /api/promptforge/*
  | WebSocket ws://backend/ws/execution/{task_id}
  v
FastAPI app
  | app state dependency container
  | routes: execute, projects, files, build, flash, monitor, workspace, health, websocket
  v
Canonical workflow composition (backend/main.py)
  v
WorkflowRunner
  v
Planner -> ExecutionPlan -> Coordinator
  |                    |
  |                    +-> ToolRegistry -> build/flash/detect/monitor/simulate tools
  v
GenerateCodeHandler -> CodeGenerationService -> LLMService providers
  v
ProjectService / WorkspaceManager / ArtifactService / PersistenceManager
  v
PlatformIO / serial ports / Wokwi / filesystem artifacts
```

## Frontend Architecture

The frontend is a single Next.js 15 React 19 workspace application. `next.config.ts` rewrites `/api/promptforge/:path*` to the FastAPI backend. The UI is built from a shell layout: header, sidebar, prompt command, workflow timeline, file explorer, Monaco editor, task inspector, build console, and status bar.

`frontend/hooks/use-promptforge-workspace.ts` is the state owner. It loads health/projects/files/logs/build history, manages tabs, handles create/rename/delete/save, starts executions, opens a WebSocket before POSTing `/execute`, applies progress events to stage state, and refreshes the workspace after terminal events.

Current frontend gaps: no route-level pages, no dashboard/device/model/board/agent centers, no Electron IPC boundary, no local device permissions UI, no durable project layout abstraction, no real build/flash forms in the UI, no split state store, no automated frontend tests.

## Backend Architecture

The backend is the V1 core. `backend/api/app.py` composes FastAPI state: services, subprocess manager, PlatformIO service, LLM service, board detector, progress hub, workspace manager, metrics, and runtime logger. `backend/main.py` composes the canonical workflow and registers default tools.

The backend has clean layers:

- API transport: `backend/api/routes/*` and `backend/api/schemas/*`.
- Contracts: immutable dataclasses in `backend/contracts/*`.
- Agents: deterministic `Planner`, sequential `Coordinator`, and `Debugger`.
- Workflow: `WorkflowRunner`, `GenerateCodeHandler`, and adapters.
- Services: LLM, code generation, project, artifact, PlatformIO, serial.
- Tools: hardware/toolchain operations with structured results.
- Runtime: subprocess, retry, result, serial runtime, sessions, failure classification.
- Validation: board, firmware, and safety validators.
- Storage/observability: SQLite persistence manager, workspace manager, runtime logs, metrics.

## Agent Architecture

V1 has agent-like components, not a multi-agent system:

- `Planner` converts prompt text into deterministic `ExecutionPlan`.
- `Coordinator` executes plan steps sequentially, aggregates failures, and calls injected handlers/tools.
- `Debugger` classifies failures and recommends recovery actions.
- `GenerateCodeHandler` implements the generation step and validation/persistence handoff.

There are no separate Hardware/Firmware/Build/Flash/Test/Knowledge agents, no task graph scheduler, no agent memory, no permission ledger, and no human approval gate beyond direct API/UI flow.

## Runtime Architecture

Runtime primitives are mature for V1:

- `SubprocessManager` owns process lifecycle, timeouts, output capture, cancellation, and process cleanup.
- `SerialRuntime` and `SerialService` manage serial connection state and observations.
- `RetryEngine` and `failure_classifier` classify transient/build/tool failures.
- `runtime/result.py` models build, flash, observe, simulation, and execution results.
- `Engine` and `Session` provide a lower-level staged execution/session state model but are not the primary API execution path.

Risk: there are two orchestration tracks: canonical `backend/main.py` + `WorkflowRunner`, and older `runtime/engine.py` + `session.py`. V2 should preserve both while converging public workflow entrypoints around one runtime API.

## Service Layer Architecture

Services own durable application concerns:

- `LLMService` abstracts OpenAI, Anthropic, Gemini, OpenRouter over HTTP.
- `CodeGenerationService` constructs prompts, calls LLMs, extracts/validates generated files.
- `ProjectService` persists generated projects safely on disk.
- `PlatformIOService` wraps PlatformIO build/upload command execution and project introspection.
- `SerialService` wraps serial runtime lifecycle.
- `ArtifactService` stores typed artifacts and reproducible archives.

V2 should extend these services behind interfaces rather than replacing them.

## Tool Layer Architecture

Tools are callable operation endpoints registered by `tool_registry.py`:

- `build_firmware.py` validates PlatformIO projects and invokes build.
- `flash_firmware.py` selects flash backends and runs PlatformIO/esptool/ST tooling.
- `board_detector.py` enumerates serial ports and classifies supported boards.
- `serial_monitor.py` captures serial observations into `ObserveResult`.
- `wokwi_simulator.py` prepares/runs Wokwi simulations.

Tool outputs already use structured runtime results. V2 should add permission metadata, dry-run capability, tool sandbox policies, and user-approval requirements before destructive or hardware-affecting actions.

## Database Architecture

There is no application database schema for projects/users/settings. V1 has:

- File-backed project storage through `ProjectService`.
- Workspace storage through `WorkspaceManager`.
- Artifact manifests through `ArtifactService`.
- SQLite generic records through `PersistenceManager`.
- Runtime logs as structured file-backed logs.

V2 needs an explicit desktop database: SQLite with migrations for workspaces, projects, devices, boards, model providers, model usage, agent sessions, tool runs, approvals, artifacts, logs, and settings.

## API Architecture

Current routes:

- `GET /health`
- `POST /execute`
- `GET /projects`
- `GET /projects/{project_id}`
- `DELETE /projects/{project_id}`
- `GET /projects/{project_id}/files`
- `GET /files/content`
- `POST /files`
- `PUT /files`
- `DELETE /files`
- `POST /build`
- `POST /flash`
- `POST /monitor/start`
- `POST /monitor/stop`
- `GET /monitor/status`
- `GET /build-history`
- `GET /logs`
- `WS /ws/execution/{task_id}`

V1 has no authentication, no versioned API prefix, no provider settings API, no board manager API, no device inventory API, no task graph API, no approvals API, and no model usage/cost API.

## WebSocket Architecture

`ExecutionProgressHub` is an in-process bounded event fan-out hub. The `/execute` route calls `hub.begin(task_id)`, background execution emits typed events, and `/ws/execution/{task_id}?after=n` streams replay plus live events. It closes after `WORKFLOW_COMPLETED` or `WORKFLOW_FAILED`.

Strengths: simple, bounded, typed, supports replay. Gaps: process-local only, no durable event store, no multi-window session sync, no backpressure protocol beyond queue dropping, no auth, no resumable desktop session bus.

## Hardware Support Architecture

Supported board data is split across:

- `backend/hardware/boards/*.json`: ESP32, Arduino Uno, STM32 Blue Pill metadata.
- `backend/tools/board_detector.py`: USB VID/PID/text classification for ESP32 family, STM32, Arduino Uno, unknown.
- `backend/hardware/compatibility/compatibility_matrix.py`: board/framework/library/capability metadata loader and query layer.
- `backend/hardware/constraints/constraint_engine.py`: pin/peripheral/memory/capability violation checks.
- `backend/validation/board_validator.py`: final selected-board compatibility and project metadata validation.

V1 board support is useful but static. V2 needs board registry, plugins, SDK/toolchain resolution, device profiles, probe-driven capability detection, and board templates.

## File Inventory, Status, Risk, Refactor Priority

Risk: Low means stable/supporting. Medium means shared or likely to change. High means hardware/process/LLM/security/user data blast radius or architectural mismatch.

Priority: P0 immediate V2 foundation, P1 near-term, P2 planned extension, P3 leave mostly alone.

### Root And Config

| File | Purpose | Status | Dependencies | Risk | Priority |
| --- | --- | --- | --- | --- | --- |
| `.env.example` | Example backend env vars. | Active | dotenv/config | Medium | P1 |
| `.gitignore` | Ignore rules. | Needs review | Git | High | P0 |
| `README.md` | Product overview; partly stale vs tree and contains encoding artifacts. | Active docs, dirty in worktree | All modules | Medium | P2 |
| `requirements.txt` | Backend Python dependencies. | Active | FastAPI/httpx/pydantic/pyserial/pytest/uvicorn | Medium | P1 |
| `pyproject.toml` | Python project metadata placeholder. | Empty | Python tooling | Medium | P1 |
| `package-lock.json` | Root npm lock placeholder. | Stale/no root package | npm | Low | P3 |

### Tracked Runtime Logs

| Files | Purpose | Status | Dependencies | Risk | Priority |
| --- | --- | --- | --- | --- | --- |
| `.promptforge/*.log` | Dev server stdout/stderr/job logs. | Generated but tracked | local dev servers | High | P0 |

### Backend Source

| File | Purpose | Status | Dependencies | Risk | Priority |
| --- | --- | --- | --- | --- | --- |
| `backend/main.py` | Canonical workflow composition and tool invocation binding. | Active core | agent, workflow, services, tools, runtime, validators | High | P0 |
| `backend/agent/planner.py` | Deterministic prompt-to-plan rules and compatibility exports. | Active | contracts | Medium | P1 |
| `backend/agent/coordinator.py` | Sequential plan executor and failure aggregator. | Active core | contracts, tool registry | High | P1 |
| `backend/agent/debugger.py` | Failure diagnosis and recovery recommendations. | Active | failure classifier, runtime results | Medium | P2 |
| `backend/api/app.py` | FastAPI app factory and dependency composition. | Active core | services, runtime, routes | High | P0 |
| `backend/api/dependencies.py` | State lookup and project resolution helpers. | Active | FastAPI, ProjectService | Medium | P1 |
| `backend/api/errors.py` | API error model and handlers. | Active | FastAPI/Pydantic schemas | Medium | P1 |
| `backend/api/observability.py` | Request IDs, logging middleware, redaction. | Active | FastAPI/logging | Medium | P1 |
| `backend/api/progress.py` | In-process progress event hub. | Active | asyncio, WebSocket schemas | High | P1 |
| `backend/api/serialization.py` | Converts execution outcomes to API responses. | Active | coordinator, schemas | Medium | P1 |
| `backend/api/__init__.py` | API app exports. | Active | app | Low | P3 |
| `backend/api/routes/build.py` | Build endpoint. | Active | PlatformIOService, ProjectService | High | P1 |
| `backend/api/routes/execute.py` | Async workflow start endpoint. | Active core | main workflow, progress hub | High | P0 |
| `backend/api/routes/files.py` | Workspace file CRUD endpoint logic. | Active | ProjectService, generated project contracts | High | P1 |
| `backend/api/routes/flash.py` | Build-validate-flash endpoint. | Active hardware | PlatformIO, detector, flasher, board validator | High | P0 |
| `backend/api/routes/health.py` | Service health endpoint. | Active | app state | Low | P2 |
| `backend/api/routes/monitor.py` | Serial monitor lifecycle endpoints. | Active hardware | SerialService | High | P1 |
| `backend/api/routes/projects.py` | Project listing/get/delete endpoints. | Active | ProjectService | Medium | P1 |
| `backend/api/routes/websocket.py` | Execution event WebSocket endpoint. | Active | progress hub | Medium | P1 |
| `backend/api/routes/workspace.py` | Build history and logs endpoints. | Active | WorkspaceManager | Medium | P1 |
| `backend/api/routes/__init__.py` | Route package marker. | Active | none | Low | P3 |
| `backend/api/schemas/*.py` | Pydantic API request/response models. | Active | Pydantic | Medium | P1 |
| `backend/contracts/*.py` | Immutable canonical task/plan/context/generated-project contracts. | Active core | dataclasses, serialization utils | High | P0 |
| `backend/core/config.py` | Environment loading and LLM timeout/retry settings. | Active | dotenv | Medium | P1 |
| `backend/core/constants.py` | Empty placeholder. | Unused | none | Low | P3 |
| `backend/core/exceptions.py` | Empty placeholder. | Unused | none | Low | P3 |
| `backend/hardware/boards/*.json` | Static board metadata. | Active but narrow | validators/matrix | Medium | P1 |
| `backend/hardware/compatibility/compatibility_matrix.py` | Board/framework/library compatibility loader and query API. | Active | board JSON | Medium | P1 |
| `backend/hardware/constraints/constraint_engine.py` | Resource/pin/peripheral constraint evaluator. | Active | compatibility matrix | High | P1 |
| `backend/models/*.py` | Alternate serializable execution/firmware/runtime/tool result models. | Active/tests | utils serialization | Medium | P2 |
| `backend/observability/metrics.py` | In-memory counters/timers. | Active | threading | Medium | P2 |
| `backend/observability/runtime_logs.py` | Structured logs persisted to files. | Active | filesystem | Medium | P1 |
| `backend/runtime/engine.py` | Older staged runtime engine. | Active but secondary | session, retry, subprocess | Medium | P2 |
| `backend/runtime/failure_classifier.py` | Regex/category failure classifier. | Active | runtime results | Medium | P1 |
| `backend/runtime/result.py` | Canonical runtime result dataclasses. | Active core | dataclasses | High | P1 |
| `backend/runtime/retry.py` | Retry policy and transient failure handling. | Active | failure categories | Medium | P2 |
| `backend/runtime/serial_runtime.py` | Low-level async serial runtime. | Active hardware | pyserial | High | P1 |
| `backend/runtime/session.py` | Session state machine. | Active but secondary | threading | Medium | P2 |
| `backend/runtime/subprocess_mgr.py` | Async subprocess execution, output draining, cleanup. | Active core | asyncio/subprocess | High | P0 |
| `backend/services/artifact_service.py` | Artifact save/load/list/delete/archive service. | Active | filesystem, zip | Medium | P1 |
| `backend/services/code_generation_service.py` | LLM prompt/output pipeline for generated projects. | Active core | LLMService, contracts | High | P0 |
| `backend/services/llm_service.py` | Provider-neutral LLM HTTP clients. | Active, pre-router | httpx, config | High | P0 |
| `backend/services/platformio_service.py` | PlatformIO project/build/upload facade. | Active | subprocess manager | High | P1 |
| `backend/services/project_service.py` | Generated project filesystem persistence. | Active | contracts, PlatformIOService | High | P1 |
| `backend/services/serial_service.py` | Serial lifecycle facade. | Active | SerialRuntime | High | P1 |
| `backend/state/persistence.py` | Generic SQLite persistence manager. | Active but not central API DB | sqlite3 | Medium | P1 |
| `backend/state/sqlite/.gitkeep` | Keeps DB directory. | Placeholder | none | Low | P3 |
| `backend/tools/board_detector.py` | USB/serial board detection and classification. | Active hardware | pyserial | High | P1 |
| `backend/tools/build_firmware.py` | PlatformIO build tool implementation. | Active hardware/toolchain | subprocess, failure classifier | High | P1 |
| `backend/tools/flash_firmware.py` | Flash backend selection/execution. | Active hardware/destructive | subprocess, board detector | High | P0 |
| `backend/tools/serial_monitor.py` | Serial monitor tool result wrapper. | Active hardware | SerialRuntime | High | P1 |
| `backend/tools/tool_registry.py` | Tool registration and async invocation. | Active core | tools | Medium | P1 |
| `backend/tools/wokwi_simulator.py` | Wokwi CLI simulation integration. | Active optional | subprocess, Wokwi token | Medium | P2 |
| `backend/utils/logger.py` | JSON logging helper. | Active | logging | Low | P2 |
| `backend/utils/paths.py` | PathManager for workspace roots. | Active | os/pathlib | Medium | P1 |
| `backend/utils/serialization.py` | JSON freezing/thawing helpers. | Active | core contracts/models | Medium | P2 |
| `backend/validation/board_validator.py` | Board compatibility validation. | Active hardware | board metadata | High | P1 |
| `backend/validation/firmware_validator.py` | Generated project and PlatformIO firmware validation. | Active | configparser | High | P1 |
| `backend/validation/safety_validator.py` | Prompt/metadata safety checks. | Active safety | board DB | High | P0 |
| `backend/workflow/workflow_runner.py` | Task-plan-context-coordinator composition. | Active core | planner, coordinator, contracts | High | P0 |
| `backend/workflow/generate_code_handler.py` | Generate/validate/persist coordinator handler. | Active core | code gen, project service, validators | High | P0 |
| `backend/workflow/adapters/*.py` | Pure conversion between generation/build/flash/monitor stages. | Active | contracts/tools/runtime | Medium | P1 |
| `backend/workspace/workspace_manager.py` | Workspace, logs, builds, archive management. | Active | persistence, logs, paths | Medium | P1 |

### Frontend Source

| File | Purpose | Status | Dependencies | Risk | Priority |
| --- | --- | --- | --- | --- | --- |
| `frontend/package.json` | Frontend package/scripts/dependencies. | Active | Next/React/Monaco/Tailwind | Medium | P1 |
| `frontend/package-lock.json` | Frontend lockfile. | Active | npm | Medium | P1 |
| `frontend/next.config.ts` | API rewrite to backend. | Active | Next | Medium | P1 |
| `frontend/tsconfig.json` | TypeScript config. | Active | TypeScript | Low | P2 |
| `frontend/tsconfig.tsbuildinfo` | TypeScript build cache tracked. | Generated but tracked | TypeScript | Medium | P0 |
| `frontend/eslint.config.mjs` | ESLint config. | Active | ESLint/Next | Low | P2 |
| `frontend/postcss.config.mjs` | PostCSS config. | Active | Tailwind | Low | P2 |
| `frontend/tailwind.config.ts` | Tailwind theme/content config. | Active | Tailwind | Medium | P2 |
| `frontend/next-env.d.ts` | Next type environment. | Generated convention | Next | Low | P3 |
| `frontend/app/.gitkeep` | Placeholder. | Unneeded now | none | Low | P3 |
| `frontend/app/globals.css` | Global UI theme/layout CSS. | Active | Tailwind | Medium | P1 |
| `frontend/app/layout.tsx` | Root app layout metadata. | Active | Next | Low | P2 |
| `frontend/app/page.tsx` | Single page rendering workspace shell. | Active | WorkspaceShell | Low | P2 |
| `frontend/components/code/code-viewer.tsx` | Monaco editor tabs/save UI. | Active | Monaco | Medium | P1 |
| `frontend/components/console/build-console.tsx` | Console/log tab UI. | Active | React/lucide | Medium | P1 |
| `frontend/components/explorer/file-explorer.tsx` | File tree CRUD UI. | Active | React/lucide | Medium | P1 |
| `frontend/components/inspector/task-inspector.tsx` | Task/project/build metadata side panel. | Active | React/lucide | Medium | P1 |
| `frontend/components/layout/app-header.tsx` | Top header/status. | Active | React | Low | P2 |
| `frontend/components/layout/status-bar.tsx` | Bottom connection/error bar. | Active | React | Low | P2 |
| `frontend/components/layout/workspace-shell.tsx` | Main IDE shell layout. | Active core UI | components, hook, panels | High | P1 |
| `frontend/components/prompt/prompt-command.tsx` | Prompt execution input. | Active | React | Medium | P1 |
| `frontend/components/sidebar/workspace-sidebar.tsx` | Project/build/log navigation. | Active | React/lucide | Medium | P1 |
| `frontend/components/ui/*.tsx` | Button, badge, separator, tooltip primitives. | Active | Radix/CVA | Low | P2 |
| `frontend/components/workflow/workflow-timeline.tsx` | Workflow stage display. | Active | stage types | Medium | P1 |
| `frontend/hooks/use-promptforge-workspace.ts` | Frontend state and API/WebSocket orchestration. | Active core UI | API client, WebSocket, stage state | High | P1 |
| `frontend/lib/api.ts` | Fetch client for backend routes. | Active | browser fetch, types | Medium | P1 |
| `frontend/lib/websocket.ts` | Execution WebSocket client. | Active | browser WebSocket | Medium | P1 |
| `frontend/lib/workflow-stage-state.ts` | Maps backend events to UI stage state. | Active | types | Medium | P1 |
| `frontend/lib/utils.ts` | CSS and formatting helpers. | Active | clsx/tailwind-merge | Low | P3 |
| `frontend/types/index.ts` | Frontend API/domain types. | Active | TypeScript | Medium | P1 |
| `frontend/components/.gitkeep`, `frontend/websocket/.gitkeep` | Old placeholders. | Unneeded | none | Low | P3 |

### Docs And Examples

| Files | Purpose | Status | Dependencies | Risk | Priority |
| --- | --- | --- | --- | --- | --- |
| `docs/api.md` | FastAPI route and configuration notes. | Active | backend API | Low | P2 |
| `docs/workflow-runner.md` | WorkflowRunner integration docs. | Active | workflow | Low | P2 |
| `docs/workflow-adapters.md` | Adapter architecture notes. | Active | workflow adapters | Low | P2 |
| `docs/generate-code-handler.md` | GenerateCodeHandler docs. | Active | workflow/codegen | Low | P2 |
| `docs/execution-plan-migration.md` | Plan contract migration notes. | Active | contracts/planner | Low | P2 |
| `docs/architecture.md`, `docs/runtime.md` | Empty placeholders. | Incomplete | none | Medium | P1 |
| `examples/*.py` | Usage examples for services/contracts/workflow. | Active examples | backend modules | Low | P3 |

### Tests

| Files | Purpose | Status | Dependencies | Risk | Priority |
| --- | --- | --- | --- | --- | --- |
| `tests/api/test_api_routes.py`, `tests/api/test_websocket.py` | API and WebSocket behavior tests. | Active | FastAPI test client/fakes | Medium | P1 |
| `tests/integration/test_blink_led_workflow.py` | End-to-end canonical workflow tests. | Active | backend workflow/fakes | High | P1 |
| `tests/unit/test_*.py` | Unit tests for agents, services, tools, runtime, validators, contracts, workspace. | Active broad coverage | pytest/fakes | Medium | P1 |
| `tests/**/.gitkeep` | Placeholder directories. | Low value | none | Low | P3 |

### Workspace And Generated Artifacts

| Files | Purpose | Status | Dependencies | Risk | Priority |
| --- | --- | --- | --- | --- | --- |
| `workspace/artifacts/**`, `workspace/builds/**`, `workspace/logs/**` | Sample/generated build reports, binaries, metadata, flash logs. | Generated but tracked | PlatformIO/workflow | High | P0 |
| `workspace/projects/*/platformio.ini`, `workspace/projects/*/src/main.cpp`, `.promptforge-project.json` | Generated sample projects. | Generated but tracked | PlatformIO | Medium | P0 |
| `workspace/projects/*/.pio/libdeps/**` | Third-party PlatformIO library dependencies vendored into generated projects. | Vendor/generated but tracked | Adafruit/DHT libs | High | P0 |
| `workspace/**/.gitkeep` | Placeholder runtime directories. | Active placeholder | workspace manager | Low | P3 |

## Architectural Risks

1. Hardware actions are callable through API routes without a formal approval ledger.
2. Model provider abstraction exists, but not a router with cost, fallback, benchmarking, and task policy.
3. Board metadata is static and not plugin-ready.
4. Progress events are process-local and not durable.
5. UI is a single page, not the V2 desktop workspace/product surface.
6. Repository tracks generated logs, build outputs, and PlatformIO library dependencies.
7. README and docs diverge from actual file layout and current dependency versions.
8. `pyproject.toml`, docs placeholders, and empty core modules are architectural debt.

## Refactor Priorities

P0:
- Add Electron desktop shell without disrupting backend/API.
- Add approval model for flash/hardware actions.
- Introduce model router around existing `LLMService`.
- Stop tracking generated runtime/vendor artifacts in a planned cleanup.
- Establish V2 SQLite schema/migrations.

P1:
- Version API and add settings/provider/board/device endpoints.
- Refactor frontend state into domain stores or query hooks.
- Convert board metadata into registry/plugin model.
- Add durable event log for workflow progress.

P2:
- Consolidate secondary runtime engine with canonical workflow entrypoint.
- Expand docs and examples after V2 architecture lands.

