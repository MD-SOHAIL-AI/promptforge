# ForgeX Complete Code Audit

**Checkout:** `D:\projects\ForgeX-Phase6-Final` • **Audit date:** 2026-10-05 • **Git:** `main`, HEAD `0f293c540fcede4f2d3e95e74cc80b5c15f0faee`, with extensive existing modified/untracked work.

## 1. Executive Summary

**Assessment: Internal Beta, principally Windows development, not a qualified public beta or production release.** ForgeX contains a substantial working product: durable Python jobs/sessions, model-driven tool use, staged edits, build/repair, native provider streaming, bounded specialists, named skills, project memory, sealed ESP32 upload sets and explicit one-use flash approvals. This is considerably more than interface scaffolding. Its weaker areas are inconsistent boundary enforcement, competing implementation paths, large stateful functions and missing artifact-specific release qualification.

The strongest systems should survive: `AgentOrchestrator`, the durable SQLite journal/jobs, the settled-assistant/tool-invocation contracts, staged file policy, immutable firmware manifests and hardware approval binding. The TypeScript model engine is useful provider infrastructure. The Harness is an optional shadow host with largely disconnected execution abstractions.

**Highest-priority findings:**

- Model-writable native tests execute as ordinary host programs after project trust. They are not fenced from files, network or serial devices. A typed `pio test` command and `hardware_used: false` do not establish the claimed hardware restriction.
- On this Python 3.10 environment, project skill loading and indexing follow Windows directory junctions outside the selected project. Both were reproduced with harmless audit fixtures. The primary editing policy is stronger and rejects reparse points.
- A simulated crash between two ChangeSet publications leaves one file changed, one unchanged and metadata still `pending`; normal apply/undo cannot recover it.
- The authenticated legacy `/execute` generation path still writes active project files outside the modern ChangeSet review path.
- Instruction-file context loading bypasses ordinary content-secret checks and reads entire files before truncation. A synthetic key reached rendered model context.
- Contract-free build-only runs can store model-authored final prose as `verified_build` memory. Write runs with task contracts substitute a host-generated summary; this finding does **not** apply indiscriminately to every successful build.
- Default OpenCode request execution bypasses the implemented provider circuit breaker. Endpoint circuit status is therefore not authoritative health for that path. Its stdin write also lacks its own configured deadline enforcement.
- The current desktop dependency audit fails: eleven affected package entries propagate one High node-forge advisory through the optional SAP provider dependency. Exploitability in ForgeX remains unverified.

**Conclusive feature answers:** MCP is not implemented. Codex OAuth is not implemented. Google account/subscription OAuth into Gemini is not implemented. The normal Gemini path requires an API key; Vertex cloud credentials are a separate authentication mechanism. Skills and bounded Python subagents are implemented. Failures do not automatically switch the current provider/model. Two substantial live Python iteration implementations remain under one Python run owner.

**Observed verification:** 2,231 Python test passes, one initial timing-sensitive failure and four skips. The failing test passed three isolated reruns. Another 106 Node tests and 79 UI-state tests passed. All 52 browser assertions passed, but their command stalled during teardown and was interrupted; this is not a clean successful command exit. Typecheck, lint, production frontend, model-router/Harness/Electron compilation, Python syntax and source-release scan passed. A real `esp32dev` Arduino fixture compiled and produced a sealed upload set. No physical device was flashed.

### Evidence and coverage conventions

**VERIFIED-RUNTIME** means a command/probe in this audit established the specific observation. **VERIFIED-CODE** means the inspected implementation/call path establishes it. **LIKELY** is an inference. **UNVERIFIED** means external credentials, devices, operating systems, exact installers or a dedicated reproduction were unavailable/not exercised.

All 659 source files were inventoried and structurally parsed/searched. The [file annex](File-by-File-Audit.md) identifies **165 files with call-path annotations**, **423 additional owned/test files with structural review**, and **71 vendored/generated files**. The audit does **not** claim line-by-line manual behavioral verification of every file, exhaustive branch coverage, or live verification of every catalog provider. No coverage instrumentation was run. That limitation is material to interpreting “complete”: repository coverage is complete within the stated exclusions; behavioral verification is explicitly scoped by evidence.

Documentation and earlier reports were used for claim comparisons, never as proof of runtime success. No feature implementation or silent product fix was performed. Audit helpers/fixtures/logs live here; builds generated their normal output. Next regenerated its type-reference/configuration artifacts; the added audit include was removed and the inventoried generated reference was restored by an exact SHA match. All 659 inventoried source hashes matched the final integrity check; this is an inventory-baseline comparison, not proof that pre-existing checkout modifications belong to the audit.

Primary evidence: [inventory](inventory.json), [coverage](coverage-ledger.json), [global searches](global-searches.json), [test/build summary](evidence-summary.json), [safety probes](safety-probes.json), [additional probes](additional-probes.json), [compiler result](esp32-compile.json). Source locations below include function line ranges; clickable links point to the starting line.

## 2. Repository Statistics

Counts exclude virtual environments, node_modules, git internals, runtime workspaces, `.forgex`/`.promptforge` data, build outputs, generated Monaco public assets and static assets. Physical LOC include comments/blank lines; they are not executable-line metrics. Configurations/catalogs are counted separately.

| Classification | Files | LOC |
|---|---:|---:|
| ForgeX-owned source/helpers | 411 | 95,148 |
| Test source, including fixtures/helpers | 177 | 35,566 |
| Vendored source | 68 | 10,901 |
| Generated source references/catalogs | 3 | 2,049 |
| **Total** | **659** | **143,664** |

| Language extension | Files |
|---|---:|
| Python | 382 |
| TypeScript `.ts` / `.tsx` / `.mts` | 135 / 66 / 22 |
| JavaScript `.mjs` / `.js` / `.cjs` | 41 / 8 / 1 |
| CSS / PowerShell | 2 / 2 |

There are 128 Python test/helper files. Additional native `.c/.cpp/.h/.ino` source was searched outside excluded build/data directories and none was found; firmware templates are principally strings/generated outputs, not a large checked-in native application.

| Source area | Files | LOC | Actual role |
|---|---:|---:|---|
| backend | 223 | 69,349 | Product authority plus legacy workflow |
| frontend | 151 | 21,311 | Next/React UI, hooks, libraries, UI tests and small vendor subtree |
| electron | 23 | 3,414 | Desktop shell/supervisors/preload plus tests |
| harness | 13 | 3,225 | Shadow RPC primitives, generated catalog and tests |
| model-router | 3 | 607 | Node worker/engine and engine tests |
| scripts | 43 | 3,806 | Development, packaging, qualification and tests |
| tests | 128 | 31,054 | Python regression suite |
| third_party | 60 | 9,998 | Pinned OpenCode source subset |
| examples | 11 | 549 | Python usage/project examples |
| docs source helpers | 3 | 304 | Prior evidence helpers; not authoritative audit evidence |
| Electron builder config | 1 | 47 | Packaging policy |

Forty-five separately inventoried configuration/catalog/fixture files include manifests, locks, CI, board JSON, skill Markdown and release schemas. See [configuration inventory](configuration-inventory.json). The Python provider registry currently contains **229 descriptors**, including ten named providers and catalog additions. Their catalog-model count is not an account-specific availability count.

### Repository map and entry points

```text
ForgeX-Phase6-Final
├── backend
│   ├── api                  FastAPI composition, security, routes/schemas
│   ├── agent_runtime        Python run authority, loops, jobs, context/tools
│   ├── agent / workflow     Reachable older planner/coordinator/generation path
│   ├── model_router         Selection, runtime, retries/admission, credentials
│   ├── changes              Snapshots, stage/diff/apply/undo
│   ├── services             Projects, PlatformIO, manifests, flash, terminal
│   ├── runtime              Process ownership, Windows Jobs, serial transport
│   ├── tools / validation   Typed embedded adapters and source/board checks
│   ├── hardware             Action binding and board/constraint data
│   ├── settings / state     Settings and legacy generic persistence
│   ├── workspace / utils    Managed data paths, older artifact/workspace helpers
│   └── contracts / models / observability / security / harness
├── frontend                 Next application; app/page.tsx → NexusShell
├── electron                 main.ts → managers/window/preload/IPC
├── model-router/src         worker.mjs → engine.mjs
├── harness/src              server.ts → shadow HarnessRuntime
├── third_party/opencode     Vendored provider/schema/route/tool-runtime subset
├── scripts                  Launch, bundle, release and qualification scripts
├── tests / frontend/e2e     Python and browser regression suites
├── examples / docs          Examples and intended/historical behavior
├── .github/workflows        Windows beta pipeline
└── audit/2026-10-05          This audit's non-product artifacts
```

`package.json` names the desktop package `forgex-desktop` 0.2.0, entry `dist/electron/main.js`. Root scripts build model-router before Harness before Electron. Frontend has its own package/lock; Harness has a small manifest/tsconfig; model-router is built using root dependencies, without a separate package manifest. Python manifests are `requirements.txt`, `.in`, runtime hash lock and `pyproject.toml`. API startup is `backend.api.app:app`, whose module bottom calls `create_app()`. CLI entry is `backend/cli.py`. There is no separate durable queue-worker startup: jobs execute as owned async tasks inside the API process. The model Node worker starts per physical request; Harness server startup is optional.

## 3. Current Architecture

```text
Electron main ── launch secrets/cookies, supervision, folders/background IPC
  ├─ standalone Next frontend [packaged] / Next dev server [development]
  ├─ FastAPI on loopback + local token + Host/Origin validation
  └─ optional Harness RPC [shadow, disabled by default]
           ▲ mirrors session/event data only; no run authority

NexusShell + React hooks
  ├─ Chat routes → ChatService → ModelRouterService [no tools]
  ├─ Agent session/messages → intent/intake → ProductAgentService facade
  │    → AgentOrchestrator [run/job/lease/grant/journal/apply/approval owner]
  │      ├─ AutonomousForgeAgentLoop [embedded staged copy]
  │      └─ ForgeXToolRuntime [read/general overlay/staged iteration]
  │          ↘ AsyncAgentEngine / settled frames / ordered tool scheduler
  │          ↘ bounded Python Explore/Review/Verify specialists
  │          ↘ ContextAssembler, skills, memory, lexical/project profile hints
  │          ↘ ToolPolicy + grants → executor + typed host handlers
  │               ├─ stage edits → ChangeSet → review/apply/undo
  │               ├─ PlatformIO → owned process → build/manifest evidence
  │               └─ approved host flash → immutable images → selected port
  ├─ Files API → direct HUMAN editor CRUD
  ├─ Terminal API/WS → HUMAN ConPTY/pipe shell
  └─ legacy /execute → main.py → Planner/WorkflowRunner/Coordinator
                         → CodeGenerationService → ACTIVE project writes

All live model paths → ModelRouterService/ModelRuntime
  → pinned selection + physical admission + shared attempt budget/retry
  → Python OpenCodeProvider → stdin/NDJSON Node worker
  → ForgeX engine adapter → vendored OpenCode native provider / AI SDK
  → selected remote/local endpoint → normalized settled response

Persistence: orchestration/session/job SQLite + model/selection/admission SQLite
             settings/registries/ChangeSets JSON + immutable artifact files/logs
```

OpenCode supplies model/provider infrastructure. It does not own ForgeX reasoning iterations, tools, session state or hardware authorization.

## 4. Startup Architecture

**Packaged flow:** Electron module sets runtime switches/protocols and single-instance lock → `app.whenReady().then(bootstrap)` → resolve packaged resources/user data and embedded executables → generate local/Harness tokens and pass private backend environment → register IPC/background protocol and HttpOnly cookies → optional `HarnessManager.start()` (failure tolerated) → `BackendManager.start()` → Python import/configuration/service composition → loopback health identity validation → `FrontendManager.start()` standalone Next with private identity challenge → main BrowserWindow → UI hooks load settings/projects/models/sessions and open observations. With the default flag, no shadow host starts.

**Development differs:** `scripts/dev-desktop.mjs` starts the development frontend; build/dev Electron then starts/reuses the backend. The renderer has `sandbox: true`, but development globally adds Chromium `--no-sandbox` ([main.ts](D:/projects/ForgeX-Phase6-Final/electron/main.ts:24)). Do not transfer a dev security result to a packaged application.

The real backend composition root is [create_app, 97–806](D:/projects/ForgeX-Phase6-Final/backend/api/app.py:97). It constructs subprocess ownership, paths/data migration, settings/terminal/workspace, PlatformIO/artifact services, detector, model registry/usage/selection/admission/runtime, legacy generation compatibility, projects/imports, ChangeSets, session/orchestration stores, product provider facade, orchestrator, chat and activity projection. `BuildOperations` shares the orchestration store. Optional generic `PersistenceManager` is not passed into the default `WorkspaceManager`.

Security initialization requires a desktop token; source mode without one leaves authenticated operations unavailable. Supplied tokens under 32 characters fail startup. Cookies are HttpOnly/SameSite Strict and host scoped on localhost/127.0.0.1; cookies are **not port scoped**. Provider keys are not renderer state. Model accounts need not be configured to show the ready desktop.

Managed data/workspace directories initialize during service composition. The active imported/managed project is selected/reconciled by frontend hooks and resolved again for each run; there is no universal selected-project initialization that proves all project dependencies are ready. Agent/session/store services initialize at boot, while actual planners/tool executors and Node provider workers are instantiated for a task/request. Provider account health is a separate operation from application readiness.

