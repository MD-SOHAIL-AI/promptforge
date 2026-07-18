# ForgeX Agent Control Plane — Implementation Prompts

Use these prompts in order, one at a time. Do not combine phases.

## Rules for every phase

- Inspect the current worktree and preserve unrelated changes.
- Read the current implementation and architecture documents before editing.
- Keep existing APIs compatible until a prompt explicitly migrates them.
- Providers may generate proposals only. ForgeX owns validation, review, apply, build, flash, and monitor.
- Never weaken workspace, credential, sandbox, approval, or hardware safety.
- Add focused tests, run them, and report changed files, results, and remaining risks.
- Stop if a phase requires an unapproved architectural or safety expansion.

## Target model

```text
Connections
  -> Model Endpoints and Agent Adapters
  -> Versioned Agent Profiles
  -> Durable Workflow Engine
  -> Sandbox -> Review -> Apply -> Build -> Flash -> Monitor -> Verify
  -> One event stream and run history
```

Canonical concepts:

- **Connection:** authentication and transport for one provider account or local runtime.
- **Model endpoint:** a model accessible through a connection.
- **Agent adapter:** a bounded agent runtime such as API Coding Agent, Codex CLI, or AGY CLI.
- **Agent profile:** versioned instructions, capabilities, context, routing, budgets, and workflow policy.
- **Workflow run:** the ForgeX-owned durable state machine.

---

## Prompt 0 — Baseline map

```text
Audit the current ForgeX provider, agent, router, auth, workflow, review, and hardware paths. Do not change runtime behavior.

Inventory model-router providers, ProductProviderRegistry, CodingProviderRegistry, feature flags, Codex and AGY auth/status paths, agent run stores, workflow states, APIs, frontend selectors, and event transports. Trace V1 generation, product-agent generation, unified coding workflow, review/apply, build, flash, and monitor.

Create an ADR and migration matrix marking every component as keep, wrap, migrate, or retire. Include compatibility requirements and protective test commands. Clearly separate Connection, Model Endpoint, Agent Adapter, Agent Profile, and Workflow Run.

Acceptance: all current entry points are accounted for, no production behavior changes, and ForgeX retains all mutation and hardware authority.
```


## Prompt 1 — Canonical contracts

```text
Implement immutable versioned domain contracts without changing live routing:

- ProviderConnection and ConnectionStatus
- ConnectionAuthType: api_key, oauth_pkce, oauth_device_code, cli_owned_session, local_no_auth, enterprise_proxy
- ModelEndpoint
- AgentAdapterDescriptor and AgentCapability
- AgentProfile and AgentProfileVersion
- RoutingPolicy and RoutingDecision
- WorkflowRun, WorkflowStep, WorkflowEvent, and ApprovalRequest

Represent detection, authentication, health, enablement, policy eligibility, and production eligibility independently. Support multiple named connections per provider. Reject forbidden provider authority such as active-workspace mutation, apply, build, flash, monitor, approval, or secret access. Add safe serialization, typed failures, schema versions, and compatibility adapters for existing registries.

Test validation, redaction, invalid state combinations, forbidden capabilities, and compatibility mappings. Codex CLI and OpenRouter must not share the same provider type.
```

## Prompt 2 — Unified Connection Registry

```text
Implement one Connection Registry as the authoritative read model for authentication and transport status. Initially wrap the existing model ProviderRegistry, CodexStatusService/login helper, and AGY/Antigravity detector.

Expose list, get, discover, connect, refresh_status, disconnect, capabilities, models, and safe_diagnostics. Store API secrets only in the OS credential store. Never read Codex or AGY auth files. Report auth_unknown when official status cannot be safely verified. Authentication must not imply agent execution permission.

Keep existing provider APIs working through compatibility facades. Test multiple accounts, API-key lifecycle, CLI signed-in/signed-out/unknown states, redaction, and old API compatibility.
```

## Prompt 3 — SQLite control-plane storage

```text
Add migration-managed SQLite persistence in WAL mode for connections, status, model endpoints, routing policies/decisions, agent profiles/versions, workflow runs/steps/events, approvals, artifacts, operation leases, and usage.

Require transactions, foreign keys, optimistic run versions, monotonic per-run event sequences, idempotency keys, and append-only events. Do not store raw credentials, auth output, unrestricted prompts/responses, or unbounded logs. Keep JSONL and current stores behind compatibility repositories during migration.

Test migrations, rollback, restart persistence, concurrent updates, event ordering, idempotency, and safe failure on corruption.
```

