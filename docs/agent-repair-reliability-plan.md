# ForgeX repair and run-reopening reliability plan

Date: 2026-09-28
Status: investigation and implementation plan; fixes have not been implemented or verified by this review.

## Objective

Identify why firmware repair failed to converge, stop unproductive work before exhausting the task budget, preserve trustworthy draft and verification state, and eliminate redundant requests when reopening runs. Treat these as reliability weaknesses; this review does not establish an exploitable security vulnerability or claim to find every possible defect.

## Evidence and uncertainty

The reported incident contains failed builds, continued repair, `AGENT_PROVIDER_BUDGET_EXCEEDED`, and an unverified draft. The reported 900-second exhaustion still needs correlation with the persisted run and budget counters.

Current source distinguishes two boundaries:

- `backend/model_router/run_budget.py::charge_attempt` raises `AGENT_PROVIDER_BUDGET_EXCEEDED` for request, reserved output-token, or input-byte limits. It also converts a persistence callback's `ValueError` into that same code, losing the specific reason.
- `backend/agent_runtime/autonomous_loop.py` emits `AGENT_RUNTIME_LIMIT_EXCEEDED` for elapsed runtime. A default 900-second limit is present. The provider code alone therefore cannot establish elapsed-time exhaustion.

The local `forgex-current.stderr.log` contains repeated successful session reads on 2026-09-20 around 08:47:44 UTC, including `/v1/chat/sessions` and the same chat session detail. This is historical supporting evidence, not a verified match to the user's latest incident. HTTP reads alone do not prove duplicate agent execution.

Existing protections must be retained and tested: diagnostic repair groups, deterministic repair limits, unchanged-source failed-build caching, a no-progress guard, shared provider accounting, verification freshness, durable checkpoints, terminal-run settlement, and concurrent agent-read coalescing. The working tree already contains substantial changes, including these protections; their presence in the incident's running application is unknown.

## Findings and risk register

| Priority | Finding or risk | Evidence/status | Required outcome |
| --- | --- | --- | --- |
| P1 | Ambiguous budget failure | Confirmed: several provider dimensions and persistence errors share one code | Persist the actual exhausted dimension, consumed amount, limit, attempted charge, origin, and causal error |
| P1 | Repeated failures can appear to make progress | Confirmed mechanism: autonomous-loop observations and build errors hash raw output; `ProgressState.observe` includes nested output without removing volatile fields | Stable, semantic progress accounting; demonstrate whether this affected the incident |
| P1 | File churn can keep resetting the stall counter | Source changes currently count as progress; incident causality unproven | Detect repeating source/diagnostic cycles while permitting legitimate multi-step repairs |
| P1 | Redundant reads on reopen | Observed historical logs; `SingleFlight` covers overlapping agent reads, but chat list/detail reads bypass it | One owner for hydration and refresh; no repeated terminal refresh cascade |
| P1 | Work may start without enough deadline remaining | Runtime checked at turn boundary; tool execution has its own timeout | Trace all deadline propagation, then bound each provider/tool/rebuild operation by remaining task time |
| P1 | Draft or verification state could become inconsistent on stop/resume | Risk to verify, not a demonstrated defect | Durable final checkpoint and source-bound verification across timeout, cancellation, and restart |
| P2 | Environment failures may trigger inappropriate source repair | Risk to verify against compiler and dependency evidence | Distinguish compiler failures, unavailable tools, dependency/network faults, and provider failures |
| P2 | Nested repair calls or resume may obscure accounting | Multiple builds can occur within one build invocation; existing shared budgets/checkpoints need coverage | Account for physical attempts and preserve cumulative limits through retries and recovery |

## Phase 1 — Reconstruct the incident and reproduce it

1. Resolve the exact run ID, session ID, job ID, application revision, provider/model, and effective limits from persisted records and diagnostics. Do not infer the deployment revision from this working tree.
2. Create a timestamped timeline for every provider request, build, repair decision, patch, retry, checkpoint, and terminal transition. Include source revision, normalized diagnostics, duration, budget before/after, and verification status.
3. Determine whether each repair received the relevant compiler diagnostics, changed the intended files, and rebuilt the changed revision. Distinguish deterministic repairs from model-proposed edits.
4. Attribute the terminal stop to a specific budget dimension or elapsed deadline. Record the reason repair failed separately from the reason execution stopped.
5. Capture sanitized replay fixtures. Keep credentials and unrelated user/project contents out of fixtures.

Completion: one evidence-linked incident report and a deterministic failing regression for each established defect. If the exact persisted trace is unavailable, explicitly leave incident causality unresolved and proceed with independently reproduced weaknesses.

## Phase 2 — Make budgets and stopping behavior precise

Target: `run_budget.py`, `router_planner.py`, `jobs.py`, `autonomous_loop.py`, `failure_contract.py`, `run_reporting.py`, and frontend failure rendering.

1. Add structured budget reason fields while retaining compatible legacy codes. Preserve unexpected persistence failures as persistence/internal failures instead of silently relabeling them budget exhaustion.
2. Propagate an absolute deadline/remaining duration into provider requests, tools, deterministic rebuilds, and child work. Reserve time for checkpointing and terminal publication; do not start another repair that cannot fit its bounded operation.
3. Verify cancellation reaches subprocesses and prevents late results from changing terminal state. Preserve uncertain operation outcomes for reconciliation after interruption.
4. Persist cumulative accounting before dispatch and restore it on recovery. Confirm retry, fallback, and nested physical attempts cannot reset or bypass limits.