| Startup condition | Implementation / consequence | Evidence status |
|---|---|---|
| Backend port already occupied | Reuse only matching runtime contract, source fingerprint and launch-token fingerprint; otherwise reject | Code + manager tests |
| Frontend port occupied | Private-key challenge identifies owned standalone server; unrelated server rejected | Code + Node tests |
| Slow backend | Approximately 60-second readiness limit; individual health requests 12 seconds | Code; clean machines unverified |
| Slow packaged frontend | Approximately 30-second manager limit; window load readiness has another bounded wait | Code; cold installer unverified |
| Backend later dies | Bounded restart budget, supervision and explicit state; not an unlimited recovery loop | Code + Node tests |
| Invalid legacy generation configuration | Captured configuration error can leave other services running | Code |
| No cloud credentials | UI/backend ready, selected provider later returns configuration/auth failure | Code |
| Harness unavailable | Optional shadow behavior; failure does not replace Python authority | Code |
| Provider worker bundle missing | `MODEL_ENGINE_UNAVAILABLE` on request; backend boot is not a worker health certificate | Code |
| Checkpoint/retention/profile hint failures | Some are caught/logged or omitted; diagnostics may be incomplete without fatal startup failure | Code |

**Remaining risks:** free-port CLI probe/start TOCTOU; fixed cold-start time assumptions; some bootstrap setup lies outside manager-specific catches; parent Electron crash containment is not the same as build-tool Windows Job ownership. No real installer startup, upgrade migration or crash-restart sequence was exercised here. Existing output directories and prior screenshots are not evidence for this checkout.

## 5. Agent Runtime

### Exact user-message flow

`NexusShell` → `useForgeAgentSession.sendMessage` → frontend API → POST `/agent-runtime/sessions/{session_id}/messages` → [append_agent_session_message, 438–1035](D:/projects/ForgeX-Phase6-Final/backend/api/routes/agent_runtime.py:438).

The route authenticates, resolves session/project, persists the user message, processes slash commands and any approved-plan binding, routes the latest intent, obtains clarification when necessary, and either answers an inspection/conversational question, starts a deterministic monitor/flash-confirmation action, queues steering for existing work, or calls `_start_product_run` (1169–1329). That function fixes project/environment, autonomy, requested/prohibited actions, execution profile and model selection. It creates/revises a task contract when the request requires writes. Model interpretation cannot issue host permissions.

`ProductAgentService` delegates to `AgentOrchestrator.start_run` (125–270). The orchestrator acquires the session lease/fence, creates durable job/run/events, issues scoped grants and starts an owned async operation. It chooses the embedded full-stage loop or general overlay loop. The chosen loop assembles context → requests a plan/native tool turn through `RouterProductPlanner` → uses ModelRuntime → waits for physical provider completion and attempt settlement → validates tools → schedules reads/mutations/host processes → records observations and revises evidence/context → requests another turn. Source writes invalidate earlier build/review/test evidence. Completion requires the requested mode's current evidence; successful generation prose alone does not prove compilation or hardware behavior.

Orchestrator finalization persists status and session reply, exposes ChangeSets/review, may apply authorized changes, preserves recovery drafts and can prepare a one-time flash authorization. Live frames and durable events are distinct; interrupted uncommitted assistant/tool frames cannot silently become executable recovered work.

### Every substantive loop / orchestration family found

| Implementation / size | Callers and responsibility | Tools/model/context | State, cancellation, recovery and verification |
|---|---|---|---|
| `AutonomousForgeAgentLoop.run`, autonomous_loop.py 260–880, **621 lines** | Orchestrator `_execute_autonomous` 1622–2088 → app autonomous adapter 259–447; embedded build/repair | Router planner; shared engine requests; stage executor + typed host build/sim/test; assembled project context + observations | Orchestrator persists parent; owned cancellation and shared budgets; loop repair/stall/reorientation; source-bound build and contract review/acceptance |
| `ForgeXToolRuntime.run_product_async`, tool_runtime.py 129–1140, **1,012 lines** | Orchestrator `_execute_staged` 1355–1620; read/general edit workflows | Router planner; overlay executor; own context/history/completion branches; settled frames/shared scheduler | Parent durable store; owned cancellation; own retry/recovery/reporting; diff/staged result, no automatic hardware permission |
| `ForgeSubagentRunner._run`, subagents.py 107–326, **220 lines** | Parent spawn host handler; Explore/Review/Verify | Role-selected planner, independent context, read policy; Verify may build; no spawn tool | Parent budget/child observations; bounded turns/tools/parallelism; propagating cancellation; child prose is not parent build certification |
| `WorkflowRunner.run` → `Coordinator.execute` + main.execute_task 143–310 | Still-live `/execute` and internal legacy workflow callers | Deterministic Planner plus CodeGenerationService/LLM; injected low-level tools; legacy generation context | In-memory legacy execution/progress + project metadata; own cancellation/error aggregates; generation/compile repair; **direct active writes** |
| main `_repair_build_failures`, 313–481; CodeGenerationService chunk loops 897–1226 / 1300–1447 | Legacy generation/build repair | Chunk/file prompts, validator feedback, router-compatible LLM contracts | Separate repair budgets/diagnostics/rollback conventions; not modern conversational loop |
| `ApiPlannerProvider` older planner class, module **1,724 lines** | Exports/tests; shared helpers imported by current planner | Old native/tool-plan handling | Class is not the default product planner; shared helpers are live, so module is not wholly dead |
| Harness `WorkflowEngine` / `SubagentRuntime` | Constructed by shadow runtime; no production execution RPC/provider registration | Injectable abstractions, no registered live handlers | Shadow JSONL, no current ForgeX task owner; templates/migration experiments |

**Authority:** the Python `AgentOrchestrator` owns execution. It does not follow that a single semantic loop exists. `AsyncAgentEngine` shares yielding/request/settlement/scheduling, while the two adapters still own substantial reasoning-cycle/context/verification logic. They are alternative dispatch paths under one parent, not independently competing Python and TypeScript owners for the same normal desktop run. The legacy HTTP route provides a third reachable workflow architecture.

Reasoning belongs to the selected LLM. Intent/permissions/budgets/public progress and whether evidence is sufficient belong to the host. Public thinking summaries are user-visible text, not a trustworthy grant or a claim of hidden chain-of-thought inspection.

## 6. Conversation and Sessions

`AgentSessionStore` stores agent/chat session identity, messages, titles, project association and active run, backed by the shared orchestration SQLite database. Legacy histories can be imported. The route retains the original user turn; slash/plan/intake state and run lifecycle are projected to the session. `SessionRunProjector` prevents an ephemeral stream from becoming the only record of completion.

Agent session leases prevent concurrent ownership of one session; heartbeat/fence loss revokes publication. That is **not a per-project lock across independent sessions**. Two sessions can inspect/build the same project concurrently; ChangeSet baseline/hash conflicts reduce publication loss, but are not comprehensive cross-session process/build isolation.

Chat routes/service are separate and have no agent tools. Chat retries may occur before published text; failures after partial text are surfaced rather than silently replaying duplicated public output. The frontend reconciles persisted messages when stream delivery is missed. Its generation guards prevent late detail responses from selecting an older chat.

The historical `ExecutionProgressHub` remains ephemeral and separate from the orchestration journal. Persisted job recovery adopts/replaces run identity without re-posting the original user task. These are implemented, but actual desktop-kill/provider-outage recovery on a real packaged installer remains unverified.

## 7. Context System

Three related contexts exist: host intake/task contract, provider-native conversation history, and tool-observation/compacted project context. `ContextAssembler` renders task, workspace revision/inventory, `FORGEX.md`/`AGENTS.md`, active verified memory, named skill labels, project profile/compile hints and lexical candidate paths. Inventory defaults to 120 paths, memory to 12 entries of 700 characters, candidates to 12. Compilation summaries bound units/defines/includes. Tool reads acquire detailed source on demand.

`RouterProductPlanner` preserves native assistant tool-call/tool-result history; older observations are compacted deterministically and stored in recoverable context artifacts. The host treats files, instructions, skills, memory and tool/device output as untrusted data that cannot grant authority. Context retrieval bounds are implemented, but boundary consistency is incomplete:

- [context_assembler.py 75–86](D:/projects/ForgeX-Phase6-Final/backend/agent_runtime/context_assembler.py:75) reads entire instruction files then truncates to 16,000 characters. It does not apply the normal secret scanner or robust Windows reparse checks.
- Profile/index inspection errors can be swallowed and their summaries omitted.
- `WorkspaceIndex` is a lexical index/cache, not semantic compiler indexing. It hashes files beyond the bounded text snippets without an overall scan budget, and the junction probe crossed the selected root.

The synthetic instruction probe establishes secret inclusion in rendered context, not a live external disclosure. The outgoing planner/provider path forwards that context and no equivalent whole-request secret gate was found. Treat that as a code-established disclosure path requiring repair.

## 8. Model Runtime

The authoritative model chain is [ModelRouterService](D:/projects/ForgeX-Phase6-Final/backend/model_router/router.py:79) → resolver/catalog/selection → `ModelRuntime` → physical attempt slot → `OpenCodeProvider` → Node worker/engine → provider endpoint → `CanonicalStreamAssembler` → planner-native history/settled plan.

Selection precedence incorporates session role, global role, session main, global main and legacy task routes. Route decisions are pinned for a request. Capability metadata controls native tools, structured response or prompted JSON fallback; schema fallback is a response-format adaptation, **not another provider**. Host validation remains necessary even with provider schemas.

Jobs bound logical turns and total wall time/tools/tokens, while attempt accounting charges actual physical requests including repair and child/review calls. Standard defaults: 900 seconds, 100 tools, 120 requests, 245,760 output tokens and 8,000,000 input bytes. Advanced: 2,700 seconds, 300 tools, 360 requests, 737,280 output tokens and 24,000,000 input bytes. These are execution budgets, not advertised subscription allowances.

Provider usage is normalized from completed response metadata; incomplete requests may not have billable usage observations. Pre-reservation/settlement provides conservative local control, not authoritative account billing.

## 9. Provider Layer

**Implemented dispatch:** every default registry provider is instantiated as `OpenCodeProvider`, not the individual older Python API adapter. Those older classes remain for tests/injection/local discovery/shared helpers. There are ten named provider definitions; 219 additional descriptors are loaded from a pinned catalog. Catalog registration plus a generic compatible adapter is less evidence than successful real-account integration.

| Named provider | Auth / environment | Configured base endpoint | Current dispatch |
|---|---|---|---|
| OpenAI | API key, `OPENAI_API_KEY` | `https://api.openai.com/v1` | Native OpenCode OpenAI Responses |
| Anthropic | API key, `ANTHROPIC_API_KEY` | `https://api.anthropic.com/v1` | Native Messages |
| Gemini | API key, `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com/v1beta` | Native Google generate-content |
| DeepSeek | API key, `DEEPSEEK_API_KEY` | `https://api.deepseek.com` | Compatible/native provider request adapter |
| OpenRouter | API key, `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` | OpenCode OpenRouter adapter |
| Groq | API key, `GROQ_API_KEY` | `https://api.groq.com/openai/v1` | SDK factory under common engine |
| Cerebras | API key, `CEREBRAS_API_KEY` | `https://api.cerebras.ai/v1` | SDK factory under common engine |
| NVIDIA NIM | API key, `NVIDIA_API_KEY` | `https://integrate.api.nvidia.com/v1` | Compatible request; agent-specific generation options |
| Ollama | No API key | `http://localhost:11434`, request adapter adds `/v1` | Compatible requests; local live model discovery |
| LM Studio | No API key | `http://localhost:1234/v1` | Compatible requests; local live model discovery |

For each of these the common request layer provides streaming, normalized usage/errors/request IDs, bounded header/first-event/idle/total timeouts, same-selection retries and physical admission. Native tool calling depends on the selected model's metadata/provider response; a provider name alone does not prove all its models support tools. Malformed/truncated responses are rejected. Default circuit enforcement is absent from their OpenCode dispatch. Real provider credentials/access were **not** tested in this audit.

Custom providers accept explicit user-configured OpenAI completions/Responses or Anthropic-compatible endpoints, keys and options. They use the same worker/recovery pipeline. Catalog SDK integrations include AWS Bedrock credential chains, Vertex Google Cloud project/ADC options, Azure, Copilot bearer adaptation and many compatible services. A user-supplied Copilot/account token is not a complete account login/refresh implementation. All descriptor IDs, endpoint/env/SDK metadata and status qualifications are exported in [providers.json](providers.json) and [provider annex](Provider-and-API-Inventories.md).

**Credentials:** default secure persistence uses Windows Credential Manager. Non-Windows default storage is explicitly unavailable, not secretly plaintext. Environment variables remain an alternative. There is no completed native macOS/Linux secure credential-store implementation.

**Codex OAuth:** no actual provider identifier, login bridge, credential refresh or OAuth transport implementation was found. `.env.example` lines 13–21 advertises ignored Codex settings. **NOT IMPLEMENTED.**

**Google subscription OAuth:** no Google account → AI One/AI Pro allowance → Gemini provider bridge was found. `google-auth-library`/Vertex ADC establish cloud SDK credentials, not Google consumer subscription entitlement. **NOT IMPLEMENTED.** The ordinary Gemini definition uses an API key. No inference is drawn from environment examples or catalog marketing names.

Cloud model listing normally returns pinned catalog metadata. Only the local Ollama/LM Studio branch performs the older live-discovery calls. Consequently README's Cerebras account-specific `/v1/models` promise is incorrect for current default dispatch. Model/provider listing availability and “free” flags are static metadata, not verified remote access or current billing claims.

## 10. OpenCode Integration

`model-router/src/engine.mjs` imports vendored LLM, provider factories, route client/executor and tool message schema classes. It builds a single-attempt stream and converts normalized native events to ForgeX NDJSON. Additional provider factories use installed AI SDK packages; `maxRetries: 0` preserves Python retry ownership. `RequestExecutor.singleAttemptLayer` similarly prevents hidden native retries.

The vendored subset is 60 files/9,998 LOC under `third_party/opencode`; 68 vendor files overall include local frontend braces code. Provenance is pinned to the supplied OpenCode 1.18.34 subset; modifications/adaptation are ForgeX-owned at the boundary and must be treated as fork maintenance. Engine/worker are 3 inventoried files including their tests, 607 LOC. Python integration still owns selection, request accounting, sessions, tools, staging and hardware.