## Prompt 4 — Workflow state machine

```text
Implement one durable workflow state machine without replacing public APIs yet.

States: draft, preparing_context, awaiting_context_consent, routing, generating, validating, awaiting_review, awaiting_apply_approval, applying, awaiting_build, building, repairing, awaiting_flash_approval, flashing, awaiting_monitor, monitoring, verifying, completed, failed, cancelled, timed_out.

Every transition requires run ID, expected state/version, idempotency key, actor, policy decision, and input/output artifact hashes. Invalid transitions fail closed. Emit one terminal event. Use renewable operation leases and safe stale-run recovery. Apply, build, flash, and monitor remain separate ForgeX executors; providers cannot invoke them directly.

Test the transition matrix, duplicate commands, concurrent actions, restart/resume, cancellation, timeout, stale leases, and approval invalidation.
```

## Prompt 5 — Events and synchronization


```text
Make durable workflow events the authoritative UI synchronization source.

Commit events transactionally with state changes. Add WebSocket live delivery, replay by after_sequence, reconnect recovery, bounded sanitized payloads, and backend-owned conversation messages. Use an outbox/fan-out design so committed events are not lost.

Migrate frontend hooks to load the backend projection and replay from the last sequence. Remove localStorage as conversation authority while retaining harmless UI preferences.

Test reconnect, duplicate/reordered delivery, slow clients, backend restart, and event redaction.
```

## Prompt 6 — Agent Adapter contract

```text
Implement one AgentAdapter contract with descriptor, readiness, prepare_run, execute_turn, cancel, collect_result, and safe_diagnostics.

Adapt verified templates, structured API coding, and the deterministic fake provider first. Add disabled Codex CLI and AGY descriptors until their containment evidence passes.

Adapters receive only approved bounded context and return untrusted proposals. Local CLIs operate only inside ForgeX-managed sandboxes. ForgeX derives the authoritative diff and review. Adapters cannot apply, build, flash, monitor, approve, mutate the active workspace, or execute model-suggested shell strings.

Test sandbox containment, context limits, cancellation, timeout, output limits, undeclared changes, secret/path rejection, and active-workspace integrity.
```

## Prompt 7 — Production model router

```text
Redesign the model router to route model calls only. Migrate Codex CLI out of the normal model-provider abstraction; it remains an Agent Adapter using a cli_owned_session Connection.

Route by capabilities, context size, privacy, approved recipients, local/remote policy, health, cost, latency, quality, and exact-model preference. Persist an explainable RoutingDecision with eligible/rejected candidates, selection, fallback chain, consent, and usage.

Support fallback policies: none, same_provider, approved_provider_group, ask_before_cross_provider. Never fallback on auth errors, policy denial, invalid credentials, or unapproved external disclosure.

Test capability filtering, privacy/local-only enforcement, cost constraints, consent, auth errors, and deterministic decisions.
```

## Prompt 8 — Agent Profiles and Agent Studio backend

```text
Implement versioned Agent Profiles containing identity, role, system instructions, capabilities, context policy, model-routing policy, time/step/token/cost budgets, approval policy, workflow template, and verification criteria.

Drafts are editable; publishing creates an immutable version. Every run references one exact version. Profiles cannot override base safety denials. Add built-in Firmware Engineer and Build Repair profiles plus validated secret-free import/export.

Test publishing, immutability, forbidden capabilities, import/export, and run reproducibility.

```

## Prompt 9 — Unified coding workflow

```text
Connect Agent Adapters to the workflow engine and migrate the existing coding workflow behind compatibility APIs.

Flow: request -> bounded context -> disclosure consent -> routing -> sandbox/proposal -> validation -> review -> apply approval -> preflight/rollback -> apply -> build -> bounded repair loop -> flash approval -> flash -> monitor -> verification -> final report.

Reuse existing ForgeX review, patch, rollback, build, flash, and monitor services. Repair creates a new review and repeats normal approval gates. Flash approval must bind artifact, device, port, board, and command hashes. Existing coding-workflow endpoints become compatibility commands over the new engine.

Test the full fake flow, repair, restart at every waiting state, approval expiry, wrong-device denial, and hardware safety.
```

## Prompt 10 — Codex CLI adapter

