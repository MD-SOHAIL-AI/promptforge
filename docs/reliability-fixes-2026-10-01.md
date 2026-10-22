# Implemented reliability fixes — 2026-10-01

This implements the recovery and verification changes identified in
[the complete review](reliability-review-and-fix-plan-2026-10-01.md).
Existing work in this checkout was preserved. This is a source implementation,
not a claim that an already installed executable has been upgraded.

## Changes

| Area | Implemented behavior |
| --- | --- |
| Continuation checkpoints | Every autonomous and durable staged attempt seals admission, including resumed attempts. An outage before the first settled turn therefore leaves a checkpoint owned by that attempt. Cumulative consumption and previous continuation metadata are retained. |
| Existing interrupted attempts | A continuation without its own checkpoint may use its verified parent seal only within the same session and job, with matching inherited checkpoint identity and no child tool, build, settlement or operation records. Workspace and stage revisions must still match. Unknown effects and corrupt journals remain blocked. |
| Recovery controls | Retry same model follows the available resume action. Unsupported recovery reports an error. Recovery submissions have an immediate in-flight guard. Continue task is disabled without eligible recovery, and budget exhaustion disables model continuation. |
| Explicit model selection | Resume accepts a selected provider and model, validates their configuration and route capabilities, and records explicit selection. The checkpoint, task, job and consumed budget remain attached to the replacement attempt. No automatic provider substitution was added. |
| Saved draft verification | A new authenticated local `POST /agent-runtime/runs/{run_id}/verify-draft` action creates an idempotent replacement and compiles the latest integrity-checked review draft without resolving or calling the model. The new attempt seals that source at admission. It requires a review draft, eligible recovery, project trust, tool allowance and remaining execution time. Exhausted model allowances do not prevent this local build. It neither repairs source nor applies or flashes changes. |
| Build evidence | `build_verified` is persisted and projected independently of model response validity and task completion. Successful local verification creates a warning draft, not a completed task. A timeout, overload or truncated final answer no longer erases fresh host build evidence. Requirement checks and hardware qualification remain separate. |
| Earlier compilation | Durable firmware generation checks settled source after three write turns, near the verification reserve, and after an outage or admission into the time reserve. Unchanged failed source is not repeatedly compiled by this host milestone. These builds remain bounded by trust, tool allowance and the job deadline. |
| Model deadlines | The planner uses its configured turn timeout within the remaining job deadline, leaving an explicit reserve: 120 seconds for firmware verification, 45 otherwise. It no longer arbitrarily divides remaining runtime by three. |
| Endpoint outages | A SQLite circuit keyed by provider, model, endpoint and credential identity coordinates cooldown across processes and restarts. Repeated logical failures, or an explicit server Retry-After, open the circuit. After cooldown one probe is admitted. Cancellation releases that probe. Request-level recovery does not retry an open circuit. Credentials, prompts and responses are not persisted in circuit records. |
| Backoff and admission | Durable retries use bounded jitter and honor longer Retry-After hints. Provider queue waiting is cancellable without an abandoned acquisition thread. Each turn clears its HTTP diagnostic fields so an earlier HTTP 200 is not displayed as the current failure response. Existing physical-attempt limits remain in force. |
| Test isolation | Test-process and per-test default roots, settings and model-router settings point to temporary storage. Default credentials use memory storage. This protects saved user tasks and provider credentials when API fixtures construct applications without supplying every storage dependency. |

Verification-only recovery temporarily uses build autonomy. A subsequent model
continuation restores the original autonomy; the local verification flag is
removed. Prior hardware approvals are invalidated when recovery starts.

## Validation

| Check | Result |
| --- | --- |
| Full isolated Python run | 2,088 passed, four Windows symlink skips, two dependency deprecation warnings; 380.99 seconds. |
| Focused recovery, verification, budget and repair run following the reserve adjustment | 156 passed, including timeout, overload, truncation and reserve-triggered host verification. |
| Verified draft presentation and truncation regression | Two passed. |
| Final circuit, provider admission and planner regressions | 27 passed, including immediate respect for Retry-After and restart-safe probe admission. |
| Final continuation and API regressions | Ten passed, including verification of a review draft newer than its checkpoint. |
| Frontend UI state | 71 passed. |
| Browser recovery flows | Eight passed in 28.7 seconds using a separately managed Next development server and mocked backend responses. |
| Electron tests and TypeScript build | 38 passed. |
| Harness tests and TypeScript build | 11 passed. |
| Release script tests | 26 passed. |
| Frontend TypeScript and lint | Passed; zero lint warnings allowed. |
| Production frontend build | Passed in the isolated `.next-reliability-build` directory. The final presentation condition was subsequently checked with TypeScript, lint and browser tests. |
| Patch whitespace check | Passed. |

The API regression verifies that draft compilation succeeds while model
resolution is deliberately unavailable, requires project trust, remains
isolated from the active workspace, preserves a review draft and replays the
same verification request without duplicating the build. Compiler results in
these automated regressions are controlled fixtures, not hardware qualification.

Browser checks cover explicit checkpoint resume, retry from a provider pause,
duplicate-click suppression, explicit model transfer, local verification,
preserved build evidence during an outage, exhausted model allowance and an
uncertain hardware operation. A screenshot was visually inspected at
`frontend/test-results/reliability-draft-verified.png`.

The first full implementation run found two outdated test assumptions: exact
retry delays and a manually constructed planner missing its new reserve field.
Those fixtures were corrected before the passing full run. Browser testing
found and corrected an incorrectly disabled verification button when a run has
no budget projection. Automatic browser-server teardown stalled on this Windows
host; using a separately managed test server produced complete passing reports.
The task's test server was stopped after verification.

The full run preceded the final focused boundary adjustments listed above.
The backend source fingerprint at that verification point was
`97f66f70c61d0ce46e175a1c1e4d905d670d41ed337e5f9d5c8a0e6d37a63d43`.
This identifies the entire current backend, including pre-existing checkout
changes; it has not been compared against an installed running process.

Later live-log findings, fixes and the updated fingerprint are recorded in
`live-log-fixes-2026-10-01.md`.

## Using the changes

For source development, fully close an older ForgeX instance and launch this
checkout with `npm run dev:desktop` from `D:\projects\ForgeX-Phase6-Final`.
An installed desktop copy needs to be rebuilt and installed from this source;
the isolated build above is validation output, not an installer.

For an unavailable model:

1. Review the saved draft and recovery details.
2. Use **Retry same model** or **Resume saved work** for eligible continuation.
3. To change models, choose the desired configured model and use
   **Continue with selected model**.
4. To check existing firmware without another model response, trust the project
   and use **Verify saved draft**. Extend tool or runtime allowance when needed.

Local compilation may still require PlatformIO toolchains or dependency
downloads. It does not depend on the model provider. A passing compile does not
prove all requested features, unit tests or physical device behavior.

## Remaining external qualification

No live NVIDIA request campaign, real new firmware compilation, physical flash,
signed installer build or installed-process fingerprint comparison was performed
in this implementation. The provider and hardware qualification gates described
in the review remain necessary before declaring a packaged release qualified.

ForgeX cannot prevent a remote NVIDIA endpoint from returning 503. These changes
make outages bounded and recoverable while preserving saved work and truthful
verification evidence. A genuinely unbuilt or failing draft will still be shown
as needing verification.
