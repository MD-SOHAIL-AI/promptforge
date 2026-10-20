# Live runtime fixes — 13 October 2026

The interrupted request was to inspect the recent live logs and fix their underlying errors. Existing checkout changes were preserved. This work changes ForgeX application/runtime behavior; it does not rewrite, resume, apply or flash the user's generated firmware.

## Evidence and interpretation

The inspected session uses desktop launch `26238e3f-76d9-4b0a-afee-3fd981b3d5cb` and backend instance `ae600cdbba0a43a592db1f2c10fad053`. The backend incident window begins at 18:15 IST. Evidence was read from those instance logs and from a read-only SQLite connection. A bounded snapshot is retained in [live-log-evidence.json](../audit/2026-10-13/live-log-evidence.json).

| Observed event | Interpretation and change |
| --- | --- |
| Seven rejected exact edits; the second run repeatedly reread `ButtonManager.h` and failed against its unchanged hash | The available logs retain argument sizes, not the rejected source strings, so the precise text mismatch cannot be proved. Errors now distinguish a missing match, multiple matches and a required fresh read, and supply a bounded exact source excerpt. Instructions direct the model to copy a small unique block with its original whitespace. Matching remains exact and failures apply no edit. Current failure advice survives compaction and expires when the source hash changes. |
| Undefined references, undeclared methods, class/member drift and inline-method redefinitions in generated C++ | Current declarations and paired paths are now pinned even before an implementation exists. The advisory scanner handles single-line classes, balanced inline bodies and constructor initializers, and identifies missing implementations and duplicate inline definitions. Distinct argument/const overloads are preserved. A read-only replay of the second run's turn-26 checkpoint produces ten interface warnings. Compilation still owns C++ signature/type correctness. |
| Six deletion conflicts | Running/cancelling work and pending flash confirmation must remain protected. The deletion guard now uses the canonical settled status set, including `completed_with_warning`. Deleting a conversation paused on an unavailable model first cancels that run so its recovery cannot keep dispatching work. Existing active-task conflicts remain intentional. |
| Twenty-seven permission-change events logged as errors | The logger was deriving severity from the historical run's failed status. Severity now follows the event: routine permission changes are informational, slow/blocked/paused events are warnings, and actual failure events are errors. |
| Two renderer 503s with a shared health-probe timeout | Repeated context inspection, workspace preparation, draft persistence and workflow cleanup still performed filesystem work on the API loop. Those paths now use awaited workers. Stage enumeration prunes build/dependency directories instead of descending into them through `rglob`. Workers are drained on cancellation before ownership is released. A newly created unpublished recovery draft is discarded on cancelled publication. Terminal failure/warning status is published after the live stage has been cleaned up. |
| Recovered read failures appeared as terminal renderer errors | Intermediate retries now emit reconnection events with their sanitized cause. Only an exhausted or non-retryable request emits a final failure. Existing read deadlines, three-attempt limit, backend identity checks, cancellation and single mutation dispatch remain enforced. |
| Generation reserve reached, rejected unsafe provider path, provider cancellation and development CSP warning | These are retained as actual budget/policy/cancellation outcomes or development behavior. No deadline, path guard, compiler check or production CSP was weakened to hide them. |

The original runs stopped with `AGENT_VERIFICATION_RESERVE_REACHED` and `BUILD_REPAIR_EXHAUSTED`. Both preserved drafts remain unverified. The source/excerpt changes improve model repair evidence; they cannot guarantee that the selected model will generate a complete correct project within its budget.

## Validation

- Backend runtime, orchestration, recovery, checkpoint compaction, verification freshness, durable continuation, permissions and API regressions: **147 passed**, retained in [backend-final.xml](../audit/2026-10-13/backend-final.xml).
- After the final context/scanner adjustments, the incident-focused group: **64 passed**, retained in [incident-final.xml](../audit/2026-10-13/incident-final.xml).
- Proxy, renderer diagnostics and read recovery: **17 passed**, retained in [frontend-regressions.log](../audit/2026-10-13/frontend-regressions.log).
- Frontend TypeScript check and targeted ESLint passed. Python compilation and `git diff --check` passed.

New checks cover exact whitespace preservation and failed-edit recovery, advice freshness after compaction, missing implementations, inline definitions and legitimate overloads, API-loop scheduling during slow context inspection, real ASGI health requests during delayed recovery-draft creation, repeated cancellation with joined cleanup, canonical conversation deletion, and retry diagnostics without mutation replay.

The first broader run exposed a terminal-publication/cleanup race introduced by moving cleanup into a worker. The implementation was corrected; the final 147-test run passed. An initial new health test used the wrong fixture shutdown method; that fixture was corrected as well. The failed JUnit records remain available instead of being presented as successful runs.

## Limits and activation

The deterministic tests isolate the affected runtime paths; they do not establish the exact historical cause of every health timeout. SQLite persistence and some other runtime inspections remain synchronous. The production frontend build, installer, full NVIDIA generation request and physical ESP32 behavior were not requalified in this turn.

The running desktop/backend was left available. Restart ForgeX to load the backend changes, then continue the preserved draft if desired. A new successful build and applicable reviews are still required before treating its firmware as complete.