```text
Integrate Codex CLI through AgentAdapter while preserving official CLI-owned authentication.

Use the existing safe executable resolver, official login launch, and bounded status command. Never read token files. Run only in ForgeX disposable sandboxes with scrubbed environment, cancellation, process-tree cleanup, time/output/file limits, and containment verification. Reject reparse escapes, protected files, secrets, binaries, and undeclared changes. Produce a review only.

Register Codex as an Agent Adapter, not a Model Endpoint. Keep production eligibility disabled until all safety and parity tests pass.

Test installation/auth states, login safety, containment, cancellation, orphan cleanup, review generation, and malicious paths/content.
```

## Prompt 11 — Antigravity/AGY adapter


```text
Migrate AGY/Antigravity into AgentAdapter using CLI-owned authentication. Keep detection, auth, compatibility, enablement, health, and production eligibility separate.

Consolidate scratch generation, explicit scratch import, and sandbox execution as adapter execution modes. Never discover arbitrary newest folders. Bind execution to an explicit or precomputed managed path. Reuse ForgeX validation/diff/review and never mutate, build, flash, or monitor directly.

Test detection/auth mapping, path binding, import containment, limits, cancellation, cleanup, review-only results, and existing AGY regressions.
```

## Prompt 12 — New control-plane UI

```text
Build focused UI surfaces without removing old panels yet:

1. Connections: login, keys, local endpoints, health, disconnect.
2. Models & Routes: discovery, routing, fallback consent, usage.
3. Agent Studio: draft, validate, publish, clone, and inspect profiles.
4. Policies: privacy, context, workspace, and hardware approvals.
5. Run History: events, reviews, artifacts, costs, and recovery.

Do not keep provider setup, bridge QA, patches, rollback, routing, and agent execution in one settings component. Clearly distinguish Connected, Authenticated, Enabled, Healthy, Policy Eligible, and Production Eligible. Backend state is authoritative and every command uses an idempotency key.

Add UI tests for unavailable, reconnecting, approval-waiting, failed, and completed states.
```

## Prompt 13 — Unified Agent workspace

```text
Replace the separate Product Agent and experimental Coding Workflow experiences with one Agent workspace backed by the workflow engine.

Show conversation, selected Agent Profile, plan, current stage, event timeline, context disclosure, routing decision, review, approvals, build/flash/monitor status, and final verification report. Users select an Agent Profile by default; advanced users may override routing per run.

Render allowed actions from backend state. Reconnect/replay must restore the run exactly. Do not hardcode provider, model, COM port, or board when backend project/device state exists. Keep old panels behind a temporary compatibility flag until parity passes.
```

## Prompt 14 — Production hardening

```text
Add structured correlated logs, safe metrics, explicit diagnostic export, budgets, rate limits, provider circuit breakers, database backup/recovery, retention policies, startup reconciliation, and threat-model documentation.

Diagnostics must exclude credentials, raw auth output, secret environment values, and unrestricted source. Telemetry failures must not replace workflow results.

Test process termination, database contention, provider timeout, WebSocket loss, cancellation, and device disconnection.
```

## Prompt 15 — Migration completion

```text
Remove compatibility paths only after caller inventory, data migration, full regression coverage, rollback planning, and proven UI/runtime parity.

Then remove duplicate registries, Codex from model routing, JSONL workflow authority, browser-local conversation authority, legacy agent panels, and redundant feature flags. Update architecture, security, operations, and user documentation.

Release gates:
- Providers cannot directly mutate the active workspace.
- Hardware mutation always requires valid ForgeX approval.
- Every run is durable, resumable, auditable, and profile-versioned.
- Every model call has an explainable routing decision.
- Secrets remain provider-owned or OS-credential-store-owned.
- Supported providers share the Connection and AgentAdapter contracts.
```

## Final audit prompt

```text
Audit the completed ForgeX Agent Control Plane without implementing new features. Verify domain boundaries, auth and secrets, adapter/router separation, fallback consent, state-machine integrity, concurrency, recovery, sandbox containment, review/apply/rollback, hardware authority, event replay, profile reproducibility, observability, redaction, and legacy removal.

Produce a pass/fail evidence matrix, P0/P1/P2 risks, release recommendation, and rollback checklist. Do not mark production-ready while any P0 safety, persistence, authentication, or hardware-authority issue remains.
```