Vendored tool-runtime/other abstractions being present does not make OpenCode an agent dependency. Only actual engine imports determine authority. Generated Harness provider descriptors derive from the pinned catalog at bundle time; hand-maintaining a second authoritative provider list would add drift.

## 11. Retry / 503 / Recovery

`ModelRuntime` and `request_recovery` own retries; Node provider layers are single attempt. [retry.py](D:/projects/ForgeX-Phase6-Final/backend/model_router/retry.py:13) defaults to five retries/six attempts. Transient 429, 500, 502, 503, 504, 524/network failures are eligible; auth/configuration/quota/content/context/budget failures are not generally eligible. OpenAI 404 has a provider-specific retry rule. Every physical attempt remains charged to the shared budget.

**503 flow:** provider HTTP error → bounded normalized category/status/header/request-ID → Python same-selection decision → Retry-After (milliseconds/seconds/date) or exponential backoff/jitter → deadline/admission/job checks → another request to the **same provider and model**. Longer waits become durable job cooldown/pause/recovery. Exhaustion remains visible; it does not select a different provider to hide the outage. Managed outage recovery is bounded by cycle/time limits, not a fresh infinite deadline at every resume.

| Concern | Actual implementation | Limitation |
|---|---|---|
| Physical/logical counts | Job attempt ledger, request reserve/settlement and per-turn limits | Failed provider billing may be unknown |
| Concurrent physical requests | SQLite admission slots; NVIDIA 1, local 2, usual cloud 4, identity scoped | No proactive RPM/token bucket based on account quota |
| Fairness / crashed owner | Queue deadline and expiring heartbeat leases | Whole-machine/multi-backend stress not qualified |
| Timeouts | Header/semantic first-event/idle/total deadlines in Python + Node | Real very slow/reasoning-only providers unverified |
| Partial private agent stream | Buffered until settlement; malformed/incomplete tools not executed | Partial failed attempt text is not durable successful content |
| Partial public chat | Retry before text; surface failure after published text | Different semantics are deliberate |
| Cancellation | Abort signal, Node child kill/reap, owned tools and fenced publication | Human terminal uses weaker separate cleanup |
| Provider request IDs | Selected HTTP request-ID headers normalized into diagnostics | Some endpoints do not provide IDs |
| Circuit | Implemented SQLite breaker for older/injected adapters | **Skipped for default OpenCode providers** |
| Fallback | `allow_fallback=False`; persisted fields normalized off | Explicit role selection or retired-default migration can change selected IDs independently of failure |

The code migrates one shipped retired OpenRouter model identifier to the current default at resolution, including saved values matching that identifier. That is a selection migration, not failure fallback, and should be made visible to users. Comment text saying “OpenCode session retry owns upstream recovery” at the circuit skip is imprecise: current physical retries are in ForgeX Python; native executor/SDK retry is disabled.

**Additional timeout gap:** TransportPolicy declares connect/write/pool controls, but the current Node payload forwards only header/idle/total values. Python awaits worker stdin `drain()` before entering its response deadline loop, and final kill/reap waits have no explicit bound. A fake stalled stdin exceeded the configured 0.1-second total and required an external 0.3-second cancellation. RouterProductPlanner wraps agent turns in `asyncio.wait_for`, which mitigates the main agent path; direct provider calls/probes still should enforce their own write/total/cleanup guarantees. Cancellation killed the probe child; this is not evidence all real workers hang.

## 12. Tool System

There are **32 canonical ToolName/schema entries**, not 32 simultaneously executable tools. The active surface is the intersection of capability profile, issued grant, wired native/host handler and prerequisites. `run_command`, `present_files` and the three session inspection tools have no allowlist membership in the six normal profiles reviewed. Shell aliases normalize to `run_command`, which remains denied/unwired.

`LLM request` → canonical stream assembly → settled assistant attempt → strict ProductToolPlan / argument validation → grant + ToolPermissionPolicy → `OrderedToolScheduler` → local executor or typed host handler → typed result/observation → journal/context update. Unknown tools/extra keys/invalid paths/content are rejected. At most six pure reads overlap; mutation/process/device/control operations preserve ordering. Some host tools take the host-handler branch after executor validation rather than `execute()` itself. Low-level legacy tools form a separate trusted registry.

Full per-tool argument schemas, defaults/bounds, profiles, description and registry timeout/recovery fields: [tool-schemas.json](tool-schemas.json). The table below distinguishes behavior/evidence; common grant/cancellation/error semantics follow it.

| Tool | Required arguments | Implementation / behavior | Writes/process/evidence |
|---|---|---|---|
| list_files | none; optional path | ToolExecutor directory view | Read; bounded paths |
| glob_files | pattern | Executor glob under policy | Read; path matches |
| grep_search | query | Executor bounded textual search | Read; matches, limits |
| read_file | path | Executor safe text/overlay read | Read; bounded content/offset |
| write_file | path, content | Executor staged/overlay atomic write | Stage mutation; diff later |
| edit_file_simple | path, old_text, new_text | Exact bounded replacement | Stage mutation; rejects ambiguous/missing match |
| apply_patch | path, patch | Bounded structured patch parser | Stage mutation; content/path policy |
| delete_file | path | Stage delete / overlay tombstone | Stage mutation; ChangeSet deletion |
| move_file | path, destination | Stage move / overlay mutation | Stage mutation; both paths checked |
| update_plan | steps | Executor live structured plan | In-memory/journal plan; not authorization |
| load_skill | name | SkillRegistry named load | Read; 96KB input/24K chars; junction defect |
| memory_search | query | Host active verified memory search | Read; provenance-labeled entries |
| spawn_subagent | role, task | Host ForgeSubagentRunner | Child model requests; no recursive spawn |
| present_files | files | Retained executor/schema support | Not in normal profiles |
| session_event_read | none | Retained host journal handler/schema | Not in normal profiles |
| session_event_search | query | Retained host journal handler/schema | Not in normal profiles |
| session_trace | none | Retained host/context artifact handler/schema | Not in normal profiles |
| build_firmware | none; optional environment | Host PlatformIO build | Host process; source-bound build + artifact manifest |
| verify_requirements | checks | Host source/contract independent model review | Model request/evidence; not physical verification |
| run_simulation | none | Host Wokwi adapter | Host process/network as configured; virtual evidence |
| run_command | argv | Schema/alias retained | **Denied/unwired** in normal profiles |
| project_doctor | none | esp32_tools local inspection | Read; configuration/host hints, no execution |
| inspect_symbols | symbol | Lexical definitions/references | Read; at most 80 source files/100 results, not compiler truth |
| resolve_dependency | query | Host PlatformIO registry search | Network/process; bounded package IDs, not verified versions |
| search_docs | query | Curated official documentation catalog | Read; seven curated entries |
| fetch_docs | url | Public HTTPS official-domain pinned fetch | Network; bounded response/text, redirect/DNS checks |
| validate_hardware_config | none | Local embedded source pin/bus heuristics | Read; `hardware_verified=false` |
| run_unit_tests | environment | Trusted native PlatformIO test handler | Host process, 180s; actual executed summary required; **not OS isolated** |
| analyze_firmware | none | Host current build diagnostics/sizes | Read current evidence; no device proof |
| observe_serial | none; duration_seconds | Host already-connected monitor snapshot | 1–15s observation, 20s registry bound; cannot open/reset/flash |
| decode_crash | addresses | Exact current ELF/toolchain decoder | Host argv process; at most 32 validated addresses |
| verify_acceptance | none | Host task/source-bound evidence report | Reads trusted collected checks; cannot model-assert success |

**Permissions/restrictions:** reads belong to registered workspace/stage and sensitive-path/content rules. Writes require edit/repair/review grants. Build/test/simulation and network need host-wired services/prerequisites; native tests also require stored project trust, exact native environment and GCC availability. Flash is not a canonical model tool. All results are typed status/classification/output/observations; returned text is bounded before context and remains untrusted. File changes and tools create journal/activity evidence, but a returned string alone is not verified execution.

**Timeout/cancellation:** tool definitions publish limits, but these are not a universal OS watchdog around every local Python function. Fetch uses its own approximately 10-second I/O deadline/1MB raw/16K text; new local tools default 15 seconds in metadata; native tests 180 seconds; serial observation 20 seconds. Build/simulation use host process deadlines and run budgets. Local synchronous scans can still consume work before cancellation. Mutations are fenced before publication; host processes use run ownership. A test native executable has process lifetime control but no device/filesystem sandbox.

**Bypass families:** old `backend/tools/tool_registry.py` dispatches five low-level typed adapters without the canonical model grant wrapper; legacy generation publishes directly; manual Files API writes intentionally bypass agent staging; human terminal is a separately authorized shell. These are distinct trust surfaces, not evidence that `run_command` is secretly enabled.

## 13. Workspace / ChangeSets

Normal agent file mutation uses a full managed stage or immutable lower workspace + private overlay. `ToolPermissionPolicy.resolve_path` checks canonical relative paths, traversal, absolute/drive/UNC/ADS/reserved names, sensitive directories, symlinks and Windows `st_file_attributes` reparse bits. Writes are UTF-8/size/content bounded; atomic temp/fsync/replace and publication fences protect individual changes. Move/delete are represented as staged changes, not arbitrary direct host paths.

`workspace` → snapshot/baseline hashes → stage/overlay edits → compare actual bytes → ChangeSet with diff/base/staged manifest → verification/review/risk policy → apply with conflict checks → fresh revision/memory invalidation. Undo verifies applied hashes before restore. Files manually edited after planning can produce a conflict rather than silently being overwritten.

**Crash defect:** [ChangeSetService.apply, 298–398](D:/projects/ForgeX-Phase6-Final/backend/changes/service.py:298) validates the change batch and writes each file atomically, but lacks a durable multi-file publication/recovery transaction. [undo, 400–478](D:/projects/ForgeX-Phase6-Final/backend/changes/service.py:400) requires applied status. An audit subprocess exited after the first publication: `a.txt=after`, `b.txt=before`, persisted `pending`, undo rejected `CHANGE_SET_NOT_APPLIED`, apply rejected `CHANGE_CONFLICT`. Exception rollback is not process-crash recovery.

**Staging is not universal:** manual editor CRUD is deliberately direct; legacy `/execute` → main → GenerateCodeHandler `_write_project_to_workspace` changes existing active files. Native tests can write beyond their CWD because host process execution is not filesystem isolated. Runtime logs/settings/metadata are also host writes, not ChangeSets. No direct normal editing-tool traversal escape was established; the verified boundary failures are alternative readers and host execution.

Checkpoint copy/retention are stronger than the index/skills walkers: file identity checks, reparse rejection, bounded total files/bytes, immutable hashes and owned-stage cleanup references. Consolidate around those helpers. Normal filesystem validations still have TOCTOU assumptions absent OS handle-relative traversal/locking; physical manifests apply stronger immutable-file checks. This audit does not prove resistance to every concurrent hostile filesystem race.

## 14. Memory

`ProjectMemoryService` stores `memory_id/project/kind/value/source_type/source_id/source_hash/verified/confidence/revision/affected_paths/board/status/timestamps` in orchestration SQLite. User choices are narrowly regex bounded and exclude credential-key names. Source observations do not expose a generic model `memory_write` tool; host code writes memories for accepted choices, applied ChangeSets and successful builds.

Retrieval selects active verified entries, invalidates incompatible revision/board/path evidence, and injects bounded summaries into context. Memory is principally explicit project facts with deterministic filtering/search; it is not a complete semantic/vector memory system. Recency/relevance/TTL policy is limited. Session observation compaction is a separate mechanism.

**Safety gap:** [orchestrator.py 1979–1990](D:/projects/ForgeX-Phase6-Final/backend/agent_runtime/orchestrator.py:1979) stores `result.message` as `verified_build` while hashing build-result JSON as provenance. In [autonomous_loop._result, 1797–1859](D:/projects/ForgeX-Phase6-Final/backend/agent_runtime/autonomous_loop.py:1797), contract-bearing successful write tasks replace the final message with a host summary. Contract-free build-only paths preserve planner summary. A successful compile therefore can endorse extra model prose as verified memory without proving the prose. Derive persisted build facts from host metadata for every path, and keep model narrative explicitly unverified.

No live model hallucination was persisted by this audit; this is a traced code path. Hashing provenance authenticates the build payload, not semantic truth of a different summary string.

## 15. Skills

`SkillRegistry` searches, in order: project `.forgex/skills`, project `.agents/skills`, then packaged `builtin_skills`; first named occurrence wins. The three actual built-ins are `firmware-review`, `platformio-repair`, `serial-debug`. It discovers `*/SKILL.md`, uses first nonblank text as a label and lazy loads by a safe name (alphanumeric/underscore/hyphen, ≤128 characters). Reads cap at 96,000 bytes and returned content at 24,000 characters. It does not parse a comprehensive plugin/frontmatter execution model and has no global user-skill root.

`load_skill(name)` → executor/policy → registry → bounded skill text → observation/context. Named loading rejects arbitrary `../file`/absolute path arguments.

Nevertheless [_read_skill, 10–20](D:/projects/ForgeX-Phase6-Final/backend/agent_runtime/skills.py:10) treats absent `os.path.isjunction` as false. Python 3.10 lacks that API; `.agents/skills` can resolve outside the project and containment is then checked against the already-escaped skills root. The audit junction loaded an external harmless marker. Intended release Python 3.13 has an additional check, but relying on interpreter-version detection instead of common reparse enforcement is inconsistent and does not qualify all ancestor races. Skill content also lacks a uniform content-secret gate.

## 16. Subagents

Live specialists are Python **Explore, Review, Verify**, created by a parent host handler. They receive independently assembled task/project context, verified memory and skill labels, not an unrestricted parent history or inherited mutation privileges. Role selection uses explicit model-role bindings if configured; otherwise current selection. The model can request a role/task, while the host fixes tools/limits.

Defaults are eight turns and 24 tool calls, clamped to twelve/forty. Parent concurrency is two and physical provider/job budgets are shared. Read policies omit spawn and writes; Verify may invoke typed build. This prevents recursive unlimited spawning through the current tool surface. Cancellation propagates and bounded child summaries/observations are merged into the parent.

