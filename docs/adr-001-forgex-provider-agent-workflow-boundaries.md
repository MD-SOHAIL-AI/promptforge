# ADR-001: ForgeX provider, agent, workflow, and hardware boundaries

- Status: Accepted migration guardrail
- Date: 2026-07-11
- Runtime change: None

## Decision

Wrap current entry points before migrating internals. URLs, IDs, flags, schemas, events, confirmations, and state transitions remain compatibility contracts. ForgeX remains the sole authority for active-workspace mutation, review apply/restore, build, flash, and serial monitoring. Providers may infer, plan, or create sandbox artifacts; auth or run success grants no mutation/hardware authority.

## Canonical concepts

| Concept | Definition | Not equivalent to |
|---|---|---|
| **Connection** | Credential/session plus vendor/executable availability: API key, official Codex OAuth, AGY install/auth. | Model, adapter, profile, run. |
| **Model Endpoint** | Protocol, base URL and model ID for inference. | Credential or filesystem agent. |
| **Agent Adapter** | ForgeX translation to/from API, CLI, app server, template, or fake provider. | Permission to mutate/use hardware. |
| **Agent Profile** | Selection joining connection, endpoint, adapter, capabilities, policy and defaults. Legacy provider IDs are aliases. | Execution state. |
| **Workflow Run** | Versioned instance with identity, state, events, artifacts/review, confirmations and recovery. | Registration/auth status. |

## Compatibility requirements

1. Preserve REST/SSE/WebSocket paths, DTOs, safe errors, replay and ordering.
2. Preserve provider IDs, routes, fallbacks, env precedence, masking, and readable stores; secrets never enter run/event metadata.
3. Preserve default-deny flags/conjunctions; never silently enable network, agent, apply/restore, build, flash, monitor, trust, or native write.
4. Codex/AGY own auth sessions; ForgeX reports sanitized evidence only.
5. Agent output stays untrusted: bound, validate, sandbox, diff, create review, stop at `awaiting_apply`.
6. Preflight, approval, apply history, rollback, containment, secret/symlink/binary controls, audit and locks stay ForgeX-owned.
7. Build, flash, board/port validation and monitor lifecycle stay ForgeX-owned and are reachable only through allowed ForgeX tools/APIs.
8. Use explicit cross-references; never overload one ID across the five concepts.

## Current inventory

### Provider planes

| Plane | Components and IDs | Role |
|---|---|---|
| Model router | Registry/router/storage/credentials/usage; `openrouter`, `openai`, `groq`, `gemini`, `anthropic`, `cerebras`, `nvidia_nim`, `lmstudio`, `ollama`, `codex`; routes for generation, planning, debugging, docs, serial analysis, chat. | Connection/endpoint settings, health, test, routing/fallback. Codex is agent-shaped compatibility. |
| Product registry | `ProductProviderRegistry`; `verified_template`, `fake_planner`; seven API planners; AGY scratch/assisted and Codex evidence; paused AGY/Codex; reference OpenCode. | Plans bounded ForgeX tools. |
| Coding registry | `CodingProviderRegistry`; `verified_template`, `fake_api_coding_agent`, `api_coding_agent`, `codex_cli`, `agy_cli`, `claude_code_cli`, `opencode_cli`, `manual_patch`. | Proposal-to-review selection; several intentionally non-routeable. |
| Generic bridges | Bridge/capability registries, default-deny lifecycle/routing; implemented `agy`, `codex`; Antigravity, Codex, Claude detectors. | Sandboxed execution, cancellation, artifact validation, review eligibility, safe events. |
| V1 LLM | `_llm_from_environment`, LLM/generation strategy/router; `PROMPTFORGE_LLM_PROVIDER`/`PROMPTFORGE_MODEL`. | Legacy `/execute`. |

Repeated vendor IDs express different contracts and must never be merged by string equality.

Feature flags group as provider/network (`FORGEX_ENABLE_*_PROVIDER`, real API coding, Codex, AGY SDK); agent runtime (agent runtime/fake, generic routing/runs/provider/cutover, AGY bridge); assisted paths (AGY scratch/assisted and Codex/AGY QA gates); workflow/mutation (unified/fake coding, repair, patch apply, rollback restore). Literal flag scan in Protective tests is authoritative.

### Auth, stores and transports

