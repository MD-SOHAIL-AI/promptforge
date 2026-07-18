# ForgeX Unified Coding-Agent Workflow

Status: Architecture proposal; no unified runtime behavior is implemented by this document

Scope: A ForgeX-native provider contract and workflow that can compose verified templates, API coding agents, and local coding-agent CLIs with the existing review, apply, build, flash, and monitor systems.

Related documents:

- [Provider and Runtime Boundaries](provider-runtime-boundaries.md)
- [API Coding Agent Architecture](api-coding-agent.md)

## Architectural decision

ForgeX remains the authority for every state-changing or hardware-facing operation:

```text
Agents generate or review code.
ForgeX validates.
ForgeX creates the review and diff.
The user approves.
ForgeX applies.
ForgeX builds.
ForgeX flashes.
ForgeX monitors.
```

A provider's installation, authentication, availability, model route, or successful run never grants active-workspace, patch-apply, build, flash, or serial-monitor authority. Coding providers operate through provider-neutral contracts and ForgeX-managed sandboxes. Existing V1 build, flash, and monitor implementations remain the owners of those operations until an explicit migration is designed and tested.

## OpenCode patterns used as inspiration

The reference reviewed was `opencode-dev.zip`. ForgeX adopts architectural ideas, not OpenCode implementation code or its TypeScript/Effect runtime.

| OpenCode pattern | ForgeX-native interpretation | Deliberate ForgeX difference |
|---|---|---|
| Declarative agent profiles | Immutable coding-provider descriptors with identity, execution mode, capabilities, readiness, and policy references | A profile records eligibility; it grants no authority by itself |
| Materialized tool registry | A coding capability registry filters provider-visible capabilities for each run | Build, flash, and monitor are never materialized as coding-provider capabilities by default |
| `allow` / `deny` / `ask` permission rules | ForgeX-owned permission decisions evaluated against a provider, run mode, resource, and workflow stage | Active-workspace apply and hardware actions cannot be converted into provider authority by a remembered answer |
| Durable session runner and event stream | A bounded, resumable coding run with canonical events and terminal states | Provider turns stop at validated review output; ForgeX owns later workflow stages |
| Structured write/edit/patch operations | Versioned provider output converted into ForgeX sandbox changes, diffs, and patches | Model-authored paths and patches are untrusted; ForgeX generates the authoritative review/patch |
| Command markdown workflows | Versioned ForgeX workflow commands or task presets that resolve into `ExecutionPlan` inputs | Command text cannot smuggle shell, build, flash, or monitor permission |
| Provider/model separation | Coding-provider selection is separate from `model_router` model selection | `model_router` routes model calls only; a model route is not coding-provider or filesystem permission |
| Bounded shell output and timeouts | Future CLI adapters require bounded time, output, environment, cancellation, and managed working directories | Arbitrary model-supplied shell strings are not an initial coding-agent capability |

Useful details from the reference include filtering unavailable tools before a model turn, recording calls before side effects, rejecting stale tool registrations, bounding steps and outputs, persisting typed events, and detecting stale file content before edits. These ideas should be re-expressed with frozen Python dataclasses, existing ForgeX stores, and ForgeX error classifications.

OpenCode behavior that accepts host-level shell access or direct location mutation is not a ForgeX precedent. ForgeX's stronger sandbox, review, apply, and hardware boundaries take priority.

## 1. Canonical target workflow

```text
User request
    |
Planner creates ExecutionPlan
    |
WorkflowRunner executes stages and authorization gates
    |
Generation stage selects a coding provider
    |
Provider generates only into a managed sandbox or structured proposal
    |
ForgeX validates paths, content, declared changes, and active-workspace integrity
    |
ForgeX creates review/diff
    |
Workflow pauses: apply.waiting_for_approval
    |
User approves apply
    |
ForgeX preflights, snapshots rollback state, and applies patch
    |
ForgeX build adapter / PlatformIO service
    |
Workflow pauses for explicit flash action and valid device
    |
ForgeX flash adapter / board and serial services
    |
ForgeX monitor adapter / serial service
    |
Sanitized run summary stored
```