A Verify child can answer from inspection without running a build. Its success result is not sufficient to mark the parent compiled or hardware-qualified. Error/progress events remain attributable to child identity. The shadow Harness has separate child/provider classes, but no registered product providers and no execution RPC; do not count them as another enabled subagent system.

## 17. MCP

**MCP IS NOT IMPLEMENTED.** Searches covered MCP / Model Context Protocol / server/client / stdio / SSE / Streamable HTTP / tools/list / resources/list / prompts/list and the repository's imports/call paths. SSE activity streams and a bespoke Harness RPC server are present; neither implements MCP protocol discovery/transports. Vendored comments/future architecture language do not provide a client/server.

The best integration point is a **Python-owned tool adapter** after settled tool-schema validation and before `OrderedToolScheduler` execution, using the same capability grants, registered workspace boundaries, timeout/cancellation, result bounding, journal provenance and non-replayable action semantics. Resource/prompt retrieval should feed ContextAssembler through the same secret/path/size gates. Maintain per-server consent and tool effect classification; a remote server must not inherit local flash/file privileges by name. This is a recommendation, not existing behavior.

Consolidate tool result/effect contracts and filesystem readers before MCP. A protocol bridge that bypasses these boundaries would amplify current inconsistencies. MCP absence alone is not a blocker for a beta that does not promise it.

## 18. Harness

[HarnessRuntime.invoke](D:/projects/ForgeX-Phase6-Final/harness/src/runtime.ts:26) returns `authoritative:false`, `mode:shadow`, `runAuthority:python-agent-orchestrator`. Implemented RPC covers status, provider descriptors, tool/plugin/provider listings and session list/create/events/append/child listing. There is no agent-execute RPC. The constructed `ToolPipeline`, `PluginRegistry` and `SubagentRuntime` are not populated with production handlers/providers/plugins. `WorkflowEngine` is constructed but disconnected from real ForgeX prompt execution.

The event store has per-session promise serialization, fsynced JSONL, operation-id deduplication and checksum chains. Its `.writer-lock` uses exclusive file creation, with no stale owner expiry/recovery; a process crash can strand a shadow session. Plugin manifests are validated/listed, not loaded/activated. Sandbox command validation has no default actual OS isolation runner. Provider catalog listing is descriptors, not account-backed model execution.

**Classification:** optional shadow experimentation, not production authority and not a demonstrated migration target with execution parity. Python/TypeScript duplication covers tools, workflows, session events, subagents and plugin/sandbox abstractions. Current disabled status limits immediate risk, but indefinite dual-runtime maintenance is costly. Preserve shadow contract tests/provenance as needed; decide deliberately whether to keep it diagnostic, consolidate shared schemas, or remove disconnected execution abstractions. Do not hand execution authority to it merely because TypeScript interfaces compile.

## 19. Embedded Runtime

The best-connected product path is **PlatformIO + Arduino + classic `esp32dev`**. Project import/profile inspection reads environments, board/framework, source layout and compile database. Board detection uses pyserial port metadata and known families, not a universal live hardware interrogation protocol. The registry recognizes ESP32, S3, C3, Uno and STM32; recognition is broader than qualified firmware upload support.

Source changes occur in the agent stage; PlatformIO builds the selected environment with validated INI constraints and owned subprocesses; compiler diagnostics feed repair/verification; final source revision and immutable images bind the build result. Compiler warnings, artifact existence and source-bound requirement review are recorded separately. `project_doctor`, source symbol/pin/bus inspection, dependency lookup and crash decoding are genuine implementations, with bounded heuristic scope.

The normal manifest adapter restricts sealing to `board=esp32dev`, Arduino and classic ESP32 with known upload offsets/partition layout. S3/C3 support in detector/manifests does not establish a connected standard build-and-flash path for those boards. AVR/STM low-level adapters exist but were not qualified here. An experimental ESP-IDF adapter requires explicit enable/trust and is not instantiated in the normal app composition. Framework enums, template generation and offline profile detection are not equivalent to complete Arduino CLI/ESP-IDF/STM32 workflows.

Dependency lookup searches the PlatformIO registry for bounded exact IDs and labels versions unverified. Build configuration rejects arbitrary project scripts, local/VCS dependency paths, package overrides, unsafe external compiler flags and custom upload commands. These are useful compiler-invocation controls, but running native tests is ordinary host code execution.

Simulation runs Wokwi CLI when its executable/token are configured. It uses generated fixture/artifact/source bindings and virtual serial acceptance data. No live simulation ran during this audit. Simulated acceptance, source model review, compile/test execution and physical hardware qualification must remain distinct.

## 20. Build System

`PlatformIOService.build` → validated project/environment/configuration → `SubprocessManager` → actual `pio run` → compiler/process result → sealed upload manifest (when supported). Manual build API adds durable operation identity and changed-source detection through `BuildOperations`; a restarted incomplete operation is marked interrupted rather than replayed blindly.

**Real compiler experiment:** an audit-only `esp32dev` Arduino project compiled against the already installed PlatformIO core 6.2.0 and Espressif32 platform 7.0.1. This was an actual compiler/toolchain execution, not a mocked build fixture. The initial sandbox attempt failed because PlatformIO could not write its existing user cache/lock; the permitted rerun succeeded in approximately 62.57 seconds. No global software was installed and no upload target was invoked.

| Observed output | Value |
|---|---|
| RAM | 21,464 / 327,680 bytes |
| Firmware flash allocation | 266,973 / 1,310,720 bytes |
| Application binary file | 267,344 bytes |
| Sealed upload set | Bootloader `0x1000`, partitions `0x8000`, OTA data `0xe000`, application `0x10000` |
| Manifest SHA-256 | `536f9f5227ecad8131230346b1cf89f2604b77dd9962a1f25207c7c2c793a4ae` |
| Firmware SHA-256 | `34c6f0c4ecba1bf2fd148d87388d476c752d2b0beabeb59f1fef3a9c5efcbac6` |
| Result flags | `flash_ready=true`, `hardware_verified=false` |

See [compile result](esp32-compile.json), [actual compiler output](esp32-compiler-output.log) and [sandbox failure](esp32-compile-sandbox-failure.json). A small fixture does not prove all generated tasks, libraries, environments or boards compile. Tests exercise many failure/repair classifications, but real remote-model-generated firmware quality remains unqualified in this checkout.

## 21. Hardware / Flash / Serial

**Actual intended execution:** user firmware request → intent/contract/run → staged source changes → selected-environment build → source/current-hash requirement review → manifest seal → ChangeSet review/apply as required → host prepares flash approval → user explicitly confirms → host validates full binding and atomically consumes authorization → immutable images flash to selected port → optional monitor start → serial observations/explicit acceptance checks → final result with separate device qualification.

Approval bindings include run/project/workspace revision, board family, selected port/stable device metadata, environment, build/application/upload-set manifest identity and action digest. Expiry is bounded (normally fifteen minutes, with clamped limits); consumption is one-use in SQLite. The host revalidates before use, and action journal state distinguishes prepared/started/completed/unknown interruption. An consumed/uncertain hardware action is not automatically replayed.

**Can approval for one image be reused for another?** The normal orchestrated path rejects changed hashes/manifests/workspace/board/environment/port and replayed authorization. This is a code-established and test-covered binding. It was not exercised on a physical board. `ProjectFlashService` consumes sealed images rather than silently rebuilding a different firmware after consent.

Device binding confidence is strong with USB serial+VID/PID, medium with VID/PID+port, weak with port/board. Weak identities are accepted and labeled; physical replacement/port reuse between validation and upload remains a qualification concern. Preparation can describe an unbound port, while actual upload requires a selected port; late port binding should be tested as an approval UX case, not treated as permission to upload arbitrary hardware.

`flash_firmware.py` can invoke typed esptool/AVR/OpenOCD/STM tools, but it is a trusted low-level library, not a model schema. It lacks independent approval consumption and relies on the caller. The old HTTP `/flash` route is disabled. A legacy internal task can supply build/flash metadata, but the HTTP ExecuteRequest does not supply the required build artifact; **no functioning unauthenticated or ordinary model direct-flash HTTP bypass was demonstrated**.

The broader invariant “a model cannot affect hardware without explicit authorization” still fails as a guarantee because trusted native test code executes on the host and can call device APIs/tools itself. GCC/native test prerequisites were missing here, so that escape was established from process architecture, not by running a malicious native binary or touching a device.

SerialRuntime is the single live transport owner, with port claims, stable-identity reconnection, blocking read/write offload, bounded observations and cancellation. Typed `observe_serial` only reads an already connected monitor. Human/deterministic monitor start opens pyserial; the open call does not force DTR/RTS inactive before opening, so USB auto-reset is possible and connect drains input. No physical boot-marker/reset/reconnect qualification was performed.

Release scripts demand repeated artifact-bound physical build/flash/nonce cycles, but this audit performed **zero flashes and zero serial acceptance cycles**. Logical authorization tests and a compiled binary do not substitute for those gates.

## 22. Security

### Boundaries inspected

| Surface | Verified implementation | Remaining gap / classification |
|---|---|---|
| Local API | Loopback bind, exact backend Host port, constrained Origin, random local token; middleware covers all non-health routes | Health is token-free for discovery; no evidence of remote unauthenticated product control |
| CSRF-like browser access | HttpOnly SameSite Strict cookie, Origin/Host checks; native missing Origin still needs token | Cookies are host-wide across ports; adjacent hostile local services are outside a strong OS isolation model |
| WebSockets | Cookie/bearer + Host/Origin check before accept; query token not accepted | Terminal socket input/output limitations differ from REST/process manager |
| Electron | contextIsolation, no nodeIntegration, packaged renderer sandbox, narrow preload, restricted popups/navigation | Dev `--no-sandbox`; sender identity validation/parent crash containment deserve further hardening |
| Markdown/XSS | Raw HTML skipped; normal URL transformations; code HTML comes from escaped highlight.js output | `dangerouslySetInnerHTML` is a real sink whose escaping invariant must be retained; no XSS exploit was reproduced |
| File tool paths | Normalized relative names, protected content/files, symlink/reparse component rejection, staged atomic writes | Weaker skill/index/instruction/legacy paths; concurrent filesystem races unqualified |
| Builds/processes | shell=false typed argv, config scripts/package restrictions, owned tree cleanup | Native test programs are not an access-control sandbox |
| Hardware | One-use bound approval and immutable upload set | Unrestricted trusted native code can bypass typed host controls; weak identity/hotplug qualification absent |
| API keys | Windows secure store/backend-only, masked settings; worker stdin rather than arguments | Linux/macOS persistence unavailable; instruction/skill secret scanning inconsistent |
| Logs/export | Bounded instance logs, key/field/bearer redaction, prompt/source exclusions in diagnostic export | Redaction is heuristic; inherited process environment and raw tool outputs require continued scrutiny |
| Telemetry | Application diagnostics are local; desktop Next telemetry disabled | PlatformIO/dependencies have their own telemetry behavior; do not claim no external telemetry globally |
| Arbitrary eval/shell | No current agent arbitrary shell handler found; human terminal intentionally has a shell | Host-native test execution can invoke arbitrary commands indirectly |

**No Critical/P0 product exploit was established.** High issues are conditional host-native execution/device isolation and Python 3.10 skill boundary escape; the dependency advisory is High at dependency level, with app exploitability not established. Medium issues include index/secret disclosure seams, legacy staging bypass, publication crash consistency, memory labeling and cleanup/health inconsistencies. Development sandbox disablement and stale configuration are Low/Informational depending distribution.

### Current dependency security results

The project-defined npm audits were attempted in the sandbox, failed to reach the registry, then rerun with permission. **Desktop/root audit exited 1: 11 High affected package entries, zero Critical. Frontend audit exited 0: zero entries.** These are not eleven independent exploitable ForgeX bugs: the results trace to one `node-forge` signature-verification advisory propagated through SAP provider dependencies.

Installed chain: `@jerome-benoit/sap-ai-provider-v2@5.0.3` → SAP cloud connectivity → `jks-js@1.1.7` → `node-forge@1.4.0`. The engine contains a dynamic SAP SDK factory. Whether a vulnerable RSA verification operation is reachable in actual configured ForgeX SAP use was not proven. [GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv) reports affected node-forge versions through 1.4.0 and no listed patch at the time of this audit. Treat automatic npm downgrade/fix suggestions as candidates needing compatibility review, not instructions to apply blindly.

Evidence: [root audit](npm-audit-root.json), [frontend audit](npm-audit-frontend.json), [installed dependency path](npm-forge-dependency-path.json). No dependency was changed. Python dependency consistency passed `pip check`; Python vulnerability advisory scanning was **unable to run** because `pip_audit` is absent. Those are different checks. CI includes advisory gates, but configured CI does not prove a successful run for this working tree.

## 23. Frontend

The real root is `frontend/app/page.tsx` → IdeErrorBoundary/ForgeXDialogProvider → NexusShell. Main state is React hook state/refs and some localStorage; there is no external central Redux-style authority. Backend remains authoritative for durable runs/messages/build results; hooks cache/project UI state and use generation/sequence guards.

| State owner | Scope | Observed strengths / risks |
|---|---|---|
| usePromptForgeWorkspace | Projects/imports, files/dirty tabs, builds, devices/serial, compatibility methods | Single-flight requests and stale-result guards; enormous cross-domain coupling |
| useForgeAgentSession | Session/run/job, steering, approvals, replay/streams/recovery | Bounded activity and adoption of durable replacement runs; many interacting refs/effects |
| useChatSession | Chat selection/messages/stream reconciliation | Late-response guard, bounded/coalesced polling and durable completion recovery |
| useForgeXSettings + write queue | Confirmed/pending preferences and publish/rollback | Serialized/coalesced writes and rollback implemented |
| useModelRuntimeState | Catalog/model UI loading/cache | Multiple selection representations/settings/session compatibility stores remain |
| useTerminalSession | Human terminal WS/session lifecycle | Separate from agent authority; backend termination weakness remains |

