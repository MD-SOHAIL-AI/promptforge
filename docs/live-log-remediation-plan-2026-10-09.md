# Live-log remediation plan — 9 October 2026

**Status: implementation authorized by the user's “implement” instruction. Application changes are implemented; validation and remaining live limitations are recorded in the [implementation report](live-log-remediation-implementation-2026-10-10.md).**

This plan covers the recorded ForgeX desktop sessions and the code paths behind their failures. The initial planning investigation read logs, source, and SQLite through read-only connections and produced this document. Implementation started only after the user said “implement”. The issue register and work packages below preserve the original plan; the linked implementation report describes the resulting changes, executed checks, isolated live qualifications, and unresolved outcomes.

## 1. Evidence and scope

Primary evidence, with line numbers referring to the inspected JSONL files:

| Reference | Source | Recorded interval (IST) | Records |
| --- | --- | --- | ---: |
| B1 | [Latest backend](../.forgex/logs/live-runtime.backend-422728e6f0ab424aac05fb36ea3479ef.jsonl) | 20:00:14–20:27:58 | 2,712 |
| D1 | [Matching desktop](../.forgex/logs/live-runtime.desktop-4d144cd3-7023-413b-9451-d0522a326e13.jsonl) | 20:00:12–20:27:59 | 1,077 |
| B2 | [Preceding backend](../.forgex/logs/live-runtime.backend-bb50e7aa4a854e0da25475fae04f0e56.jsonl) | 19:51:53–19:58:31 | 859 |
| D2 | [Preceding desktop](../.forgex/logs/live-runtime.desktop-9998fba9-2f14-4f45-bd9e-4bc1a9a26000.jsonl) | 19:51:50–19:58:33 | 405 |

Both backend sessions have source fingerprint `605559c9de2e2e9ed18003d466013c52035933c42478ed0aa74a6ae07d288233`. All four files parsed without malformed JSONL records. They are the latest retained sessions, not a claim that the application is currently running.

Persisted runs, jobs, events, nodes, requirement reports and ChangeSets were checked in `.promptforge/state/agent-orchestration.sqlite3` and `.promptforge/state/changesets/`. Earlier investigations provide regression context: [8 October review](live-log-review-2026-10-08.md), [9 October morning review](live-log-review-2026-10-09.md), and [verification pipeline repairs](verification-pipeline-fix-2026-10-09.md). Their historical test results are not validation of this proposed work.

### Actual run outcomes

| Run | Outcome supported by logs and saved state |
| --- | --- |
| `agent-run-2b5659c956a340feb34d80e4a600854e` | Temperature monitor: dependency errors were repaired, build and requirement review passed. A separate risk reviewer returned no usable content. The draft was subsequently applied successfully at 19:58:15; another apply request at 19:58:20 returned 409. Current classification is `CHANGESET_APPLIED`. |
| `agent-run-3a01e52381e34dbd899d1a178af87581` | Advanced environment-monitor request under the standard execution profile: unresolved AsyncTCP package, then a cancelled build. Terminal classification `AGENT_RUNTIME_LIMIT_EXCEEDED`; an unapplied recovery draft remains. Its job is paused for budget despite approximately 557.5 seconds remaining in its 900-second allocation. |
| `agent-run-b31a6499ae464535bb74713f684db9aa` | Same advanced request under the advanced profile: missing dependency/header errors and incompatible module interfaces. Terminal classification `BUILD_REPAIR_EXHAUSTED` at 20:23:00. Approximately 1,808.4 seconds remained in its 2,700-second allocation. Its draft remains unapplied. |
| `agent-run-c01616325873401c8549d5799223fe4f` | Advanced request with `nvidia/nemotron-3-ultra-550b-a55b`: turn 5 failed on an invalid stream event after HTTP 200, with no canonical content events. Terminal classification `PROVIDER_REQUEST_FAILED`; its draft remains unapplied. |
| Task interpretation at 20:27:58 | `AGENT_BUDGET_PERSISTENCE_FAILED` before a successful physical provider attempt was recorded. This is an intake/accounting path, separate from the preceding generation failure. |

The empty temperature-monitor response belongs to **risk review**, not requirement review or the complex-task completion gate. Its saved report has `completion_review_required: false`. An application-level `completed` status can mean work is staged for approval; it must be interpreted with classification and ChangeSet status.

