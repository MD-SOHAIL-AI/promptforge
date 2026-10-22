"""Build file-level records with explicit manual versus structural coverage."""
import collections
import json
import re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).parent
inventory=json.loads((OUT/'inventory.json').read_text())
callers=json.loads((OUT/'static-import-callers.json').read_text())
ts={r['path']:r for r in json.loads((OUT/'typescript-analysis.json').read_text())}
# Purpose, material finding, proposed disposition. These are code-path annotations,
# not claims inferred from docstrings or names.
notes={
'backend/api/app.py':('Composes the live FastAPI services, workflow adapters, model router, orchestrator and shutdown lifecycle.','create_app is 710 lines; unavailable generation configuration can coexist with a ready backend.','REFACTOR'),
'backend/api/local_security.py':('Checks exact loopback Host/Origin and launch-token cookie/bearer authentication for HTTP and WebSockets.','Missing Origin is permitted only with token authentication; no query-token WebSocket fallback.','KEEP'),
'backend/api/service_container.py':('Declares the service container and compatibility app-state view used by routes.','Compatibility attribute lookup obscures some dependency ownership.','KEEP'),
'backend/api/dependencies.py':('Resolves registered projects and retrieves composed services for route handlers.','External roots are intentionally supported; do not confuse registration with arbitrary model-selected roots.','KEEP'),
'backend/api/routes/agent_runtime.py':('Routes sessions, prompts, jobs, approvals, recovery, memory and tool inspection into the Python orchestrator.','598-line append handler; two distinct route handlers share agent_tool_catalog name; write-only task contracts leave a memory provenance gap.','REFACTOR'),
'backend/api/routes/chat.py':('Routes chat session operations to ChatService and durable/live message observation.','Chat is a separate conversational path without agent tool execution.','KEEP'),
'backend/api/routes/execute.py':('Keeps the authenticated older execute_prompt workflow API reachable.','FX-004: generation can write active project files outside ChangeSets; global in-memory execution bookkeeping remains.','MERGE'),
'backend/api/routes/files.py':('Serves human file reading and direct editor CRUD against a registered project.','Direct writes are a deliberate human-editor path; do not advertise universal staging without that qualification.','KEEP'),
'backend/api/routes/changes.py':('Exposes ChangeSet list, diff, apply, undo and discard operations.','Delegates filesystem publication to ChangeSetService; crash safety must be fixed there.','KEEP'),
'backend/api/routes/build.py':('Starts and observes host build operations with operation identity.','Durable build observation improves uncertain-request recovery; build readiness is not device qualification.','KEEP'),
'backend/api/routes/flash.py':('Rejects the old direct flash endpoint and directs users through run-bound approval.','Disabled endpoint compatibility is intentional; low-level flash functions still need a single internal authority boundary.','KEEP'),
'backend/api/routes/terminal.py':('Starts human terminals and routes interactive input/output and resizing.','FX-008 cleanup bounds; REST and WebSocket input limits differ.','REFACTOR'),
'backend/api/routes/websocket.py':('Authenticates sockets before acceptance and exposes terminal/workflow observations.','Durable orchestrated activity and ephemeral legacy progress are separate mechanisms.','KEEP'),
'backend/api/routes/models.py':('Maintains compatibility provider/task model routes and configuration APIs.','Overlaps v2 model-gateway selections; endpoint circuit health does not cover default OpenCode execution.','MERGE'),
'backend/api/routes/model_gateway.py':('Exposes newer provider catalogs, model selection and session/role bindings.','Catalog support is metadata-backed; actual account access is unverified.','KEEP'),
'backend/api/routes/monitor.py':('Routes monitor start/stop and serial observations to the serial service.','Opening pyserial may assert modem lines; observe_serial itself cannot open a device.','KEEP'),
'backend/api/routes/projects.py':('Routes managed and imported project registration, refresh, relocation and deletion.','Separate registry persistence and managed-directory operations need shared boundary helpers.','KEEP'),
'backend/api/routes/settings.py':('Reads and updates typed application settings.','Provider secrets are handled separately in backend credential storage.','KEEP'),
'backend/api/routes/health.py':('Reports service runtime identity and readiness for desktop supervision.','Ready state does not certify provider credentials, compilation or hardware.','KEEP'),
'backend/api/routes/diagnostics.py':('Exports bounded/redacted diagnostic observations.','Redaction is format based; secret scanning is not uniformly applied to all context inputs.','KEEP'),
'backend/api/routes/harness.py':('Exposes optional shadow host state through HarnessClient.','No TypeScript execution authority is transferred by this route.','KEEP'),
'backend/agent_runtime/orchestrator.py':('Owns durable runs, leases, jobs, capability grants, staging, review/apply, cancellation and flash approvals.','3,029-line lifecycle hotspot; semantic loops remain duplicated; verified_build can retain model summary on contract-free build-only paths.','REFACTOR'),
'backend/agent_runtime/autonomous_loop.py':('Runs the live embedded model/tool/build/repair/verification iteration over a managed stage.','621-line run and 308-line build method retain mode-specific orchestration despite shared engine.','MERGE'),
'backend/agent_runtime/tool_runtime.py':('Runs the live general/read/staged overlay model/tool iteration.','1,012-line run_product_async duplicates settlement, observations, recovery and completion semantics.','MERGE'),
'backend/agent_runtime/engine.py':('Shares asynchronous turn yielding, planner requests, attempt settlement and ordered tool scheduling.','A cycle seam, not the complete authoritative reasoning/context/verification loop.','KEEP'),
'backend/agent_runtime/canonical_runtime.py':('Freezes settled assistant attempts and tool invocations; orders tool scheduling and recovery classes.','Keep this boundary as the shared provider-independent execution contract.','KEEP'),
'backend/agent_runtime/router_planner.py':('Adapts canonical model requests/responses to typed product plans and native tool history.','Owns model conversation history and repair requests; large _generate has multiple failure branches.','REFACTOR'),
'backend/agent_runtime/api_planner_provider.py':('Retains an older planner class plus shared schemas, normalization and error helpers.','The old class is not the default router planner; module helpers remain live, so wholesale deletion is unsafe.','MERGE'),
'backend/agent_runtime/product_agent_service.py':('Provides the route-facing facade around provider selection and AgentOrchestrator.','Facade naming should not be mistaken for a second run owner.','KEEP'),
'backend/agent_runtime/product_provider_registry.py':('Connects product session/workflow selection to the model router and diagnostic providers.','Diagnostic fake/template providers are not release qualification evidence.','KEEP'),
'backend/agent_runtime/product_providers.py':('Provides planner adapters and explicitly diagnostic fake/template behavior.','Keep fake providers visibly diagnostic and excluded by release gates.','KEEP'),
'backend/agent_runtime/tool_executor.py':('Enforces file policy and performs overlay/stage reads and mutations with host tool dispatch validation.','Typed host handlers dispatch through a different branch; centralized validate remains necessary.','KEEP'),
'backend/agent_runtime/tool_policy.py':('Checks strict tool arguments, content/secrets, relative paths, protected names and reparse components.','Strong primary editor boundary; other context/index/skill readers duplicate weaker filesystem checks.','KEEP'),
'backend/agent_runtime/tool_registry.py':('Defines added typed tool schemas, timeouts, scheduler classes and handler availability.','Registry inventory does not prove all 32 tools are available in any one profile.','KEEP'),
'backend/agent_runtime/tool_contracts.py':('Defines tool enum, schemas, typed calls/plans/results and runtime classifications.','run_command exists as a schema but is denied/unwired in current production profiles.','KEEP'),
'backend/agent_runtime/engine_tools.py':('Publishes an inspection catalog of host tools and profiles.','Inspection metadata must be distinguished from actual prerequisites and handler availability.','KEEP'),
'backend/agent_runtime/capability_profiles.py':('Computes Ask/Plan/Edit/Build/Repair/Simulate allowlists and completion constraints.','The comment claiming one model/tool loop overstates the consolidation presently implemented.','KEEP'),
'backend/agent_runtime/capabilities.py':('Issues expiring run/node/workspace grants and fences/revokes tool and service publication.','In-process grants are host authority, not OS process isolation.','KEEP'),
'backend/agent_runtime/context_assembler.py':('Assembles task, inventory, instructions, verified memory, skills and bounded profile/index hints.','FX-005: whole instruction files are read before truncation without the normal content-secret gate.','REFACTOR'),
'backend/agent_runtime/context_memory.py':('Compacts older observations and retains recoverable context artifacts.','Context compaction remains deterministic and separate from verified project memory.','KEEP'),
'backend/agent_runtime/observation_context.py':('Normalizes and bounds tool observations inserted in model context.','Different loops still maintain independent observation/update logic.','MERGE'),
'backend/agent_runtime/workspace_index.py':('Builds a cached lexical file index and task-relevance hints.','FX-003: Windows junction traversal reproduced; full-file hashing has no overall byte/file budget.','REFACTOR'),
'backend/agent_runtime/embedded_context.py':('Derives embedded configuration, source symbols, bus/pin and compilation-unit hints.','Heuristic source evidence is not semantic compiler analysis or physical wiring verification; Python 3.10 reparse gaps also apply to its walker.','KEEP'),
'backend/agent_runtime/memory.py':('Persists host-submitted project memory with provenance and revision/board/path invalidation.','FX-006: trusted caller can label model-authored build-only summary verified; no arbitrary model memory-write tool exists.','REFACTOR'),
'backend/agent_runtime/skills.py':('Discovers three built-ins and project-local skills and loads named bounded content.','FX-002: .agents/skills junction escapes selected project on Python 3.10; no global user-skill directory.','REFACTOR'),
'backend/agent_runtime/subagents.py':('Runs bounded one-level Explore/Review/Verify specialists against read-only tools and optional Verify build.','No recursive spawn tool; a Verify answer is not automatically actual build evidence.','KEEP'),
'backend/agent_runtime/jobs.py':('Stores shared execution budgets, attempt charges, job recovery and project trust.','Trust permits native process execution; hardware isolation is not established by that boolean.','KEEP'),
'backend/agent_runtime/orchestration_store.py':('Owns SQLite WAL run/events/journal/leases/memory/jobs/checkpoints and migration/corruption handling.','Single backend is assumed for in-memory session projections; SQL authority is stronger than legacy JSON.','KEEP'),
'backend/agent_runtime/session_store.py':('Persists sessions/messages and imports legacy histories into the shared SQLite store.','Caches assume a single authoritative backend; schema metadata is not a distributed synchronization protocol.','KEEP'),
'backend/agent_runtime/session_run_projector.py':('Projects durable run completion/events into session histories.','Separate public projection and live streams must keep idempotent sequence semantics.','KEEP'),
'backend/agent_runtime/chat_service.py':('Runs conversational model responses without tools and persists stream reconciliation.','Private agent attempt buffering differs intentionally from already-published chat streaming.','KEEP'),
'backend/agent_runtime/activity.py':('Publishes live activity and transient assistant frames.','Live frames are distinct from durable settled journal evidence.','KEEP'),
'backend/agent_runtime/activity_stream.py':('Projects journal records into bounded frontend activity items.','288-line projection function is a schema/compatibility hotspot.','REFACTOR'),
'backend/agent_runtime/turn_router.py':('Routes user intent into inspect/plan/edit/build/monitor/flash/confirmation/clarification.','Typed host intent and authorization remain outside model reasoning.','KEEP'),
'backend/agent_runtime/task_understanding.py':('Uses bounded model-assisted interpretation for ambiguous requests.','Three physical requests and a separate deadline; interpretation cannot itself grant hardware rights.','KEEP'),
'backend/agent_runtime/task_contract.py':('Versions user requirements and binds requirement/acceptance checks to source state.','A compile, model review, simulation and device behavior must remain separately labeled.','KEEP'),
'backend/agent_runtime/verification.py':('Binds independent requirement review to current contract/source evidence.','Model review is not execution verification or hardware qualification.','KEEP'),
'backend/agent_runtime/approvals.py':('Creates and atomically consumes single-use expiring approval fingerprints.','Strong run/artifact/device binding; low-level native execution has no matching OS device fence.','KEEP'),
'backend/agent_runtime/checkpoint_files.py':('Copies and hashes bounded stages with link/reparse and file-identity checks.','One of the stronger filesystem boundary implementations; reuse it instead of weaker walkers.','KEEP'),
'backend/agent_runtime/retention.py':('Retains recovery drafts/checkpoints and removes only owned unreferenced stages.','Cleanup protects retained references and rejects links; unexpected cleanup failures can be logged and tolerated.','KEEP'),
'backend/agent_runtime/esp32_tools.py':('Implements lexical inspection, project doctor and bounded official-document fetches.','Official-domain public-IP pinning bounds network fetch; lexical results remain untrusted hints.','KEEP'),
'backend/changes/service.py':('Snapshots, diffs, stages, checks conflicts and publishes/reverts multi-file ChangeSets.','FX-007: crash after first publication leaves pending metadata and un-undoable partial state.','REFACTOR'),
'backend/changes/models.py':('Declares snapshots, file changes and ChangeSet lifecycle metadata.','Publication transaction state needs extending for crash reconciliation.','KEEP'),
'backend/main.py':('Composes the older deterministic generation/build/repair/flash workflow.','Still reachable through /execute; active writes and trusted internal flash calls bypass newer run machinery.','MERGE'),
'backend/agent/planner.py':('Maps prompt keywords to immutable legacy firmware workflow steps.','Legacy deterministic planner is distinct from the model-driven RouterProductPlanner.','MERGE'),
'backend/agent/coordinator.py':('Executes injected legacy workflow steps sequentially and aggregates typed failures.','Another orchestration/cleanup system; merge callers incrementally after legacy endpoint migration.','MERGE'),
'backend/agent/debugger.py':('Interprets legacy execution/build failures for recovery decisions.','Preserve useful classification rules while removing duplicated orchestration ownership.','MERGE'),
'backend/workflow/workflow_runner.py':('Binds legacy task/plan/context to Coordinator invocations.','Live through the compatibility execute path; not the modern conversation run authority.','MERGE'),
'backend/workflow/generate_code_handler.py':('Generates, validates and publishes legacy projects, including active existing-project writes.','FX-004 direct _write_project_to_workspace; large generation branches overlap staged product agent.','MERGE'),
'backend/services/code_generation_service.py':('Generates whole/chunked legacy firmware projects and repairs missing/invalid outputs.','2,559-line prompt/generation/validation coupling; fallback helper always denies route fallback.','MERGE'),
'backend/services/llm_service.py':('Defines shared LLM contracts/errors and retains older native transport implementations.','Default product transport is ModelRouterService/OpenCodeProvider; do not delete shared types with unused transports.','MERGE'),
'backend/model_router/router.py':('Creates the default OpenCode provider adapters and owns admission, usage and runtime routing.','FX-009: _physical_slot skips the implemented circuit for default OpenCode providers.','REFACTOR'),
'backend/model_router/runtime.py':('Pins resolved model/provider for each request and executes same-selection retries.','No automatic failure-based provider switch; explicit selection migration/role bindings are separate.','KEEP'),
'backend/model_router/retry.py':('Normalizes Retry-After and transient same-model retry decisions.','Default six attempts; durable waiting and deadline/job budgets bound continued recovery.','KEEP'),
'backend/model_router/request_recovery.py':('Coordinates managed same-selection transient waits, cooldown and job-visible recovery.','Keep one retry owner; avoid hidden SDK retries.','KEEP'),
'backend/model_router/admission.py':('Leases fair per-endpoint/model/provider physical request capacity in SQLite.','Concurrency admission is not a proactive provider RPM/token quota limiter.','KEEP'),
'backend/model_router/circuit.py':('Implements persistent endpoint failure/cooldown protection.','Production OpenCode path deliberately bypasses it; health labeling should expose that fact.','REFACTOR'),
'backend/model_router/run_budget.py':('Reserves and settles physical-attempt costs against shared execution context.','Budget accounting includes repairs and specialists rather than only top-level turns.','KEEP'),
'backend/model_router/normalization.py':('Assembles canonical streamed text/reasoning/tool calls/usage and rejects malformed frames.','Partial or malformed tool arguments must never become executable invocations.','KEEP'),
'backend/model_router/registry.py':('Registers ten named providers plus pinned catalog-derived descriptors and task routes.','229 descriptors are not 229 live-tested provider accounts; Codex OAuth is absent.','KEEP'),
'backend/model_router/opencode_catalog.py':('Loads pinned Models.dev metadata, aliases provider IDs and derives capability/protocol hints.','Capability flags are metadata, not observed compatibility for each remote account.','KEEP'),
'backend/model_router/catalog.py':('Supplies model availability/capability lookup and catalog caching.','Cloud model listings generally use the pinned catalog rather than authenticated live discovery.','KEEP'),
'backend/model_router/resolver.py':('Resolves provider/model from session, role, global selection and task routes.','Explicit role selection may differ from main selection without being fallback.','KEEP'),
'backend/model_router/selection.py':('Stores global/session and role-specific model selections in SQLite.','Overlaps compatibility selected-model settings; two-store updates are not one transaction.','KEEP'),
'backend/model_router/credentials.py':('Stores secrets with Windows Credential Manager; provides test and unavailable-store variants.','No production macOS Keychain/Linux Secret Service implementation.','KEEP'),
'backend/model_router/sqlite_storage.py':('Persists provider settings and migrates legacy plaintext JSON keys into secure storage.','Migration relies on credential-store availability and SQLite transactions.','KEEP'),
'backend/model_router/storage.py':('Retains atomic JSON provider-setting storage and secret-store delegation.','Current app usually selects SQLite unless explicitly overridden.','MERGE'),
'backend/model_router/usage.py':('Tracks normalized provider usage and supports SQLite/legacy JSONL persistence.','Failed/partial requests may lack provider-reported cost; local reserves remain conservative.','KEEP'),
'backend/model_router/providers/opencode.py':('Spawns one Node worker per physical attempt and bounds/normalizes NDJSON response transport.','FX-014: stdin drain/final reap lack their own deadlines. Cloud list_models uses the pinned catalog; no Codex/Google subscription OAuth account flow.','REFACTOR'),
'backend/services/platformio_service.py':('Validates and runs typed PlatformIO build/test/dependency commands and seals upload artifacts.','FX-001: trusted native tests execute generated host code without filesystem/device sandbox. Real esp32dev compile passed.','REFACTOR'),
'backend/security/build_policy.py':('Rejects project-controlled scripts, external packages/paths and unsafe build flags before PlatformIO.','INI filtering cannot confine arbitrary compiled native test code at runtime.','KEEP'),
'backend/security/secret_scanner.py':('Recognizes high-confidence credential patterns at normal tool content boundaries.','Instruction/skill loading does not uniformly use the same gate.','KEEP'),
'backend/services/firmware_manifest.py':('Hashes and seals application and upload-set artifacts with immutable manifest metadata.','Normal PlatformIO adapter currently seals only classic esp32dev Arduino configuration.','KEEP'),
'backend/services/partition_preflight.py':('Checks ESP partition tables, application offsets and image size consistency.','Logical preflight is useful; physical flash qualification remains separate.','KEEP'),
'backend/services/flash_service.py':('Checks selected hardware/current immutable manifest before typed firmware upload.','Trusted library lacks its own approval-token argument; caller orchestrator supplies authorization.','KEEP'),
'backend/hardware/action_binding.py':('Builds hardware action identity/digests for device/port/board/environment artifacts.','Weak port-only identities are accepted with confidence labeling; repeated physical-cycle evidence is missing.','KEEP'),
'backend/tools/flash_firmware.py':('Constructs typed ESP/AVR/STM flash commands from approved host inputs.','Not an LLM-exposed tool, but independently callable without central approval consumption.','KEEP'),
'backend/tools/board_detector.py':('Maps serial metadata and selected port into board-family detection.','Five named families do not establish complete flash/build support for all boards.','KEEP'),
'backend/validation/board_validator.py':('Checks board constraints and generated source/configuration heuristics.','1,039 lines; heuristic pin checks are not wiring or device observations.','REFACTOR'),
'backend/services/esp_idf_adapter.py':('Provides an explicitly enabled/trusted experimental ESP-IDF build adapter.','No normal product composition instantiates it; it is not the default ESP32 workflow.','INVESTIGATE'),
'backend/services/project_profile.py':('Inspects offline project type, selected environments and compilation database hints.','Detection and metadata support exceed currently qualified build/flash support.','KEEP'),
'backend/services/project_service.py':('Manages project metadata, staged directory publication and manual file operations.','Registration/filesystem ownership differs from agent ChangeSet ownership.','KEEP'),
'backend/services/project_import_service.py':('Persists recent external folder registrations without copying projects.','Walker rejects symlinks but does not consistently reject Windows reparse entries.','REFACTOR'),
'backend/workspace/workspace_manager.py':('Writes legacy managed workspace project/build/artifact/log files and reports workspace health.','Optional PersistenceManager is not supplied by default app; metadata uses direct JSON writes and several walks rely only on is_symlink.','MERGE'),
'backend/state/persistence.py':('Offers a general legacy SQLite project/execution persistence manager.','No production default constructor call; do not count its schema as active persistence ownership.','INVESTIGATE'),
'backend/services/build_operations.py':('Persists host build operation identity/results and detects changed source/restarts.','Build tasks interrupted by restart are not silently replayed.','KEEP'),
'backend/services/artifact_service.py':('Validates managed artifact publication and retrieval.','Shares responsibilities with legacy WorkspaceManager artifacts; consolidate after caller migration.','KEEP'),
'backend/runtime/subprocess_mgr.py':('Runs argv-based owned subprocess trees with concurrent bounded pipe drains and cancellation/timeouts.','Windows Jobs provide lifecycle containment, not security isolation; human terminal has a different weaker implementation.','KEEP'),
'backend/runtime/windows_job.py':('Assigns tool wrapper processes to kill-on-close Windows Jobs before invoking child tools.','No breakaway; no restricted access token or filesystem/device security sandbox.','KEEP'),
'backend/runtime/process_owner.py':('Propagates run-local subprocess owner IDs through context variables and child operations.','Cancellation must retain owner context into async tasks and worker threads.','KEEP'),
'backend/runtime/serial_runtime.py':('Owns serial transport, port claims, blocking I/O workers and bounded observations/reconnection.','Pyserial opening can assert DTR/RTS and connect drains input; no physical reset/nonce reliability qualification was performed.','KEEP'),
'backend/services/serial_service.py':('Wraps SerialRuntime for API and host callers.','Retain a single transport owner rather than parallel serial implementations.','KEEP'),
'backend/services/terminal_service.py':('Runs user-controlled ConPTY/pipe shells and exposes interactive streaming sessions.','FX-008: post-kill wait is unbounded; ConPTY staging queue is unbounded; process-tree guarantees differ from SubprocessManager.','REFACTOR'),
'backend/tools/wokwi_simulator.py':('Runs bounded Wokwi CLI simulations and gathers virtual serial/check evidence.','Requires executable/token; no live Wokwi execution performed in this audit.','KEEP'),
'backend/tools/build_firmware.py':('Builds legacy board/framework firmware configurations and classifies compiler results.','942-line overlapping build orchestration should feed canonical typed build evidence.','MERGE'),
'backend/tools/tool_registry.py':('Dispatches five trusted low-level board/build/flash/serial/simulation adapters.','This registry is separate from the 32 model tool enum; no universal grant check lives here.','MERGE'),
'backend/settings/service.py':('Reads and atomically publishes typed JSON settings with process-local locking.','Corrupt JSON can yield defaults; no cross-process transaction authority.','KEEP'),
'backend/utils/paths.py':('Resolves runtime data/workspace/artifact locations and managed paths.','Persisted project roots need stronger shared reparse/read helpers in callers.','KEEP'),
'backend/utils/data_migration.py':('Moves/copies legacy application data into current owned data paths with migration checks.','Upgrade qualification must exercise interruptions and existing user data on real installers.','KEEP'),
'backend/observability/diagnostics.py':('Bounds, redacts and writes per-instance diagnostic events.','Redaction matches known patterns/fields; do not claim arbitrary secret-proof logs.','KEEP'),
'backend/observability/runtime_logs.py':('Writes rotating/bounded runtime observations and workflow/build logs.','Legacy logs remain separate from durable orchestration evidence.','KEEP'),
'backend/cli.py':('Calls the authenticated canonical API, starting a local engine when necessary.','Local free-port probe closes before backend starts; startup port race remains possible.','KEEP'),
'model-router/src/engine.mjs':('Adapts vendored native providers and AI SDK factories into a bounded single-attempt stream.','Uses OpenCode provider infrastructure, not its agent loop; SDK maxRetries=0 and singleAttemptLayer pin retry ownership in Python.','KEEP'),
'model-router/src/worker.mjs':('Reads a bounded stdin request and emits sanitized NDJSON engine events.','Credentials are stdin payload; inherited host environment enables cloud SDK authentication.','KEEP'),
'harness/src/runtime.ts':('Exposes shadow RPC status/catalog/session event operations.','No agent-run RPC and no registered tool/plugin/subagent execution providers.','INVESTIGATE'),
'harness/src/server.ts':('Runs the optional authenticated loopback Harness RPC server.','A bespoke RPC server is not MCP.','KEEP'),
'harness/src/event-store.ts':('Appends fsynced hash-chained JSONL session events with exclusive writer locks.','FX-010: crashed writer leaves lock path with no stale-lock recovery.','REFACTOR'),
'harness/src/tool-pipeline.ts':('Defines a generic permissioned tool dispatch pipeline.','Runtime creates it but registers no production tools; duplicates Python boundaries in shadow.','INVESTIGATE'),
'harness/src/plugins.ts':('Validates and lists plugin manifests/capability trust metadata.','Does not load modules or activate plugins in the actual runtime.','INVESTIGATE'),
'harness/src/subagents.ts':('Defines child-provider registration, child event handling and sandboxed delegation primitives.','No production provider registered; Python specialists are the live implementation.','INVESTIGATE'),
'harness/src/workflow.ts':('Defines bounded parallel/pipeline workflow composition over Harness child providers.','No production agent-run entry calls this engine.','INVESTIGATE'),
'harness/src/sandbox.ts':('Defines sandbox command validation and an injectable terminal-runner boundary.','Default OS runner unavailable; declaring a sandbox is not deployed isolation.','INVESTIGATE'),
'harness/src/providers.ts':('Publishes generated provider descriptor metadata to shadow RPC.','Catalog descriptors do not establish provider execution in Harness.','KEEP'),
'electron/main.ts':('Owns desktop single-instance startup, launch secrets, services, cookie and shutdown.','Dev mode globally adds --no-sandbox; ready is service startup rather than provider/hardware qualification.','KEEP'),
'electron/backend-manager.ts':('Starts/reuses an identity-matching authenticated backend and supervises bounded restarts.','Parent crash containment differs from build-tool Windows Jobs; readiness deadline assumptions need clean-machine qualification.','KEEP'),
'electron/frontend-manager.ts':('Starts standalone Next with Electron-as-Node and verifies private-key challenge identity.','Strong occupied-port identification; production build/staged assets are prerequisite.','KEEP'),
'electron/harness-manager.ts':('Starts/stops optional shadow Harness host.','Default disabled; failure does not promote it to execution owner.','KEEP'),
'electron/window-manager.ts':('Creates isolated renderer windows and restricts navigation/popups.','Packaged contextIsolation/sandbox true and nodeIntegration false; development global flag weakens sandbox.','KEEP'),
'electron/preload.ts':('Exposes the narrow typed desktop IPC bridge without general Node access.','Maintain narrow folder/status/background APIs.','KEEP'),
'electron/ipc.ts':('Handles desktop folder, diagnostics and background requests.','Validate input and sender identity together; same-origin renderer controls are the current sender boundary.','KEEP'),
'electron/background-assets.ts':('Stores validated UUID/image background assets and serves a bounded custom protocol.','Protocol is data delivery, not arbitrary file or executable protocol handling.','KEEP'),
'electron/paths.ts':('Resolves dev/package runtime paths, ports and embedded executables.','parseInt accepts trailing garbage in configured ports; harden before release.','KEEP'),
'frontend/app/page.tsx':('Selects the actual Nexus application root through error/dialog providers.','Earlier prompt/IDE compositions are not the active frontend root.','KEEP'),
'frontend/middleware.ts':('Proxies validated loopback API targets and maintains compatibility prefixes.','Do not permit arbitrary backend target URLs or expose launch tokens to renderer JS.','KEEP'),
'frontend/components/nexus/nexus-shell.tsx':('Composes conversation, agent, project/settings and workspace detail panes.','752-line function couples nearly all hooks and settings/background transactions.','REFACTOR'),
'frontend/hooks/use-promptforge-workspace.ts':('Owns files, projects, dirty editor state, builds, devices, serial and compatibility actions.','1,097-line hook; shared state responsibilities and legacy execute method remain.','REFACTOR'),
'frontend/hooks/use-forge-agent-session.ts':('Owns agent session selection, prompt/run submission, steering, stream replay and job recovery UI.','1,021-line hook with many refs/effects; preserve sequence/generation guards during extraction.','REFACTOR'),
'frontend/hooks/use-chat-session.ts':('Owns chat selection/messages and durable/stream reconciliation.','Late-response generation guards and coalesced polling are implemented.','KEEP'),
'frontend/hooks/use-forgex-settings.ts':('Keeps confirmed and optimistic settings with serialized writes and rollback.','An existing reliable queue/rollback pattern to reuse when splitting state.','KEEP'),
'frontend/hooks/use-model-runtime-state.ts':('Caches provider/model selection and catalog UI observations.','Separate compatibility/settings/session model state can diverge across stores.','KEEP'),
'frontend/lib/api.ts':('Defines typed frontend HTTP methods for current and retained compatibility endpoints.','Endpoint presence does not imply current Nexus uses every method.','REFACTOR'),
'frontend/lib/settings-write-queue.ts':('Serializes/coalesces preference updates with error recovery.','UI state tests verify rapid settings publication and rollback.','KEEP'),
'frontend/components/nexus/conversation.tsx':('Renders model selection, composer and chat/agent message content.','ReactMarkdown skips raw HTML; avoid treating assistant text as authority or executable markup.','KEEP'),
'frontend/components/code/code-viewer.tsx':('Renders highlighted/copyable code blocks with escaped highlighter HTML.','dangerouslySetInnerHTML uses highlight.js output, not raw assistant HTML; preserve this invariant.','KEEP'),
'frontend/components/terminal/use-terminal-session.ts':('Manages authenticated terminal lifecycle and WebSocket observations.','Separate human-terminal authority; shutdown fault behavior must be fixed in backend.','KEEP'),
'frontend/components/terminal/forgex-terminal.tsx':('Renders interactive xterm tabs and forwards human input.','Terminal escape handling relies on xterm; no current model run_command dispatch points here.','KEEP'),
'scripts/build-model-router.mjs':('Bundles request engine/worker and regenerates pinned Harness provider descriptors.','Generated/vendor coupling requires provenance-aware builds, not duplicated catalog hand edits.','KEEP'),
'scripts/prepare-release.py':('Stages pinned portable Python, locked dependencies, frontend and compiled runtime with manifest/SBOM.','Current Python 3.10 is not the required release Python 3.13.15; no exact installer staged in this audit.','KEEP'),
'scripts/release-desktop.mjs':('Runs package gates and Windows installer build/signing policy.','Unsigned test packaging is not a public signed release.','KEEP'),
'scripts/verify-release-evidence.mjs':('Validates artifact-bound provider, quality, Windows install and physical ESP32 qualification evidence.','Validator code is implemented; current-checkout qualification evidence was not produced.','KEEP'),
'scripts/scan-release-content.mjs':('Rejects source-release secrets, unwanted artifacts and unsafe paths.','Actual current source scan passed; packaged resource scan is a separate unperformed gate.','KEEP'),
'scripts/verify-beta.mjs':('Runs the prescribed beta checks.','Fail-fast runner; independent audit groups were used so one failure did not suppress later results.','KEEP'),
'scripts/dev-desktop.mjs':('Starts development frontend/Electron and manages owned launch processes.','Dev startup sequence differs from packaged backend-first startup.','KEEP'),
'scripts/dev-backend.mjs':('Runs the project-defined development Python API launcher.','No agent worker queue startup is hidden here.','KEEP'),
'electron-builder.config.cjs':('Defines Windows x64 NSIS resources/signing and installer behavior.','No Linux/macOS packaging targets; clean install/upgrade/uninstall and code signing remain unqualified.','KEEP'),
}

