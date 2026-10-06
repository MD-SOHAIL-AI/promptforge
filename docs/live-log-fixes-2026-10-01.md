# Live log review and fixes — 1 October 2026

## Evidence reviewed

Reviewed the desktop stdout/stderr captures under `.forgex/logs`, the live JSONL
diagnostics, and saved run/event/checkpoint projections using a read-only SQLite
connection. Desktop captures end at about 14:26 IST. Run IDs, process identity
and timestamps distinguish real desktop incidents from automated test events.

| Incident | Evidence and cause | Change |
| --- | --- | --- |
| Selecting another NVIDIA model still pauses after 30 seconds | Runs `agent-run-84b598bd69cb4b72a5a71e1d02b44f94`, `agent-run-63fd5d57b2514ff090a3ac1938366b6e`, `agent-run-0701dc6e5f48457b9d5d71dc3f39c429` and `agent-run-17bd6626fa8d4fb99b8acb6c376c5ba3` requested `nvidia/nemotron-3-super-120b-a12b`. A restored 550b run held the provider-wide local semaphore. The blocked requests had no physical provider request. | Admission queues now use model, endpoint and credential identity. NVIDIA remains limited to one turn per such identity. A FIFO queue prevents a running task from taking the next slot ahead of an existing waiter. Cancellation removes the waiter immediately. |
| Local queue exhaustion shown as NVIDIA endpoint unavailable | `PROVIDER_BUSY` was normalized to `PROVIDER_UNAVAILABLE`; failure origin became provider transport even with zero outbound requests. | Preserve `PROVIDER_BUSY`, report `local_model_queue` and ForgeX origin, and show “Waiting for another task.” Stop remains available while paused. |
| Resumed tasks cannot save a checkpoint | Desktop stderr contains `AGENT_CHECKPOINT_BOUNDARY_INVALID` for resumed runs including `agent-run-e4efd9f3cb8741efbe60d0f041346ade`. A `runtime.admitted` seal used an inherited nonzero reasoning turn, although the store requires admission turn zero. | Seal admission at turn zero of the new attempt and retain the inherited reasoning turn in `resume.turn`. Store validation remains strict. Repeated continuation from saved turn three is covered by regression tests. |
| Harness shadow events rejected | `harness.shadow_mirror.failed`, `RPC_IDEMPOTENCY_KEY_REQUIRED` on mutating session calls. The Python mirror omitted the key required by the host. | Supply a stable SHA-256 idempotency key derived from the method and canonical parameters. Replays reuse a key; distinct message IDs produce distinct keys. |
| Tool arguments fail after HTTP 200 and one unsuccessful repair | Run `agent-run-d632456cb4104c10acd9cb6e547b036c` ends with `TOOL_ARGUMENTS_INVALID`, native tool response shape, and failed repair. Native parsing replaced the actual validation reason with a generic schema error. The old logs do not retain the rejected arguments, so their exact defect cannot be established retrospectively. | Preserve the validation reason and recognized tool identity. Provide the repair request with the trusted schema, missing required fields, argument types and unexpected field count. Emit those facts without argument values, source paths or unknown argument names. Unknown tools retain their distinct classification. There is still only one correction request within the existing turn attempt budget. |
| Saved firmware is not verified when model planning fails | Host verification previously ran on selected transport/truncation failures but omitted a local busy queue or invalid tool plan. | Attempt the existing bounded local build milestone for those failures too. Trust, source revision, deadline and tool budget checks still apply. A successful build preserves verification evidence; a failed build remains unverified. |
| Development window crashes after a hook edit | React logs show changed hook order and “Should have a queue” during the Stop-button hot update. Fresh browser sessions do not reproduce the crash. | Mark the agent-session hook with `@refresh reset` so development edits remount its consumers. This is a development refresh safeguard; browser verification below used fresh sessions. |
| Test diagnostics mix with live diagnostics | Default test storage was isolated, but diagnostic emission still defaulted to the workspace live JSONL file. | Isolate the diagnostic path both before test imports and per test. Tests that inspect diagnostics can override their own path. Existing log history is retained. |

## Genuine upstream and firmware failures

The same desktop session also contains real NVIDIA HTTP 503 responses and
provider timeouts. These are distinct from the local queue defect. ForgeX's
bounded retry, circuit cooldown, saved work and explicit model recovery still
apply. An SDK replacement cannot guarantee endpoint availability.

The saved build for `agent-run-e4efd9f3cb8741efbe60d0f041346ade` failed. Its
PlatformIO configuration names missing `default_4MB.csv`, and
`src/DisplayManager.cpp` calls undeclared `priorityLevelToString`,
`temperatureStateToString`, `securityStateToString` and `lightStateToString`.
Macro redefinition warnings also appear. That particular generated firmware
needs source/configuration repair and a fresh successful compile. The runtime
fixes do not establish that this historical draft now compiles.

## Verification

| Check | Result |
| --- | --- |
| Orchestration, API cancellation, continuation, circuit, recovery and native planner regression run | 218 passed. |
| Verification freshness, agent reliability and repair run after adding the additional local build triggers | 44 passed. |
| Final focused backend run, including planner compatibility, admission, harness keys, nonzero continuation, verification and diagnostic isolation | 99 passed; two dependency deprecation warnings. |
| Browser regression run with mocked ForgeX APIs | Seven passed: correct local queue cause, upstream retry display, queued/running/paused cancellation, stale spinner removal and retrying a failed Stop request. |
| Final frontend TypeScript and lint checks | Passed. |
| Patch whitespace check | Passed. |

The counts above belong to separate overlapping test runs, not a sum of unique
tests. The earlier full-suite results are documented in
`reliability-fixes-2026-10-01.md`; the entire suite was not rerun for this follow-up.
Visually inspected `frontend/test-results/local-model-queue.png`. The isolated
Next test server was stopped after verification. No paid provider calls were
made by these automated checks, and saved runtime journals were read only.

Backend source fingerprint after this follow-up:
`38bcd66f08a9eda3fd0ac64b3c77d86989aa8b89980357059a885a6593be773f`.

## Loading the fixes

Launch this source checkout with `npm run dev:desktop` from
`D:\projects\ForgeX-Phase6-Final`. A previously running backend needs a full
restart. A packaged installed copy needs rebuilding to include these edits.
For an eligible saved run, use its resume/retry control; Stop is available for
active and model-paused tasks. **Verify saved draft** checks saved firmware
locally, and compiler failures remain visible until the source is repaired.