TypeScript AST measured `usePromptForgeWorkspace` at 1,097 lines (335–1431), `useForgeAgentSession` at 1,021 (48–1068), NexusShell at 752 (102–853), ModelSettingsPanel 570 and TaskCanvas 528. Large callbacks/components complicate stale-closure and optimistic-state review even where tested guards currently work.

Current Nexus flash button directs the user through Forge approval and returns without using legacy direct `/flash`; API client compatibility methods alone do not prove UI use. Older IDE/prompt components and workflow methods should be classified by root import/caller reachability before removal. Some shared old components still serve current editors/settings, so deleting an entire directory would break live paths.

Browser/UI-state tests validate navigation, drafts, model settings, stale response rejection, activity, recovery and workspace/background UI. Browser routes are mocked fixtures and some unmocked polling printed ECONNREFUSED to backend 8000. They are not provider/backend/device end-to-end qualification. No specific stale-closure bug was asserted solely because a hook is large.

## 24. Electron

`main.ts` is the launch/security owner; BackendManager and FrontendManager supervise their owned child processes, with optional HarnessManager. `window-manager.ts` supplies renderer constraints; preload exposes typed folder/status/diagnostics/background operations. User data separates packaged runtime from program resources; the backend receives launch-local tokens and embedded executable paths.

Standalone Next identity challenge and backend source/auth fingerprint checks are strong existing protections against talking to an unrelated loopback process. Errors produce desktop error UI rather than silently adopting an incompatible server. Windows child logging/closed-pipe behavior has dedicated tests/scripts.

Gaps are distribution qualification, development global sandbox disablement, parent process-tree containment on abrupt desktop death, and large reliance on inherited environment/executable/resource paths. `paths.ts` uses parseInt and accepts numeric prefixes in malformed port strings, while backend validation uses strict integers; resolve this small configuration mismatch. Node/Electron compilation and tests passed, but no new installer or GUI desktop launch/crash test was performed for this audit.

## 25. API

All **124 decorator-declared HTTP/WebSocket entries** were structurally mapped in [api-routes.json](api-routes.json) and the [human route inventory](Provider-and-API-Inventories.md). This includes non-router entries; the deployment's common frontend proxy prefix is `/api/forgex`, with retained `/api/promptforge` compatibility. The backend route prefixes are not those frontend URLs.

| Frontend/API surface | Service/runtime | Persistence / authority |
|---|---|---|
| agent sessions/messages/jobs/runs | ProductAgentService → AgentOrchestrator + selected loop | Shared session/orchestration SQLite |
| chat sessions/messages | ChatService → model router | Durable session/message store |
| changesets/diff/apply/undo | ChangeSetService | JSON metadata + stage/baseline files |
| project/import/recent/relocate | ProjectService/ProjectImportService | Project metadata and external JSON registry |
| files CRUD | Project file service | Direct human editor files |
| build operations | BuildOperations → PlatformIO | SQL identity/results + build/artifact files |
| flash legacy | Rejecting route | Must use orchestrated authorization |
| monitor/device | SerialService/Runtime + BoardDetector | Live transport state; bounded observations |
| terminal | TerminalService | Live human sessions, no durable replay |
| models/settings/v2 gateway | Router/registry/selection/settings | SQLite/credential store + settings JSON |
| execute | main legacy planner/coordinator/generation | In-memory execution hub + active project writes |
| diagnostic/health | Logging/metrics/service container | Bounded logs/runtime identity |
| harness | Optional authenticated shadow client | Separate shadow JSONL, not main authority |

Authentication is middleware-backed, with additional sensitive handler checks. Route schemas constrain important IDs, prompts, settings and plan binding. Body-size middleware only checks Content-Length for POST `/agent-runtime/runs`; it is not a global/chunked request limit. Token/Origin protection reduces the unauthenticated attack surface, but oversized authenticated requests remain a hardening opportunity.

Two different `/tools` and `/tool-catalog` handlers share the Python function name `agent_tool_catalog` at lines 163 and 227. FastAPI decorators retain their distinct function objects, so this is a naming/maintenance defect, **not proof one route disappeared**. Compatibility model APIs duplicate some state/response models and updates across stores are not universally atomic. Unused frontend API methods do not make an authenticated route dead; `/execute` is the important example.

## 26. Persistence

| Data | Actual owner/storage | Durability/recovery |
|---|---|---|
| Workspaces/projects | ProjectService manifests/managed dirs; imported-folder registry JSON | Atomic managed publication/registry replacement where implemented; legacy helper writes weaker |
| Sessions/messages | AgentSessionStore, same SQLite as orchestration | Legacy import, schema row metadata, process-local cache |
| Runs/jobs/events/intake | AgentOrchestrationStore | WAL, transactions, sequences/checksums, leases/fences, bounded migration backups |
| Tool/action journal | Orchestration SQLite | Prepared/started/settled semantics; interrupted non-replayable actions remain unknown |
| Context/recovery/checkpoints | SQL metadata + hashed retained files | Source/hash validation, safe copy/retention |
| Project memory | Orchestration project-memory table | Provenance and invalidation; summary-label gap |
| Approvals | SQLite | Expiry/fingerprint and atomic one-time consumption |
| Model/provider settings | Model router SQLite by default; JSON override/legacy migration | Credentials separated into secure store |
| Model/role/session bindings | Selection SQLite | Overlap with compatibility settings; separate transaction boundaries |
| Provider admission/health | Dedicated SQLite sibling databases | Lease expiry; default path skips breaker |
| Usage | SQLite tracker, older JSONL migration | Normalized reported usage; no account billing authority |
| ChangeSets | Atomic JSON metadata + snapshots/stages | Individual-file durability; **no full crash transaction** |
| Build operation identity | Orchestration SQLite | Dedup/fingerprint, interruption status, observed stale source |
| Preferences | Atomic settings JSON | fsync/replace + process lock; corruption may fall back to defaults |
| Logs/metrics | Local JSONL/files/in-memory metrics | Bounded/rotated and heuristic redaction |
| Harness | Independent hash-chained JSONL | fsync/lock; crash lock can strand shadow writer |

`AgentOrchestrationStore` uses schema migrations through user_version 3, SQL transaction discipline and event/journal integrity/corruption handling. Not all subsystem schemas share one version or transaction. SQLite transactions help concurrent requests; process-local caches/locks still assume the single authoritative desktop backend. Multi-process distributed session consistency was not qualified.

`backend/state/persistence.py` defines a useful general SQLite manager, but no default product constructor uses it; WorkspaceManager's optional persistence remains None. Do not report its schema as current workspaces/sessions authority. Legacy workspace metadata/log helpers use direct JSON/text writes and overlap stronger artifact/project services.

Actual crash evidence from ChangeSet publication shows a durability hole despite other crash-recovery tests. Database checksum/migration classes and passing fixture tests are not evidence that every state transition is atomic across DB, stage files and active project files.

## 27. Test Results

Installed environment: Python **3.10.6** in the existing virtual environment, Node **24.14.0**, npm **11.9.0**. Release scripts target Python **3.13.15** and CI Node **22**; therefore these observations do not substitute for the release interpreter/toolchain matrix. Dependencies were already installed. No random global software was installed.

| Group / actual command | Result | Evidence / qualification |
|---|---|---|
| `.venv/Scripts/python.exe -m pytest tests/unit ...` | **2,109 passed, 1 failed, 4 skipped** | [unit log](python-unit.log), [JUnit](python-unit.xml), 596.87s; pytest exit 1 |
| `pytest tests/api tests/integration tests/benchmarks ...` | **122 passed** | 99 API + 14 integration + 9 benchmark; [log](python-api-integration.log), [JUnit](python-api-integration.xml), 211.59s |
| Isolated heartbeat test, three runs | **3/3 passed** | [run 1](heartbeat-rerun-1.log), [2](heartbeat-rerun-2.log), [3](heartbeat-rerun-3.log); do not add these to unique baseline total |
| `node --test electron/*.test.mjs harness/tests/*.test.mjs model-router/tests/*.test.mjs scripts/*.test.mjs` | **106 passed, zero failed/skipped** | [Node log](node-tests.log), 21.34s; includes release/security boundaries |
| `npm run test:ui-state` | **79 passed, zero failed** | [UI-state log](ui-state-tests.log), 6.24s |
| `npm run test:conversations` | **52 assertion cases logged passed; runner did not settle** | [browser log](playwright.log); interrupted stuck teardown, exit 1; mocked browser transport |
| Junction + ChangeSet crash probes | Reproduced both read escapes and crash inconsistency | [safety-probes.json](safety-probes.json); no outside-workspace writes |
| Instruction secret + terminal driver probes | Secret in rendered context; stuck post-kill wait | [additional-probes.json](additional-probes.json); synthetic key/fake driver, no live disclosure/process exploit |

Unique initial Python result: **2,231 passes, 1 failure, 4 skips, 2,236 outcomes. The initial suite did not pass.**

### Failure classification

`tests/unit/test_agent_orchestrator_v2.py::test_orchestrator_heartbeats_session_lease_during_long_workflow`, line 646, expected `renewals >= 2` but observed one. Its fake execution window is approximately 80ms with a 10ms heartbeat interval; concurrent test/build load can consume most of that window. Three isolated passes support **likely timing-sensitive test fixture/flakiness**, not a proven durable lease-loss regression. The original failure is retained. Replace elapsed tiny sleeps with synchronization/observable progress; do not silently weaken the assertion.

Four skips concern unavailable directory symlink creation. These do **not** certify Windows junction safety; the audit's junction probes succeeded and found defects. Tests mention security features but their specific skipped cases did not run.

Browser assertions all ran; subsequent teardown never returned its final successful summary/exit and was stopped. Cause is **unresolved environment/test-runner process teardown**, not a proven UI logic regression. Backend ECONNREFUSED output reflects fixture/unmocked polling, not a live backend end-to-end test. No failing product scenario was erased or silently fixed.

There are no standalone `tests/security` or `tests/release` directory groups in this checkout. Relevant security/release tests are distributed among unit/API/Node suites. Real cloud provider, OAuth login, physical flash/serial, Linux/macOS and exact-installation tests remain unverified. Passing mocked responses do not qualify those.

## 28. Build Results

| Gate | Actual result | Material limit |
|---|---|---|
| Python AST syntax | Zero parse errors across Python inventory | Static syntax only |
| `python -m compileall -q backend tests scripts` | Passed after permitted rerun | Initial two pycache write PermissionErrors were sandbox environment issues, not code syntax errors |
| `npm run typecheck:frontend` | Passed | Existing installed dependencies, generated types |
| `npm run lint:frontend` | Passed | No source fixes |
| Isolated frontend production build | Passed | `.next-audit-20261005`; five pages; main page 205KB, first-load JS 307KB |
| `npm run build:electron` | Passed | Includes model-router bundle, Harness TypeScript and Electron TypeScript; **does not build an installer** |
| Source-release content scanner | Passed | Source tree only; not current packaged resource scan |
| `pip check` | Passed | Dependency compatibility, not CVE audit |
| Root npm advisory audit | **Failed gate: 11 High affected package entries** | One propagated node-forge advisory; exploit reachability unproven |
| Frontend npm advisory audit | Passed, zero entries | Advisory-service snapshot on audit date |
| Python advisory audit | Unable to run | `pip_audit` missing; no package installed to conceal this gap |
| Actual ESP32 fixture compilation/sealing | Passed | No physical upload; classic esp32dev only |
| Installer/signing/full release staging | Inspected, **not executed** | Current interpreter differs from required 3.13.15; exact artifact, certificate and clean-machine qualification absent |

Logs: [compile](python-compile-escalated.log), [typecheck](frontend-typecheck.log), [lint](frontend-lint.log), [frontend build](frontend-build.log), [desktop/runtime compilation](build-electron-harness-router.log), [source scan](release-content-scan.log). The first compiler sandbox failure and pycache errors are preserved separately. Existing historical installer/build outputs were not credited as current-checkout success.

## 29. Bugs

Priority is engineering urgency; security severity is separate. **P0: none established.** A static issue can be real without a physical exploit, but conditions and reproduction limits are explicit below. Fix recommendations are proposals only.

### P1

**FX-001 — Host-native execution does not enforce the hardware/filesystem restriction.**

- **Security:** High, conditional on explicit project trust, native environment and toolchain availability.
- **File/function:** [PlatformIOService.test_native, 494–521](D:/projects/ForgeX-Phase6-Final/backend/services/platformio_service.py:494), canonical `run_unit_tests` host callers.
- **Description/impact:** model-writable C++ tests run under ordinary user privileges through `pio test`. They can execute commands, open serial/device APIs and write outside stage. `hardware_used:false` and native-platform validation do not confine the executable. This undermines a universal “all physical actions require bound approval” claim.
- **Reproduction:** trusted project with native test environment and GCC; model edit introduces a harmless test writing a marker outside its stage; invoke typed run_unit_tests. **Not executed here because GCC/native prerequisites were absent; verified from argv/process architecture.** Never use real flashing as a security reproduction.
- **Recommended fix:** explicitly model native execution as broad host-code permission until a real OS sandbox excludes devices/secrets/outside writes; use a dedicated isolated test runner for the stronger invariant. Keep upload authorization separate.

**FX-002 — Skill junction escapes the selected project on Python 3.10.**

- **Security:** High for confidentiality/context integrity of an imported hostile project; version-dependent.
- **File/function:** [skills._read_skill, 10–20](D:/projects/ForgeX-Phase6-Final/backend/agent_runtime/skills.py:10), discover/load 42–83.
- **Description/impact:** fallback false for missing isjunction lets `.agents/skills` refer outside project. The resolved containment root is the escaped skill root, so an external SKILL.md is accepted and returned to model context.
- **Reproduction:** run [safety_probes.py](safety_probes.py) under this Python 3.10; harmless directory junction and external marker were loaded. Actual output in safety-probes.json.
- **Recommended fix:** common lstat reparse detection on every selected-root descendant/ancestor and final file identity; enforce selected project/built-in root identity independently of symlink resolution. Add junction tests that run without symlink privileges.