- Codex: detector -> secret-filtered status -> official login launcher -> OAuth/subscription evidence. App-server adapter is gated/sandbox-only. APIs: `/agent-runtime/providers/codex-oauth/{status,status-diagnostics,login/launch,standalone-smoke}`.
- AGY: detector/auth, legacy sandbox runner, compatibility execution router, generic adapter, trusted baseline leases, bounded scratch/assisted import. APIs include bridge runs and `/agent-runtime/{agy-scratch-import,agy-assisted-runs}`.
- V1: in-memory task/session and ProgressBroker; `/ws/execution/{task_id}?after=`.
- Product: unified/product services; `/agent-runtime/runs/*`; SSE plus polling.
- Generic: run store schema 1/evidence; queued -> validating -> preparing sandbox -> running -> collecting artifacts -> completed, with cancel/fail/timeout/interruption/block branches; SSE replay/heartbeat.
- Coding: run/event v1 JSONL plus locks; awaiting apply -> applying -> awaiting build -> building -> awaiting flash -> flashing -> awaiting monitor -> monitoring -> completed, plus fail/repair/cancel/recovery.
- Review/patch/audit/apply/rollback stores are mutation records, not agent runs.

### APIs, selectors, and path traces

| Family | Current surface |
|---|---|
| V1 | `POST /execute`, cancel, execution WebSocket; Forge shell Workflow mode and `lib/websocket.ts`. |
| Product agent | `/agent-runtime/providers`, Codex/AGY helpers, run start/get/cancel/approval/review/events; Product panel/hook. |
| Generic agent | `/models/bridges/runs` start/list/get/cancel/events and generic-events alias; Generic panel/hook (currently `agy`). |
| Model router | `/models/providers` list/configure/test/models; `/models/routes` list/save/test; usage/diagnostics; provider cards, task route editor, diagnostics. |
| Bridge review/apply | detection/refresh/safety/Antigravity run; snapshot/diff/review/export; patch list/detail/cleanup/preflight/apply/history; rollback snapshot/preflight/restore. |
| Unified coding | `/models/coding-workflow` context, fake/API generation/status, apply/build/flash/monitor/repair/cancel/recovery, list/detail/events; coding panel. |
| Direct authority | `/build`, `/flash`, `/devices/boards`, `/monitor/*`, project/file CRUD, terminal; Device Tools/workspace shell. |
| Fake coding | `/models/api-coding-agent/fake/generate-review`; dev-only review creation. |

Model Settings currently mixes Connection, Endpoint and Profile records. Agent, Workflow and Coding modes remain separate. Product/generic hooks use `EventSource`, local active-run recovery and polling fallback; V1 uses WebSocket; coding uses REST snapshots/events.

- **V1:** `/execute` -> context/task -> `WorkflowRunner` -> planner/coordinator -> generation adapter/handler/strategy -> model/LLM -> validation/managed writes -> optional build -> flash -> monitor. ForgeX performs every effect and emits WebSocket progress.
- **Product agent:** Agent UI -> product registry -> run API -> unified/product service -> planner -> bounded tool plan -> ForgeX policy/runtime/executor -> sandbox/workspace -> review/artifact. SSE carries lifecycle; polling carries snapshots/text; ForgeX brokers approvals.
- **Unified coding:** context preview -> fake/real API adapter -> proposal validation -> sandbox/review -> `awaiting_apply`; separately confirmed ForgeX apply -> build -> flash -> bounded monitor. Gated repair returns build failures to `awaiting_apply`.
- **Review/apply:** sandbox -> snapshot/diff -> review/export -> patch store -> integrity/path/size/secret/symlink preflight -> approval -> rollback snapshot -> ForgeX apply -> audit/history. Restore has a separate gate/preflight/confirmation.
- **Hardware:** direct APIs, V1 adapters and coding services converge on ForgeX project/firmware/board/port/concurrency/output validation. No adapter gets raw device authority.

## Migration matrix

Keep = canonical; wrap = compatibility facade; migrate = move internals after parity with facade retained; retire = separate approval after usage/storage review.