## 2. Issue register and priorities

P0 means a request cannot reliably start or failures are classified incorrectly in ways that block recovery. P1 means generation, verification, or user actions repeatedly fail. P2 means secondary cleanup and regression qualification.

| ID | Priority | Observed problem | Evidence and confidence |
| --- | --- | --- | --- |
| F01 | P0 | Intake cannot reserve its request budget | B1:2708. `charge_attempt` adds `_attempt_id`; `AgentIntakeStore.reserve` indexes every supplied key in `consumed`, which has only numeric budget dimensions. This is a deterministic source mismatch consistent with the wrapper error. The original exception is not retained in that event. |
| F02 | P0 | Build-phase timeout becomes job-budget exhaustion | B1:567 and saved job/run. Consumption is about 341–342 seconds against a 900-second job limit. `PhaseBudget.build_deadline` imposes a separate build window; `_OperationDeadlineExpired` is converted to `AGENT_RUNTIME_LIMIT_EXCEEDED`, and reporting defaults its origin to `job_deadline`. The phase timeout and whole-job deadline are conflated. |
| F03 | P1 | Invalid NVIDIA stream loses its actionable category | B1:2669–2676. Ultra model, HTTP 200, zero canonical stream events, worker exit 1. `normalizeError` maps many failures to `PROVIDER_REQUEST_FAILED`; the planner's same-model stream recovery expects `PROVIDER_STREAM_INVALID`. The exact rejected upstream packet is unavailable. |
| F04 | P1 | Library identity passes preflight while a version cannot resolve | B1:222; B2:186–200. AsyncTCP `1.2.3`, missing `DHT.h`, and rejected DHT `1.4.3` specification. Dependency validation strips the version and explicitly reports `versions_verified: false`. Registry availability or correct replacement versions must be verified during implementation. |
| F05 | P1 | Multi-file generation compiles incomplete or inconsistent modules | B1:1023, 1122, 1424, 1545, 1651, 1863, 1974, 2063, 2281. Missing local headers, undeclared classes, constructor mismatch, absent `getInstance()` and sensor accessors. The current loop can force a host build after three write turns. The exact generation architecture must be recovered from the saved draft and full request. |
| F06 | P1 | Repair loop loses progress through failed edits and rigid rebuild timing | Four `agent.tool.failed` events in B1 and one in B2 report `edit_match_must_be_exactly_one`. B1:2510 stops after six turns without a fresh build. The guard is real; globally increasing retries would not correct interface inconsistency. |
| F07 | P1 | Citation repairs repeatedly exceed the inclusive 80-line limit | B2:480, 526, 577, 669. Field-specific feedback already exists, but the model repeatedly submits overlong references. The run eventually passes. Improve submission/recovery without accepting invalid evidence. |
| F08 | P1 | Apply action remains available after successful application | B2:850–852 records success; D2:393 and B2:857 record the later 409. Code canvas gates on changed-file count, and the hook checks only ChangeSet presence/in-flight state. The backend correctly rejects an already-applied run. Exact user interaction triggering the second request is not recorded. |
| F09 | P1 | Risk-review failure is reduced to a generic completed node | B2:837–841. The reviewer uses a 512-token output budget and catches all exceptions into a generic block result. Whether that limit caused this response cannot be proven from retained diagnostics. The explicit-review fallback worked and must remain truthful. |
| F10 | P2 | Development CSP warning and older log issues need qualification | D1:3 and D2:3 are WARNING-level development CSP messages. D1 has zero ERROR records and one DEBUG cancellation. Earlier `/v1/models` 401s were attributed to a separate OpenCode CLI in the morning review; neither latest backend session has HTTP 401. |

B1 contains 12 `build.failed` records, three failed agent runs and four failed agent-tool records. `agent.tool.failed` and `tool.failed` can describe the same invocation, so raw error-line counts must not become incident counts. Dependency-preflight failures can also appear as agent events without a compiler-process failure record.

## 3. Planned work packages

### WP1 — Restore durable intake accounting (F01)

**Proposed changes:**