Completion: separate tests for elapsed time, request count, reserved tokens, input size, persistence failure, and cancellation. The displayed explanation matches the actual stop reason. Increasing the 900-second limit is not the primary remedy.

## Phase 3 — Make repair progress measurable

Target: `autonomous_loop.py`, `progress_state.py`, `canonical_runtime.py`, `firmware_repair.py`, and build-service diagnostics.

1. Use shared canonical diagnostic fingerprints that exclude volatile paths/IDs/timestamps/durations but retain error identity and relevant source location. Test against both false novelty and accidental merging of different failures.
2. Track repair history with source revision, diagnostic group, proposed action, changed paths, actual diff, and resulting build evidence.
3. Detect unchanged failures and A-to-B-to-A source/diagnostic cycles. New reads or irrelevant edits must not indefinitely reset the repair-progress budget.
4. Apply a bounded strategy-change policy: after repeated failure of the same repair, switch only when new evidence supports another strategy; otherwise return an actionable blocker and saved draft.
5. Separate network/dependency/toolchain failures from compiler failures. Retry transient infrastructure faults within the shared deadline; patch code only when diagnostics support it.
6. Rebuild after each accepted repair and bind success to the current source/environment. Never reuse a successful build after a relevant edit.

Completion: unchanged diagnostics with changing metadata stop predictably; cyclic edits stop; genuine improving multi-step repairs can complete; invalid repairs never produce verified status.

## Phase 4 — Give UI hydration and refresh one owner

Target: `frontend/hooks/use-forge-agent-session.ts`, the chat-session hook located during tracing, `frontend/lib/api.ts`, `single-flight.ts`, stream policy, and conversation tests.

1. Instrument refresh causes: initial hydration, selection, stream event, polling fallback, terminal settlement, and focus. Correlate requests without logging message contents.
2. Trace callback/effect dependencies and terminal callbacks before altering them. Remove proven refresh feedback loops.
3. Coalesce concurrent chat reads as well as agent reads, scoped by resource/context. Coalescing alone does not prevent sequential duplicates; fix redundant callers too.
4. Maintain one active stream and at most one fallback poller per selected run. Coalesce refresh triggers, stop fallback work on settlement, and clean up timers/listeners on selection changes and unmount.
5. Reject obsolete responses after rapid session/project switching. Define cancellation ownership so one caller cannot invalidate another shared read.

Completion: deterministic browser tests assert request counts for settled-run reopen, rapid switching, repeated clicks, stream reconnection, and development effect remounts. Each required resource is hydrated once per deliberate open, no mutations are triggered by reopening, and a settled run causes no recurring polling or refresh cascade. Explicit user refresh remains fresh.

## Phase 5 — Preserve drafts and truthful recovery

1. Validate final checkpoint and draft persistence at each failure boundary, including interrupted edits and build cancellation.
2. Keep failure evidence alongside the draft: last compiler errors, attempted repairs, remaining limits, and the next supported action.
3. Require current-revision verification before reporting build success or permitting any operation whose existing policy requires verified firmware.
4. Test resume idempotency and cumulative budget restoration. Budget exhaustion must not trigger an automatic fresh-budget retry loop.
5. Keep hardware actions behind the existing trust/approval protections; no hardware operation is needed for these regressions.

Completion: reopening or restarting retains the same draft and honest verification status; duplicate recovery requests cannot create parallel replacement runs; interrupted operations cannot silently appear complete.

## Phase 6 — Regression gates and release evidence

Extend the existing suites rather than replacing current protections:

- Python: firmware repair, unified agent runtime, progress continuation, shared subagent budget, durable provider boundary, request recovery, verification freshness, jobs API, and product runtime API tests.
- Frontend: single-flight, stream policy, recovery, type checking, lint, and Playwright conversation tests with network-request assertions.
- Integration fault cases: provider timeout/rate limit, toolchain unavailable, dependency download failure, non-converging compiler errors, insufficient remaining deadline, cancellation during rebuild, backend restart, and stale responses after navigation.
- Finish with a controlled firmware-build replay and reopening the resulting verified and unverified runs. Use mocked providers for deterministic regressions; isolate any live replay from active user runs and account for its provider usage.

Release evidence must include the exact revision tested, test commands/results, incident replay result, request counts before/after, final budget reason, and draft recovery checks. A test that was not run remains explicitly unverified.

## Execution order and definition of done

Execute phases 1 through 6, landing focused changes with their reproductions. Address confirmed P1 defects first; label hypotheses until reproduced. Preserve existing local work and scope each diff to the issue being fixed.

Done means the incident is explained to the available evidence, every confirmed in-scope defect has a regression and fix, unproductive repair terminates with a useful reason before wasteful exhaustion, reopening does not amplify requests, and generated files never acquire a verification claim unsupported by a fresh build. Any unresolved cause or untested environment remains explicitly listed rather than described as fixed.
