# Generic Headless Bridge Architecture

## Shared Codex status service

The Codex OAuth bridge has one status authority: `CodexStatusService`. The UI route, QA status command, sandbox-smoke precheck, and product provider registry consume its sanitized classification. This status dependency is separate from prompt execution and cannot make the provider routeable.

Phase 2.5.8.17B selects the safely resolved executable runner with `codex_safe_user_env` for that shared authority. Session parity diagnostics compare direct, resolved, and fixed-CMD status behavior but do not alter prompt execution or routing.

Phase 2.5.8.17R2 adds a strict normalized-single-line validator shared by smoke gating and review creation. It accepts encoding/newline representation differences only; review eligibility still requires the exact expected one-file diff and unchanged workspace boundaries.

Phase 2.5.8.17R3 separates known CLI failure classification from validated artifact success. The QA runner and backend adapter both enforce the full safe pass predicate; only that result can enter persistent review creation.

## Phase 2.5.8.17 Codex OAuth smoke adapter

`codex_cli_oauth_bridge` remains QA-only and non-routeable. Status comes from official CLI behavior. The standalone smoke validates one exact created file plus an unchanged marker and active workspace in an external disposable sandbox, then persists only sanitized metadata and the expected-file patch after an exact pass. It is not normal product Codex routing.

## Phase 2.5.8.14 AGY assisted scratch generator

`agy_scratch_runner` is an experimental generator, not a direct editor. An explicit action runs AGY from a marked external invocation directory, precomputes one exact expected scratch folder, and imports only that folder through `AGYScratchProjectImportService`. Stdout, scratch-root enumeration, and modification time never select import authority.

If the expected folder exists and passes the existing importer, the review provider is `agy_scratch_runner` and includes template, project type, file tree, counts, total bytes, warnings, and active-workspace integrity. If it is missing, ForgeX creates no review and preserves the explicit manual import UI. Product routing, production eligibility, automatic apply, build, and flash remain disabled.

## Phase 2.5.8.13 manual AGY scratch project import

AGY remains paused as a direct workspace editor. Its supported project path is now a manual artifact boundary: the user runs AGY separately, selects one exact generated folder under the AGY scratch root, and asks ForgeX to import it. ForgeX does not launch AGY, enumerate candidate folders, choose the newest output, or trust provider-output links.

The `agy_scratch_import` provider is `manual_artifact` / `manual_import` / `managed_import_sandbox` with no authentication responsibility. It validates the selected tree and captures allowlisted text bytes before creating `.promptforge/agy-import-sandboxes/<run_id>`. A marker-only baseline, exact created-file diff, and unchanged active workspace are required before persistent review creation. Normal planner routing and production eligibility remain false.

## Phase 2.5.8.12C experimental Codex CLI-auth bridge

The Codex subscription bridge is intentionally separate from normal product routing:

```text
official Codex CLI auth + model execution
        -> external ForgeX-managed disposable sandbox
        -> ForgeX hash diff and exact-output validation
        -> persistent Bridge Review after exact pass only
        -> later explicit user approval; no automatic apply/build/flash
```

The working standalone proof showed that global approval must precede `exec`, and `exec` must precede the workspace sandbox and cwd options. The ForgeX builder now reproduces that order and uses a unique direct child of `C:\forgex-codex-sandboxes` rather than the earlier nested OneDrive/repository state path. Authentication remains inside the official CLI; ForgeX neither implements OAuth nor handles session material.

The provider registry exposes `codex_cli_subscription` only as experimental QA state. A passing native smoke may make its generated review eligible, but production eligibility and product routing remain false.

## Phase 2.5.8.11 product runtime integration

The production direction is now wired through `POST /agent-runtime/runs` and related status, event, cancellation, provider, and review routes. These routes call `ProductAgentService`, which resolves only routeable planner providers and delegates all filesystem work to `ForgeXToolRuntime`. The runtime uses one managed sandbox for a bounded multi-turn session, validates `forgex.toolplan.v1`, returns sanitized tool results to the planner, and creates a persistent Bridge Review only after safe diff and active-workspace integrity checks.