1. Define the allowed numeric charge dimensions shared by job and intake reservation. Separate `_attempt_id` and other metadata from that map; validate charges before updating durable state.
2. Make intake attempt reservations atomic and idempotent by attempt identity. Preserve budget transfer when intake attaches to a job and account for completed versus unresolved output consistently.
3. Preserve a bounded internal cause code for reservation/storage failures. Distinguish invalid charge shape, lifecycle rejection and actual database errors in the public failure contract.

**Primary files:** `backend/model_router/run_budget.py`, `backend/agent_runtime/intake.py`, `backend/agent_runtime/router_planner.py`, `backend/api/routes/agent_runtime.py`.

**Pass criteria:** Unknown-intent intake reaches interpretation with a real attempt identity; the same reservation charges once; invalid dimensions cause no partial write; failed persistence dispatches no provider request; attaching an intake transfers its charges exactly once. Use an isolated database. Existing intake failure handling must retain the user's request.

### WP2 — Separate phase deadlines and job exhaustion (F02)

**Proposed changes:**

1. Carry operation/phase, effective deadline, elapsed time, allocation and remaining job budget through timeout exceptions and persisted failures.
2. Give dependency resolution and compilation explicit phase allocations. Assess cold dependency installation and measured build durations before selecting the build window; avoid spending the entire compilation allowance on preflight.
3. Classify build timeout separately from job deadline, verification reserve and provider timeout. Pause for job-budget exhaustion only when that budget or its explicitly reported reserve is actually reached.
4. Offer continuation from the saved draft for a phase interruption while preserving cumulative accounting, finite limits and review reserves.

**Primary files:** `backend/agent_runtime/engine.py`, `autonomous_loop.py`, `jobs.py`, `failure_contract.py`, `run_reporting.py`; `backend/runtime/subprocess_mgr.py` if cancellation cleanup needs adjustment.

**Pass criteria:** A simulated 120-second build-phase expiry inside a 900-second job does not report 900-second exhaustion. Genuine parent deadline stops still pause correctly. Dependency time, cold/warm compilation, review reservation and resumed elapsed time agree across backend, persisted job and UI. Cancelled processes settle before recovery proceeds.

### WP3 — Preserve provider stream failures and bounded recovery (F03)

**Proposed changes:**

1. Map invalid provider-output/stream shape errors to a specific canonical category across the Node worker and Python adapter instead of losing them as a generic request failure.
2. Record safe failure-shape metadata: protocol, validation location/category, completion flag, worker outcome and HTTP status. Retain no raw credentials or project-bearing upstream packets.
3. Route eligible stream failures through the existing bounded same-model recovery path, subject to durable budgets and deadlines. Use a non-streaming request only where the provider adapter actually supports that transport.
4. Keep incomplete tool calls non-executable. Authentication, policy and configuration failures retain their own handling.

**Primary files:** `model-router/src/engine.mjs`, `worker.mjs`; `backend/model_router/providers/opencode.py`, `normalization.py`, `request_recovery.py`; `backend/agent_runtime/router_planner.py`, `failure_contract.py`, `run_reporting.py`.

**Pass criteria:** Fixtures cover HTTP 200 with a malformed event, missing completion, empty/truncated output, fragmented tool calls and provider error packets. Classification survives all layers. Eligible recovery consumes a separately recorded attempt; exhaustion preserves the original cause and draft. No partial tool call executes.

### WP4 — Validate dependencies and complete module milestones (F04–F05)

**Proposed changes:**

1. Resolve exact registry package identity and requested version against the selected environment before full compilation. Use registry metadata and installed headers to check APIs; derive replacements from evidence rather than guessing versions or changing requested hardware.
2. Classify include failures as framework/library dependencies or project-local files. Report all currently missing local files together.
3. Build a deliverable/module manifest from the full advanced request. Establish shared types, constructors, method names and ownership before dependent implementations. Preserve required sensor, network, storage, display, FreeRTOS and dashboard behavior.
4. Make host compilation follow meaningful module milestones. Check local include closure first; continue reporting incomplete generation while required headers are missing. A readiness check cannot certify C++ semantics; compilation and independent review still establish their respective results.

**Primary files:** `backend/services/platformio_service.py`; `backend/agent_runtime/autonomous_loop.py`, `deliverables.py`, `task_contract.py`, `observation_context.py`, `tool_contracts.py`; internal `builtin_skills/platformio-repair/SKILL.md` if prompt guidance needs alignment.