**FX-013 — Current desktop dependency advisory gate fails.**

- **Security:** High dependency advisory; ForgeX exploitability **unverified**.
- **File/function:** root package/lock SAP provider dependency; `model-router/src/engine.mjs` SAP dynamic factory.
- **Description/impact:** node-forge 1.4.0 carries GHSA-86w9-cpqp-85rv; eleven affected package entries propagate this issue. `npm audit --audit-level=moderate` exits 1, blocking the prescribed CI/release gate.
- **Reproduction:** project-defined root npm audit, with permission to reach registry; npm ls identifies SAP → jks-js → node-forge.
- **Recommended fix:** assess actual SAP keystore/signature usage, update or isolate/remove the optional vulnerable dependency through a reviewed compatible change; reevaluate lock/bundle/SBOM. Do not blindly accept npm's downgrade suggestion. A release exception would require explicit reachability evidence, not “tests passed.”

### P2

| ID / severity | File / function | Description and impact | Reproduction / evidence | Recommended fix |
|---|---|---|---|---|
| **FX-003 / Medium** | workspace_index.py, index construction/query | Windows junction walker reads/indexes beyond selected workspace; embedded/import walkers also need review | Runtime junction marker appeared in indexed candidate list; safety-probes.json | Shared reparse-safe walker + overall file/byte/time budget |
| **FX-004 / Medium** | execute route → main → GenerateCodeHandler.execute / `_write_project_to_workspace` | Reachable legacy generation changes active project outside modern staged review | Inspected authenticated HTTP call chain; write call in execute around line 404; no live external model used | Redirect compatibility route through canonical stage/ChangeSet authority; preserve API compatibility |
| **FX-005 / Medium** | ContextAssembler.assemble 75–86; skill loading | Instruction context bypasses ordinary secret/content/reparse gates and reads whole file before truncation | Synthetic sk-format key survived render; additional-probes.json; outgoing context path inspected | Bounded binary read + common secret/path policy on every context acquisition boundary |
| **FX-006 / Medium** | orchestrator 1979–1990; autonomous_loop._result 1797–1859 | Contract-free build-only model summary becomes verified_build memory though compile only proves build | Traced no-write task-contract branch, planner summary result and memory sink; no real hallucination injected | Persist structured host build facts; separate unverified prose from verified memory |
| **FX-007 / Medium** | ChangeSetService.apply 298–398 / undo 400–478 | Process crash can leave partially applied files and pending metadata; normal recovery refuses both operations | Fault subprocess exit 73 after first publication, next apply/undo errors; safety-probes.json | Durable publish journal/backups + startup reconciliation/compensating transaction; test kill after every step |
| **FX-008 / Medium** | TerminalService._stop_session 469–500 | Post-kill wait has no deadline; closed-session early path may skip pending task cleanup | Fake stuck driver reached kill and still timed out at 2.3s with session not closed; additional-probes.json | Bound kill and final wait, always settle pumps/tasks, reuse process owner/tree cleanup; stress queues |
| **FX-009 / Medium** | ModelRouterService._physical_slot 114–130; endpoint-health API | Default OpenCode paths skip breaker while endpoint circuit status remains available | Verified conditional return before circuit.require_available/slot; no live outage needed | Make protection mode/status explicit or reconnect circuit with recovery-aware semantics; test current default adapter |
| **FX-014 / Medium** | OpenCodeProvider.generate_event_stream, stdin drain / final reap | Configured attempt/write deadline does not bound stdin drain; cleanup waits lack explicit deadlines | Fake drain stalled beyond 0.1s total; external cancel at 0.3s killed child. Agent planner outer timeout mitigates normal agent use | Wrap spawn/write/read/final cleanup in explicit remaining-deadline bounds; test direct provider probes and cancellation |

FX-004 is not a workspace traversal vulnerability and manual editor writes are not themselves a bug. FX-008 proves missing termination bounds for a faulty driver, not that every real terminal hangs. FX-009 is partly deliberate design; the defect is presenting disconnected protection/health as relevant to current requests and leaving recovery ownership unclear.

### P3 / test infrastructure / documentation

| ID | Location | Problem / impact | Reproduction / recommendation |
|---|---|---|---|
| **FX-010** | harness/src/event-store.ts appendUnlocked | Crash leaves .writer-lock permanently blocking shadow session writer | Static exclusive-file lock/finally path; add owner lease/stale-lock recovery if Harness retained/enabled |
| **FX-011** | test_agent_orchestrator_v2.py heartbeat test | Tiny elapsed-time fixture flakes under concurrent load | One baseline failure, three isolated passes; use synchronization instead of short sleeps |
| **FX-012** | .env.example Codex settings; README Cerebras discovery | Users configure nonexistent OAuth or expect account-specific models | Search provider constructor/loader/list_models; remove or explicitly mark intent-only claims after implementation decision |

Browser teardown is recorded as unresolved environment/test-runner behavior, **not entered as a confirmed product bug**. Port parsing, body bounds, ConPTY queue growth, weak hardware identity and cross-session build contention are documented risks/investigation targets rather than exaggerated confirmed exploits.

## 30. Architectural Problems

### Duplication map

| Responsibility | Implementation A | Implementation B / C | Current authority | Risk / action |
|---|---|---|---|---|
| Agent iteration | AutonomousForgeAgentLoop | ForgeXToolRuntime; legacy workflow; Harness WorkflowEngine | Python orchestrator dispatch | High maintenance: unify semantic iteration with explicit workspace adapters |
| Tool execution | Canonical executor/grants/scheduler | Legacy five-tool registry; Harness ToolPipeline | Python canonical for modern runs | High boundary drift: one guarded host adapter seam |
| Provider runtime | ModelRouter/OpenCode worker | Old Python providers/LLM transports; Harness descriptors | OpenCodeProvider + Python routing | Medium: extract shared errors/types; retire unused transport branches only after caller proof |
| Retry | ModelRuntime/request_recovery | Old LLM/generation repair logic; vendor executors | Python managed retries; Node retries disabled | High: distinguish retry from repair, maintain physical-attempt accounting |
| Sessions/events | SQL session/journal/projector | Legacy progress hub; Harness JSONL | Python SQL for modern product | Medium: leave shadow diagnostic or consolidate contracts, not dual authority |
| Context | ContextAssembler + RouterPlanner | Two loop observation paths, child context, old generation prompts | Python host + model-native planner history | High: common context/evidence update rules |
| Workspace mutation | Stage/overlay/ChangeSets | Legacy generation; manual editor; old WorkspaceManager | Modern ChangeSets for agent | High: preserve deliberate human editing while closing agent bypass |
| Verification | Contract review/acceptance + build evidence | Legacy validators/repair; Verify child; simulation | Host current-source evidence | High: one evidence contract, separate model/compile/sim/device levels |
| Subagents | Python bounded specialists | Harness child-provider classes | Python | Low immediate / medium future drift |
| Skills | Python SkillRegistry | Generic/vendor tool abstractions and docs | Python named skill loader | Medium security: common safe read policy |
| Build | PlatformIOService/BuildOperations | FirmwareBuilder/legacy adapters/ESP-IDF experiment | Typed PlatformIO product path | High: normalize outputs and authorization consistently |
| Hardware actions | Orchestrator approvals → flash service | Low-level flash tool + trusted legacy calls/native execution | Orchestrator for normal UI | High: explicit single internal gateway plus process sandbox policy |
| Persistence | Orchestration/model SQL + typed project service | Generic unused PersistenceManager; JSON compatibility | Composed live services | Medium: ownership unclear from class names/docs |

The long-term architecture should keep Python as host authority and the Node engine as a request adapter. Splitting the same task's reasoning/state/tool execution between Python and a partially wired shadow Harness would require a major distributed lifecycle protocol with no demonstrated benefit here. Consolidation is safer than a rewrite.

There is no evidence that every duplicate file is safe to delete. For example ApiPlannerProvider's class is unused in default execution but its schemas/helpers are imported; old LLM modules contain still-live errors/contracts. Deleting by filename or “legacy” marker would be incorrect.

## 31. Technical Debt

Owned source has **55 files over 500 lines**, **17 over 1,000**, **84 function/callback blocks over 100 lines**, and **44 over 150**. Python AST and TypeScript AST supplied the sizes; CSS LOC are files, not functions. Nested callbacks count as blocks, so these are maintainability indicators rather than independent business-function counts. Full definitions/ranges are in inventory.json and typescript-analysis.json; measured hotspots in evidence-summary.json.

Broad Python `except Exception`/bare-handler scan found 146 owned handlers (147 including tests) and 16 pass-only handlers overall. Some intentionally tolerate hint/diagnostic failure; others can conceal partial state. These counts do not themselves prove bugs. Owned search matches were TODO 3, FIXME 0, HACK 0, DEPRECATED 1, legacy 91; substring matches include names such as todo_write. Documentation/vendor comments have different counts. Few TODOs do not establish feature completeness.

### Twenty highest-risk maintainability hotspots

| # | File / measured hotspot | Why it is risky | Incremental action |
|---:|---|---|---|
| 1 | orchestrator.py, 3,029 LOC; `_execute_autonomous` 467 lines | Jobs, lease fencing, grants, graph, recovery, apply and hardware in one object | Extract lifecycle/state transition, publication and hardware services while retaining owner |
| 2 | tool_runtime.py, 1,466 LOC; `run_product_async` 1,012 | General loop completion/context/retry branches duplicate embedded loop | Common cycle controller + explicit overlay adapter |
| 3 | autonomous_loop.py, 2,075 LOC; `run` 621, `_build_once` 308 | Model decisions, tool observations, build/dependency repair and acceptance coupling | Extract typed evidence/build-repair strategies |
| 4 | api/routes/agent_runtime.py, 2,489 LOC; append 598 | HTTP request, routing, plan/steering/model/workspace state combined | Thin use-case handlers with stable response schemas |
| 5 | api/app.py, 946 LOC; create_app 710 | Large dependency graph and nested workflow closures obscure live authority | Explicit service factory/composition modules |
| 6 | code_generation_service.py, 2,559 LOC; chunk project 330 | Legacy prompts, output validation, chunk mutation and repair | Move callers to canonical stage then retire redundant branches |
| 7 | services/llm_service.py, 1,591 LOC | Shared contracts mixed with old transport implementations | Separate contracts/errors from retired transports |
| 8 | api_planner_provider.py, 1,724 LOC | Old planner class and still-live helpers share module | Extract shared schema/normalization, remove class only after caller proof |
| 9 | router_planner.py, 1,125 LOC; `_generate` 283 | Native history/schema fallback/repair/stream buffering and metadata | Split request/history/normalization policies |
| 10 | frontend/use-promptforge-workspace.ts, 1,431 LOC; hook 1,097 | Project/editor/build/device/legacy actions share refs/state | Domain hooks with explicit backend snapshots/recovery |
| 11 | frontend/use-forge-agent-session.ts, 1,068 LOC; hook 1,021 | Stream/session/job/approval/selection generations interact | Extract stream controller and durable job/session state reducers |
| 12 | NexusShell, function 752 | Nearly all product destinations/hooks/settings/background transactions coupled | Keep shell composition, extract independent controllers/views |
| 13 | ModelSettingsPanel, function 570 | Provider config, selection, credentials/status/UI state | Split provider form/selection/catalog with stable queue semantics |
| 14 | TaskCanvas, function 528 | Run activity/plans/recovery/review controls combined | Present typed run projection with smaller control components |
| 15 | main.py, 1,310 LOC; task 168 + repair 169 | Reachable alternate orchestration/mutation/hardware policy | Compatibility facade into canonical services |
| 16 | generate_code_handler.py, 1,270 LOC; execute 318 | Legacy model generation and active file publication | Canonical staged adapter, preserve validated generation helpers |
| 17 | orchestration_store.py, 1,088 LOC | Schema migration/events/jobs/memory and lifecycle journal mixed | Internal repositories sharing one transaction owner |
| 18 | serial_runtime.py, 1,152 LOC | Worker threads, identity/reconnect, buffering and port state | Explicit transport state machine and driver adapters |
| 19 | subprocess_mgr.py, 1,023 LOC | Multiple drain strategies, cancellation/ownership/OS paths | Preserve tested concurrent drains; isolate platform lifecycle adapters |
| 20 | PlatformIOService 940 / FirmwareBuilder 942 LOC | Configuration parsing, command execution, artifact sealing and overlapping workflows | One typed build adapter/evidence contract, legacy caller bridge |

Large `globals.css` (3,030 LOC), board_validator (1,039), ToolPolicy.validate_call (274), activity projection (288) and runtime result schemas are additional hotspots; they are not all execution defects. Dead initialization, dormant Harness abstractions, stale environment flags and overlapping API clients should be pruned by runtime/import ownership, not by string matches.

**Removal candidates, after migration/tests:** dormant Harness execution/plugin/sandbox abstractions if no explicit product goal retains them; unused ApiPlannerProvider class after helper extraction; obsolete native Python transports after injected/local discovery callers are accounted for; legacy orchestration once `/execute` delegates; unreachable old frontend compositions after import graph verification; unused generic PersistenceManager if no retained embedding API uses it. Keep templates, typed validators, stable contracts and vendor provenance that still serve live paths. No deletion was performed.

## 32. Documentation Drift

Statuses refer to current implementation, not whether an earlier historical result was truthful.