The product route is disabled unless `FORGEX_ENABLE_AGENT_RUNTIME=1`. The deterministic fake planner additionally requires `FORGEX_ENABLE_AGENT_RUNTIME_FAKE_PROVIDER=1`. AGY and Codex remain paused; OpenCode remains reference-only. The product runtime contains no local CLI process launcher and no shell, network, dependency-install, build, flash, or apply tool.

## Phase 2.5.8.9 ForgeX-owned runtime boundary

Local CLI providers are no longer the production runtime direction. AGY and Codex are paused and non-routeable after failing safe positive-write validation; Claude CLI is disabled; OpenCode remains reference-only. Generic detection and isolated QA evidence do not grant routing authority.

The new internal path is `provider ToolPlan -> ForgeX validation -> ForgeX sandbox tools -> exact diff -> Bridge Review`. Providers do not write the active workspace. The fake API provider proves this path without network, keys, or external processes. Real API adapters remain disabled design placeholders. Existing patch export, verification, preflight, approval, apply, rollback, build, and flash boundaries are not weakened.

## Phase 2.5.8.7 Codex investigation boundary (historical)

Codex CLI 0.142.5 locally documents `codex exec` as its non-interactive mode. ForgeX now has detection and a native-only guarded sandbox harness using direct argv, a unique marked child under `.promptforge/codex-sandboxes`, relative hash baselines, exact one-file validation, and memory-only process diagnostics. The corrected real attempt was blocked by Codex workspace/permission policy and produced zero changes.

Because native write did not pass, Codex is not registered in the generic provider registry, is not accepted by the generic API, and has no live QA, review, or patch authority. Its feature flags remain disabled defaults. The architecture remains:

```text
ForgeX UI -> Generic Bridge API -> provider adapter -> managed disposable sandbox
-> provider execution -> diff capture -> review artifact
-> patch export / verify / preflight -> no auto-apply
```

AGY remains paused; Codex investigation does not execute AGY, Claude, or OpenCode.

## Phase 2.5.8.6.5 AGY scratch artifact boundary

AGY is treated as an artifact-generation provider when its non-interactive mode writes to provider-owned scratch instead of the managed cwd. The QA integration generates one run ID, nonce, and exact filename before execution. After AGY exits, ForgeX checks only that exact home-relative scratch path; it never enumerates the directory, selects a recent file, or treats stdout links as authority.

The initial accepted type is a `.txt` smoke artifact capped at 16 KiB. Import requires exact content, matching run ID and nonce, a regular non-link file, and realpath containment. A passing import creates a metadata-only Bridge Review with `artifact_source=agy_scratch`, `artifact_type=scratch_smoke`, a content hash, and size. It does not fabricate a workspace diff and cannot be exported or applied as a patch because it has no changed files.

## Phase Boundary

Phase 2.5.8.1 adds a provider-neutral vocabulary under `backend/bridges/generic`. It does not add a generic run endpoint, provider adapter, command builder, executable resolver, prompt route, or process launcher. Existing AGY code remains outside this layer.

```text
ForgeX UI (no generic run API)
          |
          v
Read-only safety diagnostics
          |
          v
Generic contracts + orchestration-owned state
          |             |                 |
          v             v                 v
Sandbox owner      Run metadata       Artifact references
(existing service) (atomic JSON)       (review/patch IDs)
          |                               |
          +------ future adapter ---------+
                          |
                          v
              Existing review -> preflight -> apply/rollback gates
```

The AGY adapter connection now exists as a registered translation boundary, but generic routing and execution remain disabled. Existing AGY API traffic does not use it.

## Discovered Ownership Boundaries

