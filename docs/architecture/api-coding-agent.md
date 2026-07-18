# ForgeX API Coding Agent Architecture

Status: Architecture reference; Phase C dev-only fake review flow is implemented, live provider flow is not

Scope: Provider-backed code generation, workspace context, structured change proposals, sandbox validation, review creation, and future repair loops

Related boundary: [ForgeX Provider and Runtime Boundaries](provider-runtime-boundaries.md)

## Phase C fake-provider review flow

Phase C provides a deterministic, dev/test-only fake coding agent behind
`FORGEX_ENABLE_FAKE_API_CODING_AGENT=1`. It makes no real API calls. Fake JSON
output passes through the v1 contract parser, is applied only to a ForgeX-managed
sandbox, and becomes a normal ForgeX review/diff. The active workspace remains
unchanged until the existing explicit review, patch, preflight, and apply flow is
used. This path validates the contract-to-review pipeline before live providers
are connected and is not registered as a production model provider.

## Non-negotiable authority model

```text
API model proposes code changes.
ForgeX validates them.
ForgeX creates review/diff/patch.
User approves.
ForgeX applies.
ForgeX builds/tests/flashes.
```

The model is an untrusted proposal source. Provider availability, a valid response, or a successful sandbox run never grants active-workspace mutation authority. ForgeX remains the only component that can validate a proposal, create a review, apply an approved patch, run a build or test, flash hardware, or restore a rollback snapshot.

This design extends the current boundaries; it does not replace the V1 `CodeGenerationService`, Codex OAuth/CLI behavior, AGY behavior, verified templates, or the existing build/flash/monitor pipeline.

## 1. Agent purpose

The API coding agent is:

> A provider-backed coding agent that uses configured API models to plan, generate, repair, and review code changes inside a ForgeX-managed sandbox/review flow.

Its intended capabilities are:

- Generate new project files.
- Modify existing project files.
- Fix build errors through a bounded repair flow.
- Explain build, test, and source errors.
- Propose reviewable patches.
- Create tests.
- Update documentation.
- Perform limited, policy-bounded multi-step coding tasks.

It must not:

- Edit the active workspace directly.
- Run arbitrary shell commands by default.
- Build, test, flash, or monitor hardware on its own authority.
- Read or request secrets.
- Interpret a command suggestion as permission to execute it.
- Apply, approve, or bypass a ForgeX review.

## 2. High-level flow

```text
User request
    |
ForgeX classifies task
    |
Verified-template decision (provider_runtime)
    |-- simple, known request --> verified template --> artifact validation
    |
    `-- custom or ambiguous request --> API Coding Agent
                                      |
                              workspace context builder (read-only)
                                      |
                              model_router provider/model call
                                      |
                              strict structured response
                                      |
                              parse + schema validation
                                      |
                              path/content/safety validation
                                      |
                              write proposed files to managed sandbox only
                                      |
                              sandbox integrity check + ForgeX diff/review/patch
                                      |
                              explicit user approve/reject
                                      |
                              ForgeX preflight + rollback snapshot + apply
                                      |
                              optional ForgeX-owned build/test/flash/monitor
