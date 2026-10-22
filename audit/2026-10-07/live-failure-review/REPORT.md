# ForgeX live failure review — 7 October 2026

The latest ESP32 Room Monitor workflow failed through a sequence of three confirmed provider problems: OpenRouter rejected the first run because its daily free-model quota was exhausted; NVIDIA Nemotron twice exhausted ForgeX's 4,096-token response allowance, then its repair streams stalled; the final continuation selected a retired Qwen model and NVIDIA rejected it with HTTP 410. No firmware build was reached.

ForgeX also has confirmed defects in task parsing, model-list provenance, failure classification, recovery handling, and project readiness. These defects either contributed to the failure or hid the real cause. Repeatedly pressing Retry with the saved Qwen selection will repeat a permanent rejection.

## Scope and evidence

This review inspected the most recent desktop stdout/stderr, the corresponding backend and desktop JSONL diagnostics, all five Room Monitor run records and their durable job records, the saved stage, the model cache, and the active implementation. ForgeX was not running when inspection began: process inspection found no ForgeX/Electron/frontend/backend process, and the later TCP listener check found no listener on ports 3000 or 8000. The newest application logs ended on **6 October at 22:08 IST**. The findings describe that captured session; they do not claim a fresh provider request was made today.

The current backend source fingerprint exactly matches the failing backend's fingerprint: `5599246e3d43159038b3f40d789ba75d85b5747dff9dd321cf1e84a39bfa9343`. This establishes that the code reviewed is the code used by the latest failing session.

Primary evidence:

- `.forgex/logs/live-runtime.backend-3b9e2a890609417f91772784a4c4eaf3.jsonl`
- `.forgex/logs/live-runtime.desktop-434863e5-1ee8-496b-a919-724665ae90c1.jsonl`
- `forgex-esp32-human-test.stdout.log` and `forgex-esp32-human-test.stderr.log`
- `.promptforge/state/agent-orchestration.sqlite3` and `.promptforge/state/model-router.sqlite3`, opened read-only
- `.promptforge/state/change-staging/workflow-agent-run-91875dbc199d4b39a69c231765f78ab1/`

[evidence.json](evidence.json) preserves 31 relevant events with their original line numbers, five run summaries, job allowances, the retired cached model, and source hashes. [probe-results.json](probe-results.json) records local reproductions. The reproduction scripts isolate application state inside this audit folder and use an in-memory credential store. No live provider generation, app restart, hardware access, or production code/settings changes were performed.

## Failure timeline

All times below are **6 October 2026, IST**. Line numbers refer to the primary backend JSONL above.

| Time | Run / action | Evidence | Outcome |
| --- | --- | --- | --- |
| 21:56:08 | `78fbff…`, OpenRouter `cohere/north-mini-code:free` | Lines 54–55: HTTP 429; `free-models-per-day`; daily limit 50, remaining 0 | Provider rejected the first model request; no source was generated. |
| 21:59:53–55 | `6de77e…`, NVIDIA Nemotron | Line 96: HTTP 200 and complete tool call; line 123: `write_file` completed | `platformio.ini` was staged successfully. Authentication and basic tool execution worked. |
| 22:00:47 | Same run, turn 2 | Line 144: `MODEL_OUTPUT_TRUNCATED`, `finish_reason=length`, requested/output tokens both 4,096 | Response stopped before a complete source-writing action. |
| 22:01:50 | Same turn, repair | Line 156: allowance raised to 8,192; `PROVIDER_TIMEOUT`; durable diagnostics specify `stream_idle` | Repair stream became idle for 60 seconds. Run paused. |
| 22:03:14 | `5c59b0…`, continuation, turn 3 | Line 240: another 4,096-token response ending with `length` | Same truncation recurred after continuation. |
| 22:04:18 | Second repair | Line 259: 8,192-token repair; another `stream_idle` timeout | Run paused again. |
| 22:05:11–12 | `91875d…`, after guidance to use smaller modules | Lines 313, 339: complete 1,431-token tool call and successful file write | `src/main.cpp` was staged. Supporting modules remained unfinished. |
| 22:05:19 | Pause request | Lines 353–354: `CANCELLED`, following explicit cancellation | This attempt stopped because pause was requested, not because generation independently failed. |
| 22:05:41–45 | `7011b9…`, resumed with Qwen | Lines 372, 389–390: model selection changed; HTTP 410; zero content/tool events | NVIDIA permanently rejected the retired model. Final run failed. |