The workflow is resumable. Review creation is a successful generation outcome, not permission to continue automatically. Approval, apply, build, flash, and monitor are separate transitions with fresh preconditions.

### Required change from the current plan model

The current planner can emit `GENERATE_CODE` followed immediately by `BUILD_FIRMWARE`. Sandbox-agent generation currently produces a review instead of an applied `GeneratedProject`. A future unified workflow must represent review and apply as explicit gates or equivalent resumable stage state. It must never interpret `GENERATE_CODE -> BUILD_FIRMWARE` as implicit permission to apply a provider patch.

The initial implementation should preserve current V1 plan execution and add the unified path behind an explicit feature boundary. Migration can occur only after parity and safety tests cover both paths.

## 2. Coding provider types

All provider classes implement one ForgeX coding-provider contract. They differ in transport and authentication, not in authority.

| Provider type | Purpose | Execution mode | API keys | Local CLI auth | Active workspace mutation | Review required | Current status |
|---|---|---|---|---|---|---|---|
| `verified_template` | Deterministic output for simple, known firmware requests | In-process verified generation, then ForgeX validation | No | No | No | Yes in the unified workflow | Available for simple known tasks |
| `api_coding_agent` | Structured provider-backed code proposals | Remote model proposal to managed sandbox/review; deterministic fake for tests | Live form: yes; fake: no | No | No | Yes | Versioned contract, parser, and dev-only fake review flow exist; live routing is not enabled |
| `codex_cli` | Local Codex coding-agent execution | Official CLI/app-server session constrained to a managed sandbox | No ForgeX API key | Yes, owned by official CLI | No | Yes | OAuth/status and experimental sandbox work exist; default-disabled and not production-eligible |
| `agy_cli` | Local AGY coding-agent execution and artifact import | Managed sandbox or bounded imported artifact | No ForgeX API key | Provider-owned local session | No | Yes | QA/sandbox work exists; capability evidence and flags remain conservative |
| `claude_code_cli` | Future Claude Code adapter | Future managed CLI sandbox | No ForgeX API key expected | Yes | No | Yes | Future; no execution integration in this phase |
| `opencode_cli` | Future OpenCode adapter | Future managed CLI sandbox | Depends on OpenCode configuration, never stored as a parallel ForgeX secret | Yes or provider-managed | No | Yes | Reference-only; no execution integration in this phase |
| `manual_patch` | Optional user-imported patch or artifact | Offline import, validation, and review | No | No | No | Yes | Future/optional |

`provider_id` identifies a configured provider instance or adapter. `provider_type` identifies the class above. A live API coding agent may use `model_router` to select a model provider, but remains one coding-provider type from the workflow's perspective.

## 3. Provider selection policy

Selection is deterministic, explainable, and fail-closed:

1. Use a verified template only when deterministic matching classifies the request as simple, known, and within template scope.
2. Otherwise honor an explicit user-selected coding provider if it is detected/configured, enabled, policy-eligible, and compatible with the requested context mode.
3. Otherwise use the route-configured coding provider from the future coding-provider registry.
4. Use the API coding agent only when an API fallback is explicitly configured, ready, privacy-compatible, and permitted for the request. A fallback that changes the external recipient requires disclosure or consent covering that recipient.
5. Return a clear actionable error when no safe provider is available. Do not silently select a paused, disabled, unauthenticated, or incompatible provider.

Template keyword matches are insufficient. Complexity, ambiguity, existing-project modification, requested provider-specific behavior, or unsupported hardware must reject a simple template. Complex prompts continue to a capable coding provider or fail clearly; they are never reduced to a blink-style template merely to produce output.

Coding-provider routing must not be inferred from:

- CLI installation alone;
- authentication alone;
- `model_router` provider availability;
- a previous successful smoke test;
- frontend state;
- a provider's self-declared capabilities.

## 4. Shared agent run contract

