# October runtime repair implementation

Date: 2026-10-17. Scope: remaining agent-runtime failures in the retained October logs. Implementation was authorized after the initial review. Earlier fixes, frontend features and copy, selected models, execution budgets, and historical generated firmware are preserved.

The targeted runtime changes are implemented. The complete backend suite passes: **2,617 passed, 4 skipped**, with no failures or errors. The final Python 3.13.15 package passes its smoke test, and all five changed runtime assets match the tested source and release manifest. The latest live continuation corrected its blocked configuration and reached real compilation; generated C++ remains inconsistent, so production firmware qualification is not complete.

The review covered 117 retained log files containing 77,063 October events, plus a read-only inventory of 74 October runs across 48 jobs. Logs include test-fixture activity, so event totals are not production failure counts. The incident ledger ties findings to saved runs and separately records the new isolated qualification attempts.

## Cause and resulting behavior

The latest historical failure combined invalid PlatformIO library versions with ineffective model repair. Registry validation correctly rejected the versions. However, native follow-up requests omitted host observations without a provider call ID, including current dependency and interface repair guidance. Instructions also mixed canonical ToolPlan names with native function aliases, and recommended an unregistered dependency function. An unknown-function response therefore did not reliably receive an actionable correction.

A separate failure family involved generated C++ callers, headers, and implementations disagreeing. A previous Smart Monitor replay had 62 completed provider attempts and 13 unsuccessful compiles. Provider availability alone did not explain that failure. The runtime now carries bounded, current source evidence alongside actual compiler errors so the model can repair the declared interfaces.

| Confirmed issue | Implemented behavior |
| --- | --- |
| Host repair context disappeared after the first native turn | Send a refreshed, bounded host-context message on each turn; preserve provider tool-call/result IDs and replace stale host snapshots. |
| Native instructions used inconsistent function names | Derive native names from the same tool registry used for advertised schemas; preserve compatibility ToolPlan names. |
| Dependency guidance recommended `search_libraries` | Recommend the registered `resolve_dependency` function in the planner and built-in PlatformIO repair instructions. |
| Unknown-function correction lacked the actual tool list | Give one bounded correction using the functions advertised for that turn, including the fact that writes create parent directories. |
| An unavailable shell call was treated as malformed arguments | Check tool availability before argument schemas; retain policy restrictions and avoid suggesting how to call a forbidden tool. |
| Configuration blockers still advertised unrelated capabilities | Advertise only the intersection of the existing policy and configuration-repair functions, then restore the profile after the blocker clears. |
| Policy-blocked INI settings bypassed configuration repair | Apply the existing static build policy to the real inspector's parsed INI before generation. Fingerprint the rejected configuration and route it through the existing INI-only mutation barrier; clear the advisory only after revalidation. Build execution still rejects blocked settings. |
| Compiler repair lost caller/declaration evidence | Pin real compiler errors with fresh contained caller/header/implementation previews, hashes, and stale-evidence markers through context compaction. |
| Unqualified variable diagnostics omitted the paired header | Pair header and implementation by the diagnosed source filename as well as class names in compiler messages. The live undeclared-field case failed its new regression before this correction and now passes. |
| Shared type names differed from filenames, and short previews hid members | Reuse the current declaration inventory to find actual struct, class, and enum definitions, center their previews on those definitions, and include bounded header members. The actual `SystemStates` definition and `AlarmManager` pattern field now reach native requests. |
| Native text promising a repair was interpreted as final | For unchanged source with known, nonterminal compiler errors, allow at most two consecutive corrections requesting a real tool action before stopping. New tool evidence resets this correction count; existing progress and repair guards still apply. Avoid recompiling identical failed source; retain normal turn, tool, provider, and elapsed limits. Changed source still requires a fresh build. |
| The hosted model repeatedly chose prose instead of calling a function | A live read-only protocol probe confirmed `tool_choice=required` works for this exact hosted model. Use it only on the immediate correction turn for NVIDIA NIM's Nemotron 3 Super at the verified canonical endpoint; other turns, models, custom endpoints, and review flows keep their existing behavior. Allowed tools and policy checks are preserved. |

The raw unknown function name was not retained in the historical incident. The `search_libraries` mismatch is confirmed in the instructions; attributing that particular historical response to that name would be speculation.

