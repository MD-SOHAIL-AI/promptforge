# Live session fixes — implementation and verification

The 10 October run combined invalid model-generated dependencies and mismatched C++ interfaces with ForgeX recovery and time-allocation problems. ForgeX now validates configuration earlier, keeps repair evidence in context, blocks unrelated source changes while dependencies remain unresolved, and identifies the verification reserve separately from a provider timeout.

The implementation follows [the structured plan](live-session-fix-plan-2026-10-10.md). Existing unrelated working-tree changes were preserved.

## 1. Generation and verification time

`PhaseBudget.generation_reserve_for()` caps the advance verification reservation at one third of the task budget. For the observed cold 900-second job, the reservation changes from 480 seconds to 300 seconds. Actual dependency, build and review phase limits remain bounded by the cumulative deadline; the persistence allowance remains intact.

The router refuses a new generation request when it has less than 30 seconds of usable headroom, or less than its configured turn allowance when that allowance is shorter. A request stopped at the reserve boundary reports `AGENT_VERIFICATION_RESERVE_REACHED`. Actual local turn timeouts and exhausted whole-job deadlines retain their own classifications. The autonomous loop can collect fresh host verification for the saved draft after this boundary.

Affected implementation: `backend/agent_runtime/engine.py`, `router_planner.py`, `autonomous_loop.py`, `failure_contract.py` and `run_reporting.py`.

## 2. Dependency repair before further generation

Dependency validation runs before the missing-local-header check and immediately after a changed `platformio.ini`, within the scheduler's mutation barrier. The next source write in the same model response therefore sees the validation result.

The validator diagnoses the observed bare framework entries with a concrete removal instruction and header name. External packages still require an unambiguous registry identity, valid version and compatible selected environment. Registry outages retain their existing transport classification and retry policy.

Failed validation records a fingerprint of the configuration that was inspected. That evidence is pinned during context compaction and restored on continuation. While the configuration matches the unresolved fingerprint, source mutations outside `platformio.ini` are rejected. Three ignored repair attempts stop with `BUILD_REPAIR_EXHAUSTED`. Repairing the configuration revalidates it before further source writes. Older dependency checkpoints without a fingerprint collect fresh evidence before their first generation request. Host preflight work is charged to the existing tool budget.

Affected implementation: `backend/services/platformio_service.py`, `backend/agent_runtime/build_recovery.py` and `autonomous_loop.py`.

## 3. Paired C++ interfaces

The readiness scan now records advisory class-name and method-name inconsistencies between paired headers and implementations. Context compaction retains the findings, paired paths and available current declarations. The compiler still owns full signature/type validation and successful compilation.

A read-only replay of the original 30-file checkpoint detects the `OLEDManager` / `OledManager` mismatch and `TemperatureSensor::isValid` mismatch, along with other advisory findings. Include closure alone never qualifies this draft as built. The replay evidence is saved in [historical-interface-replay.json](../audit/2026-10-10/live-session-fix/historical-interface-replay.json).

Affected implementation: `backend/agent_runtime/build_readiness.py` and `autonomous_loop.py`.

## 4. Backend proxy and lifecycle evidence

The Node.js middleware identifies the configured loopback backend through `/health` before forwarding API requests. Unavailable and unrelated listeners return `BACKEND_UNAVAILABLE` (503) and `BACKEND_IDENTITY_MISMATCH` (502), using the existing API error shape so the renderer displays the concrete message.

The proxy forwards a mutation once and preserves its method, query, body, authentication and request ID. A connection failure after dispatch reports `REQUEST_RESULT_UNKNOWN`; it never replays the mutation. Responses remain streamed, and the response-header timeout is cleared before consuming an SSE body.

The desktop supervisor writes structured spawn, exit, process-error, failed-health, scheduled-restart and exhausted-recovery events. Each spawned process has an instance identifier. Expected termination is recorded even after process ownership has been cleared.

Affected implementation: `frontend/lib/backend-proxy.ts`, `frontend/middleware.ts` and `electron/backend-manager.ts`. The Node.js middleware runtime is supported by the installed Next.js 15.5.26 version; see the [Next.js 15 middleware documentation](https://nextjs.org/docs/15/app/api-reference/file-conventions/middleware).

## Verification

Evidence is stored in `audit/2026-10-10/live-session-fix/`.

| Check | Result |
| --- | --- |
| Final wider backend regressions | 281 passed |
| Latest recovery/dependency group | 65 passed, including 13 incident regressions |
| Frontend state and API/proxy regressions | 97 passed |
| Electron tests and TypeScript builds | 45 passed, including the launch-time startup allowance regression |
| Production frontend build, lint and type checks | Passed |
| Built standalone proxy over real HTTP | 5 checks passed |
| `git diff --check` | Passed |

The production qualification uses only temporary, owned loopback servers. It verifies identity, a single authenticated POST with its original body/query, incremental SSE, rejection of an unrelated listener before mutation, and a structured unavailable response after backend shutdown. It can be repeated with `node scripts/qualify-live-session-proxy.mjs` after building the frontend.

The provider timing tests now call the streaming API for their SSE fixtures. The deadline regression expects the explicit reserve classification. The continuation-history test checks the immediate recorded parent revision while retaining the original revision in history. These updates preserve their behavioral assertions against the current runtime contracts.

Initial validation hit Windows memory pressure, including a `MemoryError` in a temporary-state test and an out-of-memory build worker. The successful production build used `NODE_OPTIONS="--max-old-space-size=1024 --max-semi-space-size=1"`; application configuration was not changed to skip checks.

During the requested desktop launch, the backend twice exceeded the existing 60-second readiness window before completing startup. `FORGEX_BACKEND_STARTUP_TIMEOUT_MS` now supports an explicit launch allowance between 1 and 300 seconds. The default remains 60 seconds, and constructor overrides still take priority. This launch uses 180 seconds. The cold-start regression models a healthy backend becoming ready after one minute; all 45 Electron tests pass in `launch-electron-tests.log`.

## Qualification limits

- The full original 67-requirement request has not been rerun against the live NVIDIA model. These checks establish runtime recovery and transport behavior; they do not guarantee that the model will produce complete, correct firmware within 900 seconds.
- The original checkpoint remains an unverified draft. Its files and the user's target project were not repaired or applied, and no hardware was flashed.
- The historical logs do not establish why the backend later stopped serving its API. The new identity checks and lifecycle events improve detection and evidence for future failures; they do not prove a historical crash cause.
