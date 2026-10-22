# Live-log remediation implementation — 10 October 2026

The user authorized implementation by saying “implement”. Changes for WP1–WP8 are present in the checkout, and the affected regression checks passed with one existing Windows symlink test skipped. Live qualification passed the simple temperature-monitor flow; the advanced generated project still fails compilation, and the Ultra-model probe ended in a provider timeout. These outcomes are retained as failures and are not a claim that every live generation now succeeds.

This report follows the [approved remediation plan](live-log-remediation-plan-2026-10-09.md). The checkout already contained substantial uncommitted work. Existing changes were preserved. Tests and qualification used isolated databases, workspaces and owned processes. Historical runs, user projects, credentials and model selections were not rewritten. No qualification draft was applied to the user's workspace and no device was flashed.

## Implemented changes

| Work package | Resulting behavior | Main implementation |
| --- | --- | --- |
| WP1 / F01 | Intake separates attempt identity from numeric charges, validates before writing, reserves and settles atomically, and transfers charges to a job once. Persistence errors retain a bounded cause code. | `intake.py`, `jobs.py`, `run_budget.py`, intake API and planner diagnostics |
| WP2 / F02 | Dependency and compilation deadlines have separate phase outcomes. A phase expiry carries remaining job time and does not become whole-job exhaustion. Cold builds receive up to a 300-second compilation window; dependency preflight has its own bounded window. Cancellation is awaited before recovery. | `engine.py`, `autonomous_loop.py`, `failure_contract.py`, diagnostic contracts |
| WP3 / F03 | Malformed provider output becomes `PROVIDER_STREAM_INVALID`, retaining a safe failure-shape category. Eligible recovery can use the selected compatible model's actual buffered transport. Incomplete tool calls are rejected before execution. | Node router engine, OpenCode/OpenAI adapters, planner and diagnostic projection |
| WP4 / F04–F05 | Preflight resolves exact requested library specifications and registry compatibility against the selected environment. Invalid versions return available registry metadata. Missing local module headers are reported together before compilation; repair observations include header declarations and matching implementation paths. | `platformio_service.py`, new `build_readiness.py`, autonomous build scheduling and internal repair guidance |
| WP5 / F06–F07 | Failed exact edits report match count and current hash and require a fresh read before retry. Meaningful source progress resets the six-turn repair guard; unchanged loops remain bounded. Numbered reads expose 80-line ranges. Oversized valid citations are split without dropping coverage, increasing the eight-reference cap, or skipping independent review. | Tool executor/runtime, autonomous loop, requirement argument normalization and observation context |
| WP6 / F08 | Apply eligibility uses authoritative run and pending ChangeSet state in both canvases and the action hook. An immediate latch blocks duplicate dispatch. Applied/discarded runs lose the action even while an old pending response is visible. Backend conflict handling remains authoritative. | Recovery helper, ChangeSet/session hooks and work canvases |
| WP7 / F09 | Risk review uses a strict verdict/summary schema, 2,048-token first response and one 4,096-token repair. Attempts share the durable job budget. Invalid, empty, truncated, timeout and unavailable outcomes retain specific reasons and mark the review node as requiring attention. The UI reports source, completion, change-risk and hardware evidence separately. | New `risk_review.py`, API reviewer binding, orchestrator, verification contracts and UI state |
| WP8 / F10 | Production renderer responses include a restrictive CSP without `unsafe-eval`. The production browser fixture loads the offline Monaco editor and checks for CSP violations. Development warning and cancellation levels and authenticated API behavior are preserved. | `next.config.ts`, production browser scenario and qualification runner |

Saved-draft continuation also rebuilds a previously successful draft before collecting new source citations. Historical successful build observations alone cannot qualify the current continuation. The original simple prompt's two aggregated requirements were retained; the second requirement still needs both behavior and documentation evidence.

The host readiness check establishes include closure and supplies interface context. C++ compilation still determines whether constructors, member declarations and implementations are consistent; source/model reviews and physical-device evidence remain separate gates.

## Regression validation

The final backend results cover 380 passing tests and one skipped test, with zero remaining failures. [The consolidated result](../audit/2026-10-10/backend-regressions-final.json) records the latest result for each test identity and its underlying JUnit report. The original combined run was interrupted by the continuation turn; its partial log is retained and receives no completion credit.

| Check | Result | Retained evidence |
| --- | --- | --- |
| Backend intake, jobs and job API, including all 100 restart/fault cases | 151 passed | [Accounting log](../audit/2026-10-10/backend-accounting.log), [JUnit](../audit/2026-10-10/backend-accounting.xml) |
| Backend runtime, planner, transport, PlatformIO and tools | 149 passed, 1 skipped after transport fixture correction | [Runtime log](../audit/2026-10-10/backend-runtime.log), [final transport log](../audit/2026-10-10/backend-transport-final.log) |
| Backend verification, continuation, permissions and product API | 80 passed | [Verification log](../audit/2026-10-10/backend-verification.log), [JUnit](../audit/2026-10-10/backend-verification.xml) |
| Node model router | 49 passed | [Router log](../audit/2026-10-10/model-router-tests.log) |
| Frontend state contracts | 93 passed | [UI state log](../audit/2026-10-10/ui-state-tests.log) |
| Electron regression suite, with router/harness/Electron compilation | 44 passed | [Electron log](../audit/2026-10-10/electron-tests.log) |
| Production standalone browser | 9 passed | [Browser log](../audit/2026-10-09/production-renderer-remediation.log) |
| Frontend type checking and lint | Passed; lint required zero warnings | `npm run typecheck:frontend`, `npm run lint:frontend` |
| Next.js production build | Passed with `NODE_OPTIONS=--max-old-space-size=1536` | `npm run build:frontend` |
| Working-tree whitespace check | Passed | `git diff --check` |