| Concern | Current owner | Generic-layer relationship |
| --- | --- | --- |
| Provider detection | `BridgeDetectionService` and provider detectors | Separate detection result contract; no detector migration |
| AGY execution/cancellation | `AntigravitySandboxRunner` | Adapter delegates; no generic routing |
| Sandbox copies and cleanup | `BridgeSandboxService` | Context requires a verified, disjoint managed sandbox |
| Review snapshots and diffs | `BridgeDiffService` / `BridgeReviewStore` | Artifact references carry existing review IDs |
| Patch bytes and integrity | `BridgePatchExportService` / `BridgePatchStore` | No duplicate content store; patch references carry patch IDs |
| Apply/preflight/rollback | Existing patch safety services | Artifact creation grants no apply authority |
| Audit | `BridgeAuditLog` | Generic metadata excludes instructions and paths; no duplicate audit stream |
| Generic run metadata/events | `BridgeRunStore` | Bounded atomic store; startup reconciliation fails closed without taking down health |
| Packaged backend lifecycle | FastAPI app state created by Electron-managed backend | Electron owns backend/frontend; QA records sanitized lifecycle evidence |

## Provider Contract

`BridgeProvider` is a runtime-checkable async protocol. A provider has a stable allowlisted ID, declares a minimal environment allowlist, exposes versioned capabilities, and separates `detect`, `validate`, `start`, and `cancel`. `start` accepts only `BridgeRunContext`, which always contains a validated `BridgeSandboxContext`.

The request has no command, argv, executable path, shell fragment, environment mapping, or active-workspace path. Executable resolution and fixed argv construction remain future provider responsibilities. Registration is explicit; registration never makes a provider routable.

## Capabilities

Capability schema version 1 defaults every optional feature and availability to false. `sandbox_required` defaults to true and construction fails if it is false. Capability responses contain no credential, environment, executable, or filesystem fields. Codex, Claude Code, and OpenCode have valid reserved IDs but no registered provider and cannot report availability.

## Runtime-Only Request Data

`BridgeRunRequest.instruction` is required at runtime, marked `repr=False`, and omitted from safe metadata. Persistence receives only SHA-256 and character length. IDs use constrained syntax; provider IDs additionally use an explicit allowlist. Timeout is constrained to 10–900 seconds. No public serialization includes an active-workspace or sandbox root.

## Canonical State Model

| Current | Allowed next |
| --- | --- |
| `queued` | `validating`, `cancelling`, `cancelled`, `failed`, `interrupted` |
| `validating` | `preparing_sandbox`, `blocked`, `cancelling`, `failed`, `interrupted` |
| `preparing_sandbox` | `running`, `cancelling`, `failed`, `interrupted` |
| `running` | `collecting_artifacts`, `cancelling`, `failed`, `timed_out`, `interrupted` |
| `cancelling` | `cancelled`, `failed`, `interrupted` |
| `collecting_artifacts` | `completed`, `failed`, `cancelling`, `interrupted` |
| terminal states | none |

`BridgeRunStateMachine` is the orchestration-owned mutation boundary. Illegal transitions raise `invalid_transition`; terminal states are immutable; transition timestamps cannot move backward. Provider results are observations and cannot mutate a record. On restart, every nonterminal record becomes `interrupted`; terminal history is unchanged.

## Persistence

`BridgeRunRecord` schema version 1 stores IDs, timestamps, status, instruction hash/length, counts, safe failure data, and cancellation metadata. `BridgeRunStore` writes deterministic JSON through a same-directory temporary file, flushes and fsyncs it, and atomically replaces the target. It retains at most 500 records by default and retains only their related event and artifact metadata.

Malformed JSON, unexpected shapes, unsupported schema versions, and invalid records fail closed as `record_corrupt`. The store has no QA fixture overwrite mode. It never serializes instructions, model output, credentials, process output, environment variables, active-workspace paths, patch content, or file content.

## Events

Events have a run-local sequence, timestamp, enum type, bounded safe message, optional progress, failure code, and artifact ID. Sequence starts at one and must match the persisted event count. Messages redact secret assignments and filesystem paths, collapse whitespace, cap at 512 characters, and reject patch markers or NUL data. There is no streaming transport in this phase.