The latest standard replay generated `-Iinclude`, which the existing build policy rejects. PlatformIO already adds its configured `include_dir` to the compiler include path, making that generated flag redundant ([official PlatformIO documentation](https://docs.platformio.org/en/latest/projectconf/sections/platformio/options/directory/include_dir.html)). Earlier continuations edited C++ instead of correcting the INI because local inspection did not run the policy. The repair guidance now explains the safe correction without granting trust or relaxing the policy. A regression using the real `PlatformIOService.inspect_configuration` reproduced the missing routing before the fix; the focused configuration/native/security set now passes 79 cases.

## Scope of source changes

- `backend/agent_runtime/api_planner_provider.py`
- `backend/agent_runtime/router_planner.py`
- `backend/agent_runtime/autonomous_loop.py`
- `backend/agent_runtime/build_recovery.py`
- `backend/agent_runtime/builtin_skills/platformio-repair/SKILL.md`
- `tests/unit/test_october_unresolved_runtime.py`

Existing dependency validation, configuration mutation barriers, build freshness checks, continuation ownership, provider recovery, requirement evidence review, authentication, and renderer recovery remain in place. Compiler previews are model repair context; they do not count as successful build or requirement evidence. No user firmware was manually repaired.

## Verification

The final source passed the complete backend suite on Python 3.13.15: **2,617 passed**, **4 skipped**, **0 failures**, and **0 errors**, in about ten minutes. This includes unit, API, integration, and benchmark tests. The four skips concern Windows symlink permissions. Two existing deprecation warnings were recorded. The final configuration/native/security subset passed 79 cases.

All **21 targeted regression cases** pass. They cover native context delivery and refresh, tool naming and bounded correction, forbidden-tool validation order, configuration-phase transitions and real-inspector policy rejection, current compiler evidence and declaration-based header pairing, path containment, progress through inspections into an edit, restored correction counts, fresh builds after source changes, and the scope of required tool choice. The context-loss, tool-name, unknown-tool, actionless-text, unqualified-diagnostic, shared-type, and policy-routing cases were reproduced before their fixes. `typed-compiler-context.json` additionally confirms that the actual failing checkpoint's current type definitions reach native messages while saved firmware remains unchanged; its separate storage-isolation exception is recorded below.

An offline replay of the saved Smart Monitor checkpoint confirmed that compiler errors and the current caller/header/implementation contents reach the native provider request without changing the saved firmware. This is a provider-boundary regression check, not a successful live compile.

Real registry queries reproduced the rejected historical pins for Adafruit GFX 1.11.4, SSD1306 2.5.8, and DHT sensor library 1.4.3. Dependency validation was retained rather than weakened.

All **9,417** historical firmware file hashes match the pre-implementation baseline. The original database still contains 123 runs, 90 jobs, and 86 checkpoints; job and checkpoint rows are unchanged. The final audit detected a session side effect: the offline checkpoint probe imported test helpers outside pytest's storage isolation, causing import-time application construction to refresh 33 saved run records at 06:19 UTC. Differences are limited to run `updated_at`, embedded job `updated_at`, and embedded job `blocking_reason`; run identity/status, firmware, budgets, stored job rows, and checkpoints are unchanged. The original snapshot remains intact. No manual database restoration was attempted. Exact affected records and fields are in `preservation-check-final.json`. Subsequent tests and live/package checks use isolated storage.

A task-only patch was reconstructed against the already-dirty checkout, checked against Git content hashes, and passed a reverse-application check without being applied.

Local evidence is in `.forgex/october-unresolved-20261017/`: `validation-summary.json`, `backend-complete-final-python313.xml`, `policy-real-inspection-focused.xml`, `typed-compiler-context.json`, `historical-compiler-context.json`, `registry-specifications.json`, `preservation-check-final.json`, `implementation.patch`, and `review-provenance.json`. The tested development fingerprint is `2de5522b13afebed21fd1579e0b51aed397288377442edbc4fe9bc13b5bb1e96`.

## Live and packaged qualification

The October 15 request was replayed in an isolated data profile using its original NVIDIA NIM model, advanced profile, 2,700-second cumulative active budget, and normal continuation API. Historical projects remained read-only inputs. Only ForgeX wrote the generated firmware. Interim runs exposed additional contract gaps, which were fixed before the final test gate.

| Replay stage | Observed outcome |
| --- | --- |
| Initial attempt | Provider first-event timeout; draft and budget retained. |
| Early continuation | Exposed unavailable-tool argument validation; fixed availability checking before schema guidance. |
| Configuration and source generation | Nine provider requests completed, configuration repaired, real compilation reached; later stream-idle timeout. Partial tool output was not executed. |
| Text-only repair promise | Unchanged source was rebuilt and rejected; led to the bounded actionless-text correction. |
| Bounded correction | Three complete prose responses, zero actions, no repeated compile, truthful `BUILD_ERROR` stop. |
| Longer repair continuation | 41 completed provider requests, zero provider failures and unknown-tool rejections; generated C++ still inconsistent, then `AGENT_RUNTIME_NO_PROGRESS`. All 681 health probes passed, p95 65 ms. |
| Final-source continuation | One completed inspection, then the preserved no-progress guard stopped the job. No verified build. The original job consumed 1,467.14 of 2,700 active seconds, 64 provider requests, and 66 tools across continuations. No counters or budgets were reset. |

The final saved draft still contains `AlarmManager.cpp` fields and enum accesses that disagree with `AlarmManager.h` and `SystemStates`, including `buzzerPatternIndex`, `acknowledgedButtonPressed`, and `temperature` versus `tempState`. These are generated-code errors, not successful build evidence. Exact attempts, diagnostics, source fingerprints, and health results are in `live-latest-summary.json` and `live-latest-1/`.

A fresh replay of the October 13 request from an empty isolated project retained its original standard profile and 900-second limit. It stopped at a build-phase deadline, then a normal continuation reached the verification reserve after 851.37 cumulative active seconds. There was no verified build. All 33 provider requests completed without provider failure or unknown-tool rejection; all 806 health probes passed. Its last completed compiler evidence included `BuzzerPatternType` and `SystemStates` mismatches. This exposed the declaration-based context correction. Evidence: `live-standard-summary.json` and `live-standard-final/`.

The final isolated standard-profile case, `live-standard-typed/`, retained the original request, selected model, and 900-second cumulative limit across five attempts. It completed 46 provider requests with zero provider failures; all 805 health probes passed. One unknown-tool correction succeeded. Its generated INI then exposed the policy-repair routing gap described above. After the final runtime correction, ForgeX read the INI, removed the redundant flag, and reached compilation without a trust override or manual firmware edits. A build-phase timeout was followed by a normal continuation that stopped at the verification reserve after 849.15 active seconds and 54 consumed tool calls. No verified build or requirement acceptance was obtained. Evidence: `live-standard-typed-summary.json`, `policy-live-correction.json`, and `live-standard-typed/`.

The last completed compiler evidence reports an undeclared `DEBOUNCE_INTERVAL`, incompatible `SecurityState`/`BuzzerPattern` comparisons, missing temperature enum constants, and callers using methods absent from `DisplayManager`, `ButtonManager`, `AlarmManager`, and `SystemState`. For example, `main.cpp` calls `showStartupMessage` while the declared method is `displayStartupMessage`. Later failed exact-match edit attempts do not establish that these errors were repaired. The evidence preserves diagnostic freshness limits rather than claiming the draft compiles.

A separate read-only protocol probe through the actual model engine returned one native `read` call with `tool_choice=required` in 2.247 seconds. No tool was executed by that probe and it is not build evidence. NVIDIA documents that automatic tool choice leaves whether to call a function to the model; forced-choice behavior must be checked for the endpoint. See [NVIDIA's tool-calling documentation](https://docs.nvidia.com/nim/large-language-models/1.12.0/function-calling.html) and [request troubleshooting](https://docs.nvidia.com/nim/large-language-models/latest/troubleshooting/requests.html). The resulting correction is restricted to the verified model and endpoint and the immediately following correction turn.

The final Python 3.13.15 package passed its smoke test: bundled imports, local authentication, isolated per-user storage, tool catalog, selected build environments, and session persistence across two backend starts. All five changed runtime assets match the final release manifest and tested source revision. The actual packaged backend fingerprint was also recomputed and matches the manifest. Dependency pins and required hash checks were retained. An initial PyPI DNS failure from an earlier staging attempt is retained separately in the evidence. Final evidence: `prepare-runtime-complete-final.log`, `packaged-smoke-complete-final.log`, and `packaged-verification-complete-final.json`. The installer was not published.

## Production acceptance

The source changes have passed their regression gate. Production acceptance also requires a verified build and requirement review from the original requests under their existing limits, repeated on the representative standard and advanced cases as specified in the prior plan. A running backend, generated draft, or passing source tests alone does not satisfy that gate. Hardware behavior requires connected-device evidence and has not been claimed.

The remaining production work has explicit gates:

1. Reproduce the final compiler failures against the saved draft and actual declarations. Keep the original request, model, standard/advanced profile, and cumulative budget accounting. Use the contained caller/header/type evidence now delivered by the runtime.
2. Correct the generated contracts as coherent groups: button debounce configuration; security/temperature enums and alarm types; then caller/member names and signatures across each header, implementation, and `main.cpp`. ForgeX must make the edits through normal tools, preserving requested behavior. A successful edit or a model promise is insufficient evidence; require a fresh real build of the resulting revision.
3. Complete the existing independent requirement review after a verified build. Unsupported hardware claims must remain unverified until connected-device evidence is available.
4. Replay the original representative requests from fresh isolated profiles and require three consecutive successful build-and-requirement qualifications per representative before promotion. Include the advanced 77-requirement case, which has not yet been replayed in this qualification. A failing representative keeps the release gate open.

The latest replay failed that production gate. Do not publish this as a complete firmware-generation reliability fix or compensate by weakening policy, evidence checks, limits, or model selection. The passing source-suite and package checks qualify the runtime changes only.

The task-only patch is based on the pre-existing working tree, including earlier uncommitted fixes. It provides the rollback boundary for these changes; the earlier release-stage pointer is retained as `release-stage.before.json` in the evidence directory. No commit, push, installer publication, or production promotion has been performed.