The future provider-neutral run record should be immutable at API boundaries and use explicit state transitions. A logical public representation is:

```json
{
  "schema_version": "forgex.coding_run.v1",
  "run_id": "coding-run-...",
  "task_id": "task-...",
  "project_id": "project-...",
  "provider_type": "api_coding_agent",
  "provider_id": "fake_api_coding_agent",
  "mode": "sandbox_review",
  "stage": "generation",
  "status": "running",
  "input": {
    "prompt": "Create an ESP32 blink project",
    "workspace_root": "<safe display reference>",
    "context_mode": "selected_files"
  },
  "output": {
    "summary": null,
    "review_id": null,
    "files_changed": []
  },
  "events": []
}
```

### Contract rules

- Required identity: `schema_version`, `run_id`, `task_id`, `project_id`, `provider_type`, and `provider_id`.
- `mode` initially supports `sandbox_review`, `structured_proposal`, `verified_template`, and `manual_import`.
- `stage` is one canonical workflow stage, not provider-specific terminology.
- `status` uses a finite state machine such as `queued`, `validating`, `preparing_sandbox`, `running`, `awaiting_review`, `awaiting_apply`, `applying`, `building`, `awaiting_flash`, `flashing`, `monitoring`, `completed`, `failed`, `cancelled`, or `timed_out`.
- `output.review_id` is set only after ForgeX creates a persisted review.
- `files_changed` comes from the ForgeX diff, not the provider's claim.
- Events are append-only, sequenced, bounded, sanitized, and replayable.
- Raw prompts, raw model responses, workspace paths, source bodies, command output, and secrets are not automatically persisted. The logical input above may be represented internally by hashes, safe references, and bounded metadata.
- A run is idempotent by a caller-supplied key or ForgeX correlation identifier. Conflicting reuse fails.
- Cancellation, timeout, provider failure, and validation failure are terminal unless a new explicitly linked attempt is created.

Provider adapters return a normalized generation result; they do not return a build, flash, monitor, or active-workspace-apply result.

## 5. ForgeX-native permission model

Permissions use `allow`, `ask`, and `deny`, but authority is evaluated by ForgeX against provider, stage, resource, context mode, and run. A provider cannot widen its own policy.

Canonical permissions:

- `read_workspace`
- `write_sandbox`
- `create_review`
- `apply_patch`
- `run_build`
- `run_flash`
- `open_monitor`
- `network_access`
- `external_context_upload`

Recommended defaults:

| Permission | Coding provider | ForgeX workflow runner |
|---|---|---|
| `read_workspace` | Limited to filtered manifest or selected files | Allowed as required by the current stage |
| `write_sandbox` | Allowed only in an owned, verified managed sandbox | Allowed for orchestration and validation |
| `create_review` | Allowed only through a ForgeX review service request | Allowed after validated exact diff |
| `apply_patch` | Denied | Allowed only after explicit approval, fresh preflight, and rollback snapshot |
| `run_build` | Denied | Allowed through existing build adapter/service after successful apply |
| `run_flash` | Denied | Allowed only after build success and explicit user action/confirmation |
| `open_monitor` | Denied | Allowed through existing monitor adapter/service |
| `network_access` | Denied for local CLI by default; narrowly allowed for the selected API transport | Stage- and destination-scoped |
| `external_context_upload` | Ask, with manifest and provider disclosure | Allowed only after applicable consent and filtering |

`create_review: allowed` means a coding-provider run may submit validated output to the ForgeX review service. The provider does not own review persistence or decide that its output is safe.

Remembered permission decisions must be resource- and scope-specific. They cannot permanently grant `apply_patch`, `run_flash`, unrestricted network access, secret access, or external-directory writes. Denials from the base safety policy cannot be overridden by user preference or provider configuration.

## 6. Tool and capability registry

The future `CodingProviderRegistry` should store evidence-backed descriptors and produce a per-run materialized capability set. Registration and detection are data; neither is execution permission.

Initial coding-provider capabilities:

| Capability | Meaning | Required guard |
|---|---|---|
| `generate_files` | Propose complete new text files | Contract, path, content, count, and byte validation |
| `modify_files` | Propose changes to existing selected files | Read manifest, stale-content detection, exact diff verification |
| `suggest_commands` | Return advisory command text | Inert data only; never direct execution |
| `read_selected_context` | Receive a bounded filtered context manifest | Consent, protected-path filtering, secret scan, token/byte budget |
| `run_in_cli_sandbox` | Execute an approved local adapter inside an owned sandbox | Detection, auth, environment scrub, containment, timeout, cancellation, output bounds |
| `create_patch` | Request ForgeX to derive a patch from validated sandbox changes | ForgeX diff and patch services only |
| `create_review` | Request persistence of a review candidate | Exact declared change set and active-workspace integrity check |

`run_build`, `run_flash`, and `open_monitor` are workflow-adapter capabilities, not coding-provider capabilities. A future repair loop may ask ForgeX to run an allowlisted build in a sandbox, but that remains a separate ForgeX-owned tool with its own policy; it does not make general build or shell authority available to the model.

The registry should expose:

- provider descriptor and provider type;
- transport and authentication type;
- current readiness and evidence;
- supported coding capabilities;
- supported context modes;
- applicable permission-policy ID;
- production eligibility and dev/experimental status;
- safe failure reason;
- adapter factory or injected implementation.

Materialization must remove capabilities denied by base policy before constructing a provider request. Runtime settlement must reject stale registrations, undeclared tools, and capability use not present in the run snapshot.

## 7. Stage ownership and gates

| Stage | Owner | Input | Output and gate |
|---|---|---|---|
| Plan | `backend/agent/planner.py` and canonical `ExecutionPlan` | User task | Immutable plan; no execution authority |
| Generate | Selected coding-provider adapter under `agent_runtime` constraints | Task, provider selection, filtered context, managed sandbox | Normalized proposal or sandbox result |
| Validate | ForgeX contract and safety policy | Provider output and sandbox snapshots | Exact safe change set or terminal rejection |
| Review | `BridgeDiffService`, review store, patch export services | Validated sandbox changes | Pending review; workflow pauses |
| Apply | Patch preflight, rollback snapshot, and patch apply services | Approved review/patch and active workspace | Applied workspace or rollback-safe failure |
| Build | Existing build adapter, PlatformIO service, and build tools | Applied workspace | Validated firmware artifact |
| Flash | Existing flash adapter plus board/serial services | Successful build artifact and confirmed device action | Verified flash result |
| Monitor | Existing monitor adapter and serial service | Selected device/port and explicit workflow action | Bounded serial events and terminal monitor result |
| Summary | ForgeX run-summary store | Sanitized stage outcomes | Durable safe summary |

The coding provider owns none of the Validate, Review, Apply, Build, Flash, Monitor, or Summary stages. It may supply generation metadata, but ForgeX derives authoritative changed-file, artifact, and hardware results.

### Transition gates

- `Plan -> Generate`: provider selected and policy-compatible.
- `Generate -> Review`: proposal valid, sandbox contained, active workspace unchanged, exact diff confirmed.
- `Review -> Apply`: explicit approval, unexpired review, patch integrity valid.
- `Apply -> Build`: fresh preflight passed, rollback snapshot created, patch applied successfully.
- `Build -> Flash`: build succeeded, artifact validated, device available, explicit user action/confirmation received.
- `Flash -> Monitor`: flash succeeded and the user or workflow request explicitly includes monitoring.
- Any failed gate stops downstream stages and emits a stable failure classification.

## 8. Common event timeline

Events use a versioned envelope containing at least `event_id`, `sequence`, `run_id`, `task_id`, `stage`, `type`, `timestamp`, `status`, `safe_message`, and bounded `metadata`. Provider-native events are translated before reaching API/WebSocket consumers.

Canonical timeline:

```text
plan.started
plan.completed
provider.selected
generation.started
generation.completed
review.created
apply.waiting_for_approval
apply.started
apply.completed
build.started
build.completed
flash.waiting_for_device
flash.started
flash.completed
monitor.started
monitor.output
monitor.completed
workflow.completed
```

Every stage may also emit `<stage>.failed`; the workflow emits `workflow.failed`, `workflow.cancelled`, or `workflow.timed_out` exactly once as its terminal event. `monitor.output` is bounded, sequenced, and may be truncated with explicit metadata. Raw secrets, full provider responses, unbounded compiler output, and unrestricted serial output are never event payloads.

UI state is a projection of backend events and persisted run state. Missing or reordered client delivery must be recoverable through sequence-based replay. UI controls do not grant backend permission.

## 9. Safety rules

- Coding providers never directly mutate the active workspace.
- Local CLI providers run only in ForgeX-managed sandboxes with scrubbed environments, bounded time/output, cancellation, and containment verification.
- API providers receive only the disclosed, filtered, bounded context approved for the selected provider and fallback scope.
- Every generated path is canonicalized and checked for traversal, absolute paths, Windows drive/UNC paths, reserved names, Unicode normalization, symlinks, junctions, reparse points, and sandbox containment.
- Protected directories, environment files, credentials, private keys, binaries, secrets, and provider/auth state are blocked.
- Provider-declared changes must exactly match the ForgeX before/after sandbox diff. Undeclared changes fail the run.
- The active workspace is snapshotted or hashed before provider execution and checked for drift before review creation. Later apply uses fresh preflight checks.
- Command suggestions are inert text. No shell string from a model is executed directly.
- Providers cannot apply or approve patches and cannot bypass review, integrity verification, rollback, or confirmation.
- Build is ForgeX-owned and begins only after successful approved apply in this target workflow.
- Flash is never part of generation. It requires a successful build artifact, detected compatible device, and explicit user action/confirmation.
- Providers cannot open or control the serial monitor. Monitor lifecycle and output bounds remain ForgeX-owned.
- Detection, authentication, enablement, routing, capability evidence, production eligibility, and mutation authority remain separate facts.
- Safe failures expose stable classifications and bounded diagnostics, not raw credentials, prompts, response bodies, or sensitive paths.

## 10. Integration with existing ForgeX

### Existing modules to retain

| Existing location | Role in the unified design |
|---|---|
| `backend/agent/planner.py` | Produces deterministic task intent and initial ordered stages |
| `backend/contracts/execution_plan.py` | Canonical immutable plan; future version must represent resumable review/apply gates |
| `backend/workflow/workflow_runner.py` | Top-level stage orchestration and progress boundary |
| `backend/agent/coordinator.py` | Existing ordered execution composition; do not overload with provider-specific policy |
| `backend/workflow/adapters/generation_adapter.py` | Existing generated-project-to-build boundary; future coding adapter complements it for review-backed generation |
| `backend/workflow/adapters/build_adapter.py` | ForgeX-owned build artifact conversion |
| `backend/workflow/adapters/flash_adapter.py` | ForgeX-owned flash configuration and board validation |
| `backend/workflow/adapters/monitor_adapter.py` | ForgeX-owned monitor configuration and context updates |
| `backend/agent_runtime/` | Coding-provider execution envelope, run state, permissions, bounded events, and sandbox orchestration |
| `backend/provider_runtime/` | Verified templates, provider-neutral generation contracts, validation, and safe summaries |
| `backend/model_router/` | API model/provider selection, credential references, health, and normalized model calls only |
| `backend/bridges/` | CLI adapters, managed sandboxes, review/diff, patch, apply, and rollback infrastructure |
| `backend/api/routes/` | Thin transport and error translation; never provider-policy source |

The current `UnifiedAgentService`, generic bridge contracts, permission policies, API coding-agent v1 contract, fake review service, and review/apply stores are useful evidence. They should be consolidated through a new contract rather than expanded with more provider-specific branches.

### Suggested future modules