| Claim | Documentation source | Actual implementation / evidence | Status |
|---|---|---|---|
| Continuous model→tool→observation behavior | README Forge Agent V3 | Two live Python model/tool iterations with request/tool observations | VERIFIED |
| Python owns runs, Harness is auxiliary/shadow | README/runtime descriptions | App composition + Harness status/method table, no execution registration | VERIFIED |
| One unified model/tool loop | capability_profiles.py module commentary / architecture wording | AsyncAgentEngine seam exists, but two substantial mode-specific loops remain | PARTIAL |
| Model file writes occur inside managed stages | README safety model line 184 | True for modern tools; `/execute` legacy generation writes active project | PARTIAL |
| Flash approval is bound to run/board/port/environment/image | README safety model | Orchestrator fingerprint + one-use SQL consume + manifest validation | VERIFIED for normal path; physical qualification absent |
| Codex OAuth enabled/configurable | .env.example 13–21 | No actual bridge/provider/login/refresh; ignored settings | NOT IMPLEMENTED |
| Cerebras models are discovered for the entered key from /v1/models | README 152–154 | Default OpenCodeProvider.list_models returns pinned registry catalog | INCORRECT |
| Three built-in skills and project local skill directory | README 165–178 | Actual three SKILL.md files, discovery/load implementation | VERIFIED, security gap noted |
| Read-only bounded subagents | README V3 features | Python roles/budgets/read policy, no recursive spawn tool | VERIFIED |
| Memory is verified | README/context labels | Host-only writers/invalidation exist; build-only model-summary label gap | PARTIAL |
| Old delete/rename tool restrictions | docs/agent-activity-stream.md historical limits | Current delete_file/move_file schemas and handlers exist for edit/repair | STALE |
| Older test counts (1,480; 1,996 etc.) | Historical activity/quality reports | Current audit: 2,231 passes, one failure, four skips, other groups separately | STALE as current evidence; historical intent/results not re-certified |
| Linux/macOS equivalent dev commands | README line 88 | Python/dev code includes alternatives; native credential storage and release targets are Windows-focused | PARTIAL; not cross-platform release qualification |
| Native tests never upload/use hardware | Tool/docs wording + result hardware_used=false | Typed command excludes upload target, but native program itself is unrestricted host code | PARTIAL / misleading guarantee |
| MCP integration | Requested architecture question; docs/agent-quality-implementation.md explicitly limits unrestricted connections | No client/server/transports/discovery in product; no implemented feature inferred from that documentation | NOT IMPLEMENTED |
| Gemini subscription OAuth | Requested architecture question; no actual implementation claim found | Google AI Studio key path; cloud Vertex ADC separate | NOT IMPLEMENTED; do not invent a documentation promise |
| Production-beta gates/release scripts exist | README/runbook/CI | Scanner/signing/evidence validators and Windows pipeline implemented | VERIFIED existence; successful qualification UNVERIFIED |

Earlier audit/validation reports, installers, screenshots and model smoke claims were not credited to this working tree without rerunning them. Environment examples are especially weak evidence: configuration text has no effect without a code reader/caller.

## 33. Missing Features

These gaps follow existing architecture promises/boundaries; they are not a list of fashionable additions.

| Missing or incomplete capability | Why relevant | Must it block a focused Windows beta? |
|---|---|---|
| MCP client/server integration | Intended tool/resource expansion has no actual implementation | Only if promised as a beta capability |
| Codex account OAuth bridge | Environment advertises unsupported path | Remove inaccurate claim or implement before advertising |
| Google consumer subscription OAuth | Requested account-auth architecture is absent | Not required for key-based Gemini beta; label accurately |
| General account login/refresh bridge | Manual keys/tokens/cloud ambient auth dominate | Required only for advertised account-based providers |
| One canonical semantic agent iteration | Duplicated tool/context/verification/recovery rules | Consolidation before broad plugin/MCP expansion; immediate correctness fixes first |
| Activated plugin architecture | Harness manifest validation is not loading/execution | Not required for closed built-in beta |
| OS-confined model-controlled native processes | Current trust + typed argv cannot enforce device/workspace restrictions | **Yes, or disable/constrain that surface and accurately define broad trust** |
| Crash-consistent multi-file ChangeSet publication | Existing active project edits can become unrecoverable | **Yes** for dependable editing |
| Uniform reparse/secret/context reader policy | Alternative acquisition paths bypass stronger main tools | **Yes** for untrusted imported projects |
| Native Linux/macOS credential stores/installers/qualification | Current packaging/credential persistence are Windows-oriented | Not for Windows-only beta; required before OS support claims |
| Complete board/framework upload matrix | Recognition exceeds actual sealed upload adapters | Not if classic esp32dev scope is explicit |
| Artifact-bound real provider/task/hardware/install qualification | Validators exist; current artifacts/cycles unverified | **Yes** to promote exact checkout toward public beta/production |

Provider catalog breadth should be a discovery feature with compatibility tiers, not a claim that every listed subscription/account is integrated. Preserving the strongest working systems does not require rebuilding the product around a new agent framework.

## 34. Completion Matrix

Percentages are **engineering estimates of implementation completeness against the subsystem already present**, not measured test coverage, release confidence or arbitrary feature weight. A high implementation score can coexist with zero physical qualification. Estimates carry roughly ±10–15 points where live behavior was unavailable.

| Subsystem | Status | Approx. implementation | Evidence | Major gap / risk |
|---|---|---:|---|---|
| Desktop shell | Implemented | 85% | Managers/IPC/identity, tests/build | Exact installer/crash qualification; Medium |
| Frontend conversations/workspace | Implemented | 85% | Type/lint/build, UI79/browser52 assertions | Giant hooks, teardown unresolved; Medium |
| API/auth/service composition | Implemented | 85% | Routes124, API99, security code | Large handlers/body bounds/compatibility; Medium |
| Agent sessions/messages | Implemented | 90% | Shared SQL/projector/reconciliation tests | Multi-process caches not qualified; Medium |
| Jobs/runs/leases/recovery | Implemented | 85% | Durable ledger/journal/budgets | Timing fixture + real crash/outage qualification; Medium |
| Canonical agent iteration | Partial consolidation | 65% | Shared engine + two live large loops | Duplicate semantics; High maintenance |
| Context/compaction | Implemented with gaps | 75% | Assembler/history/artifacts | Secret/reparse/unbounded reads; High boundary relevance |
| Model runtime | Implemented | 80% | Canonical stream/retry/attempt tests | Write/cleanup deadlines, actual accounts; Medium |
| Provider dispatch | Implemented, catalog breadth unqualified | 70% | Ten explicit + catalog adapters/engine tests | Remote SDK/account parity and SAP advisory; High release risk |
| Same-model retries/admission | Implemented | 85% | Shared ledger/cooldown/leases | Default circuit skipped, no proactive account rate limit; Medium |
| Tool calling/validation | Implemented | 85% | 32 schemas, settled frames/grants | Some metadata tools unavailable; multiple execution seams; Medium |
| File tool staging | Implemented | 85% | Safe paths/overlay/diffs | Legacy route and unrestricted processes; High policy risk |
| ChangeSet apply/undo | Implemented with defect | 70% | Hash/conflict checks + crash probe | Multi-file crash recovery; High correctness priority |
| Project indexing | Partial | 60% | Actual lexical cache/query | Junction/scan limits, no semantic index; Medium |
| Memory | Implemented with provenance gap | 70% | Host writers/invalidation | Build-only prose labeling, retrieval policy; Medium |
| Skills | Implemented | 75% | Three built-ins/local precedence | Junction/secret safety, no global user roots; High on audited interpreter |
| Python subagents | Implemented | 80% | Three roles/bounded child loop | Real role/model quality unqualified; Medium |
| MCP | Missing | 0% | No protocol implementation | Requires guarded adapter; optional feature scope |
| Codex OAuth | Missing | 0% | No bridge/provider/login | Misleading env; High expectation gap |
| Google subscription OAuth | Missing | 0% | Gemini key + Vertex cloud distinct | No consumer allowance bridge |
| Harness shadow host | Implemented, optional | 70% | Authenticated RPC/session events/catalog real | Crash locks, no production task authority; Medium debt |
| Harness agent/tool execution | Disconnected scaffolding | 0% | Execution handlers/providers empty; no run RPC | Dormant classes do not establish connected execution |
| Activated plugins | Missing; manifest scaffolding present | 0% | Manifest validation only | No live activation/loader; Low current risk |
| PlatformIO classic ESP32 build | Implemented | 85% | Real compiler/sealed set | Real diverse tasks/envs not qualified; Medium |
| ESP-IDF product workflow | Experimental/disconnected | 25% | Trusted adapter/profile detection | No normal app caller; Medium |
| Other-board end-to-end firmware | Partial | 40% | Detector/validators/low-level adapters | Standard sealing/upload coverage narrow; High if advertised broad |
| Flash authorization/artifact binding | Implemented | 85% | One-use SQL/manifest/action digest | Native execution bypass, weak device identity; High |
| Serial runtime | Implemented | 80% | Single owner/bounded buffers/tests | Physical reset/reconnect/boot-marker qualification; Medium |
| Verification/evidence | Implemented | 80% | Revision-bound review/build/test/sim distinctions | Model review not execution; physical qualification absent |
| Simulation | Implemented, live unverified | 60% | Wokwi CLI adapter/source bindings | CLI/token/acceptance breadth; Medium |
| Human terminal | Implemented with cleanup gap | 70% | ConPTY/pipe/WS/tests + fault probe | Unbounded queue/wait/tree consistency; Medium |
| Native process security isolation | Missing | 0% | Lifecycle Jobs/groups and config validation exist separately | No host access/device sandbox; High |
| Persistence/crash recovery | Implemented with seams | 75% | WAL/migrations/checksums/journal | File/DB atomicity and legacy JSON; High for editing |
| Test infrastructure | Broad, incomplete qualification | 85% | 2,236 Python outcomes + Node/UI/browser | Fixture flake, skips, teardown, real matrix missing |
| Packaging infrastructure | Implemented | 80% | Windows stage/sign/scanner/SBOM code | Not exercised exact current installer; High release risk |
| Release qualification | Not established | 25% | Validator/test code, local builds | Signed exact artifact, machines/providers/hardware absent; High |

## 35. Release Readiness

**This exact working tree is an Internal Beta engineering checkout.** It is not a Release Candidate and has not established production readiness. Its existing breadth/testing makes “prototype” unnecessarily dismissive; passing local suites cannot promote it beyond unverified release gates and known safety/correctness issues.

| Release requirement | Evidence now | Verdict |
|---|---|---|
| Windows development code/builds | Local tests, typecheck, compile/bundles, actual ESP32 fixture | Verified in audited environment |
| Required release Python/Node matrix | Local Python3.10/Node24 vs prescribed Python3.13.15/Node22 | **Not qualified** |
| Windows10/11 clean install, no system Python/Node | Scripts/templates exist | **Not performed** |
| Installer/signature for current source/resources | Packaging/signature gates implemented | **No new exact artifact produced/verified** |
| Upgrade/uninstall/user-data preservation | NSIS config and data migration code inspected | **Not performed** |
| Real provider task quality / repeated failures | Controlled provider tests | **Not live qualified** |
| Real ESP32 repeated flash/serial nonce cycles | One real compile, zero upload/device cycles | **Not qualified** |
| Dependency security | Root npm gate fails; frontend clean; Python audit missing | **Blocked/incomplete** |
| Crash recovery | Durable journal tests; actual ChangeSet crash repro fails | **Known gap** |
| Linux/macOS | Some dev/process alternatives; no native secure store/install targets/CI matrix | **Unqualified** |
| Hardware family breadth | Five family identifiers, classic esp32dev standard manifest | **Scope broader in UI than qualified workflow** |

Windows CI is configured for hash-locked Python dependencies, npm ci, tests/bundles, audits, runtime stage/scan, smoke and unsigned candidate packaging. It is **Windows-only** and produces a test candidate, not evidence of a signed public artifact. `verify-release-evidence` binds installer/resource hashes to prompt/quality/maintenance suites, human requirement fidelity, twenty consecutive builds, ten unique physical flash/serial cycles and machine/signature results. The verifier is useful; its successful current-artifact evidence is absent.

**Before public beta:** fix or remove conditional unsafe native execution, uniformize project boundary/secret readers, fix ChangeSet crash recovery, close legacy agent publication bypass, resolve root advisory gate and test-runner/fixture reliability, qualify the declared Windows/classic-ESP32 scope with actual provider/device/install evidence. **Before production:** signed exact-artifact release/upgrade/uninstall/recovery qualification, stronger device/process isolation and identity behavior, sustained workload/provider/physical reliability, current dependency audit/SBOM/provenance and clear support/rollback guarantees. MCP/OAuth features do not inherently block a correctly scoped API-key Windows beta; advertising them does.

## 36. Recommended Final Architecture

```text
Electron: launch/session identity + narrow IPC + owned service lifetime
                      ↓
Next/React: small domain controllers + typed durable projections
                      ↓
FastAPI: thin authenticated routes + stable engine contracts
                      ↓
Python AgentOrchestrator: one run/job/lease/journal/approval owner
                      ↓
Canonical AsyncAgentEngine: one semantic turn/evidence/recovery lifecycle
  ├─ Context service: safe readers, skills, project memory, profile/index
  ├─ Planner: selected model history + public summaries / typed tool intent
  ├─ Workspace adapters: read-only / overlay / full staged embedded project
  ├─ Tool gateway: strict schema → effect class → grant → ordered scheduler
  │    ├─ Built-in safe repository/edit/context tools
  │    ├─ Typed PlatformIO/test/simulation adapters → confined process runner
  │    └─ Optional MCP adapters with server consent and identical host boundaries
  └─ Evidence service: source/contract-bound review, build, test, sim, device facts
                      ↓
Publication service: journaled crash-recoverable ChangeSet transaction
Hardware gateway: immutable upload set + identity + one-use approval + journal

ModelGateway [Python]: pin selection, attempt ledger, admission/recovery/health
                      ↓
Node request adapter [OpenCode/native SDK]: one attempt, bounded stream, no tools
                      ↓
Explicit API-key / local / cloud-account adapters
Optional account bridges only after actual login/refresh/entitlement implementation

Authoritative persistence: orchestration/session/job SQL + typed model settings
                          immutable artifacts + recoverable publication journal
Harness: explicit diagnostic shadow/contracts only, or retire dormant execution
Legacy HTTP/CLI: compatibility facades invoking the same canonical owner
```

