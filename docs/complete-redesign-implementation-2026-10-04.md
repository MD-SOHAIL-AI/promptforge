# ForgeX redesign implementation — 4 October 2026

The reliability and workflow redesign is implemented in the current workspace. It addresses all thirteen findings in the [review](complete-app-review-and-redesign-plan-2026-10-04.md). The central behavior is bounded failure, preserved settled work, accurate evidence, and explicit continuation.

This report describes source changes and local validation. It is not a signed release certificate. The installed desktop binary has not been replaced, and live model completion and physical device qualification remain separate release gates.

The backend contract is `forgex-agent-runtime-v3`; this candidate's backend source fingerprint is `a19ecde06b01f60f9ba596f5949136008d86d3ac3d1e53f1395c9aa3dc30bd91`. Runtime diagnostics identify what the running application actually loaded.

## What changed

| Review finding | Implementation | Main sources |
| --- | --- | --- |
| F1: timeout evidence lost | A typed, redacted provider diagnostic contract survives the real workspace adapter, run serialization, SQLite restore, API and renderer. It includes physical attempt identity, header receipt, first/last meaningful event, event count, completion receipt and timeout stage. Malformed fields are dropped individually. | `backend/contracts/diagnostics.py`, `tool_runtime.py`, `run_models.py`, `orchestrator.py`, generated frontend contracts |
| F2: local deadline called provider failure | Turn and job deadlines have distinct codes and origins. The original failure, subsequent recovery failure and final stopping condition remain distinguishable. Host deadlines do not become provider outage retries. | `llm_service.py`, `router_planner.py`, `failure_contract.py`, `autonomous_loop.py` |
| F3: permission mismatch | Sealed checkpoints identify the next operation. Eligible generation can continue without granting local execution permission. Builds/tests keep their permission boundary. Unknown legacy checkpoint intent stays blocked conservatively. A permission change refreshes eligibility without executing work. | `jobs.py`, `orchestrator.py`, `autonomous_loop.py`, recovery contracts and controls |
| F4: unbounded renderer requests | Request bounds cover headers and body parsing, even when a transport ignores abort. Reads and mutations report different outcomes. Selection changes cancel observation and fence late responses. A quiet or stalled SSE connection receives a bounded reconciliation read; stale state is explicit and can recover. | `request-deadline.ts`, `api.ts`, agent session and workspace hooks |
| F5: misleading build success | Manual builds have durable, addressable operation IDs. Accepting the same ID cannot start another build. GET refresh retrieves the saved outcome. Project, environment and source changes invalidate evidence. Agent builds receive a separate source/target freshness check. The interface presents idle/running/passed/failed/unavailable/stale explicitly, disables flashing without current evidence and uses actual compiler memory measurements. | `build_operations.py`, build routes, `/runs/{id}/build-evidence`, workspace hook, BuildCanvas, `build_firmware.py` |
| F6: reservation confused with usage | Every physical dispatch reserves before sending and has a durable ledger entry. Confirmed complete output settles once against known token usage. Interrupted, unknown or truncated responses retain their reservation. Stale checkpoint projections cannot restore a settled reservation. Parent and review roles share this budget, including Ollama. | `jobs.py`, `run_budget.py`, provider adapters, planner/lifecycle binding |
| F7: monolithic review ceiling | Large source reviews use deterministic requirement units with exact line spans and source hashes. Quoted local includes are expanded transitively. A final aggregate covers the same task/source revision. Completed units persist with checksums and resume after interruption; changed source invalidates the cache. Missing dependencies prevent a valid final review. | `verification.py`, job review-unit storage, planner role binding |
| F8: expensive observation | Versioned projection checkpoints replay only later events. Observation integrity results can be reused for unchanged journal bytes. Mutation/lease authorization still performs fresh integrity checks. Heavy run/graph/projection reads run outside the API event loop. SSE uses journal notifications, bounded fallback waits and heartbeats. | `projection.py`, `orchestration_store.py`, `orchestrator.py`, agent routes |
| F9: health confused with readiness | A task-specific readiness endpoint reports workspace, selected provider configuration, cached provider health age, toolchain, environment and execution permission. It generates no model output. The task strip displays these prerequisites; provider/device qualification is explicit. | project readiness route, TaskCanvas, health contract |
| F10: backend supervision | Only an owned backend is restarted. Recovery is capped at three delays within the recovery window, clears on Stop and checks identity/compatibility before reuse. Health supervision detects an unresponsive owned process. The renderer displays recovery/degraded state. | `electron/backend-manager.ts`, `backend-recovery.ts`, workspace/shell status |
| F11: duplicated lifecycle policy | Both runtime adapters bind durable reservation, settlement and resumable review resources through a shared lifecycle helper. Turn allowance and phase reserves use common host policy. Existing canonical execution, approval and side-effect ownership remain the authority. | `lifecycle.py`, planner, orchestrator and application adapter wiring |
| F12: vague deliverables | Requested documentation has explicit paths and topics such as wiring/calibration/test checklist. Arbitrary old Markdown cannot fulfill it. Presence, review and build evidence are separate and revision-bound. Generation preserves verification time, requests and output allowance. | `deliverables.py`, runtime policy, budget/planner boundaries |
| F13: stale documentation/evidence | Host contracts generate JSON Schema, frontend types, renderer deadline constants and runtime reference documents. A regression checks every generated artifact. This report, regression ledger and qualification matrix distinguish local results from production qualification. | generator, generated policy/contracts, this report and companion documents |

