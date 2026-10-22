# Recent live-log review and fix plan — 10 October 2026

Status: investigation and planning complete; the changes below are proposed. This review covers the latest desktop session, after the earlier 10 October fixes. Existing implementation reports describe earlier validation and do not establish that this new incident is fixed.

Implementation scope update: the user subsequently authorized ForgeX runtime fixes while requiring generated code to remain unchanged. Packages 1–4 have been implemented and verified in isolated fixtures. Package 5 and the original-request model/firmware qualification are excluded from that authorization. See [the runtime implementation report](recent-live-log-runtime-implementation-2026-10-10.md) for the completed changes and verification results. The diagnosis below remains the original investigation record.

## Diagnosis

The ESP32 Smart Knock Pattern Detector run stopped because the agent attempted a firmware build without creating `platformio.ini`. The host converted that missing project file into a terminal `DEPENDENCY_VALIDATION_FAILED` result, preventing the agent from repairing it within the same run.

The saved draft contains 11 source files and also lacks the requested `README.md`. All 13 provider requests completed with HTTP 200. The advanced job consumed approximately 254 of its 2,700 seconds, leaving approximately 2,446 seconds. Increasing model timeouts or the task budget would not address this failure.

There are two separate reliability issues: four transient proxy failures during generation, and repeated startup warnings about an older cancelled run whose workspace changed. The available evidence does not establish a backend crash or the cause of the transient health-check failures.

## Evidence and scope

| Item | Observed evidence |
| --- | --- |
| Desktop session | Launch `5e743c4d-7d61-4282-bb73-8bd6f48fc454`; backend instance `0a9eb3447bb14b39b57f2005dd358b39`. |
| Latest log window at initial inspection | 10 October, 17:31:01–19:54:21 IST. The generation incident occurred around 17:32–17:36 IST. JSON log timestamps use UTC. |
| Affected run | `agent-run-6b81bccdeb614c2dba6a1e5a6088d732`, session `agent-session-18eea64486c442888ce37b8501017092`. |
| Task and budget | 58 recorded requirements; advanced profile; 13 provider attempts; 7,434 completed output tokens; zero unresolved output reservations. |
| Agent plan | Backend log line 75 lists configuration initialization and documentation as pending work. The agent subsequently wrote source modules without completing either step. |
| Immediate failure | Backend log line 395: missing `platformio.ini`; line 400: terminal run failure. No successful compiler execution is recorded for this run. |
| Preserved work | Draft `changeset-97f4bc78aaa74adfab91645abeba76be` contains 11 source files. Its status is `draft`, `applied_at` is null, and the run records the active workspace as unchanged. A sealed checkpoint exists at turn 12. |
| Proxy failures | Desktop log lines 98, 134, 193 and 194: GET requests returned 503 `BACKEND_UNAVAILABLE`, with `request_dispatched: false`, after 2,056–2,352 ms. Successful API responses followed from the same backend instance. |
| Current health | Three direct `/health` checks returned HTTP 200, `engine_ready: true`, in 31, 16 and 16 ms. These idle samples do not qualify responsiveness during generation. |
| Older startup warning | Stderr lines 1–14: two failed retention attempts for cancelled run `agent-run-0fd0f6b00fff4589b17b3ef51402aa95`, reporting `AGENT_RESUME_WORKSPACE_CHANGED`. |

Sources: [backend events](../.forgex/logs/live-runtime.backend-0a9eb3447bb14b39b57f2005dd358b39.jsonl), [desktop events](../.forgex/logs/live-runtime.desktop-5e743c4d-7d61-4282-bb73-8bd6f48fc454.jsonl), [desktop stderr](../.forgex/logs/desktop-launch-20261010-173012.stderr.log), and read-only inspection of `agent-orchestration.sqlite3` and the changeset manifest. A bounded evidence snapshot is saved in [evidence.json](../audit/2026-10-10/recent-live-log-review/evidence.json).

## Implementation order

| Order | Priority | Work package | Depends on |
| --- | --- | --- | --- |
| 1 | P0 | Enforce configuration readiness and expose missing deliverables | None |
| 2 | P0 | Repair incomplete projects within the current run | 1 |
| 3 | P1 | Diagnose and resolve transient proxy failures | None |
| 4 | P2 | Resolve stale checkpoint-retention records once | None |
| 5 | P1 | Complete and qualify the saved knock-detector draft | 1–2; final desktop qualification also requires 3–4 |

### 1. Enforce configuration readiness and expose missing deliverables

Change [autonomous_loop.py](../backend/agent_runtime/autonomous_loop.py), [task_state.py](../backend/agent_runtime/task_state.py), [build_recovery.py](../backend/agent_runtime/build_recovery.py), and the existing deliverable/context projection.