def link(path,line=None,label=None):
    return '['+(label or path)+']('+str(ROOT/path).replace('\\','/')+(':'+str(line) if line else '')+')'

def structural_purpose(r):
    if r['kind']=='test':return 'Defines regression fixtures/assertions for '+Path(r['path']).stem+'. Test names are intent; executed outcomes are recorded separately.'
    if r['path'].endswith('__init__.py'):return 'Declares package imports/exports; inspect its import edges below.'
    symbols=[s['name'] for s in r['symbols'] if not s['name'].startswith('_')][:7]
    return 'Defines '+(', '.join(symbols) if symbols else Path(r['path']).stem)+'; structural review of definitions and dependencies only.'

def state(r):
    text=(ROOT/r['path']).read_text(encoding='utf-8-sig',errors='replace')
    operations=[]
    for pattern,label in [(r'sqlite3|BEGIN IMMEDIATE|\.execute\(','SQL or execution calls'),(r'write_text|write_bytes|open\(.*["\']a','file publication/append'),(r'useState|useRef','React local state/references'),(r'localStorage','browser localStorage'),(r'create_subprocess|subprocess\.|spawn\(','process creation'),(r'asyncio\.Lock|RLock|threading\.Lock','in-process locking'),(r'new Map|dict\[','in-memory maps')]:
        if re.search(pattern,text):operations.append(label)
    return '; '.join(operations) or 'No persistence sink identified by the structural scan; injected dependencies may own state.'

