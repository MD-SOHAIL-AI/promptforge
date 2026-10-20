# ForgeX reliability implementation — 14 October 2026

Status: runtime and renderer fixes implemented; successful generated-firmware qualification remains incomplete.

This change strengthens ForgeX's runtime, recovery and renderer. Generated firmware repair remains ForgeX's responsibility. Historical projects and generated drafts were not manually repaired, substituted or applied. All 9,515 protected generated files match their hashes from before implementation.

## Implemented plan

| Step | Problem | Result |
| --- | --- | --- |
| 1. Preserve work and establish evidence | The checkout already contained extensive changes, and the historical firmware was unverified. | Saved the existing diff, source hashes and a SQLite backup before editing. Reviewed the recent desktop and backend logs, and reproduced the inconsistent permission-block response. |
| 2. Publish coherent durable state | A terminal run could appear before its job's blocking reason was committed. Frequent synchronous progress persistence delayed API responses. | Run/job/event writes commit together. Public run reads use a committed snapshot. Observer notifications follow successful commit; rollback does not publish an event. High-frequency autonomous progress commits and projections run off the event loop using detached state, with cancellation draining the worker before cleanup. |
| 3. Diagnose local worker exits accurately | A closed worker pipe could surface as a raw transport exception. | Preserve `MODEL_WORKER_FAILED`, worker exit code, spawn/input/response phase, and a bounded failure category. Drain stderr privately and export only recognized categories; credentials and project contents are not copied into diagnostics. Explicit operator Node settings remain respected. |
| 4. Make continuation usable | Direct API tasks lacked their generated session binding. Resumed jobs retained the old budget-pause flag. Verification reserves were based on the original time allowance. | Bind anonymous contracts to the durable generated session, including eligible legacy direct runs. Keep foreign project/workspace/session rejection. Clear the pause flag when an authorized replacement attaches, retain cumulative accounting, and calculate verification reserves from remaining time without extending the deadline. |
| 5. Recover the local renderer session | Authentication loss could cause continued polling and stream reconnection. Reserve pauses were described as complete budget exhaustion. Startup could replace the draft key after typing began. | Suspend requests, polling, diagnostics HTTP delivery and streams after 401. An explicit authenticated Reconnect restores the session and reloads saved drafts. Await saved-conversation restoration before enabling the composer. Show verification allowance separately from exhausted budget. Active conversation deletion still requires stopping its run. |
| 6. Keep verification practical | The recovery tests were absent from the beta gate, and low-memory build/test workers were unreliable. | Add read/auth recovery to beta verification. Pass bounded Node options to the browser runner. Support `FORGEX_BUILD_CPUS=1` for constrained builds, retaining the existing default. Correct Python setup guidance to the documented 3.13 runtime. |

The user's excluded phase—manually fixing ForgeX-generated firmware—was not implemented. Qualification workspaces begin empty, and only ForgeX writes their firmware through its normal tools.

## Live evidence

The initial Smart Monitor replay used the original request, NVIDIA NIM model `nvidia/nemotron-3-super-120b-a12b`, and the standard job profile over real HTTP. ForgeX generated modules, corrected its own configuration and attempted real PlatformIO builds. It stopped at `AGENT_VERIFICATION_RESERVE_REACHED` with an unverified saved draft. All 530 health observations succeeded; the 95th-percentile latency was 76 ms. This is responsiveness evidence, not successful firmware qualification.

The saved-run replay exposed the session-binding, remaining-time reserve and retained-pause defects fixed in step 4. Regression tests now cover named sessions, generated sessions and legacy generated sessions across restart, concurrent idempotent resume, and actual provider-budget reservation/settlement after a reserve pause.

The latest continuation admitted and made additional real provider requests, but the host subsequently exhausted Windows commit memory. Python allocation failures and a failed cancellation request interrupted qualification. The original 900-second job allowance was not reset or silently enlarged. The complex request still has no verified successful final build, requirement review or hardware result. The advanced environment-monitor and knock-detector requests are also not qualified by this implementation.