| Component | Action | Compatibility requirement |
|---|---|---|
| Model registry/settings/credentials/routes/usage | Wrap | Project Connection/Endpoint/Profile; preserve IDs, tasks, masks, env precedence and APIs. |
| HTTP/local model adapters | Keep | Inference-only. |
| Model-router Codex entry | Migrate | Agent profile; preserve saved `codex` alias and gate. |
| V1 LLM env selection | Wrap | Preserve `PROMPTFORGE_*`, outputs and errors. |
| Product registry/API planners | Wrap | Project profiles; preserve status DTO/tool-plan contract. |
| Verified template and fake providers | Keep | Offline canonical and gated test profiles. |
| Paused/reference product entries | Retire | Equivalent diagnostics and no dependent clients first. |
| AGY/Codex status entries | Migrate | Connection evidence; retain fields/status-file reads. |
| Coding registry | Wrap | Adapter/profile catalog; preserve every ID/failure/disabled state. |
| Real API coding adapter/context | Keep | Review-only; confirmation/validation unchanged. |
| Placeholder coding descriptors | Retire | Equivalent profiles or explicit product removal first. |
| Generic bridge registry/routing/lifecycle | Keep | Canonical external-agent default-deny boundary. |
| AGY legacy runner/router | Wrap | Preserve flag truth table, states, lookup/cancel and APIs during generic cutover. |
| AGY generic/Codex app-server adapters | Keep | Sandbox, validation, review eligibility and ForgeX permissions. |
| Codex detector/status/login | Keep | Official auth; sanitized status/launch only. |
| Product service/tool runtime | Keep | Canonical policy/effect authority. |
| Generic run store/state/evidence/events | Keep | Candidate agent-run substrate; schema/replay readable. |
| Product run representation | Migrate | Canonical envelope; preserve `/agent-runtime` DTO/SSE/polling. |
| V1 execution/progress | Wrap | Add correlation without changing task IDs/WebSocket. |
| Coding workflow store/locks/state | Keep | Separate domain state; preserve schemas/actions. |
| Review/patch/apply/rollback | Keep | Canonical mutation authority; never merge into provider/run store. |
| Diagnostics/status stores | Wrap | Evidence projections; preserve paths/safe schemas. |
| Internal bridge events | Keep | Bounded/sanitized source. |
| Product/generic SSE | Wrap | Common internal envelope; preserve URL/payload/replay/heartbeat/fallback. |
| V1 WebSocket/coding REST events | Keep | Preserve cursors/history; any SSE is additive. |
| Model Settings | Migrate | Separate concepts behind legacy DTO adapters. |
| Agent/Generic/Coding UI hooks | Wrap | Preserve modes, local keys, actions, transport fallback. |
| File/project/terminal APIs | Keep | ForgeX mutation/session boundary. |
| Build/flash/board/monitor | Keep | Exclusive ForgeX hardware authority. |
| `/models` composed routes | Wrap | Preserve all paths; require OpenAPI parity. |

## Migration sequence

1. Add read-only projections for the five concepts; map all current records without changing routing.
2. Add cross-reference IDs and legacy aliases; never rewrite stored routes/runs in place.
3. Wrap registries behind profile queries and shadow-compare while executing old decisions.
4. Normalize an internal safe event envelope behind existing transports.
5. Move product runs to a canonical envelope with dual reads; keep coding state and mutation records separate.
6. Migrate frontend selectors one concept at a time behind compatibility DTOs.
7. Retire only after telemetry, fixture/OpenAPI parity, and an ADR amendment.

## Protective tests

```powershell
python -m pytest tests/unit/test_model_router.py tests/unit/test_model_provider_storage.py tests/unit/test_provider_runtime_contracts.py tests/unit/test_product_agent_runtime.py tests/unit/test_coding_provider_registry.py tests/unit/test_coding_workflow_store.py tests/unit/test_unified_agent_runtime.py
python -m pytest tests/api/test_model_routes.py tests/api/test_product_agent_runtime_api.py tests/api/test_generic_run_api.py tests/api/test_bridge_routes.py tests/api/test_bridge_review_routes.py tests/api/test_patch_apply_routes.py
python -m pytest tests/integration/test_unified_coding_workflow_full_chain.py tests/integration/test_coding_workflow_routes.py tests/integration/test_bridge_patch_safety_flow.py tests/integration/test_agy_generic_cutover.py
python -m pytest tests/unit/test_generate_code_handler.py tests/unit/test_workflow_runner.py tests/unit/test_tool_runtime.py tests/unit/test_patch_preflight_service.py tests/unit/test_patch_apply_service.py tests/unit/test_build_firmware.py tests/unit/test_flash_firmware.py tests/unit/test_serial_monitor.py
python -m pytest tests/unit/test_codex_status.py tests/unit/test_codex_login.py tests/unit/test_agy_generic_provider.py tests/unit/test_agy_trusted_workspace.py
npm run test:ui-state
npm run test:agent-ui
npm run test:electron
npm --prefix frontend run typecheck
```

Static compatibility inventories:

```powershell
rg -n 'APIRouter|@router\.(get|post|put|delete|websocket)|include_router' backend/api
rg -o 'FORGEX_[A-Z0-9_]+' backend frontend electron scripts | Sort-Object -Unique
rg -n 'provider_id|RUN_SCHEMA_VERSION|EVENT_SCHEMA_VERSION|BridgeRunStatus|VALID_TRANSITIONS' backend/model_router backend/agent_runtime backend/bridges
rg -n 'EventSource|WebSocket|text/event-stream|StreamingResponse' backend frontend
```

Live `qa:*`, `npm run test:codex`, and `npm run test:agy` are opt-in because they may call real providers/hardware and require existing flags and confirmations.

## Consequence

Compatibility wrappers and duplicate projections remain temporarily. Stored data and entry points stay stable: Connections authenticate, Model Endpoints infer, Agent Adapters translate, Agent Profiles select policy, Workflow Runs record execution, and ForgeX alone performs mutations and hardware operations.