lines=['# ForgeX File-by-File Audit','',
       'Snapshot: 2026-10-05. This annex covers all **659 inventoried source files**: **411 owned**, **177 test**, **3 generated**, **68 vendored**. Configuration is listed separately in configuration-inventory.json. Build outputs, node_modules, virtual environments, runtime workspaces and static assets are not product-source review records. No additional native C/C++/Arduino source files were found outside those exclusions.','',
       '**Coverage:** “Call-path review” means behavior was examined on a relevant live/legacy/shadow path; it does not mean every branch was executed. “Structural review” means the complete file was parsed/read for definitions, imports, calls and risk markers, without a dedicated behavioral proof for that file. An absence of an issue in a structural record is not a clean security verdict. Tests have a separate outcome ledger. This distinction is necessary: the audit does not establish line-by-line manual verification or live behavior for every file.','',
       '**Called by / Calls:** import edges are static cross-references, not proof of dynamic invocation. Python AST calls are expressions, not a resolved whole-program call graph. Dependencies injected through app.create_app, registries, callbacks and runtime routing are described in the main report. Full symbols/calls live in inventory.json and typescript-analysis.json.','']
for r in inventory['files']:
    if r['kind'] in {'vendor','generated'}:continue
    path=r['path'];annotation=notes.get(path)
    purpose,problem,action=annotation or (structural_purpose(r),'No dedicated behavior-specific conclusion; investigate any usage change before removal.','KEEP')
    symbols=r['symbols']
    if path in ts:
        symbols=[{'name':f['name'],'line':f['line'],'end':f['end']} for f in ts[path]['functions'] if f['name']!='<callback>']
    key=[]
    # Give precedence to public classes/functions, then long methods for hotspots.
    selected=sorted(symbols,key=lambda s:(s.get('type')!='ClassDef',s['name'].startswith('_'),s['line']))[:12]
    for s in selected:key.append(link(path,s['line'],s['name'])+(' ('+str(s['line'])+'–'+str(s['end'])+')' if 'end' in s else ''))
    callers_list=callers.get(path,[])
    dependencies=r['imports'] if path not in ts else ts[path]['imports']
    calls=r['calls'] if path not in ts else ts[path]['calls']
    sink_calls=[c for c in calls if re.search(r'execute|run|request|generate|write|save|resolve|spawn|flash|connect|verify|apply|read|invoke',c['name'],re.I)]
    unique=[]
    for c in sink_calls:
        if c['name'] not in unique:unique.append(c['name'])
    security=[]
    content=(ROOT/path).read_text(encoding='utf-8-sig',errors='replace')
    for pat,desc in [(r'api_key|credentials|secret|token','credential/authentication or disclosure boundary'),(r'subprocess|create_subprocess|\bspawn\(','host process/lifecycle boundary'),(r'write_text|write_bytes|unlink|rmtree|apply_patch','filesystem mutation boundary'),(r'flash|Serial\(|COM\d|tty','hardware/serial-related path'),(r'fetch\(|httpx|urlopen','network/provider boundary'),(r'dangerouslySetInnerHTML','HTML injection sink requiring escaped output')]:
        if re.search(pat,content):security.append(desc)
    lines+=['## '+path,'','**Coverage:** '+('Call-path review' if annotation else 'Structural review')+'; '+str(r['loc'])+' LOC; '+r['kind']+'.','',
            '**Purpose:** '+purpose,'','**Key classes/functions:** '+('; '.join(key) or 'Declarations/styles/module exports; no callable symbols in this parser.')+'.','',
            '**Called by:** '+('; '.join(link(x) for x in callers_list[:14]) or 'No resolved static importer in inventory. Entrypoints, configuration, dynamic loaders and tests may still invoke it.')+(' Additional importers in static-import-callers.json.' if len(callers_list)>14 else ''),'',
            '**Calls / dependencies:** '+('; '.join('`'+x.replace('`','')+'`' for x in dependencies[:9]) or 'No parsed imports.')+' Significant expressions: '+('; '.join('`'+x.replace('`','')+'`' for x in unique[:10]) or 'none identified')+'.','',
            '**State/persistence:** '+state(r)+'.','',
            '**Security implications:** '+('; '.join(security) if security else 'No direct high-impact sink identified by structural markers; transitive behavior remains relevant.')+'.','',
            '**Problems:** '+problem,'',
            '**Technical debt:** '+('Large module/function coupling; exact sizes in evidence-summary.json.' if r['loc']>500 else 'See dependency/caller coupling above; no separate measured debt finding for this file.'),'',
            '**Recommended action:** '+action+'.','']