## Artifacts

Artifacts are metadata references, never embedded content. Storage references are relative internal identifiers resolved beneath a caller-supplied managed root; public serialization omits them. Size limits are explicit. Diff and patch references require both an existing review ID and the pipeline-owned artifact ID. The existing review, integrity, preflight, apply, and rollback services remain authoritative. Creating a reference cannot apply, build, or flash.

## Sandbox and Cleanup

A provider start context cannot exist without a sandbox context whose containment was verified. Managed sandbox and active workspace roots are internal, canonicalized, and required to be disjoint (neither may contain the other). Public output includes only IDs, lifecycle status, cleanup policy, and the containment result. Providers cannot override a frozen sandbox root. Cleanup remains the responsibility of the existing sandbox lifecycle owner.

## Cancellation

Cancellation returns one of `accepted`, `already_requested`, `already_terminal`, `not_found`, or `rejected`. Orchestration records the timestamp and constrained reason code before provider cooperation. Repetition is idempotent. Escalation is represented as `none`, `cooperative`, or `process_termination`; generic escalation is not implemented. Cancellation does not delete artifacts, and terminal cancellation cannot later transition to completion.

## Stable Errors

Stable codes are: `provider_unavailable`, `provider_disabled`, `provider_unsupported`, `invalid_request`, `invalid_transition`, `sandbox_required`, `sandbox_invalid`, `sandbox_escape_blocked`, `timeout`, `cancelled`, `process_start_failed`, `process_failed`, `artifact_invalid`, `persistence_failed`, `record_corrupt`, and `internal_error`.

Arbitrary exceptions normalize to a fixed public message plus an internal exception type. Public errors never expose stack traces or raw exception text.

## Deferred Work

- Phase 2.5.8.2 registers `AGYBridgeProvider`; existing AGY remains authoritative and unrouted.
- Phase 2.5.8.3: add the generic coordinator, persisted lifecycle integration, cancellation orchestration, and artifact lifecycle. Event streaming transport remains deferred until its backpressure design is complete.
- Codex, Claude Code, and OpenCode execution remain deferred.
- Generic prompting/routing, resume, auto-apply, auto-build, and auto-flash remain disabled.

## Phase 2.5.8.3 Internal Coordinator

`GenericBridgeRunCoordinator` now owns internal orchestration ordering while leaving processes, sandbox operations, review data, and patch safety with their existing owners. Production uses `DefaultDenyBridgeRoutingPolicy()` with global and per-provider permissions false.

The coordinator writes canonical state using revision-based compare-and-set, appends each event only after its related state is durable, validates provider-returned references before completion, and delegates cancellation to the provider. Internal fanout has bounded queues/history and explicit resynchronization. Startup only reconciles stale nonterminal records to `interrupted`; it never detects, resumes, or executes a provider.

There is still no public generic start, run, cancellation, event-stream, or provider-selection endpoint.

## Phase 2.5.8.4 Compatibility Cutover

The existing AGY API now resolves through `AGYExecutionRouter`. With cutover off it delegates unchanged to the legacy runner. With all explicit gates on it creates one managed sandbox, invokes the generic coordinator, and the AGY adapter calls the legacy runner's sandbox-reuse entry point. A partial cutover is blocked; generic failure never falls back to legacy.

The safe execution mode is persisted on generic run records and drives status lookup, cancellation, idempotency reconstruction, and restart behavior. Canonical state is centrally mapped back to the existing AGY status vocabulary, while review IDs continue to come from the existing review service.

## AGY Adapter Mapping

`AGYBridgeProvider` uses provider ID `agy` and delegates detection to the existing `antigravity_cli_bridge` detector identity. Its internal translation passes only the validated generic sandbox root, runtime instruction, and bounded timeout to `AntigravitySandboxRunner`. That runner still owns its execution sandbox, executable resolution, fixed argv, environment filtering, process, timeout, cancellation, capped diagnostics, review creation, and audit lifecycle.