- Add a host-owned configuration prerequisite for tasks that require a PlatformIO firmware build. Inspect the effective staged/overlay project at admission and continuation, and before compiler dispatch.
- When configuration is missing, direct the next action to creating `platformio.ini` with the requested or accepted board, framework and environment. Preserve existing configuration in edit tasks. If the target is genuinely unresolved, use the existing task-decision mechanism instead of guessing.
- Prevent unrelated source rewrites from bypassing an unresolved configuration prerequisite. Permit inspection, planning, dependency investigation and configuration repair. Build verification must require valid configuration and source; documentation is a completion prerequisite, not a compiler prerequisite.
- Reuse the deliverable manifest as the source of missing-file information. It already records both omissions. Fix the current `task_state.py` condition near line 100, where an unfinished milestone can suppress the missing-deliverable next action after firmware source is present.
- Keep required configuration, missing documentation and the next corrective operation visible through context compaction and saved-draft continuation. Keep file presence, build success and independently reviewed correctness distinct.

Acceptance: an empty PlatformIO generation task reaches configuration repair before repeated source generation; an existing valid project proceeds without unnecessary reinitialization; stale plan steps cannot hide missing configuration or documentation; compaction retains those obligations.

### 2. Repair incomplete projects within the current run

Change [platformio_service.py](../backend/services/platformio_service.py), `_preflight_dependencies` and terminal classification in [autonomous_loop.py](../backend/agent_runtime/autonomous_loop.py), [failure_contract.py](../backend/agent_runtime/failure_contract.py), and recovery projections where needed.

The current chain is `_load_project` raising `PlatformIOProjectError`, followed by the generic exception handler in `_preflight_dependencies` setting `terminal: true` and `retryable: false`. The configuration repair focus currently handles only `DEPENDENCY_UNRESOLVED` with an existing configuration fingerprint, so an absent file falls outside that recovery path.

- Give known missing or invalid project configuration a typed error with the affected path, corrective operation and `execution_attempted: false`. Distinguish missing configuration from missing project directories, unavailable tools, registry outages, unsafe build settings and exhausted deadlines.
- Return recoverable configuration failures to the planner within the current run. Extend repair focus to represent absent configuration, not just unresolved libraries in an existing file.
- Use a bounded repair counter keyed to the configuration state. Revalidate after a real repair; stop repeated ignored or ineffective repairs with an actionable repair-exhaustion result.
- Preserve strict terminal behavior for policy rejection, unsafe targets, unavailable workspaces and exhausted budgets. Preserve cumulative provider/tool/time accounting across repair and continuation.
- Show “Create or repair platformio.ini” and the preserved draft in failure/recovery controls. A missing-file repair must not be presented as a provider outage or solved by replaying an unchanged build.

Acceptance: replay the observed sequence, receive a missing-configuration observation, create the configuration, then reach a real build without ending the run at the first error. Repeated unchanged attempts terminate predictably. Resuming the turn-12 checkpoint retains the 11 files and remaining allowances. Policy and deadline failures still stop correctly.

### 3. Diagnose and resolve transient proxy failures

Change [backend-proxy.ts](../frontend/lib/backend-proxy.ts), [middleware.ts](../frontend/middleware.ts), and measured slow paths in [orchestrator.py](../backend/agent_runtime/orchestrator.py) or persistence code.

The proxy verifies `/health` before each request with a two-second timeout. The failure duration is consistent with that timeout, but the catch block discards the underlying cause. Toolchain health is already cached and starts its probe in the background. Another plausible source is synchronous checkpoint copying, workspace inspection or persistence inside async progress callbacks; this remains a hypothesis until measured.

- Record proxy failure phase, elapsed time, abort reason, a sanitized transport cause, and backend identity when known. Distinguish caller cancellation, health timeout, refused connection and identity mismatch.
- Measure event-loop lag, checkpoint copy/inspection time and database transaction/lock time while generating and sealing checkpoints. Correlate them with health probes by timestamp and request ID.
- Move demonstrated blocking work off the API event loop using the existing synchronization model. Await checkpoint sealing inside the write barrier before permitting subsequent mutation; preserve event order and committed checkpoint boundaries. Do not move SQLite connections across threads or publish incomplete snapshots.
- Bound duplicate concurrent health probes where evidence supports it. Any shared identity result must have a short explicit lifetime and clear invalidation; unrelated listeners must still be rejected before receiving credentials or mutation bodies.
- Make renderer recovery for read requests bounded and automatic when transport becomes healthy, with one reconnecting state and no parallel polling storm. Preserve single dispatch for mutations and `REQUEST_RESULT_UNKNOWN` after an uncertain mutation outcome.