Three child-process transport tests initially failed because their local HTTP servers returned SSE to buffered requests. Their fixtures now return complete JSON for generation/health and verify that wire requests do not enable streaming. All 13 child-process transport tests passed on the final run; streaming, retry, authentication and cancellation cases remain covered. The raw failed runs are retained. One existing tool-runtime symlink test was skipped because this Windows host could not create the link. Two dependency deprecation warnings from the FastAPI/Starlette test clients were non-failing.

The first frontend build attempt exhausted available memory; the bounded-heap retry completed. The browser test exercised the built standalone renderer and its real production response headers, including Monaco, duplicate Apply under delayed refresh, and separate risk-review attention. An installer was not built or installed during qualification.

Router fixtures cover real HTTP 200 with malformed stream output, true non-streaming wire requests, canonical tool history and bounded recovery. Backend regressions cover durable intake reopen/settlement/attachment, invalid atomic charges, build/dependency versus parent deadline and cancellation cleanup, dependency version/environment checks, fresh reads after ambiguous edits, exact citation splitting, resumed build freshness, and strict risk-review recovery with separately counted physical attempts.

## Isolated live qualification

`scripts/qualify-live-log-remediation.py` recovered the original requests and selected models from the historical state using a read-only connection. It used new audit workspaces and copied qualification state on continuation. Provider requests used the existing credential store without printing or modifying credentials. No requirement was removed to obtain a passing build.

| Case | Evidence | Outcome |
| --- | --- | --- |
| Simple temperature monitor, selected `nvidia/nemotron-3-super-120b-a12b` | [Completed generation and requirements](../audit/2026-10-09/live-log-implementation-simple-212223/result.json), [risk review and warm build](../audit/2026-10-09/live-log-implementation-simple-212703/result.json) | `AGENT_V3_COMPLETED`; fresh compilation and independent requirement review passed. Risk review approved in one physical attempt. Warm build passed in 60.156 seconds. Cumulative provider accounting: 40 attempts, 8,028 completed output tokens, zero unresolved output reservations. Generation and risk review consumed approximately 511.55 seconds against the qualification's 900-second job budget; warm compilation was a separate qualification check. |
| Full advanced environment monitor, selected Super model | [Initial generation](../audit/2026-10-09/live-log-implementation-advanced-211542/result.json), [continued draft](../audit/2026-10-09/live-log-implementation-advanced-213146/result.json) | Seven incomplete-header milestones were blocked before compiler execution. Dependency and compiler failures remained actionable. The continuation still failed compilation, including an `OledManager::display` member/name conflict, and stopped with `AGENT_TURN_LIMIT_EXCEEDED` at approximately 672.98 cumulative seconds. The saved multi-file draft remains available. Requirements, completion review and risk review have not passed for this case. |
| Selected `nvidia/nemotron-3-ultra-550b-a55b` transport probe | [Probe result](../audit/2026-10-09/live-log-implementation-ultra-212713/result.json) | Three HTTP-200 responses were normalized; a fourth request ended in `PROVIDER_TIMEOUT`. This probe does not qualify the complete advanced generation flow. The exact malformed upstream packet from the original live incident was unavailable; its category and buffered recovery are covered by deterministic HTTP fixtures. |

The advanced replay used a **standard qualification allocation of 900 seconds**, while the original advanced-profile run had 2,700 seconds. It demonstrates saved-draft continuation and accurate failure reporting; it does not demonstrate completion under the original advanced profile.

Earlier audit attempts remain intact: simple generation first exposed repeated citation-span errors, the next continuation exposed the need for a fresh build, and two review-postprocessing attempts encountered qualification-runner errors. The final simple result links back to the actual completed generation. These failed intermediate records were not overwritten. A separate 180-second Ultra attempt stopped before provider dispatch because the verification reserve exceeded that local allocation and is excluded from transport evidence.

Qualification-runner limitations: tool usage is recorded and bounded by the loop's cumulative `tool_execution_count` and checkpoints; the qualification job rows report `tool_calls_used: 0` because this direct runner does not install the orchestrator's tool-charge hook. The earlier Ultra probe executed four turns because the loop clamped extension turns to a minimum of ten. The runner now explicitly honors `--extensions 0` for future probes. Neither limitation changes the retained provider-attempt accounting or makes a failed case successful.

## Remaining qualification

1. Resume or regenerate the full advanced request under its original profile and repair the remaining C++ interfaces, then obtain a fresh build and all applicable independent reviews. A host readiness result cannot certify generated firmware semantics.
2. Repeat the Ultra transport flow when the selected upstream provider responds within its allotted deadline. The observed live timeout remains a provider-dependent outcome.
3. Qualify a packaged desktop installer and physical ESP32 behavior when those actions are requested. The current evidence covers production renderer behavior and local compilation/model review, with `hardware_verified: false` for every live case.

Application fixes are implemented. Complete advanced generation, reliable Ultra-provider live completion, installer qualification and physical hardware sign-off are not established by this report.