Successful legacy `review_ready` maps to canonical completion only after a contained generic reference reuses the existing review ID. Generic cancellation delegates by the legacy run ID and prevents late completion from adding a second terminal event. Explicit registry registration does not invoke detection and cannot make `agy` routable.
# Phase 2.5.8.5 local API and event boundary

The coordinator now has a local, sanitized API boundary. Public execution authorization is immutable application state and requires `FORGEX_ENABLE_AGY_BRIDGE`, `FORGEX_ENABLE_GENERIC_BRIDGE_API`, `FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING`, `FORGEX_ENABLE_AGY_GENERIC_PROVIDER`, and `FORGEX_ENABLE_AGY_GENERIC_CUTOVER` together. Provider registration and diagnostics do not satisfy this policy.

Generic submission uses `POST /models/bridges/runs`. Existing legacy AGY GET/detail/cancel routes remain intact, so sanitized generic queries use `/models/bridges/generic/runs`. The backend resolves `project_id` to an owned project root and gives the path only to the existing sandbox service. No frontend-controlled command, executable, arguments, environment, working directory, or sandbox path enters coordination.

SSE at `/models/bridges/runs/{run_id}/events` serializes only `BridgeRunEvent.to_dict()`. Replay is sequence ordered, bounded to 200 events, and keyed by `Last-Event-ID`. An unknown or overflowed cursor produces one `resync_required` event. Each subscriber has an isolated bounded queue; disconnect, terminal completion, and backend shutdown release subscriptions. Event transport errors create a persisted safe warning but never alter provider execution.

The Agent hook owns SSE and bounded polling as mutually exclusive transports. It stores only the active run ID per project, rejects duplicate/stale events, and never retries a start request automatically.

## Phase 2.5.8.5.1 packaged readiness

Generic-history restoration cannot abort application composition. `AGYExecutionRouter` and FastAPI lifespan reconciliation catch `BridgeDomainError`, expose an unavailable-store diagnostic, preserve `/health` and Bridge Safety, and leave store-dependent APIs fail-closed. Provider detection and execution do not start during composition.

The packaged harness uses an isolated phase root, explicitly forces every generic execution flag off, seeds sanitized fake run/event metadata only after backend health, and validates AGY-only providers, listings, disabled start, SSE replay/heartbeat/terminal/resync, renderer DOM, and restart behavior. QA-only Agent state responses exist only under `FORGEX_QA_MODE=1`; the frontend disables submit and cancel while a fixture is active.

## Phase 2.5.8.6 operator-guided live harness

`qa:agy-generic-live -- --check-only` is a non-prompting preflight. It checks source-supported and enabled gates, exact throwaway containment/marker, AGY presence through the system locator, version through local installation metadata or a fixed non-shell `--version` fallback, explicit authentication attestation, a live-gated loopback backend, and optional required Agent UI reachability. It never submits an instruction or starts a provider run.

The confirmed path records a relative file/hash baseline, submits one fixed AGY request, consumes sanitized SSE, and requires one generic-run, compatibility-run/process, and sandbox delta. Success then validates a one-file review, patch export, integrity, and read-only preflight without apply. Cancellation and timeout are distinct confirmed modes.

The June 2026 live validation reached `READY` and executed exactly one AGY process. It exposed and fixed a Windows version-metadata fallback gap and a Python 3.10 SSE heartbeat timeout mismatch. Post-run replay was ordered and sanitized, the active workspace remained unchanged, and one review was created. AGY produced no changed file, so the expected artifact and positive patch pipeline remain unproven; the harness did not retry.

## Phase 2.5.8.6.1 positive-artifact hardening

The AGY runner now records an internal sanitized diagnostic record: sandbox entry and marker booleans, sandbox/unknown cwd identity, instruction length/hash/delivery, exit and provider-output classifications, pre/post file counts, diff count, ignored-file count, and review count. Raw instruction and provider output remain excluded.