lines+=['## Generated and vendored source','',
        'These files were inventoried, searched, parsed where applicable and separated from ForgeX-owned source. Their existence does not prove they are connected to product execution. OpenCode provider/schema/executor imports are live; its agent/tool-runtime code is not the ForgeX run owner. frontend/vendor/braces is a local vendored dependency. Build-generated Monaco/Next/bundles are excluded outputs.','',
        '| Path | Kind | LOC | Action |','|---|---|---:|---|']
for r in inventory['files']:
    if r['kind'] in {'vendor','generated'}:lines.append('| '+link(r['path'])+' | '+r['kind']+' | '+str(r['loc'])+' | KEEP with provenance / regenerate from pinned inputs |')
(OUT/'File-by-File-Audit.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
coverage={'call_path_review':[r['path'] for r in inventory['files'] if r['path'] in notes],
          'structural_review':[r['path'] for r in inventory['files'] if r['kind'] not in {'vendor','generated'} and r['path'] not in notes],
          'vendor_generated':[r['path'] for r in inventory['files'] if r['kind'] in {'vendor','generated'}]}
(OUT/'coverage-ledger.json').write_text(json.dumps(coverage,indent=2),encoding='utf-8')
print(json.dumps({k:len(v) for k,v in coverage.items()},indent=2))