The final provider response states that `qwen/qwen3-coder-480b-a35b-instruct` reached end of life on **11 June 2026** and is no longer available. This comes directly from the captured NVIDIA error body, not from an inferred meaning of HTTP 410.

## Findings and repair priorities

### 1. P1 — The model picker retains a retired model, and refresh reports cached data as provider discovery

The persisted NVIDIA model list still contains the retired Qwen entry and advertises its text-generation/native-tool capabilities. Selecting it produces the final HTTP 410 failure in this incident.

The active backend uses `OpenCodeProvider`, not the older dedicated NVIDIA adapter. Its [list_models implementation](D:/projects/ForgeX-Phase6-Final/backend/model_router/providers/opencode.py:42) performs live discovery only for Ollama and LM Studio; NVIDIA returns `registry.list_models()` directly. The [models endpoint](D:/projects/ForgeX-Phase6-Final/backend/api/routes/models.py:176) then saves that returned list and labels the response `source: "provider"`, with no error. A refresh therefore cannot discover that this hosted model has been retired, despite presenting the result as provider-sourced.

The local probe confirms that the active NVIDIA adapter returns a retired entry supplied by its cache without any provider discovery. The actual model cache independently confirms the same entry is present.

Repair: expose the real catalog source/freshness, incorporate provider-supported discovery or lifecycle data where available, and mark a model unavailable after an explicit retirement rejection. Preserve the draft and direct recovery toward a different available model. Do not silently change providers or repeatedly retry a known retired model.

### 2. P1 — The product planner repeatedly resets ordinary generation to a 4,096-token allowance

[ProductProviderRegistry.resolve](D:/projects/ForgeX-Phase6-Final/backend/agent_runtime/product_provider_registry.py:166) explicitly constructs the planner with `max_tokens=4096`. This overrides the planner's larger default. [Response allocation](D:/projects/ForgeX-Phase6-Final/backend/agent_runtime/router_planner.py:155) caps ordinary turns at that configured value; a truncation repair doubles the previous allowance to 8,192.

Both failing primary responses consumed exactly 4,096 tokens and ended with `finish_reason=length`, while the adapter had HTTP 200. This is response truncation, not an authentication failure, compiler failure, or exhaustion of the overall job allowance. At the final stop, the saved NVIDIA job still had approximately **624 seconds, 111 requests, 211,084 output tokens, and 97 tool calls** remaining.

The second continuation returned to the same 4,096-token ordinary limit and repeated the failure. Smaller-action guidance helped: the next completed write used only 1,431 output tokens. The safe stream boundary correctly prevented execution of incomplete tool arguments.

Repair: make the ordinary action allowance reflect the chosen model and task, carry relevant truncation history into continuation, and require complete smaller file edits before a large action exhausts the allowance. Raising the token limit alone does not address the separate idle-stream problem below.

### 3. P1 — Repair attempts fail at the 60-second stream idle timer

