# ForgeX firmware repair incident — 2026-09-28

This report uses the persisted orchestration database and a build replay of a **temporary copy** of the saved stage. The original run and its recovery draft were not changed. The source checkout used for the fixes was at `0f293c540fcede4f2d3e95e74cc80b5c15f0faee`; the exact application revision that produced the incident was not recorded in the run.

## What happened

| UTC time | Persisted evidence |
| --- | --- |
| 06:33:11 | Run `agent-run-49756ffd80624399825f6feaf857e3af` started under job `job-dc56c9ebfef545acab9c861b1859c777`. |
| 06:43:29–06:44:38 | First ESP32 PlatformIO build failed with compiler errors. |
| 06:44:38 | One deterministic repair inserted `Arduino.h` into `src/main.cpp`. |
| 06:44:38–06:44:52 | Rebuild failed. The same core errors remained: `SystemStateManager` unavailable in `DisplayManager.h`, `alarm` colliding with the C function, and calls to local helpers before declarations. |
| 06:45:05–06:46:44 | Unchanged-source build requests reused the failed-build cache; later turns read source files without producing another successful build. |
| 06:47:15 | Last durable checkpoint recorded turn 46, 842.954 seconds elapsed, 46 provider requests, and 7,405,587 input bytes. |
| 06:49:07 | Turn 48 failed with `AGENT_PROVIDER_BUDGET_EXCEEDED`. The run was saved as an unverified draft. |

The job ultimately recorded 955.64 elapsed seconds against 900 allowed, 48 of 120 requests, 196,608 of 245,760 reserved output tokens, 7,870,595 of 8,000,000 input bytes, and 46 of 100 tool calls. Input size is the most plausible provider budget dimension because only 129,405 bytes remained, but the old error record did not capture the rejected request's charge. It cannot prove which dimension caused that specific rejection. The 900-second overrun is independently confirmed by the job counters.

The immediate build failure came from generated C++ source. The `Arduino.h` insertion was valid for one compiler complaint but did not address the remaining errors. The repair loop then spent additional provider turns reading and revisiting the failed build. This is the concrete lack of repair progress in this run.

## Replay result

An untouched temporary copy reproduced the compiler failure with `pio run -e esp32dev`. The new compiler-evidence repairs changed only `src/main.cpp` and `include/DisplayManager.h` in that copy: renamed the conflicting `alarm` object, included the sole header defining `SystemStateManager`, and declared zero-argument helpers before their calls. A second PlatformIO build **succeeded** and produced `firmware.bin` (314,089 flash bytes; 22,264 RAM bytes). The remaining DHT debug macro redefinition warnings were nonfatal. No hardware flash or runtime device validation was performed.

## Product fixes and limits

- Provider-budget failures now carry dimension, consumed, limit, attempted charge, and origin. Budget-store failures have a distinct code. Elapsed and tool-call failures carry corresponding budget evidence.
- Provider calls, tools, dependency validation, and builds use the shared task deadline, with five seconds reserved for settlement. Build cancellation retains an unverified result.
- Repair progress uses stable compiler diagnostics; source/diagnostic cycles and six tool turns without a new build stop with `BUILD_REPAIR_EXHAUSTED`. A bounded repair history survives checkpoints and appears on the run.
- Reopening a settled agent run no longer opens a run stream or re-fetches the session list. Chat lifecycle events reconcile in a 150 ms window; stale responses are rejected after selection changes.
- The saved incident draft remains untouched. A new run through the updated code can apply the verified repair strategy; this report does not label the original draft as built or verified.

The fixture at `tests/fixtures/repair-budget-incident.json` contains selected nonsecret diagnostics and counters for regression tests. The original provider rejection charge and incident application revision remain unavailable.

## Validation

At source revision `0f293c540fcede4f2d3e95e74cc80b5c15f0faee` plus the uncommitted reliability fixes, 239 targeted and adjacent Python tests passed in the broad run; a final focused rerun passed 59 tests. Frontend state tests passed 71 cases, TypeScript type checking and lint passed, and all 18 browser conversation tests passed. Python compilation and `git diff --check` passed. The isolated `esp32dev` PlatformIO replay passed after repair. These results validate the new behavior in this checkout, not the original terminated run or physical hardware.
