# PlatformIO dependency failure repair — 2026-10-07

The latest room-monitor run failed in dependency preflight, before compilation.
PlatformIO's ArduinoJson search printed a Unicode star to a Windows cp1252 pipe,
raising `UnicodeEncodeError`. ForgeX discarded that traceback and presented a
registry outage. The original run remains recorded as failed.

## ForgeX changes

- Managed Python tool processes now default to UTF-8 output and UTF-8 mode.
  Existing explicit environment overrides and the secret-filtering allowlist remain in effect.
- Dependency failures retain bounded, redacted diagnostics, the requested library,
  exit code, timeout state, and a concrete error code. Encoding, command, timeout,
  and network failures have separate classifications and retry behavior.
- A deterministic local preflight failure stops build repair and host milestone
  processing before another model request. Public run summaries show the concrete
  dependency error instead of generic runtime advice.
- Planner instructions and the built-in PlatformIO repair skill require verified
  owner/name library IDs, omission of framework libraries, and use of actual diagnostics.

These changes do not alter provider API rate limits, prices, or model selection.

## Firmware project

The original recovery draft was materialized into a separate repair stage, leaving
its contents and saved manifest intact. The repaired dependency list uses exact
registry IDs and verified versions for six external libraries. Preferences is
provided by Arduino-ESP32; the unused, unverified PWM controller dependency was removed.

Registry preflight passed for all six declared dependencies. Preflight verifies
identity; the actual PlatformIO installer verifies the pinned versions.

The first real build check reached ESP32 framework installation and exceeded its
240-second deadline before compilation. Its verification script then encountered
an incorrect result serialization method; that script was corrected to use
`api_dict()`. No successful build is attributed to that first attempt.

The real compiler then exposed four source defects: a malformed `setup()`
declaration, missing helper declarations, assignment to macro constants, and
invalid conditional expressions combining `nullptr` with floats. The repair
initializes the alert flag, declares the helpers, makes configurable thresholds
mutable, and serializes missing readings as JSON null. Deprecated JSON document
constructors were also updated for the verified ArduinoJson version.

The repaired stage built successfully in 104.52 seconds. Its three files were
published through an atomic, undoable ChangeSet:
`changeset-4f9f0bfe7c96477d8f87a486f5804082`. The active project at
`D:\projects\ESP32-Room-Monitor` then built successfully in 123.56 seconds
(127 seconds including service checks), with zero compiler warnings. Usage:
44,556/327,680 bytes RAM and 851,309/1,310,720 bytes program flash.

Firmware: `D:\projects\ESP32-Room-Monitor\.pio\build\esp32dev\firmware.bin`.
Four upload images were sealed and their hashes verified in ForgeX's managed
artifact store. See `build-3.json`, `build-4.json`, `applied-repair.json`, and
`firmware-repair.diff`. Original draft file hashes were checked against the saved
manifest. The historical failed run remains failed; no model-run success was fabricated.
No board was flashed, and sensor/dashboard behavior on physical hardware was not tested.

## Additional startup provider-list repair

The first restart logged a separate 15-second desktop timeout for
`/agent-runtime/providers`. A local profile of that same registry operation
showed 2,288 SQLite connection opens/closes even inside its cached read snapshot.
Each cached getter unnecessarily entered a write transaction. This created
avoidable database contention when multiple lists ran during startup.

Cached snapshot reads now bypass the database transaction lock. Writes still
acquire the original transaction lock and read fresh state; legacy credential
migration also retains its protected write path. A regression test checks that
the complete provider list uses one database connection. Existing tests cover
independent concurrent writers and writes inside a snapshot without lost updates.

The local 229-provider listing decreased from 6.784 seconds to 0.486 seconds.
This measures local metadata listing, not model inference latency or provider SLAs.
See `provider-list-profile.log` and `provider-list-after.json`.

The final launch check compares the live backend's source fingerprint with the
updated source, requires PlatformIO readiness, checks frontend HTTP 200, and
requires a successful desktop `/agent-runtime/providers` response. Results and
that request's measured duration are saved in `final-verification.json`.

Final verification passed: the updated backend is healthy, PlatformIO 6.2.0 is
ready, the frontend returns HTTP 200, and the visible ForgeX desktop opened.
Its actual provider-list request returned HTTP 200 in 2,670 ms during startup,
within the unchanged 15-second renderer read deadline. The authenticated local
API remains required. The loaded source fingerprint matches the current source.

## Regression checks

These test runs overlap and must not be added together:

| Check | Result | Evidence |
| --- | --- | --- |
| PlatformIO, repair, planner, subprocess ownership, recovery | 193 passed | `python-tests.log` |
| Agent jobs, host milestones, freshness, tool runtime | 191 passed, 1 skipped | `agent-verification-tests.log` |
| Final dependency diagnostics and public summary changes | 37 passed | `final-focused-tests.log` |
| Product run API and build operations API | 32 passed | `api-tests.log` |
| Provider storage, concurrent writes, router, live-failure and API regressions | 53 passed | `provider-storage-tests.log` |

The skipped test requires symlink creation unavailable on this Windows host.
The API suites emitted existing Starlette/httpx/anyio deprecation warnings.
`git diff --check` passed. Actual subprocess coverage printed a Unicode star
successfully under a cp1252 host setting and confirmed the local API token did
not enter the child environment. Normal service registry searches also succeeded.

## Reference checks

- [Python output encoding](https://docs.python.org/3.10/using/cmdline.html#envvar-PYTHONIOENCODING)
- [PlatformIO registry search](https://docs.platformio.org/en/latest/core/userguide/pkg/cmd_search.html)
- [PlatformIO dependency declarations](https://docs.platformio.org/en/latest/projectconf/sections/env/options/library/lib_deps.html)
- [Preferences in Arduino-ESP32](https://docs.espressif.com/projects/arduino-esp32/en/latest/api/preferences.html)
- [Maintained ESPAsyncWebServer](https://github.com/ESP32Async/ESPAsyncWebServer)

Exact library versions were also checked with the live PlatformIO `pkg show` command.
