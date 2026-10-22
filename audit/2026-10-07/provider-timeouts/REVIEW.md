# Provider request handling and timeout review — 2026-10-07

Provider documentation and historical ForgeX request timings were reviewed, and timeout handling was corrected. API rate-limit settings and backoff rules were left unchanged. No project prompts or paid generation requests were sent to external providers during this review; regressions use local HTTP servers and the real bundled engine.

## What caused timeout failures

1. **The worker applied the idle timer before generation began.** With fast HTTP headers but a delayed body, the old 60-second read timer could terminate a request before its 120-second first-event allowance elapsed. Raw network reads now allow the first-event window, while the Python boundary independently enforces first meaningful output and subsequent idle deadlines.
2. **Heartbeats could disguise a stalled model.** Python previously enforced meaningful first output and total duration, but relied on raw worker bytes for stream idle detection after the first output. SSE comments continually reset that raw timer. The new semantic idle watchdog tracks text, reasoning, tool-input progress and completion; headers, comments and empty text do not extend it.
3. **Partial tool arguments were invisible to the host watchdog.** Both native OpenCode and SDK adapters can stream arguments before releasing a complete tool call. They now send a host-only progress event. Healthy long tool calls remain alive, and the agent still receives only the complete validated call. No argument content is added to diagnostics.
4. **Provider/model timing allowances were inconsistent.** The planner's NIM-only override, generic engine defaults, retry admission and legacy text wrapper used different limits. A shared policy resolver now handles free OpenRouter models, NIM generation, reasoning models, DeepSeek and local loading; explicit request overrides take precedence. The legacy OpenRouter 15-second first-token cutoff was removed.
5. **Parent deadlines were reported as provider failures.** Turn/job expiry, including expiry during worker startup or stdin write, now produces `AGENT_TURN_LIMIT_EXCEEDED` / `AGENT_RUNTIME_LIMIT_EXCEEDED`, rather than `PROVIDER_TIMEOUT`. These errors do not trigger immediate provider retries or increment outage failures. The worker also receives the effective total allowance and deadline kind.
6. **Legacy transport metadata could fail JSON validation.** The compatibility text path now preserves an explicit host policy when creating its execution context, and strips that policy from provider-facing metadata before serialization.
7. **Invalid SDK tool input could escape as an untyped Python error.** The SDK can report a tool call marked invalid with its original argument string. The worker now rejects it with a safe `TOOL_ARGUMENTS_INVALID` response error before releasing a call. This allows ForgeX's model-output repair to handle it, without exposing argument content.

## What the provider websites establish

These are documented measurement concepts or client/server limits, not promises about typical response time. The selected ForgeX allowances below are engineering choices.