```

Template matching remains first because deterministic verified output is preferable for simple known requests. Complexity or ambiguity must reject template routing and may enter the API coding-agent path. A template rejection is not itself provider permission; normal provider availability, task routing, context consent, and policy checks still apply.

The active workspace is read-only during context collection and agent execution. ForgeX creates or refreshes a managed sandbox, applies validated model-proposed content there, verifies that only declared changes occurred, and creates a review from the resulting diff. Existing patch preflight, explicit confirmation, rollback, and apply services remain the only path back to the active workspace.

## 3. Provider support and selection

Initial API providers:

- `openai`
- `openrouter`
- `groq`
- `gemini`

Later providers:

- `anthropic`
- `cerebras`
- `ollama` and other local endpoints

The API coding agent must consume provider selection, configured models, health, fallback rules, and credential references from `backend/model_router/`. It must not create a second provider-settings store or duplicate provider routing in API routes or `agent_runtime`.

API keys remain in the secure credential store exposed by `backend/model_router/credentials.py`. Non-secret settings JSON may contain only stable credential references and masked/configured state. Prompts, response metadata, diagnostics, events, reviews, and logs must never contain plaintext keys. Existing Codex CLI authentication stays owned by the official CLI; the API coding agent neither reads Codex tokens nor changes Codex OAuth behavior.

Provider fallback is allowed only when the selected model route permits it and the fallback provider passes the same readiness, privacy disclosure, context, and output-validation checks. A fallback may change who receives project context, so the UI must disclose the actual provider before transmission or require consent that explicitly covers configured fallback providers. Local-only routes must never fall back to an external provider.

The existing `ApiPlannerProvider` and fake-provider work in `agent_runtime` demonstrates strict JSON and sandbox-only writes, but is currently a narrow, disabled-by-default product/planner path. The coding-agent implementation should reuse compatible contracts and policy primitives where possible while routing live model calls through `model_router` and its secure credentials. It must not preserve environment-variable-only credential handling as a parallel production architecture.

## 4. Capability levels

| Level | Capability | Authority and default |
|---|---|---|
| 0 | Explain only | No file proposal, sandbox write, or command execution |
| 1 | Generate files into review | Create bounded text files in a managed sandbox; user review required |
| 2 | Modify existing files in sandbox | Read selected context and propose bounded create/update operations; user review required |
| 3 | Build-error repair loop | ForgeX runs an allowed build/test in the sandbox and returns bounded diagnostics for one or two repair attempts |
| 4 | Multi-step autonomous project task | Multiple model turns and ForgeX-controlled tools; exceptional, separately gated, and never the default |

Implement Levels 1 and 2 first. Level 0 can share the provider/context layer but must use a response contract that cannot contain file operations. Level 3 follows only after the proposal contract, context filtering, sandbox diff, and fake-provider tests are stable. Level 4 requires a separate threat model, cost budget, cancellation semantics, and per-tool approval design; it must not be enabled by default.

Capability level controls what ForgeX may offer to the model and execute in the sandbox. It does not grant active-workspace, build, flash, or hardware authority.

## 5. Strict response and tool contract

The model returns one JSON object with no markdown wrapper or trailing prose. The initial version should accept complete UTF-8 text file contents rather than model-generated unified diffs; ForgeX can deterministically compare complete content against the sandbox and generate its own diff and patch.

```json
{
  "version": "forgex.api-coding-agent.v1",
  "summary": "Short explanation of the change",
  "files": [
    {
      "path": "src/main.cpp",
      "action": "create_or_update",
      "content": "#include <Arduino.h>\n"
    }
  ],
  "commands_suggested": [
    {
      "command": "pio run",
      "reason": "Build the PlatformIO project"
    }
  ],
  "risks": [
    "Requires WiFi credentials in config.h"
  ],
  "next_steps": [
    "Build the project",
    "Flash to the board"
  ]
}
```

Initial schema rules:

- `version` is required and must equal the supported contract version.
- All top-level fields are required; unknown fields are rejected.
- `summary`, `risks`, and `next_steps` are bounded plain strings.
- `files` is a bounded non-empty array for Levels 1 and 2. Paths must be unique after canonical normalization.
- The initial `action` enum contains only `create_or_update`. Deletion should be a later explicit action with stronger UI and policy treatment, not an empty-content convention.
- `content` must be complete UTF-8 text. Base64 and binary payloads are rejected until an explicit binary contract exists.
- `commands_suggested` is advisory data only. Each suggestion is displayed or evaluated by ForgeX; it is never executed by the parser, provider adapter, or API route.
- Missing, truncated, invalid, oversized, or schema-incompatible JSON fails closed and creates no review.
- ForgeX records safe provider/model/contract diagnostics, not raw secret-bearing request headers or unbounded response bodies.

The model has no direct tools. Conceptually, the response is a proposal for ForgeX's bounded `write_file` operation. ForgeX parses the whole response, validates the complete proposal, then writes only approved-by-policy content into the managed sandbox. It does not execute a partially valid response.

If providers support native JSON schema, ForgeX should request this schema. Providers without native schema support still receive strict JSON instructions and pass through the identical local parser and safety policy. Provider-side schema enforcement is an optimization, never the trust boundary.

## 6. Workspace context strategy

Supported context modes:

| Mode | Content sent |
|---|---|
| `none` | Task and non-sensitive project metadata only |
| `file_tree_only` | Filtered relative file tree and bounded metadata; no file bodies |
| `selected_files` | Filtered tree plus user- or ForgeX-selected text files |
| `bounded_relevant_files` | Filtered tree plus files selected by deterministic, project-aware relevance rules |
| `full_small_project` | All eligible text files only when the filtered project fits strict count, byte, and token limits |

The safe default is `file_tree_only` plus selected relevant files. In UI terms this is one explicit mode: show the filtered tree, the files selected for transmission, the provider that will receive them, and an opportunity to remove files. Automatic relevance selection must be deterministic and explainable; it must not use an unbounded preliminary provider call.

The context builder is read-only against the active workspace. It must canonicalize the workspace root, refuse symlinks/reparse-point escapes, apply ignore and sensitivity rules before reading content, and return an immutable manifest containing relative path, size, optional content, and a content hash. Before creating the review, ForgeX should detect active-workspace drift from the captured hashes and require refreshed context/rebase if relevant files changed.

Never send directories or files matching at least:

```text
.env
secrets and credential files
node_modules/
.git/
.pio/
build/
dist/
.promptforge/
```

Also exclude ForgeX state, provider/auth files, private-key formats, common token/config stores, generated dependencies, binary files, symlinks, and files rejected by the project-aware policy. Ignore rules are mandatory safety filters and cannot be disabled by the model. User inclusion cannot override a protected-path or secret rule.

Token budgeting rules:

1. Obtain the selected model's known context window from `model_router`; if unknown, use a conservative configured ceiling.
2. Reserve fixed space for system/safety instructions, response schema, requested output, and repair diagnostics. Do not allocate the full provider context window to source files.
3. Enforce both byte and estimated-token caps before transmission. A practical initial policy is to cap input at the smaller of a configured hard limit and 50% of the known context window, while reserving at least 25% for output and the remainder for instructions/overhead.
4. Include the filtered tree first, then explicitly selected files, then deterministic relevant files by score. Never truncate a file silently; omit it with a recorded reason or include an explicitly marked bounded excerpt when the contract supports excerpts.
5. Deduplicate content, cap per-file bytes, total files, total bytes, and total estimated tokens. Reject a request that cannot include required selected files safely.
6. Repair turns send only the original task, current relevant files, prior proposal summary, and bounded normalized diagnostics needed for the failure. Do not resend accumulated conversations without a budget check.

The UI must show this warning before an external request:

> External API providers may receive selected project context.

For local providers the UI should state that the route is local, but ForgeX must still apply the same context filtering because local endpoints are not filesystem-authority boundaries.

## 7. Safety policy

All model output is untrusted and must pass a single reusable safety policy before sandbox writes or review creation.

- Accept workspace-relative paths only.
- Reject absolute paths, drive prefixes, UNC paths, backslashes where canonical syntax requires `/`, NULs, alternate data streams, empty segments, `.`/`..`, Unicode-confusable paths, reserved Windows device names, trailing dots/spaces, and canonicalization changes.
- Resolve every target under the managed sandbox and verify containment. Reject symlinks, junctions, and reparse-point traversal.
- Block protected directories and sensitive filenames, including `.git`, `.pio`, `.promptforge`, ForgeX control files, dependency/build output, credentials, environment files, and provider/auth state.
- Enforce project-aware allowed text extensions. Unknown extensions fail closed or require an explicit future policy; extension checks do not replace content inspection.
- Reject binary data, NUL bytes, invalid UTF-8, unsupported encodings, and binary-file modifications unless a future binary contract explicitly supports them.
- Set configurable hard limits for file count, per-file input/output bytes, total generated bytes, response bytes, changed-file count, and total diff size. Initial production values must be conservative and covered by tests rather than inferred from provider limits.
- Reject duplicate or normalization-colliding paths and inconsistent actions.
- Scan selected outbound context and proposed content for likely secrets. Outbound matches are omitted or block the request; generated-content matches block review creation or require a specific non-secret placeholder policy. Never log matched secret values.
- Compare the sandbox before/after snapshots and reject undeclared changes, ignored-path changes, and active-workspace changes during the run.
- Generate the diff and patch with ForgeX services, not model text. Enforce preview and patch size limits before review.
- Require an explicit user review decision before patch export/apply. Apply must retain existing integrity verification, fresh preflight, exact confirmation, rollback snapshot, atomic writes, and automatic rollback behavior.
- Command suggestions remain inert. Any future command execution must use an independent allowlist, structured argv, sandbox cwd, environment scrubbing, timeout/output/resource limits, cancellation, and ForgeX authorization. Shell strings from the model must never be passed directly to a shell.
- Build, test, flash, monitor, and hardware access remain separate ForgeX-owned operations. Flash must never occur as part of generation or repair.

Failures are fail-closed and return stable safe classifications such as invalid JSON, schema invalid, path unsafe, content invalid, secret detected, limit exceeded, provider unavailable, or extra changes. A rejected proposal must not leave a reviewable partial sandbox mutation.

## 8. Future build/error repair loop

```text
Generate and validate proposal
    |