This preserves proven investments and removes semantic duplication incrementally. It does not require replacing Python, React, Electron, PlatformIO or the OpenCode request engine.

## 37. Refactoring Roadmap

### Phase 1 — Correctness and boundary repairs

- **Goal:** address FX-001/002/003/004/005/006/007/008/009/013/014 without changing user workflows unexpectedly.
- **Modules:** shared filesystem/context readers; skills/index/profile/import walkers; ChangeSetService; legacy execute adapter; PlatformIO native runner/trust; memory sinks; terminal/OpenCode cleanup; default circuit health; package lock/provider dependency.
- **Dependencies:** explicit broad host-code trust policy or disabled native test surface; reviewed dependency advisory disposition; migration/recovery design for existing pending ChangeSets.
- **Risk:** Medium/High around publication and process ownership; keep changes isolated and reversible.
- **Required tests:** runtime junctions (not only privileged symlinks), secret context fixtures, process-write/deadline/driver faults, kill after every publish step, two-session conflicts, existing HTTP compatibility, source/artifact approval mismatch/replay, dependency/SBOM scans. Fix heartbeat synchronization and browser teardown determinism.
- **Done:** no reproduced safety/crash seam remains in enabled surfaces; current default request adapter tests enforce actual deadlines/protection status; prescribed test/audit gates settle with recorded outcomes.

### Phase 2 — Canonical runtime and thin API ownership

- **Goal:** one semantic model/tool/observation/evidence cycle with explicit workspace adapters; one Python owner.
- **Modules:** engine/autonomous_loop/tool_runtime/router_planner/observation context, orchestrator use cases, API agent_runtime and app composition; legacy main/workflow compatibility.
- **Dependencies:** Phase1 publication/evidence invariants; stable settled attempt and tool effect contracts.
- **Risk:** High behavior drift; migrate one profile at a time, keep parity fixtures and explicit temporary flags without dual authority.
- **Required tests:** Ask/Plan/Edit/Build/Repair/Simulate equivalence, streaming settlement, cancellation/fence loss, durable resume, shared child/repair budgets and verification invalidation.
- **Done:** profile differences are adapters/policy, not duplicated loop completion/recovery; `/execute` calls canonical staging; old loop branches have no product callers before deletion.

### Phase 3 — Guarded MCP integration, if in product scope

- **Goal:** connect external tools/resources through the established permission/evidence seam.
- **Modules:** Python MCP client/transport adapters, tool registry/gateway/context safe readers, user server consent/settings and diagnostics.
- **Dependencies:** canonical schema/effect/grant/result/deadline/secret policy from Phases1–2.
- **Risk:** High external disclosure/execution; server trust and effect classification must be explicit.
- **Required tests:** discovery/schema changes, stdio/HTTP lifetime, malicious paths/oversized results, disconnect/cancel, unknown side effects/replay, prompt/resource injection, network recipients and server consent.
- **Done:** actual transports/discovery and callable tools are exercised end-to-end; remote responses cannot grant local file/hardware permissions; disabled servers terminate cleanly.

### Phase 4 — Provider account authentication and compatibility

- **Goal:** accurate provider compatibility tiers; implement only intended account bridges with real login/refresh/entitlement evidence.
- **Modules:** credential stores, provider registry/options, account bridges, ModelGateway, frontend model settings, live qualification scripts and environment docs.
- **Dependencies:** stable pinned routing/attempt accounting; provider-supported auth mechanism and authorized test account.
- **Risk:** Medium/High credential disclosure/entitlement ambiguity; API keys/local providers remain supported.
- **Required tests:** refresh/expiry/revocation, account/model mismatch, login cancellation, remote error categories, no silent provider switch, live tool/stream responses, OS credential persistence.
- **Done:** Codex/Google subscription claims appear only if actual supported paths work; unsupported account flows are explicitly absent. Catalog entries have observed/tested versus metadata-only labels; no undefined OAuth option is advertised.

### Phase 5 — Harness and legacy consolidation

- **Goal:** remove disconnected duplicated responsibilities or retain an explicitly limited diagnostic host.
- **Modules:** harness runtime/workflow/plugins/tool-pipeline/subagents/sandbox, shadow client/routes/Electron manager, old Python adapters and unused frontend roots.
- **Dependencies:** canonical runtime/parity/caller ledger; explicit decision on diagnostic requirements.
- **Risk:** Low for unused pieces, Medium for shared imports/contracts; no blanket directory deletion.
- **Required tests:** import/entrypoint/packaging graphs, shadow event integrity/idempotency/crash locks if retained, CLI/compat APIs, generated catalog provenance.
- **Done:** every retained subsystem has a real caller/use case; no second execution owner; vendor/generated/shared helpers remain reproducible and documented.

### Phase 6 — Embedded and release hardening

- **Goal:** qualify the exact Windows/classic-ESP32 distribution before increasing board/OS scope.
- **Modules:** artifact seal/partition/flash/serial drivers, process runner, release stage/sign/scan/evidence scripts, installer CI and migration.
- **Dependencies:** current dependency gate pass/reviewed disposition, required release Python/Node, signing material, real providers/devices and clean Windows10/11 machines.
- **Risk:** High physical/distribution; bind each result to installer/resource/source/fixture hashes.
- **Required tests:** real prompt/maintenance/quality suites, repeated build/flash/nonce acceptance, device unplug/port reuse/reset, startup offline/occupied ports, force kill/crash recovery, clean install/upgrade/uninstall and signed artifact verification.
- **Done:** release evidence validator succeeds for the exact signed artifact with independently reviewable provider/hardware/machine results. No historical output is substituted.

### Phase 7 — Additional OSes/boards only after explicit scope decision

- **Goal:** extend established workflows through typed adapters and qualification, not enums alone.
- **Modules:** credential stores, OS lifecycle/security drivers, packaging targets, board manifests/framework adapters and tests.
- **Dependencies:** Phase6 reference workflow; actual target machines/boards.
- **Risk:** High qualification effort; do not advertise parity early.
- **Required tests:** per-OS process trees/terminal/symlink/reparse behavior, secure storage, installer updates/uninstall, each board upload set/identity/reset/serial acceptance.
- **Done:** each advertised OS/board has an artifact-specific supported workflow and documented limits.

No phase has been implemented by this audit. Architectural changes require a separate instruction.

## 38. File-by-File Audit

The [File-by-File Audit](File-by-File-Audit.md) supplies a record for every meaningful owned/test source file, with purpose, definitions/ranges, static callers, imports/significant calls, state/persistence markers, security implications, findings/debt and proposed KEEP/REFACTOR/MERGE/INVESTIGATE disposition. Generated/vendor source is explicitly separated and enumerated. Forty-five configuration/catalog/fixture records are in configuration-inventory.json. The [Provider/API annex](Provider-and-API-Inventories.md) lists every provider descriptor and route; tool-schemas.json lists every tool schema/profile.

File records distinguish traced behavior from structural analysis; this matters especially for source utility/tests and disconnected abstractions. Call-expression/import scanning does not resolve every dynamically injected Python/TypeScript call. The detailed main report establishes authority at the real composition/dispatch points. No DELETE recommendation is unconditional merely because the static importer count is zero.

Final artifact checks found all 39 numbered sections, all 659 source records and no broken local report links. The final source-release scan initially rejected the two harmless audit junction entries, correctly treating them as forbidden release content. Only those junction entries were removed (without deleting their external fixture targets); reproduction scripts/results remain. The rerun passed; [final scanner log](release-content-scan-final.log). No product source was repaired to make a test/scanner pass.

### Answers to all 28 critical questions

| # | Question | Evidence-based answer |
|---:|---|---|
| 1 | What happens on a user message? | Persist session turn → route/clarify/steer or start job → Python orchestrator → selected stage/overlay loop → context/model/settled tools/observations → current evidence/review/apply/optional approval → durable reply. Chat has no tools. |
| 2 | Authoritative agent loop? | Python run authority is AgentOrchestrator; embedded loop is AutonomousForgeAgentLoop, general/staged loop is ForgeXToolRuntime. There is no single fully consolidated semantic iteration. |
| 3 | Multiple loops competing? | Multiple live implementation paths exist, selected under one owner; not competing Python/TypeScript owners for a normal run. Legacy /execute is also reachable. |
| 4 | Who owns reasoning? | Selected LLM; host owns intent/permissions/budgets/evidence sufficiency/public projection. |
| 5 | Who owns tool execution? | Python host executor/grants/scheduler and typed handlers; low-level legacy registry is separate. |
| 6 | Who owns context? | Python ContextAssembler/observation artifacts plus RouterProductPlanner native history; loops/children update their local views. |
| 7 | Who owns persistence? | Composed Python SQL/session/job/model stores plus project/settings/ChangeSet JSON/artifact files; shadow Harness logs separate. |
| 8 | Provider routing owner? | Python ModelRouterService/selection/resolver/ModelRuntime. Node executes one physical request. |
| 9 | OpenCode agent dependency? | Provider/schema/request infrastructure only on the actual product import path. |
| 10 | MCP implemented? | **No. MCP IS NOT IMPLEMENTED.** |
| 11 | Skills implemented? | Yes: three built-ins plus project-local named loaders; junction/secret safety gaps. |
| 12 | Subagents implemented? | Yes: bounded Python Explore/Review/Verify; no recursive spawn tools. |
| 13 | Memory safe? | Host-only writes/provenance/invalidation mostly implemented; build-only model-summary verification gap prevents an unqualified “safe” verdict. |
| 14 | Codex OAuth? | **Not implemented**; environment example is misleading. |
| 15 | Google subscription OAuth? | **Not implemented**; Vertex cloud ADC is different. |
| 16 | Gemini key required? | Yes for ordinary Gemini Google AI Studio provider. Cloud Vertex alternatives use their own cloud credentials, not consumer allowance. |
| 17 | Silent provider switch? | No failure-based fallback on current runtime; explicit role bindings and retired-default model migration are distinct. |
| 18 | 503 handling? | Same-selected-provider/model bounded retry/backoff/Retry-After → durable cooldown/pause/recovery → visible exhaustion; physical attempts charged. |
| 19 | Arbitrary model shell? | No current enabled run_command/bash/pwsh handler. Trusted model-writable native tests can indirectly execute arbitrary host code/commands. |
| 20 | Model flash without authorization? | Normal typed flash path requires explicit bound one-use approval. Universal prevention is not guaranteed because native host execution lacks a device fence. No physical bypass attempted. |
| 21 | Writes escape workspace? | Ordinary file tools reject traversal/reparse paths; unrestricted native host programs can write outside CWD/stage. Alternative readers already cross project boundary on audited Python. |
| 22 | Staging consistent? | Modern tools yes; legacy generation bypass and host-code execution mean repository-wide enforcement is incomplete. Human editor direct writes are intentional. |
| 23 | Harness authoritative? | **Shadow-only/optional**, no connected agent/tool/provider execution handlers. |
| 24 | Architecture to keep? | Python orchestration/state/grants/evidence + one semantic engine + staged publication/hardware gateway; Node provider adapter; smaller frontend controllers. |
| 25 | What remove? | Dormant/obsolete execution branches after live caller migration and shared helper extraction; no blanket delete of modules containing live contracts. |
| 26 | Before MCP? | Uniform safe context readers, tool effect/grant/result policy, canonical loop/evidence/cancellation, legacy bypass closure and server consent model. |
| 27 | Blocks beta? | Enabled native isolation gap, boundary/secret/junction defects, crash publication, legacy staging bypass, advisory gate and credible scoped Windows/provider/hardware qualification. |
| 28 | Blocks production? | All beta issues plus exact signed artifact/install/upgrade/uninstall/crash/physical reliability qualification, current dependency evidence and supported OS/board guarantees. |

## 39. Final Verdict

**ForgeX Overall Engineering Score: 6.5/10.** Scores are professional judgments against the implemented product and its evidence gaps, not mathematical test-coverage measurements.

| Area | Score |
|---|---:|
| Architecture | 6.5/10 |
| Agent Runtime | 7/10 |
| Model Runtime | 7/10 |
| Tool System | 6.5/10 |
| Security | 5.5/10 |
| Embedded Integration | 6.5/10 |
| Testing | 8/10 |
| Maintainability | 4.5/10 |
| Release Readiness | 4/10 |

**Implemented and strong:** durable Python run/job/session authority, settled tool-call boundaries, request budget/admission accounting, current-source evidence invalidation, primary staged editing policy, immutable ESP32 upload sets, one-time artifact-bound flash approvals, desktop loopback identity/authentication, substantial regression infrastructure.

**Implemented but needs improvement:** context readers, memory labeling, provider protection/timeout visibility, terminal cleanup, cross-session workspace coordination, multi-file crash recovery, large frontend/API/orchestrator state owners and legacy compatibility paths.

**Partially implemented:** canonical semantic loop consolidation, provider-wide live compatibility/discovery, broad board/framework end-to-end support, plugin activation, Harness execution, cross-platform credential/distribution support and artifact-specific release qualification.

**Missing:** MCP, Codex OAuth, Google consumer subscription OAuth, a real OS security sandbox for model-controlled native execution, and demonstrated current-artifact production qualification. A missing optional capability should be stated plainly rather than counted as implemented because a catalog/interface/document mentions it.

**Must fix before beta:** the enabled host execution/device boundary, reproduced project-reader/junction/secret defects, ChangeSet publication recovery, legacy generation staging bypass, misleading verified build memory, unresolved current root dependency gate, and test/gate reliability; then qualify the declared Windows/classic-ESP32/provider scope.

**Must fix before production:** exact signed installer and resource/source provenance, clean machine install/upgrade/uninstall/data recovery, repeated real provider/device/serial acceptance and interruption cycles, dependency/security evidence, stronger lifecycle/device identity isolation and supported-platform qualification.

**Conclusion:** preserve and consolidate the existing Python host and typed embedded safety/evidence systems. The checkout has a credible engineering foundation, but the evidence does not support a production-ready label. This report is the audit deliverable; no architectural implementation follows without separate instruction.
