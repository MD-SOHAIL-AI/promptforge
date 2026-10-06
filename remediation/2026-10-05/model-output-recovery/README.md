# Model output truncation and bounded recovery

Implemented on 2026-10-05 for the live run `agent-run-e7262d334efa487dae1fd07c2818a872`.

## Confirmed incident

Provider: OpenRouter. Model: `nvidia/nemotron-3-ultra-550b-a55b:free`.

The backend log `.forgex/logs/live-runtime.backend-647a6546ce3e47b7822a5ea3270cafda.jsonl` records:

- Line 233, 20:01:10 IST: `MODEL_OUTPUT_TRUNCATED`, with no final content or tool call.
- Line 234: a same-model repair request started.
- Lines 250–251, 20:02:54 IST: repair cancelled and `AGENT_TURN_LIMIT_EXCEEDED` recorded.
- Line 256: run failed, with generated source preserved as an unverified recovery draft.

Six earlier turns succeeded. The old tool-history `undefined ... name` failure did not recur. The incident logs do not prove that reasoning consumed the output budget; new diagnostics make that distinction observable on subsequent runs.

## Changes

### Provider boundary

`model-router/src/engine.mjs` retains reasoning-token usage from the native and SDK event contracts. Native malformed tool arguments now retain `TOOL_ARGUMENTS_INVALID`, permitting semantic repair without executing partial calls.

`backend/model_router/providers/opencode.py` records completion, finish reason, requested/effective output limits, reported token usage, event counts, HTTP status/request ID, timing, and worker exit/cleanup before normalization can reject a response. Unknown usage stays unknown. Logs contain no prompt or reasoning text. Cancelled or prematurely closed streams cannot be reported as completed attempts.

Truncated responses, including apparently valid calls with a truncation finish reason, are rejected at the provider boundary. Agent recovery buffers a physical stream until it is complete, so partial calls cannot enter tool execution. EOF without completion remains an invalid stream.

For this specific OpenRouter Nemotron route, truncation repair requests `reasoning.effort: low`, using the existing provider-options path. Explicit configured reasoning controls take precedence. Other models/providers are left unchanged. Supported parameters were checked against the public endpoints API on 2026-10-05:

- https://openrouter.ai/api/v1/models/nvidia/nemotron-3-ultra-550b-a55b:free/endpoints
- https://openrouter.ai/docs/guides/best-practices/reasoning-tokens

Local wire tests verify forwarding. They do not prove that an external endpoint honors the control.

### Planner and deadlines

Generation turns have a 300-second maximum, shortened by the existing job-derived allowance and verification reserve. Intake retains its 180-second cap and review its 60-second cap.

When the available generation allowance is at least 240 seconds, the primary sequence reserves 120 seconds for repair plus three seconds for cleanup. Primary requests never exceed 180 seconds. Exactly one semantic repair can run, with a maximum 120-second request window and the same provider/model. Process cleanup is separately bounded and can add a small amount of elapsed time after cancellation.

Repair preflight checks time, shared physical-attempt allowance, request budget, and minimum useful output budget before dispatch. Budget failures retain the existing budget-exhaustion classification, with the original model failure recorded separately. Time-limited skipped repair retains the initial output failure and an explicit skip reason.

Truncation repair doubles the previous requested output allowance, capped at 16,384 tokens, the model output limit, and remaining job tokens after verification reserves. Its prompt requests one complete next action and smaller complete edit steps.

Incomplete streams get at most two retries per sequence. All attempts and the repair share the existing six-attempt ceiling. A retry must fit its useful response allowance plus backoff; therefore a default 120-second repair window ordinarily cannot accommodate another full 120-second response allowance. Retry-After is not shortened. Authentication/configuration failures do not become output repairs.

### Persistence and feedback

Provider diagnostics gain additive fields and separate initial/repair snapshots. `ProductAgentRun`, durable restoration, generated JSON Schema, and generated TypeScript expose the same fields. No destructive schema migration was needed.

User-facing failures distinguish the original model response problem from a local recovery deadline or exhausted budget. Existing staged editing, review, tool permissions, build verification, and hardware authorization remain enforced.

## Verification

Authoritative final results:

| Group | Passed | Failed | Skipped | Evidence |
| --- | ---: | ---: | ---: | --- |
| Provider/planner/budget/diagnostics regressions | 241 | 0 | 0 | `python-regression.xml` |
| Agent runtime, staging, continuation, API and transport | 254 | 0 | 1 | `python-runtime.xml` |
| Model-router Node tests | 44 | 0 | 0 | `node-router.log` |
| UI-state Node tests | 79 | 0 | 0 | Tool execution output |

The skipped test requires Windows symlink creation, unavailable on this host. These are affected test groups, not a fresh run of the entire repository suite.

