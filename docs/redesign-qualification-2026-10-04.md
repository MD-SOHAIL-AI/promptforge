# ForgeX candidate qualification — 4 October 2026

This matrix applies to the source candidate in `D:/projects/ForgeX-Phase6-Final`. The release Python target is 3.13.15; local regression execution used Python 3.10.6 and Node 24.14.0. Local passing tests do not qualify the installed app or a future packaged binary.

| Gate | Candidate result | Evidence |
| --- | --- | --- |
| Full Python suite | 2,218 passed; 4 host-dependent symlink tests skipped; 2 dependency deprecation warnings | `.forgex/redesign-validation/python-qualified.log` |
| Frontend state/transport tests | 79 passed | `ui-state-qualified.log` |
| Full browser scenarios | 52 passed | `browser-qualified-final.log` |
| Frontend TypeScript | Passed | `frontend-types-final.log` |
| Frontend lint | Passed, no warnings | `frontend-lint-final.log` |
| Optimized production renderer build | Passed | `frontend-build-qualified.log` |
| Electron compile and tests | 43 passed | `electron-final.log` |
| Harness tests | 11 passed | `harness.log` |
| Release script tests | 35 passed | `release-scripts.log` |
| Local projection benchmark | Median 10.668 ms, 6.48× versus full replay, 1,000 events/20 reads | `projection-benchmark.json`, reproducible benchmark script |
| Live NVIDIA/alternative-provider task completion | Not run for this candidate | Existing qualification prompts/tools must run against exact packaged identity |
| Real ESP32 compiler/artifact gate | Not run for this candidate | `scripts/smoke-esp32-build.py`, build/upload manifest gate |
| Physical upload, unplug/reconnect, serial contention | Not run | Requires selected connected device and explicit artifact/device approval |
| Release Python/runtime packaging, signing and clean Windows install | Not run | Release preparation, packaged smoke, installer signature and release evidence gates |
| Complete accessibility certification | Not claimed | Browser keyboard/narrow-view/theme tests and inspected screenshots cover a limited scope |

An earlier broad implementation run had 2,212 passing tests and one generator failure. The generator was fixed to create its output directories and its regression passed afterward. It is retained in `python-final.log` for traceability. Use the final candidate logs above for the outcome, rather than the pre-implementation review baseline or an interrupted browser run.

The remaining external/release gates block a production-qualified claim. They do not prevent local source development or deterministic validation. Source edits do not update a previously installed desktop binary automatically.
