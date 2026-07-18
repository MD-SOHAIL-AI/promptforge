# ForgeX Provider and Runtime Boundaries

Status: Current architecture reference

Scope: Provider configuration, generation, sandbox execution, bridges, workflow orchestration, API exposure, and frontend presentation

This document defines ownership and authority boundaries. It describes the current architecture and the constraints future Codex, AGY, API-provider, and template work must preserve. A directory name does not grant authority: active-workspace mutation, patch application, build, and flash remain explicit ForgeX operations.

## Core safety model

> Providers generate or propose changes. ForgeX validates. ForgeX applies patches. ForgeX builds and flashes. ForgeX owns active-workspace authority.

- No provider may directly mutate the active workspace without ForgeX validation and an explicit ForgeX-owned apply path.
- No bridge may read or extract OAuth tokens.
- No plaintext API key may be written to model-router JSON settings.
- Detection, authentication, availability, enablement, routing permission, and mutation authority are separate states.
- Build, flash, and monitor authority stays in the existing ForgeX V1 pipeline.

## High-level flow

```text
Frontend
   |
API routes (transport and validation, not policy source)
   |
   +-- V1 task path: workflow -> CodeGenerationService -> validation -> build/flash/monitor
   |
   +-- Product path: agent_runtime -> managed sandbox -> diff/review -> explicit apply
   |                         |
   |                         +-- provider_runtime contracts/templates/validation
   |
   +-- Model calls: model_router -> configured model provider
   |
   +-- CLI integration: bridges -> detection/auth/smoke/provider adapter

Active workspace changes occur only through ForgeX-owned review/apply services.
```

## Layer responsibilities

### `backend/model_router/`

Owns:

- Non-secret provider configuration and model defaults.
- Task-to-provider/model route selection.
- Model-provider adapters and normalized model responses.
- Provider health records, model caches, and usage records.
- Stable credential references in settings JSON.
- Secure credential-store access through `credentials.py`.

Does not own:

- Active-workspace mutation, review approval, or patch application.
- Build, flash, serial monitor, or hardware authority.
- Codex OAuth tokens or direct token-file access.
- Product-agent permission merely because a model provider is available.

`storage.py` persists non-secret settings. API keys are delegated to `credentials.py`; legacy plaintext keys are migrated out of JSON. A model route answers which model provider may receive a model request. It does not authorize filesystem changes.

### `backend/provider_runtime/`

Owns provider-neutral generation contracts and deterministic local generation support:

- Provider, authentication, workspace, generation, and summary contracts.
- The provider catalog and normalized errors.
- Deterministic verified templates and template matching.
- Template match decisions, including confidence and complexity rejection reasons.
- Generated-artifact validation and workspace-mode detection.
- Sanitized run-summary persistence.

Does not own:

- Model-provider credentials or route persistence.
- Live Codex or AGY CLI login/session management.
- Unvalidated active-workspace mutation.
- Patch apply, build, flash, or serial authority.

`provider_runtime` is a contract and validation layer, not a second provider router.

### `backend/agent_runtime/`

Owns the ForgeX product-agent execution envelope:

- Strict provider/tool-plan contracts.
- Default-deny tool policy and bounded tool execution.
- Managed-sandbox lifecycle for product runs.
- Product run state, events, diagnostics, and sanitized summaries.
- Active-workspace integrity checks during sandbox execution.
- Diff/review creation after validated sandbox changes.
- Unification of API planners, verified templates, and enabled sandbox-agent providers.

Does not own:

- Raw credential persistence or OAuth token access.
- Uncontrolled writes to the active workspace.
- Automatic patch application.
- Direct build, flash, or monitor authority.

The product runtime may create a review. A review is not permission to apply it.

### `backend/bridges/`

The directory contains two related but distinct ForgeX-owned subdomains.

Provider/CLI integration owns:

- CLI discovery and version detection.
- Official-CLI login helper UX.
- Bounded, non-shell authentication/status probes.
- QA and smoke classifications.
- Provider adapters that operate only in managed sandboxes.
- The no-token-reading boundary.

Review/apply infrastructure owns:

- Sandbox snapshots and diffs.
- Review records and patch export.
- Preflight, rollback snapshots, explicit apply, and rollback restore.

The boundary is authority-based even though both subdomains currently share the `bridges` package: a provider adapter must not call patch apply or mutate the active workspace. ForgeX-owned API/application services invoke review and apply operations after validation and explicit confirmation.

Bridges do not own model-router route selection, credential extraction, or implicit product permission. Detection means that a tool exists; it grants no execution or mutation authority.

### `backend/workflow/` and services

`workflow/` composes the existing user-task lifecycle. `WorkflowRunner` connects planning, execution context, coordinator invocations, and progress reporting without owning the underlying step implementations. Its adapters translate generation, build, flash, and monitor results into workflow contracts.

`backend/services/code_generation_service.py` is a current V1 generation engine, not dead or historical code. It:

- Converts execution plans into validated in-memory projects.
- Selects one-shot, chunked, repair, fallback, or deterministic generation paths.
- Calls the configured `LLMService`/model-router boundary.
- Enforces generated-project size, shape, target, and prompt-specific validation.
- Emits generation progress and records bounded diagnostics.

The `/execute` path composes it through `backend.main.execute_prompt`, `WorkflowRunner`, and the generation handler. The product-agent path in `agent_runtime` is a separate, feature-gated managed-sandbox path. Future work must not silently replace one path with the other or bypass V1 build/flash/monitor ownership.

### `backend/api/routes/`

API routes own HTTP concerns:

- Request/response validation and error translation.
- Calling application services through configured app state.
- Exposing status, diagnostics, review, apply, workflow, and model operations.

Routes must not become the source of truth for provider permissions. They should report or enforce decisions produced by the owning service, registry, policy, or feature flag. A UI-visible boolean must be derived from backend authority, not invented in a route response.

The `/models` surface is composed by `backend/api/routes/models.py` from focused modules: `model_providers.py`, `model_routes.py`, `bridge_safety.py`, `provider_reviews.py`, and `provider_patches.py`. Shared request-state accessors live in the private `_model_common.py`; business policy remains in the owning backend services.

### Frontend provider UI

`frontend/components/ide/model-settings-panel.tsx`, related API clients, and types own presentation and explicit user actions. They may:

- Display provider configuration, health, authentication, and safety status.
- Trigger supported backend actions.
- Require confirmations and explain disabled states.

They must not infer that installation or authentication grants routing, execution, apply, build, or flash permission. Backend state is authoritative. Frontend controls are an additional safety layer, not the security boundary.

## Source of truth

| Concern | Source of truth | Notes |
|---|---|---|
| Provider definitions and task routes | `backend/model_router/registry.py` | Model-request routing only |
| Non-secret provider settings | `backend/model_router/storage.py` | Atomic JSON; credential references only |
| API keys | `backend/model_router/credentials.py` | OS-backed secure storage; no plaintext settings JSON |
| Model execution | `backend/model_router/router.py` and `providers/` | No workspace authority |
| Verified templates and decisions | `backend/provider_runtime/templates.py` | Simple known requests only |
| Artifact validation and run contracts | `backend/provider_runtime/` | Provider-neutral validation and summaries |
| Product sandbox execution | `backend/agent_runtime/` | Default-deny tools and managed sandboxes |
| Codex CLI auth status | `backend/bridges/codex_status.py` | Official CLI status; no token reading |
| Codex login helper | `backend/bridges/codex_login.py` | Launches official CLI only |
| AGY/Codex CLI provider adapters | `backend/bridges/providers/` | Detection and managed-sandbox execution only |
| Review, patch apply, and rollback | `backend/bridges/*review*`, `*patch*`, and `*rollback*` services exposed by API routes | ForgeX-owned authority; explicit confirmation required |
| V1 generation | `backend/services/code_generation_service.py` | Current validated generation path |
| Task lifecycle | `backend/main.py` and `backend/workflow/` | Planning, progress, generation, build, flash, monitor |
| Build, flash, and monitor | Existing V1 tools/services/workflow adapters | Must remain ForgeX-owned |
| HTTP exposure | `backend/api/routes/` | Transport layer, not policy source |
| Provider display and actions | `frontend/components/ide/model-settings-panel.tsx` and `frontend/types/` | Display and triggers only |

## Codex state semantics

Codex state is deliberately split:

| Field | Meaning |
|---|---|
| `codex_cli_installed` | The official Codex CLI executable was detected |
| `codex_auth_status` | Result of the official bounded CLI status command, such as `signed_in`, `signed_out`, or `unknown` |
| `codex_model_router_available` | The CLI/session is technically ready for the model-router adapter |
| `codex_model_router_enabled` | Codex model-router use is explicitly enabled by configuration |
| `codex_sandbox_execution_enabled` | Managed-sandbox Codex execution is enabled in current application composition |
| `codex_sandbox_execution_status` | Human-readable sandbox state such as `enabled` or `disabled` |
| `codex_product_route_allowed` | Backend product-policy permission; conservative and independent of installation/authentication |
| `codex_active_workspace_mutation_allowed` | Whether Codex may directly mutate the active workspace; this remains false |
| `codex_oauth_bridge_status` | OAuth bridge maturity/scope, currently `qa_only` |
| `codex_status_reason` | Explanation combining the independent states without granting authority |

Compatibility fields such as `codex_provider_state`, `codex_routing_allowed`, and `codex_execution_enabled` are derived aliases. New code should use the explicit fields.

The key invariant is:

> Codex model-router availability does not mean Codex can mutate the active workspace.

For authority decisions, `FORGEX_ENABLE_CODEX_PROVIDER=1` means Codex is explicitly enabled as a configured model-router provider; it does not grant product-route permission or active-workspace mutation. Current application composition also reflects this flag in the separately reported managed-sandbox execution state. That sandbox capability remains contained, review-gated, non-production-eligible, and distinct from active-workspace authority.

Authentication remains owned by the official CLI. ForgeX runs bounded status commands and does not read token or authentication files.

## Verified-template routing policy

- Verified templates serve simple, known, deterministic requests such as basic blink, minimal PlatformIO, WiFi scan, OLED test, and sensor-read examples.
- Keyword matches are necessary but not sufficient.
- Complexity terms such as dashboards, web servers, OTA, authentication, cloud/MQTT, filesystems, charts, RTOS, tasks, schedulers, and interrupts reject simple-template routing.
- Complex or ambiguous prompts continue to custom generation/provider flow.
- When unsure, do not use a simple verified template.
- Rejection decisions should retain the candidate and matched complexity terms for diagnostics without changing routing authority.

## Change rules for future provider work

Before adding or promoting a provider, identify separately:

1. Detection source.
2. Authentication source.
3. Model-router availability and enablement.
4. Managed-sandbox execution permission.
5. Product-route permission.
6. Review eligibility.
7. Active-workspace apply authority.
8. Build/flash/monitor authority.

No earlier item implies a later one. Any promotion must update backend policy and tests explicitly; UI availability alone is never sufficient.