Additional successful checks: frontend typecheck, model-router build, Harness/Electron build, Python syntax checks, and source release scanner (`source-scan.log`). `file-hashes.json` records reviewed source and rebuilt worker/engine hashes. `python-results.json` summarizes the final Python groups.

Nineteen new Python cases exercise the actual bundled worker, local HTTP endpoints, planner, executor, ChangeSet handling, and durable storage:

- Reasoning-only truncation followed by successful repair, then another model turn retaining actual tool history.
- Truncated text and malformed native tool arguments never entering execution.
- Supported, unsupported, and explicit reasoning settings; bounded repair output.
- Insufficient time, tokens, or requests preventing repair dispatch.
- Repeated incomplete streams staying private and bounded.
- Repair deadline and user cancellation killing/reaping the owned worker.
- Primary/repair deadline allocation and the shorter review cap.
- Initial and terminal failures remaining distinct after durable storage reopening.
- A failed same-model repair preserving a reviewable ChangeSet; explicit continuation reading saved files without rewriting them, applying changes, or claiming compilation.

Two new Node cases verify actual reasoning usage/control forwarding and malformed-tool error normalization.

## Intermediate failures resolved

Development runs exposed an initial diagnostics snapshot key mismatch, compatibility of budget-exhaustion classification, and the minimum repair charge used to distinguish job exhaustion from verification reserves. All were corrected and covered by the final regression run.

New fixture corrections included granting the existing staged-edit policy, supplying the previous tool observation before a subsequent turn, allowing enough primary time for Windows worker startup, and waiting for HTTP arrival before cancelling. The expected retry count was corrected to account for the full response-window gate; six is a ceiling, not a required number of retries.

`python-budget.xml` is a superseded development run containing one resolved classification failure. `python-new.xml` records an earlier passing subset of eighteen new cases; all nineteen are included in the final `python-regression.xml`.

## Activation and limits

Restart ForgeX to load the modified Python planner/provider code. The model worker is rebuilt and is launched afresh per physical attempt. Do not replay the previous task automatically; use the existing explicit continuation flow for its saved draft.

## Authorized live qualification — PASS

On 2026-10-05, the user separately authorized a synthetic task using the configured OpenRouter account and the same `nvidia/nemotron-3-ultra-550b-a55b:free` model. `live_qualification.py` ran the actual ForgeX planner, bundled worker, staged tool executor, and ChangeSet service against an empty synthetic workspace. Existing project files were not sent. Credentials remained in memory; application settings and the original run were not modified.

| Check | Observed result | Evidence |
| --- | --- | --- |
| Live model generation | PASS; five physical requests, all HTTP 200, four tool-call completions and one final stop | `live-qualification/generation.json`, `usage.jsonl`, `runtime.backend-*.jsonl` |
| Native tool/history flow | Two complete writes followed by two reads; no provider switch or semantic repair | `live-qualification/progress.jsonl`, `generation.json` |
| ESP32 compilation | PASS; PlatformIO 6.2.0, espressif32 7.0.1, Arduino, esp32dev | `live-qualification/compilation.json`, `compiler-output.log` |
| Artifact verification | Four sealed images verified under Windows read locks; source hashes unchanged during compilation | `live-qualification/qualification.json`, `sealed-artifacts/` |
| Workspace safety | Active synthetic workspace remains empty; ChangeSet still requires review | `live-qualification/generation.json`, `qualification.json` |
| Tested checkout | Reviewed source and rebuilt bundle hashes still match the regression evidence | `live-qualification/qualification.json`, `file-hashes.json` |

The five responses reported 20,305 input tokens and 653 output tokens, including 312 reasoning tokens. The job's conservative output-budget charge was 40,448; this is an allowance charge, not the provider's reported token consumption. All five owned model workers exited with code zero and no incomplete cleanup.

The resulting application image is 269,440 bytes. The complete upload manifest hash is `b17b0a4af62b9c65d2e348bce9554444da547023091e0251e095ebd543dcb6f4`. RAM usage was 21,464/327,680 bytes; program usage was 269,081/1,310,720 bytes. No image was flashed, no serial port was opened, and hardware behavior remains unverified.

Environment/probe failures are recorded separately: the sandbox could not access the configured Windows credential, and its initial compiler attempt could not lock `C:\Users\diwan\.platformio\platforms.lock`. Authorized execution outside the sandbox resolved both. `compilation-sandbox-blocked.json` and `compiler-sandbox-blocked.log` preserve the compiler failure. The evidence helper initially called the lock API with unsupported keyword arguments; its call was corrected to validate the manifest first and then acquire read locks. `verify-probe-error.json` preserves that helper failure; the final verification passed.

`live-qualification/evidence-hashes.json` records the final live evidence hashes. The successful live task did not reproduce truncation, 503 responses, or repair. Those failure paths were verified with local worker fixtures; the live run does not establish that the external endpoint honors repair-only reasoning controls or guarantee future provider availability. Restarting the existing desktop application is still required to activate the changed Python backend there.