Apply proposal to managed sandbox
    |
ForgeX runs one policy-allowed build/test in sandbox
    |
Normalize and bound stdout/stderr diagnostics
    |
Send task + relevant files + bounded errors to API agent
    |
Validate and apply repair proposal to sandbox
    |
Repeat up to configured maximum (default 1; hard maximum 2 initially)
    |
ForgeX creates one cumulative review against the original snapshot
    |
User approves or rejects
```

The loop is ForgeX-controlled. The model cannot choose the executable, increase the attempt limit, expand context, change provider privacy scope, or run flash/monitor operations. Initial commands should come from project-aware ForgeX build/test definitions, not `commands_suggested`.

Each attempt needs a wall-clock timeout, provider request limit, input/output token budget, monetary estimate/ceiling where available, diagnostic byte limit, cancellation check, and no-progress detection. Stop on success, repeated equivalent patch/error, unsafe output, provider failure, budget exhaustion, cancellation, workspace drift, or the attempt limit. Default to one repair attempt; permit two only through explicit configuration or user action. Never retry indefinitely.

Review should show the cumulative diff and repair-attempt count. Intermediate proposals are diagnostic artifacts and do not authorize apply.

## 9. Integration with existing layers

Suggested implementation locations, adapted to current ownership:

| Location | Responsibility |
|---|---|
| `backend/agent_runtime/api_coding_agent.py` | Application orchestration: classify accepted capability, request context, call router, validate proposal, write sandbox, and request review |
| `backend/agent_runtime/api_agent_contracts.py` | Versioned request/response dataclasses or models, strict JSON parser, bounded safe errors |
| `backend/agent_runtime/context_builder.py` | Read-only filtered tree/file manifest, hashes, token/byte accounting, privacy manifest |
| `backend/agent_runtime/safety_policy.py` | Coding-agent path/content/limit/secret policy, preferably composed from current `tool_policy` and `provider_runtime.validation` primitives |
| `backend/model_router/` | Existing source for provider/model routes, health, fallback, normalized calls, usage, and secure credential access |
| `backend/provider_runtime/` | Existing source for verified templates, template decisions, artifact contracts, and validation; no live provider routing |
| `backend/bridges/sandbox_service.py` | Existing managed sandbox copy/lifecycle primitives |
| `backend/bridges/diff_service.py` and review/patch/apply services | ForgeX-owned snapshot, diff, review, patch, preflight, rollback, and apply authority |
| `backend/services/code_generation_service.py` | Existing V1 validated generation; keep operational and separate until an explicit migration design exists |
| `backend/api/routes/agent_runtime.py` or a focused sibling route module | Thin request/response transport for generate-review runs and status; compose through an application service |
| `frontend/components/ide/model-settings-panel.tsx` and agent/review panels | Provider availability presentation, user choices, privacy warning, progress, and review navigation |

`backend/api/routes/model_routes.py` should continue to expose model route configuration, testing, and diagnostics. A coding-generation endpoint belongs with the agent-runtime application surface (or a focused `api_coding_agent` route included by it), not in model route configuration merely because it invokes a model.

API routes must not contain prompts, provider fallback decisions, credential retrieval, context file traversal, path/content policy, sandbox writes, secret scanning, diff generation, apply logic, build commands, or repair-loop control. Routes validate HTTP shapes, resolve the authenticated/local project request, invoke the service, map safe errors, and return run/review status.

The coding-agent service should reuse the current run/event/cancellation envelope where compatible. It should not weaken current product policy to fit a model response. Existing narrow `ProductToolPlan` contracts may be evolved or kept separate with explicit versioning; do not silently reinterpret old payloads as the new file-proposal schema.

No part of this integration changes Codex CLI detection/login/status/smoke, reads OAuth tokens, changes AGY execution/import rules, removes templates, or takes ownership of V1 build/flash/monitor.

## 10. Minimal UI design

The initial UI needs only:

- Available/enabled provider selection based on backend state.
- Model selection based on the chosen provider and route.
- Context mode selection with a visible list/count of files to be transmitted.
- The external-provider privacy warning and actual fallback disclosure.
- A **Generate review** action, not a direct generate/apply action.
- Run progress and safe failure state.
- Existing review diff display with provider/model/context summary.
- Explicit approve/reject controls and existing patch preflight/apply confirmation.
- Optional ForgeX-owned build/test actions after apply; flash remains a separate explicit action.

Model settings should display configuration, health, and availability only. It must not imply that a configured provider has mutation permission. The existing review UI should remain the authority-facing surface. This phase does not redesign or implement the frontend.

## 11. Testing strategy

### Unit tests

- Valid structured response parsing and version enforcement.
- Invalid, fenced, truncated, extra-field, and oversized JSON handling.
- Relative path validation and Windows-specific path attacks.
- Protected path, symlink, junction, and reparse-point blocking.
- Context ignores, sensitive-file filtering, selection ordering, hashing, and token/byte accounting.
- Provider/model selection and policy-constrained fallback, including local-only behavior.
- File count, per-file size, total content, response, changed-file, and diff limits.
- UTF-8/text enforcement and binary rejection.
- Secret detection/redaction without secret values in logs.
- Duplicate and normalization-colliding paths.
- Advisory command inertness.
- Active-workspace drift and undeclared sandbox-change rejection.

### Integration tests

- Generate a simple new file and create a pending review without active-workspace mutation.
- Modify an existing file in a managed sandbox and create the correct diff.
- Reject traversal, absolute, protected, symlink, and reparse-point targets.
- Reject an oversized response, proposal, and patch.
- Handle unavailable primary provider and an allowed deterministic fallback; reject disallowed fallback.
- Confirm invalid provider output creates neither review nor active-workspace changes.
- Exercise one- and two-attempt build-error repair loops with a fake provider and fake build runner.
- Confirm review approval alone does not apply, and apply still requires existing preflight/confirmation/rollback services.
- Regression-test V1 generation/build/flash/monitor and Codex/AGY boundaries.

### Deterministic fake provider

Implement or adapt a fake API provider before live integration. It should implement the same model-router-facing response boundary, return fixtures for valid create/update, invalid JSON, unsafe path, secret, oversize, provider failure, fallback, and sequential repair scenarios, and expose deterministic call metadata. CI must require no API key, network, provider account, or model availability.

The fake must not bypass parsing or safety because it is trusted test code. It should exercise the same contract, context builder, sandbox, diff, and review path intended for live providers.

## 12. Future implementation phases

| Phase | Deliverable | Exit condition |
|---|---|---|
| A | Architecture document | Boundaries, contract, safety, context, tests, and rollout are reviewed |
| B | Structured response contract and parser | Strict versioned parser plus invalid/oversized/schema tests; no provider/network calls |
| C | Workspace context builder | Read-only filtering, manifest, secret rules, budgets, and drift tests |
| D | Fake provider and review generation | Deterministic Level 1/2 proposal flows through sandbox to pending review; active workspace unchanged |
| E | Real provider integration through `model_router` | One initial provider uses existing routes, health, usage, and secure credentials; fallback/privacy tests pass |
| F | Sandbox validation and patch-review hardening | Limits, undeclared-change detection, diff/patch/preflight compatibility, and rollback/apply regression tests pass |
| G | Build-error repair loop | Fake build/provider proves bounded one-attempt default and two-attempt hard maximum with cancellation/cost limits |
| H | Frontend controls | Provider/model/context choices, privacy disclosure, generate-review progress, and existing review/apply flow are wired |

Keep each phase independently reviewable and disabled by default until its tests and prior-phase invariants pass. Do not combine live provider spending, new apply authority, UI controls, and repair automation in one change.

Phase 12 adds a bounded workspace context builder for future real API coding-agent providers. It filters project files, excludes secrets/generated artifacts, enforces size limits, and does not persist raw source bodies into workflow state.

Phase 13 adds a disabled-by-default real API coding-provider adapter through ForgeX's existing model_router. It uses the bounded context builder, requires strict proposal JSON, creates review-only workflow runs, and does not auto-apply/build/flash/monitor.

Phase 16 adds a disabled-by-default build-failure repair loop. A build-failed workflow can generate a new repair review using bounded build error context and safe workspace context. Repair output is review-only and requires normal approval/apply/build gates.

Phase 17 adds a safe context preview route and frontend context preview UI. Preview uses the bounded context builder, does not call model providers, does not create workflow runs, and does not persist raw source bodies.

Phase 18 adds a real API coding-agent readiness route and frontend readiness card. The status check uses safe model_router/provider metadata, does not send prompts or context, and does not invoke generation.

### Dev-only manual real API smoke

For a local MVP smoke run, enable only the required backend flags:

```env
FORGEX_ENABLE_UNIFIED_CODING_WORKFLOW=1
FORGEX_ENABLE_REAL_API_CODING_AGENT=1
```

Then open ForgeX, open a workspace, open the Coding Agent panel, select **Real API Provider**, choose `selected_files` or `project_summary`, and run **Generate Review**. Inspect the generated diff before approving apply. Live use depends on a configured, healthy `model_router` provider and its normal API credentials; do not put API keys in prompts or docs.

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| API cost | Explicit provider/model display, conservative context/output budgets, usage accounting, per-run request/repair caps, cancellation, and optional cost ceiling |
| Context leakage | Default minimal context, mandatory ignore/secret filters, visible transmitted-file manifest, external-provider warning, local-only enforcement, no raw prompt logging |
| Hallucinated files or APIs | Validate every declared path/content, project-aware extension policy, bounded files, sandbox build/test later, and user diff review |
| Oversized responses or patches | Transport response cap, strict parser cap, per-file/total limits, changed-file and diff caps, fail closed before review |
| Bad path writes | Canonical relative-path validation, Windows attack checks, containment checks, protected paths, and symlink/reparse rejection |
| Provider downtime, quota, or rate limits | Stable safe errors, health-aware routing, policy-constrained fallback, retry limits with backoff, and no partial review |
| Invalid or weakly structured JSON | Provider JSON schema where available plus one strict local parser; no partial acceptance; fake-provider fixtures |
| Weak models produce broken code | Capability gating, deterministic templates first, bounded sandbox validation, optional build/test, visible risks, and mandatory user review |
| Users expect parity with Codex | Label this as a provider-backed bounded coding agent; publish capability levels and provider/model limitations; do not imply CLI-agent tool parity |
| Repeated repairs increase cost | One attempt by default, two maximum initially, no-progress detection, token/request/cost ceilings, and explicit user continuation beyond defaults |
| Secret-like generated content | Scan proposals, use documented placeholders, block review on likely real secrets, and never persist matched values in diagnostics |
| Active workspace changes during a run | Hash captured context, verify workspace integrity before review/apply, reject or require regeneration on drift |
| Duplicate safety logic diverges | Centralize contracts/policy in `agent_runtime`, reuse provider-runtime and bridge primitives, and test authority boundaries end to end |
| Provider fallback changes privacy destination | Disclose actual/fallback providers, require consent covering them, and prohibit external fallback for local-only routes |

Residual risk remains because model output can be semantically wrong even when structurally safe. Sandbox validation, builds, and tests reduce that risk but do not replace human review, especially for hardware behavior, credentials, destructive device operations, timing, power, or safety-critical code.

## 14. Final recommendation

After current stabilization, implement the structured API coding-agent response contract and deterministic fake provider first.

Do not begin with real OpenAI, OpenRouter, Groq, or Gemini calls. The contract and safety layer must be stable before spending API money or trusting live models. The first implementation increment should parse versioned fixtures, reject unsafe/invalid/oversized proposals, and prove that a valid fake proposal can create a ForgeX review from a managed sandbox without changing the active workspace. Secure live-provider integration should follow only after that invariant is tested.