[TransportPolicy](D:/projects/ForgeX-Phase6-Final/backend/contracts/transport.py:12) sets `idle_seconds=60`. [The engine's transport wrapper](D:/projects/ForgeX-Phase6-Final/model-router/src/engine.mjs:72) aborts a response when its next body read remains idle for that interval.

The durable diagnostics make the timeout stage unambiguous. The first repair's last canonical event occurred at 2,766 ms and it failed at 62,875 ms. The second repair's last event occurred at 3,641 ms and it failed at 63,688 ms. Both report `timeout_stage=stream_idle`, received HTTP 200, and produced no complete tool action.

These were not the outer 300-second turn deadline or the 900-second job deadline. The captured data establishes the idle timeout; it does **not** establish why NVIDIA stopped sending body data, whether its tool generation was buffered, or whether an upstream connection stalled. A successful small call does not qualify the same model for long source-writing actions.

Repair: qualify the selected model with representative bounded tool actions, prefer smaller requests, retain idle/header/turn timeout distinctions in the UI, and use persisted recovery for genuine outages. Any idle-policy adjustment should remain bounded and be validated with stalled-stream tests; indiscriminately extending all deadlines would obscure the cause.

### 4. P1 — A same-paragraph prohibition excludes the entire positive specification

[Task partitioning](D:/projects/ForgeX-Phase6-Final/backend/agent_runtime/task_contract.py:18) splits the request by line, then treats any whole line containing `do not`, `don't`, `never`, `must not`, or `prohibited` as an exclusion. The 1,286-character original Room Monitor specification is one paragraph ending with “Do not flash or open serial on any connected device.” The parser therefore puts **all 1,286 characters** into exclusions and creates **zero positive requirements**.

This is visible in the real persisted run and reproduced using its exact original request. The generated task contract contains only a generic source-review acceptance check; it omits the requested firmware-build check and firmware/documentation deliverables. A subsequent continuation ending with another prohibition suffers the same issue. Later guidance introduces two new requirements, but does not restore the original hardware, dashboard, timing, and persistence requirements.

The raw specification still exists in conversation context. The defect is in the host-owned task contract and review coverage; it does not prove the model never saw the request. However, the independent reviewer consumes that contract, so losing the positive requirements undermines requirement verification and task progress.

Repair: partition affirmative requirements and prohibitions at sentence/clause boundaries while preserving their scope. Add cases covering a multi-sentence paragraph ending with a prohibition, and verify that subsequent revisions retain all unsuperseded obligations. Rebuild the affected saved contract from its original request before relying on it for completion checks.

### 5. P1 — Rate-limit codes disagree across the active engine, job recovery, and failure reporting

The [active adapter](D:/projects/ForgeX-Phase6-Final/backend/model_router/providers/opencode.py:323) maps provider rate limits to `RATE_LIMITED`. [Durable job recovery](D:/projects/ForgeX-Phase6-Final/backend/agent_runtime/jobs.py:22) recognizes `API_RATE_LIMITED` and `PROVIDER_RATE_LIMITED`, but excludes `RATE_LIMITED`. The [orchestrator](D:/projects/ForgeX-Phase6-Final/backend/agent_runtime/orchestrator.py:1861) consequently marks this incident's 429 run `failed` instead of entering the provider pause/recovery path.

[Failure reporting](D:/projects/ForgeX-Phase6-Final/backend/agent_runtime/failure_contract.py:21) also categorizes any code containing `limit` as `budget_exhaustion` before considering provider failures. The actual 429 record is labeled `origin=forgex`, `category=budget_exhaustion`, `retryable=false`. This contradicts the provider boundary's preserved HTTP 429 and retryable flag. The probe reproduces both the misclassification and missing recovery-code membership.

The original quota rejection is real and external: the response says the free-model daily quota was used up. A code fix cannot replenish that quota. The reset timestamp in the captured response applies to that failure time; this review did not make a new request to check today's quota.

Repair: use canonical provider codes consistently and categorize failures using typed origin/status rather than substring matching. Distinguish daily quota exhaustion from short-lived throttling, honor provider reset times, and offer an explicit model/provider change where appropriate. Avoid rapid automatic retries against a depleted daily quota.

### 6. P2 — Retired-model errors become a generic ForgeX runtime failure

[Engine normalization](D:/projects/ForgeX-Phase6-Final/model-router/src/engine.mjs:350) has no distinct unavailable/retired-model classification for HTTP 410. The error becomes `PROVIDER_REQUEST_FAILED`. [Failure normalization/reporting](D:/projects/ForgeX-Phase6-Final/backend/agent_runtime/run_reporting.py:229) then produces “ForgeX could not continue the Agent runtime,” and the run advertises a generic `retry` recovery action.

The real final run records `http_status=410`, `cause_origin=opencode`, and `retryable=false`, yet reports `category=runtime`, `origin=forgex`, `normalized_code=FORGEX_RUNTIME_ERROR`. The useful NVIDIA retirement reason survives in the provider log but is absent from the normal explanation. The probe reproduces this behavior.

Repair: preserve permanent provider model errors as provider failures with a clear explanation and a choose-another-model action. Keep the saved stage available for continuation; do not frame the same retired selection as a recoverable temporary outage.

### 7. P2 — An empty platformio.ini makes readiness return HTTP 500

The older open project `ESP32-WiFi-Dashboard-v2` has an empty `platformio.ini`. Reading that exact project reproduces `PlatformIOProjectError: platformio.ini contains no [env:<name>] sections` from [environment discovery](D:/projects/ForgeX-Phase6-Final/backend/services/platformio_service.py:793).

[The readiness route](D:/projects/ForgeX-Phase6-Final/backend/api/routes/projects.py:31) calls project inspection without handling this expected invalid-project condition, so the exception escapes as HTTP 500. Three occurrences appear at the start of the latest session. In comparison, the neighboring [profile route](D:/projects/ForgeX-Phase6-Final/backend/api/routes/projects.py:68) catches the error and returns HTTP 422. The isolated HTTP probe reproduces **500 for readiness and 422 for profile** with the same empty file.

This affects readiness for that older project; it is separate from the final Room Monitor model failure. Repair: represent incomplete configuration as an actionable readiness result or a handled validation error, keeping generation/recovery usable for a newly empty or incomplete project.

## Saved draft and remaining work

The persisted stage contains only `platformio.ini` and a 182-line `src/main.cpp`. The source includes six project headers that do not exist anywhere in that stage:

- `sensor_handler.h`
- `display_handler.h`
- `web_server.h`
- `alert_handler.h`
- `button_handler.h`
- `demo_mode.h`

`README.md` is also absent. All five Room Monitor run records have no build result, and the final summary shows build/verification pending. The draft is recoverable work, not a completed or compiled firmware project. Missing modules are expected consequences of interrupted generation here; no actual compiler output establishes additional library or firmware defects.

Recovery should retain the existing stage, correct its task contract, select a model known to accept the requested tool workload, generate the missing modules and documentation in bounded actions, then perform the real build and independent requirement checks. A retry should not start by discarding the successful writes.

The stdout/stderr also contain later SSE `ECONNRESET` and frontend `ECONNREFUSED` messages. These show lost backend connections. The provider failures and paused/failed run records already occurred before the final disconnects, and the backend continued servicing requests afterwards. The available logs do not establish a separate backend crash as the cause of those generation failures or establish who closed the final processes.

## Verification

The following existing targeted checks passed:

| Scope | Result | Log |
| --- | --- | --- |
| Task state, router planner, beta reliability, engine redesign, readiness/build API | 79 passed | [python-baseline.log](python-baseline.log) |
| Actual Python-to-Node worker boundary and model-output recovery | 32 passed | [python-worker.log](python-worker.log) |
| Engine transport, normalization, and native tool history | 44 passed | [node-worker.log](node-worker.log) |

**155 targeted tests passed.** The worker checks use local fake HTTP endpoints and exercise the real compiled model engine. They do not validate current remote provider availability or hardware behavior. [probe_findings.py](probe_findings.py) separately reproduces the defects; its assertions deliberately check the current faulty behavior so the evidence capture succeeds. Its passing exit code does not mean the defects have been repaired.

The test coverage reviewed primarily uses multi-line positive/prohibited requirements, valid PlatformIO fixtures, and mocked successful model lists. It therefore misses the incident's mixed paragraph, empty configuration, stale model provenance, and cross-layer error-code mismatch. This review leaves the findings concrete and reproducible without changing the production implementation.