After available memory recovered, a final fresh Smart Monitor replay ran alone with the completed runtime fixes. It ended at the verification reserve with an unverified saved draft. All 799 health samples succeeded, with an 80 ms 95th-percentile latency; no provider attempt or local model worker failed. ForgeX attempted and repaired generated source through its own tools, but compiler errors remained. Evidence is in `live-smart-monitor-final/result.json`, `run.json` and `summary.json`. Runtime responsiveness and recovery are established; successful completion of this generated firmware request is still not established.

## Verification evidence

Evidence is stored under `.forgex/reliability-20261014/`, outside the historical project directories.

- The original baseline had 2,575 Python passes, four skips and one reproducible permission-publication failure. The affected recovery group subsequently passed 144 checks.
- The first complete post-change Python run had 2,578 passes, four skips and two short-deadline failures while heavy checks ran concurrently. The ten provider deadline checks passed unchanged when rerun separately. Later allocation-failed checks are retained in the evidence; they do not count as passes.
- The final serial Python run passed 2,594 tests and skipped four unavailable directory-symlink cases. Two header-before-deadline tests repeated their machine-dependent startup failure. Those tests now use a controlled worker to establish the required ordering, preserving the timeout, no-retry, cleanup and expired-request assertions; the ten deadline checks pass. All 2,596 non-skipped tests have passing final coverage across the serial run and targeted rerun. A fresh full-suite invocation after that fixture change was not performed.
- Frontend state: 104 passed. Model router: 49 passed. Harness: 12 passed. Electron: 45 passed. Release scripts: 35 passed. Read/auth recovery and request diagnostics passed.
- A production frontend build passed with `NODE_OPTIONS=--max-old-space-size=512 --max-semi-space-size=1` and `FORGEX_BUILD_CPUS=1`.
- The final source also passed a fresh production build, its TypeScript checks, and standalone ESLint with zero warnings. Frontend state and read/auth recovery were rerun after the last renderer fix: 104 and five passes respectively.
- Eleven production renderer checks passed, covering CSP, offline editor loading, saved-draft controls, permission updates, automatic replacement and authentication reconnect/draft preservation. The final reserve UI adds another browser regression.
- The final built production renderer passed the complete 65-test browser suite in 4.1 minutes. This includes the corrected composer startup/validation behavior, both authentication-loss cases, verification-reserve controls, conversation and chat reconciliation, build evidence, settings, keyboard use, responsive layout and CSP/offline editor checks. See `production-browser-final.log`.
- Six standalone production proxy checks passed: backend identity, shared slow health observation, single mutation dispatch, incremental SSE, identity mismatch and unavailable-backend errors.
- A broader browser attempt passed 42 checks before the memory-constrained run stalled; one browser target crashed. This attempt does not establish a passing full browser suite.
- A later production-browser attempt passed 37 checks, exposed the composer restoration race, and then stalled during chat observation testing. The restoration ordering was corrected in source and covered by a held-response regression. This attempt also does not establish a passing full browser suite.

The interrupted browser attempts above were followed by the passing complete production run. All qualification processes owned by these runners were closed. Build-generated `frontend/next-env.d.ts` was restored to its exact original bytes, including the pre-existing workspace reference.
- `firmware-preservation.json` records zero changed or missing files among 9,515 protected files. `git diff --check` passed.

## Practical limits

Source and deterministic regressions establish the runtime fixes. Full firmware correctness requires ForgeX to finish generation, compile the final source revision and pass the independent requirement checks in a successful live run. Device behavior requires actual hardware evidence. Neither is claimed here.

The changes preserve the repository's existing work and use real provider/compiler paths for live qualification. Browser transport fixtures and deterministic planners are confined to tests; no placeholder runtime success paths were added.