Acceptance: under an isolated generation/checkpoint load fixture, health responses stay within the existing proxy deadline and run/graph/queue reads avoid spurious 503s. A truly unavailable or unrelated backend still produces concrete errors. SSE remains incremental; uncertain mutations are never replayed. Idle health samples alone are insufficient.

### 4. Resolve stale checkpoint-retention records once

Change `_retain_sealed_checkpoint` and startup restoration in [orchestrator.py](../backend/agent_runtime/orchestrator.py), with the existing [recovery_draft.py](../backend/agent_runtime/recovery_draft.py) invariants intact.

- Treat a changed baseline as a specific recovery conflict. Record that outcome and the applicable workspace/checkpoint revision so startup does not repeatedly attempt the same impossible retention operation.
- Preserve the sealed checkpoint and explain why automatic continuation/apply is unavailable. If inspection or export is offered, keep it separate from an applicable changeset against the changed workspace.
- Retry retention only when the relevant state or explicit recovery decision changes. Distinguish an expected recovery conflict from an unexpected storage failure in diagnostics.

Acceptance: reopening the historical cancelled run against a changed workspace records one understandable conflict and does not repeat the traceback on every launch. An unchanged, healthy checkpoint still retains its draft. No stale draft is silently rebased or applied.

### 5. Complete and qualify the saved knock-detector draft

Use a new isolated copy of the sealed checkpoint or recovery draft. Keep the historical run, original draft and user's target workspace intact while qualifying the repair.

1. Recover the original request, accepted target and remaining job budget. Create valid `platformio.ini` and the requested `README.md`, including architecture, pins, wiring, the requested input-protection explanation, state diagram, setup, testing and hardware limitations. `Preferences` must remain a framework facility rather than an invented external dependency.
2. Obtain fresh compiler diagnostics and repair actual errors. No compilation result exists for this incident, so source inspection cannot establish that adding the configuration alone will produce a successful build.
3. Repair the observed firmware behavior defects. In `knock_detector.cpp`, while `currentState` differs from `_lastState`, each call resets `_lastDebounceTime` before testing whether the debounce interval has elapsed. For the configured positive debounce interval, this appears to prevent accepting the transition. Test a piezo pulse/refractory design with a simulated clock and ADC input rather than copying switch debounce assumptions.
4. Remove blocking delays from the detection, learning, success/error feedback and buzzer paths. Inspect `main.cpp` and the buzzer implementation together; use elapsed-time transitions so sensing and button handling continue during feedback.
5. Verify pattern ordering and timing tolerance, learned-pattern comparison, empty/corrupt storage behavior, button short/long presses and persistence. The generated pause pattern uses `{400, 800}`; reconcile interval order with the requested knock–pause–knock–knock behavior. Record an explicit interpretation if the request's examples differ.
6. Run the existing requirement, completion and risk reviews against the final source revision and documentation. All 58 active requirements and recorded exclusions must be accounted for. Rebuild after any source/configuration repair so the final evidence matches the delivered revision.

Acceptance: the qualified project has all requested deliverables, a fresh successful PlatformIO build and current independent reviews. Behavioral tests demonstrate one event per qualifying pulse, recovery for subsequent knocks, correct interval conversion/tolerance, learning persistence and continued sensing during feedback. Physical knock recognition remains `hardware_verified: false` until actual device evidence exists.

## Verification and release gates

Add incident regressions to the existing focused suites rather than rerunning broad tests before the affected behavior is covered:

- Backend: `test_live_session_recovery.py`, `test_platformio_service.py`, `test_task_state.py`, `test_agent_recovery.py`, `test_context_checkpoint_compaction.py`, and the relevant journal/recovery suites. Use isolated databases, stages and deterministic planners.
- Proxy/UI: `backend-proxy.test.mts`, `agent-recovery.test.mts`, `recovery-controls.spec.ts`, and the production proxy qualification script. Cover slow health checks, caller cancellation, concurrent reads, SSE and single mutation dispatch.
- Firmware: run meaningful clock/ADC/pattern/storage behavior tests in the isolated project, then a real PlatformIO build. Stubbed peripherals establish software behavior only.
- Desktop: rerun the full original request with the same selected NVIDIA Super model and advanced profile in an isolated workspace, collecting provider accounting, checkpoint timings, health latency and final review results. A successful small fixture does not qualify the original request.

Definition of done: the incident replay repairs missing configuration within the run; source and documentation are complete; build/reviews refer to the final revision; the desktop remains responsive during generation; cancelled historical recovery conflicts are stable; no historical work is lost or silently applied. Provider-dependent failures during live qualification remain explicit failures with preserved drafts.

This review performed log inspection, read-only state/source inspection and three health GETs. It did not implement these fixes, rerun generation, compile or flash firmware, or run the proposed regression suites.