Diff collection starts only after the AGY process terminates and uses at most three scans with 100-millisecond settling intervals. A zero-change result becomes `completed_no_changes` at the legacy boundary and `no_changes_produced` at the generic boundary. It creates no review artifact and grants no apply authority.

AGY 1.0.14 still requires per-folder trust/write approval for unique sandboxes. Its documented `-p` mode is retained because alternative project/workspace flags either required interaction or failed. ForgeX does not automate trust, alter global AGY permissions, or use the unsafe permission-bypass flag.

## Phase 2.5.8.6.2 reusable trusted AGY workspace

Unique live AGY cwd folders are replaced only in explicit QA trusted mode. The generic router asks `AGYTrustedWorkspaceService` for a stable, workspace-hash-keyed direct child under ForgeX managed state. The service requires its marker and validates containment, protected-root separation, and recursive symlink absence before preparation and every run.

Preparation/reset copies the active throwaway workspace without ignored directories. An exclusive tokenized lock serializes runs and remains held until the provider exits and the existing diff/review pipeline finishes. The generic coordinator receives the trusted root as its sandbox root and the original throwaway workspace as the active root, so review changes come from AGY's managed copy while patch preflight still targets the unchanged active baseline.

The bootstrap command never executes AGY. The verify command reports operator attestation required when AGY cannot expose trust status. Check-only and live classifications distinguish missing preparation from missing per-run attestation. A confirmed live run requires all prior flags, the trusted-workspace flag, and `--trusted-workspace-attested`; no attestation is persisted.

### July 2026 trusted-workspace validation

The gated loopback backend reached ready state with all seven QA/live flags, and attested check-only returned `READY` without executing a provider. One confirmed AGY `1.0.14` process then ran in the stable managed workspace. It produced zero changes and was classified as `no_changes_produced`; no review, patch, or apply authority was created.

The active and managed workspace copies had zero relative hash differences after the run, and the expected smoke artifact was absent from both. This validates containment and safe zero-change handling, but not the positive artifact or patch pipeline. Phase 2.5.8.6.2 therefore remains incomplete.

## Phase 2.5.8.6.3 native reproduction boundary

The QA-only native reproduction command bypasses the generic coordinator, not ForgeX containment. It reuses the stable managed workspace, resets from the marked throwaway source, tracks relative hashes and the internal marker separately, and requires both explicit native-execution confirmation and per-run trust attestation. It starts one `agy -p` process with direct argv and `shell: false`; the instruction exists only in process memory and provider output is reduced in memory to a bounded classification.

The command cannot create review, patch, apply, build, or flash authority. It rejects protected roots, active-workspace overlap, symbolic links, arbitrary argv, and unsafe permission flags. The first native run was `NATIVE_AUTH_BLOCKED` with zero changed files, so no generic rerun or invocation matrix was authorized.

## Phase 2.5.8.6.4 auth-only investigation boundary

The native QA command supports a mutually exclusive auth-only mode. It requires trusted-workspace attestation, validates the existing managed workspace without resetting it, records relative hash baselines, and performs only a fixed bounded `--version` probe when official local help exposes no safe auth status command. It submits no instruction and reports separate version, auth, and write execution counts.

The system locator may miss an application that direct process resolution can execute on Windows. Check-only therefore has a bounded PATH scan fallback that returns an internal executable identity but never exposes a path. This corrected a false installation result; subsequent check-only detected AGY and stopped at missing authentication attestation. No routing, provider, review, or apply architecture changed.
## Phase 2.5.8.12 provider direction

Local CLI providers remain paused or reference-only for production routing. API-backed providers are now planner-only entries in the product Agent Runtime registry:

- Gemini
- Groq
- OpenRouter
- OpenAI
- NVIDIA NIM

These providers do not execute files, do not receive shell/network/install/apply/build/flash tools, and do not write the active workspace. They return strict ToolPlan JSON. ForgeX executes validated tools in a managed sandbox, captures a validated diff, and creates a persistent Bridge Review for explicit user apply only.