The setup screens now give workspace entry and submission prerequisites prominent actions. Upload confirmation is an explicit always-required policy. Model configuration displays its last check and observed status. About shows the actual data profile, source fingerprint, runtime contract and backend instance. Runtime and request diagnostics can be downloaded through local actions without sending them to another service.

The recovery interface groups current state, saved work and next action. Budget exhaustion remains visible, while detailed physical-request fields are expandable. Build evidence identifies saved drafts separately from the active workspace. The editor, logs, changes, tests, simulation, device approval and serial ownership continue using the existing scoped runtime contracts.

Inspected browser captures: [failed build](redesign-evidence-2026-10-04/build-failure.png), [saved-task recovery](redesign-evidence-2026-10-04/saved-task-budget.png) and [narrow task layout](redesign-evidence-2026-10-04/agent-narrow.png). These use controlled browser fixtures and are not live-provider or packaged-desktop qualification evidence.

## Runtime defaults and limits

[Generated runtime policy](runtime-policy.generated.md) and its [machine-readable document](runtime-policy.generated.json) are the source of truth. Ordinary renderer reads default to 15 seconds and mutations to 30 seconds. Legacy synchronous build/flash calls retain their longer bound; current manual builds use accepted operations plus bounded observation reads.

Generation reserves two review requests and 2,048 output tokens, as well as its existing verification time allowance. This is a minimum reserve. Large reviews preflight their actual remaining units plus final aggregate and request an allowance extension when necessary. Review units are at most 64 KB, with at most 32 units and 128 discovered source dependencies. Oversized individual lines and excessive dependency graphs produce explicit bounded errors rather than false certification.

Durable build observation lasts at most five minutes per observing caller. Ending observation does not replay the mutation. An interrupted backend marks unfinished build operations interrupted; a new build requires another explicit action. Manual builds are not automatically resumed after restart.

Known completed token usage releases unused output allowance. Provider responses without reliable usage, incomplete responses and output truncation retain their reservation. The ledger exposes reserved, completed actual and unresolved output separately. This preserves a conservative ceiling without charging every successful short reply as its maximum length.

## Validation

The final commands and results are recorded in [qualification matrix](redesign-qualification-2026-10-04.md). Raw logs are under `.forgex/redesign-validation`. The [regression ledger](redesign-regression-ledger-2026-10-04.md) maps faults to their acceptance tests.

An isolated 1,000-event journal benchmark measured full replay at a median **69.147 ms**, compared with checkpoint observation at **10.668 ms**, a **6.48×** improvement over 20 reads. This measures local observation after warming the read cache. It does not measure provider completion, production disk contention or lease renewal under concurrent load. Reproduce it with:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark-agent-observation.py --events 1000 --repeats 20
```

The incident regression uses an actual HTTPX stream through provider → planner → workspace execution adapter. After a complete write turn, the next response stops sending meaningful output. The test verifies finite idle termination, stream cleanup, preserved draft, physical attempt identity and timings, and absence of private response text in diagnostic fields. A separate adapter/store regression verifies the same fields after SQLite reopening. Browser tests cover held body parsing, stale-state recovery, saved operation refresh without replay and failed/stale build presentation.

The broad tests caught and corrected integration issues in generation reserve precedence, restoration cancellation, generator destination creation and Ollama stream usage normalization. Their corrected tests remain in the suite.

## Data preservation and rollout

The existing uncommitted workspace was preserved. Before editing, the source was captured at `.forgex/redesign-baselines/source-20261004-141229.zip`. It is a source snapshot, not a backup of credentials or all user runtime data. `changed-files.json` in the validation folder compares this candidate with that snapshot, separately from unrelated pre-existing Git changes.

The store uses additive tables for provider attempts, completed review units and build operations. Schema v2-to-v3 migration takes a SQLite backup before upgrade. Existing journals, jobs, run IDs, sealed checkpoints and approvals are retained. Legacy unclassified recovery intent does not acquire new execution authority.

Deploy a candidate only after the release gates in the qualification matrix pass for that exact packaged identity. Keep the prior application and a SQLite-aware backup of its actual data profile. Do not downgrade the schema-v3 database into a schema-v2 binary; use a compatible rollback build or restore a separately retained pre-upgrade profile. Never discard jobs to make a downgrade start.

Local compilation and deterministic tests establish implementation behavior. A release still needs current real-provider qualification, a real ESP32 build/upload/serial exercise, signing and a clean Windows installation. Accessibility checks here cover tested browser interactions and reflow, not a full assistive-technology or packaged-renderer certification.