**Pass criteria:** Invalid version is identified before a full compiler run. Missing local headers are listed in one actionable result. A fixture with mismatched `.h`/`.cpp` interfaces receives repair context containing both files and the compiler evidence. The saved advanced project can be resumed toward a complete manifest without dropping requirements to obtain a passing build.

### WP5 — Make repair and citation recovery advance reliably (F06–F07)

**Proposed changes:**

1. Preserve exact-one-match edit safety. Return zero-versus-multiple match count, current revision and a bounded fresh-read instruction; require fresh source before retrying a stale or ambiguous edit.
2. Track progress by changed content, resolved diagnostics and dependency/module readiness. Reconcile the six-turn rebuild guard with meaningful multi-file repairs and the build milestone policy. Retain bounded limits for unchanged/repeated failed approaches.
3. Expose citation-ready ranges from current numbered reads and include the inclusive line calculation in verification guidance. Repair all overlong references in a batch where practical.
4. If host-side span splitting is adopted, preserve exact original coverage, file revision and evidence limits; revalidate every generated reference. Reject bad bounds and missing/misleading evidence. Keep independent review mandatory.

**Primary files:** `backend/agent_runtime/tool_executor.py`, `tool_runtime.py`, `autonomous_loop.py`, `requirement_arguments.py`, `tool_contracts.py`, `router_planner.py`.

**Pass criteria:** Zero-match and multiple-match edits leave source unchanged and produce actionable feedback; successful fresh edits make progress. An advancing module repair can reach a rebuild; repeated unchanged work stops with its actual cause. Exactly 80 inclusive lines is accepted, 81 is rejected or safely subdivided before submission, and edits invalidate stale citations.

### WP6 — Align Apply behavior with authoritative state (F08)

**Proposed changes:**

1. Define shared application eligibility from completed run, approval/review classification and pending ChangeSet status. Use it in Code and Review canvases and in the action handler.
2. After successful apply, refresh run and ChangeSet state together. Show applied state and remove or disable further apply actions.
3. Use an immediate in-flight latch for rapid duplicate actions. Consider idempotent API handling only for the exact already-applied ChangeSet owned by that run; retain conflicts for stale, mismatched or unapproved changes.

**Primary files:** `frontend/components/nexus/work-canvases.tsx`, `frontend/hooks/use-forge-agent-session.ts`, `frontend/hooks/use-agent-change-set.ts`, `frontend/lib/agent-recovery.ts`; `backend/agent_runtime/orchestrator.py`, `backend/api/routes/agent_runtime.py` as needed.

**Pass criteria:** The first valid application succeeds once. Rapid double-click, delayed state refresh, canvas switch and replay after reconnect do not produce a misleading failure banner or duplicate workspace mutation. Draft/failed/discarded ChangeSets remain ineligible. Test an already-applied state matching the recorded temperature-monitor run.

### WP7 — Report each review gate accurately (F09)

**Proposed changes:**

1. Give the risk reviewer a strict verdict/summary response contract with bounded, model-appropriate output and separately recorded attempts. Reserve its required resources alongside applicable source/completion review allocations.
2. Replace the blanket diagnostic loss with structured reasons for invalid output, truncation, timeout, budget failure or reviewer unavailability. Preserve the existing explicit-review fallback according to product policy.
3. Display a review node as needing attention when automation did not pass. Distinguish successful requirement review, complex completion review and risk-selected change review.
4. Verify requirement granularity against the actual prompt. The latest simple run has two aggregated requirements; determine whether its input formatting justifies that grouping before changing partitioning or the count-based complex-review trigger.

**Primary files:** `backend/api/app.py` (`risk_reviewer`), `backend/agent_runtime/orchestrator.py`, `review_contract.py`, `task_contract.py`, `run_reporting.py`; frontend review/evidence components.

**Pass criteria:** Valid verdicts parse; empty, truncated and malformed responses produce a specific review outcome. A passing source review cannot make a failed risk review appear successful. Explicit local approval behaves consistently with the documented fallback. Neither compilation nor model review is presented as physical device qualification.

### WP8 — Qualify remaining warnings and regressions (F10)