| Provider | Verified documentation | Consequence for request handling |
| --- | --- | --- |
| NVIDIA NIM | Benchmarks distinguish time to first token, inter-token latency, throughput and request latency. Workload and concurrency must be measured together. [NVIDIA benchmarking](https://docs.nvidia.com/nim/large-language-models/latest/reference/benchmarking.html) | Token throughput does not predict total firmware-generation time. Allow tool progress and measure first output separately. Self-hosted benchmarks do not establish a hosted API SLA. |
| OpenRouter | Provider routing exposes latency/throughput percentiles over a rolling five-minute window. Streams may contain keepalive comments. [Routing](https://openrouter.ai/docs/guides/routing/provider-selection), [Streaming](https://openrouter.ai/docs/api_reference/streaming) | Timing varies by upstream provider/model and load. Comments preserve the connection but do not prove model progress. Existing routing choices are preserved. |
| OpenAI | Python SDK default timeout is ten minutes. Latency guidance distinguishes token generation speed from request duration and output size. [Official SDK](https://github.com/openai/openai-python#timeouts), [Latency guidance](https://developers.openai.com/api/docs/guides/latency-optimization) | A ten-minute SDK timeout is not an expected response time. ForgeX retains its own finite deadlines, especially for reasoning and long output. |
| Anthropic | The TypeScript SDK defaults to ten minutes and recommends streaming for long requests; non-streaming limits can vary with output allowance. [Claude SDK](https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/typescript#timeouts) | Streaming progress should keep a productive request alive without removing turn/job bounds. |
| Cerebras | The official Python SDK defaults to a one-minute timeout. [Official SDK](https://github.com/Cerebras/cerebras-cloud-sdk-python#timeouts) | This is a client default, not measured latency. Without local timing evidence, the existing conservative generic allowance is retained rather than assuming advertised throughput guarantees a quick response. |
| DeepSeek | The current Chinese documentation describes SSE keepalive comments while waiting. It says the server closes a request after ten minutes if inference has not started. The English URL was unavailable to the browsing tool. [Current official documentation](https://api-docs.deepseek.com/zh-cn/quick_start/rate_limit/#请求保活机制) | Comments must not reset the meaningful-output watchdog. ForgeX uses a shorter bounded response window; it does not wait indefinitely for queueing. Older unofficial copies describing thirty minutes were not used. |
| Ollama | Native responses expose model-load and generation durations, and `keep_alive` controls model residency. [Generate API](https://docs.ollama.com/api/generate) | Local cold starts need additional first-output allowance. Native timing fields are not assumed to exist in ForgeX's OpenAI-compatible stream. |
| LM Studio | With just-in-time loading enabled, an inference call loads a downloaded model into memory if needed; idle models can be unloaded automatically. [Official service documentation](https://lmstudio.ai/docs/developer/core/headless) | A listed model is not necessarily warm. The local policy allows extra first-output time; no hardware-specific latency estimate is claimed. |

## Historical response times and observed fractions

`inspect_timings.py` reads only final physical-attempt diagnostic events from workspace live logs and deduplicates attempt IDs. It excludes prompts, credentials and response bodies. The sample comprises **96 physical attempts**, across two providers and four model/provider pairs. No provider entries were found in the workspace settings stores inspected; environment and OS credentials were not read. These historical desktop events do not establish the current installed application's credential/configuration state.

| Provider/model | Attempts / successful completions | First event median / p95 | Successful completion median / p95 / max |
| --- | --- | --- | --- |
| NIM Nemotron Super 120B | 8 / 3 | 3.406s / 12.485s (8 observed first events) | 12.875s / 15.578s / 15.578s |
| OpenRouter Cohere North Mini Code free | 56 / 50 | 6.063s / 59.421s (50 observed first events) | 6.110s / 59.485s / 70.921s |
| OpenRouter Nemotron Ultra 550B free | 31 / 15 | 8.640s / 77.906s (15 observed first events) | 8.718s / 78.031s / 78.031s |
| NIM retired Qwen Coder 480B | 1 / 0 | No first output | HTTP 410; a longer timeout cannot recover a retired model |

The following fractions answer how frequently **successful recorded attempts** completed within different durations. They are conditional on successful completion, not overall success probabilities or predictions for future requests.

| Model | Within 5s | Within 15s | Within 30s | Within 60s | Within 120s |
| --- | --- | --- | --- | --- | --- |
| NIM Nemotron Super (n=3) | 0% | 66.7% | 100% | 100% | 100% |
| Cohere North Mini (n=50) | 30% | 78% | 92% | 96% | 100% |
| Nemotron Ultra (n=15) | 13.3% | 73.3% | 80% | 93.3% | 100% |

Limitations: the workload and output sizes vary; samples are small, especially NIM; failed and timed-out attempts are censored and omitted from completion percentiles. First events may be reasoning, tools or text, rather than first visible text. Percentiles use the nearest-rank method. No response-time sample exists here for OpenAI, Anthropic, Cerebras, DeepSeek, Ollama or LM Studio.

Historical failures remain visible in [observed-timings.json](observed-timings.json): NIM Super had two truncations, two stream-idle timeouts and one cancellation; Ultra had fourteen incomplete streams, one first-event timeout and one cancellation. Cohere's six rate-limit failures are recorded but its rate-limit behavior was not changed. Increasing timing allowances cannot guarantee upstream availability or repair every incomplete upstream response.

## Implemented allowances

All durations below are seconds and are upper bounds. Exact request overrides win. Reviews select the generic policy; existing outer phase/turn/job limits, request/token budgets, verification reserves and output-repair reservations remain authoritative.

| Request category | First meaningful event | Idle after meaningful progress | Transport total |
| --- | --- | --- | --- |
| Generic provider/model; standard Cerebras | 120 | 60 | 300 |
| OpenRouter `:free` models | 120 | 120 | 300 |
| NIM generation | 120 | 120 | 300 |
| DeepSeek, or another reasoning-capable model | 180 | 120 | 600 |
| Local Ollama / LM Studio | 180 | 120 | 600 |
| Review phase | 120 | 60 | 300 |

The table does not override the existing 60-second review unit, 180-second ordinary turn, 300-second generation turn or 120-second repair cap. The planner normally caps a generation primary request at 180 seconds and reserves repair/verification time; smaller remaining job budgets shorten that further. OpenRouter retains a 120-second first-event window, covering the recorded 78-second tail and allowing short-failure recovery within a primary turn. A local/reasoning 600-second transport allowance is available only when a caller's outer deadline allows it. Health checks retain their explicit short probe policy (20-second first event, 10-second idle, 30-second total), so a failed short probe should not be interpreted as a provider latency SLA.

The watchdog starts at the physical attempt, including worker startup. Raw network reads use at least the larger of first-event and idle allowances; Python enforces the precise semantic timers. Worker spawn/write and cleanup remain bounded. Retries use the same provider response window, preserve physical-attempt/token accounting, disable hidden SDK retry multiplication, and never replay partial visible output or execute incomplete tool calls.

## Validation

- **284 Python checks passed** across 19 suites covering the real worker boundary, transport policies, legacy providers, router/planner behavior, output repair, request recovery, admission, circuit handling, model routes and outage recovery. Final run: 134.27 seconds. [Captured output](python-tests.log).
- **46 engine checks passed** using `node --test model-router/tests/*.test.mjs`, including actual local HTTP requests for OpenAI Responses, Anthropic Messages, Gemini, OpenRouter, native tools and SDK fallback protocols. This includes the new slow-first-body and malformed SDK tool regressions.
- **11 focused checks passed** for provider policies, first-output/idle separation, heartbeats, complete tool calls with streaming arguments in native and SDK paths, parent deadline identity, expired-request admission, retry funding and legacy explicit overrides. These are included in the broader results above.
- **Desktop build passed:** `npm run build:electron` (model engine bundle, harness and Electron TypeScript). The engine bundle was rebuilt again after the SDK input guard and exercised by the final engine/Python checks.
- **Real isolated backend startup passed:** authenticated application initialization and `/health` returned `healthy`, `engine_ready: true`, with PlatformIO available. [Startup evidence](startup-validation.json). This used temporary state and an in-memory credential store; no production job was resumed.
- **Diff whitespace checks passed.** Python emitted two existing FastAPI/Starlette test-client deprecation warnings; there were no test failures.

The updated worker bundle is in `dist/model-router`. Start the rebuilt workspace version of ForgeX to use the fixes; this review did not package or deploy an installed desktop release.