| Future module | Responsibility |
|---|---|
| `backend/agent_runtime/coding_provider_contracts.py` | Provider type, descriptor, capabilities, run request/result, event envelope, and typed failures |
| `backend/agent_runtime/coding_provider_registry.py` | Evidence-backed registration, readiness, selection, and per-run capability materialization |
| `backend/agent_runtime/coding_workflow_service.py` | Generate/validate/review orchestration and resumable handoff to workflow gates |
| `backend/agent_runtime/coding_agent_permissions.py` | ForgeX-native resource/stage permission evaluation and non-overridable denials |
| `backend/workflow/adapters/coding_agent_adapter.py` | Translate workflow generation inputs into a coding run and review-backed stage result |

No stubs are required in this documentation phase. Before implementation, decide whether the canonical workflow adds new `ExecutionStep` values or introduces explicit gate records alongside steps. That decision must preserve serialized-plan compatibility or use a new schema version.

## 11. Implementation phases

These are small implementation milestones for the unified workflow; they do not rename prior API coding-agent phases.

1. **Phase 1 - unified workflow architecture doc.** Agree on authority, contracts, stages, events, and migration boundary. This document completes that design milestone.
2. **Phase 2 - coding provider contract and registry.** Add immutable provider descriptors, capabilities, run request/result, typed failures, and deterministic selection tests without changing live routing. Implemented as contract-only foundations in `backend/agent_runtime/coding_provider_contracts.py` and `backend/agent_runtime/coding_provider_registry.py`; live routing remains unchanged.
3. **Phase 3 - fake API coding agent as provider.** Adapt the existing deterministic fake/parser/review service to the shared provider contract; keep its dev flag and no-network guarantee. Implemented: the dev-only fake API coding agent now has a unified adapter and fake-only internal execution helper, while existing direct route behavior remains unchanged.
4. **Phase 4 - workflow adapter connects planner to fake provider and review.** Add a feature-gated generation adapter and resumable `review.created` / `apply.waiting_for_approval` outcome. Do not build automatically. Implemented: the experimental coding workflow adapter can run the dev-only fake API coding provider into a ForgeX review and pause at the apply gate. It remains disabled by default and does not build, flash, or monitor.
4.5. **Phase 4.5 - persist unified workflow runs and events.** Implemented: persistent, versioned run records and append-only event records now cover the experimental fake-provider review path. This phase does not resume apply, build, flash, or monitor.
5. **Phase 5 - explicit approved-apply resume gate.** Implemented: Phase 5 adds an explicit approved-apply resume gate for persisted unified coding-workflow runs. It applies an approved review through ForgeX's existing patch/preflight/rollback path and stops before build. A successful apply persists `awaiting_build` / `run_build`; it does not start build, flash, or monitor.
6. **Phase 6 - explicit build resume gate.** Implemented: Phase 6 adds an explicit build resume gate for unified coding-workflow runs that reached `awaiting_build` after approved apply. It uses ForgeX's existing build path and stops before flash. A successful build persists `awaiting_flash` / `confirm_flash`; it does not flash or monitor.
7. **Phase 7 - explicit flash resume gate.** Implemented: Phase 7 adds an explicit flash resume gate for unified coding-workflow runs that reached `awaiting_flash` after successful build. It uses ForgeX's existing flash path and stops before monitor. A successful flash persists `awaiting_monitor` / `open_monitor`; it does not open a serial monitor.
8. **Phase 8 - explicit bounded monitor resume gate.** Implemented: Phase 8 adds an explicit bounded monitor resume gate for unified coding-workflow runs that reached `awaiting_monitor` after successful flash. It uses ForgeX's existing monitor/serial path and completes the workflow after bounded monitor capture.
8.5. **Phase 8.5 - end-to-end service smoke coverage.** Implemented: Phase 8.5 adds end-to-end service-level smoke coverage for the full fake-provider unified workflow chain from review generation through approved apply, build, flash, bounded monitor, and completion. No routes or frontend behavior are added.
9. **Phase 9 - internal route wiring.** Implemented: Phase 9 exposes the experimental service-level unified fake-provider workflow through internal/dev backend routes. Routes are feature-gated, stage-gated, and do not auto-advance downstream stages.
10. **Phase 10 - experimental frontend controls.** Implemented: Phase 10 adds minimal experimental frontend controls for the internal/dev unified fake-provider workflow routes. The UI remains manual and stage-gated; no downstream stage auto-runs.
11. **Phase 11 - main IDE access.** Implemented: Phase 11 moves the experimental unified coding-agent workflow controls into the main IDE Forge panel as a Coding Agent mode while Settings remains focused on provider configuration/status.
12. **Phase 12 - bounded API context builder.** Implemented: Phase 12 adds a bounded workspace context builder for future real API coding-agent providers. It filters project files, excludes secrets/generated artifacts, enforces size limits, and does not persist raw source bodies into workflow state.
13. **Phase 13 - real API coding adapter.** Implemented: Phase 13 adds a disabled-by-default real API coding-provider adapter through ForgeX's existing model_router. It uses the bounded context builder, requires strict proposal JSON, creates review-only workflow runs, and does not auto-apply/build/flash/monitor.
14. **Phase 14 - frontend provider selection.** Implemented: Phase 14 adds frontend provider selection for the experimental Coding Agent panel. Generate Review can use either the fake/dev provider route or the real API provider route, while apply/build/flash/monitor remain provider-agnostic and stage-gated.
16. **Phase 16 - build-failure repair loop.** Implemented: Phase 16 adds a disabled-by-default build-failure repair loop. A build-failed workflow can generate a new repair review using bounded build error context and safe workspace context. Repair output is review-only and requires normal approval/apply/build gates.
17. **Phase 17 - context preview.** Implemented: Phase 17 adds a safe context preview route and frontend context preview UI. Preview uses the bounded context builder, does not call model providers, does not create workflow runs, and does not persist raw source bodies.
19. **Phase 19 - workflow locking, cancellation, and stale recovery.** Implemented: Phase 19 adds workflow operation locking, explicit cancellation, stale in-progress run detection, and manual recovery marking. It does not auto-recover, delete files, rollback, or auto-advance stages.
18. **Phase 18 - real API readiness.** Implemented: Phase 18 adds a real API coding-agent readiness route and frontend readiness card. The status check uses safe model_router/provider metadata, does not send prompts or context, and does not invoke generation.
6. **Phase 6 - flash after build success and user confirmation.** Reuse board detection and flash adapter; no provider access to hardware.
7. **Phase 7 - monitor after flash.** Reuse monitor adapter and serial service with bounded output and explicit lifecycle.
8. **Phase 8 - Codex and AGY provider adapters.** Conform existing experimental sandbox work to the shared contract without changing their auth ownership or safety flags.
9. **Phase 9 - Claude Code and OpenCode CLI adapters.** Add only after threat modeling, detection/auth contracts, Windows behavior, sandbox evidence, and fake adapter tests pass.
10. **Phase 10 - real API coding-agent provider.** Route through `model_router`, secure credentials, context disclosure/consent, budgets, strict v1 response parsing, and identical review validation.

Each phase must include contract tests, permission-denial tests, active-workspace integrity tests, event tests, and regression coverage for Codex, AGY, provider routing, and V1 build/flash/monitor behavior.

## 12. Recommendation

Implement the coding provider contract and registry first.

All providers must plug into one ForgeX contract before adding more local CLI agents. The contract should normalize identity, readiness, capabilities, context modes, run results, safe events, and failure classifications while preserving the non-overridable authority boundary. Starting with Claude Code or OpenCode CLI would create another provider-specific execution path before ForgeX has one stable place to enforce selection and permissions.

The first implementation should be contract-only and default-disabled. It should adapt the deterministic fake provider in tests, prove that selection is deterministic, and demonstrate that no registry entry can grant apply, build, flash, monitor, or active-workspace mutation authority.