**Proposed work:** Check the development renderer's policy requirements and verify a restrictive production CSP in a packaged test. Preserve DEBUG classification for caller-aborted reads and WARNING classification for Electron console warnings. Retain authenticated API behavior and the earlier caller-attribution fix; a separate CLI's missing credentials do not justify changing authentication.

**Primary files:** `electron/window-manager.ts`, frontend response/header configuration, existing desktop/logging and authentication tests.

**Pass criteria:** Production packaging has the intended CSP and required UI still works. Genuine network failures remain visible. No new malformed log records or unexplained authenticated-request 401s appear. Historical issues already repaired remain regression coverage rather than speculative new patches.

## 4. Implementation order and dependency gates

| Phase | Work | Gate before proceeding |
| --- | --- | --- |
| A — Request admission and truthful recovery | WP1, WP2, WP3 | Intake reservations succeed; phase expiry and provider failures retain correct origins; isolated regression tests pass. |
| B — Generation and verification | WP4, WP5, WP7 | Dependency/version checks, coherent module milestones, safe edits/citations and distinct review outcomes work through the production runtime with fixtures. |
| C — User actions and qualification | WP6, WP8; integrated regression/qualification | Apply state is consistent and replay-safe; frontend/backend checks pass; representative live flows produce attributable outcomes. |

WP2 precedes changes to build scheduling. WP3 precedes risk-review response recovery. WP4 supplies the readiness/progress signals needed by WP5. WP6 can be developed independently once classification expectations are agreed. These are implementation dependencies, not instructions to launch additional agents.

Before implementation, refresh the log cutoff and record the working-tree baseline because this checkout already contains substantial uncommitted work. Make focused changes on top of that state; preserve existing drafts, historical events, user projects, model selections and prior fixes. Use isolated fixtures and databases for regression work. If a schema migration is required, make it backward-compatible and verify it against a copy of persisted state.

## 5. Validation plan after “implement”

This checklist governed implementation. Executed checks and their results are recorded in the linked implementation report.

1. Add failure-reproducing regressions to the relevant existing suites: `test_agent_jobs_and_tools.py`, `test_agent_jobs_api.py`, `test_router_product_planner.py`, `test_agent_orchestrator_v2.py`, `test_platformio_dependency_failures.py`, `test_native_agent_tools.py`, `test_live_verification_arguments.py`, `test_verification_pipeline_regression.py`, provider transport/normalization tests and Node model-router tests.
2. Exercise intake → durable reservation → generation → stage → build → references → applicable reviews → approval → apply through the production adapters with deterministic provider responses. Inject each recorded failure and verify both public state and underlying accounting.
3. Add browser scenarios for applied ChangeSets, delayed refresh, repeated actions and review-attention states in the existing conversation/recovery suite. Run `npm run test:ui-state`, `npm run typecheck:frontend`, `npm run lint:frontend`, the affected Node/Electron suites and required builds.
4. Perform controlled live qualification of the simple temperature monitor and the advanced environment-monitor request after the implementation is locally verified. Use the explicitly selected models, isolated workspaces and retained evidence. Include cold/warm compilation and the failing Ultra-model transport path; record provider-dependent failures honestly.
5. Check saved-draft continuation with current source citations and cumulative budgets. Compare live logs, persisted reports and UI state. A prepared-project review alone does not qualify generation through completion.

Use isolated application fixtures to test Apply. Applying changes to the user's project or flashing hardware requires the corresponding explicit instruction in addition to permission to implement application fixes.

## 6. Completion criteria

The remediation is complete when each issue has a passing regression and an evidenced disposition; intake works with physical attempt identities; phase limits no longer masquerade as exhausted jobs; stream failures recover or stop with an accurate cause; generation handles dependencies and complete module interfaces; safe edits and valid citations progress; review outcomes remain distinct; and repeated Apply actions behave consistently.

Final qualification must identify remaining provider/environment limitations and separately state whether physical ESP32 behavior was tested. Historical failed runs remain historical. Roll back a failing work package using only its isolated changes; do not reset this dirty checkout or rewrite old records to make the history appear successful.

**Current disposition: application changes implemented. Consult the implementation report for qualification results; advanced generation and Ultra-provider live qualification have unresolved outcomes.**
