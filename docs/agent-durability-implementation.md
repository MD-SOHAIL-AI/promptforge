# ForgeX agent durability implementation

Implemented September 27, 2026 in the existing Python runtime and Electron/Next.js shell.

## Incident evidence

The installed desktop log showed two recent runs failing after repeated reads reached the turn cap, and after a selected-model outage followed by invalid model output. Other inspected incidents involved request-local callback serialization, capability expiry, denied paths, budgets, and stalled progress. Those are separate causes; a provider retry cannot repair every failure.

`tests/fixtures/durability-incidents.json` contains seven sanitized events from the two recent runs. It includes timestamps and failure codes, without prompts, credentials, tool output, or workspace paths. Existing changes documented in `agent-quality-implementation.md` and `agent-quality-verification.md` were preserved.

## Implemented behavior

| Area | Result |
| --- | --- |
| Request validation | Validate serializable provider input before a physical request reservation. Local callbacks, shared attempt state, and recovery hooks remain local. Report `FORGEX_REQUEST_INVALID` as a ForgeX request failure. |
| Request recovery | One chain of at most four physical attempts per logical planner turn, including output repair. Keep the selected provider and model. Retry eligible transient errors; respect Retry-After. Authentication and policy failures stop. |
| Streaming | Keep an agent attempt's events private until it completes. Discard an interrupted stream before retrying, preventing duplicate text and tool calls. The buffer is bounded to 50,000 events / 8 MB. This delays live assistant text until an attempt finishes; progress and retry events remain live. |
| Durable scheduling | Persist short retry status before waiting. Longer delays use existing durable jobs. A wake removes its own scheduling slot so another outage can schedule a successor. No budget reset. |
| Initial recovery | Seal a `runtime.admitted` checkpoint at turn zero before the first request. This permits safe recovery when the first request fails. Existing settled-turn checkpoints remain supported. |
| Continuation | Real router planners continue while useful evidence accumulates, subject to tool, provider, time and hard 600-turn limits. Unique reads count as progress. Repeated unchanged observations stop with `AGENT_RUNTIME_NO_PROGRESS`. |
| Saved state | Preserve progress fingerprints, observations, milestone plans, staged files, budget usage, and a concise objective/evidence summary. Resume with a linked replacement run. |
| Safety boundaries | Preserve workspace/stage revision checks, trust, user/budget pauses, journal checks and unknown-operation blocks. Never replay a hardware operation. |
| Failure reporting | Preserve original and repair failure codes, request counts and origin. Separate stalled progress, budgets, policy rejection, invalid output, provider errors and local runtime failures. |
| Interface | Show selected-model retry status and low remaining budgets. Coalesce overlapping run/graph GET requests, reject stale responses, and follow session recovery to its new run without resubmitting a task. |
| Renderer | At most three automatic reloads within 60 seconds. Further crashes show a stable recovery page with a manual reload link. Backend jobs remain independent. |
| Packaged routing | Honor the configured backend port and route API/SSE traffic to that loopback backend at runtime. This allows an isolated candidate alongside an existing desktop instance. |
| Diagnostics | Include backend source fingerprint in diagnostic records. Qualification follows durable jobs through replacement runs and reports request counts, recovery, elapsed time and immediate local failures. |

## Reference archives

All eight supplied archives were inspected as reference material. Their contents were treated as data, not user instructions; their scripts were not executed. ForgeX-specific implementations were written without importing or copying their code.

| Archive | Patterns used in the implementation |
| --- | --- |
| Codex | Bounded response retry and original failure preservation. |
| Grok Build | Transient retry handling and evidence from retry tests. |
| Claude Code | Retry coordination and conversation recovery concepts. No implementation copied; root licensing was not established. |
| DeepSeek harness | Shared retry coordination and durable recovery events. |
| OpenCode | Retry state and repeated-work detection. |
| Orca | Renderer crash recovery with a circuit breaker. |
| Hermes workspace | Saved conversation summaries for continuation. |
| OpenClaw | Separate conversation restoration from replaying mutations. |

Archive SHA-256 hashes and reference file paths are recorded in `agent-durability-references.json`.

## Operational controls

`FORGEX_ENABLE_REQUEST_RECOVERY=0` disables the new request retry waits. `FORGEX_ENABLE_PROGRESS_CONTINUATION=0` restores bounded turn slices for router planners. The shared request-attempt cap still applies. Existing auto-recovery, trust and budget controls retain their behavior.

Admission checkpoints use version `forgex.agent-checkpoint.v3`; settled checkpoints continue to use v2. Older versions remain readable. A rollback to software lacking v3 support cannot automatically resume an admission checkpoint.

## Verification

- Full backend run: **2,035 passed, four skipped**, two dependency deprecation warnings. Log: `test-results/durability-backend-full.log`. This run started before the final admission/scheduler changes; those changes received the additional tests below.
- Final admission/checkpoint, continuation and physical adapter recovery tests: **43 passed**.
- Final orchestrator, durable jobs and unified runtime checks: **164 passed**, two dependency deprecation warnings.
- Conversation browser tests: **14 passed**, including selected-model retries, single-flight reads and automatic replacement-run following.
- UI state tests: **71 passed**, including configured-port routing and rejection of remote proxy targets.
- Electron tests: **38 passed**, including the renderer crash circuit.
- Frontend typecheck, lint and production build passed.
- Bundled Python backend: imports, authentication, per-user storage and session persistence across restart passed.
- Qualification metrics: **three tests passed**, including recovery within one run and replacement attempts that still fail.
- Final packaged desktop: isolated profile and ports, restricted PATH, authenticated API, restored Agent workspace and survival after parent output pipes close passed.
- Actual renderer crash drill: **three automatic reloads, fourth crash stopped at the recovery page, manual reload restored the app**. Backend authentication identity remained unchanged. Screenshots: `test-results/durability-packaged.png` and `test-results/durability-packaged-circuit.png`.

The tested desktop candidate is unsigned and isolated under `.forgex/durability-candidate/win-unpacked/ForgeX.exe`. Your running older desktop instance was preserved. Its processes must be fully closed before launching the candidate with the default ports; running processes do not load newly built source files. This candidate has not replaced the installed app.

Local build hashes and verification results are recorded in `test-results/durability-verification.json`.

## Qualification still requiring release evidence

The 30 real-provider scenarios repeated three times, all recorded regression prompts and follow-ups, physical ESP32 upload/serial cycles, publisher signing, and clean Windows installation/upgrade evidence remain release gates. Deterministic recovery tests and local packaging do not establish Codex/Claude Code capability parity or error-free operation of an external provider.

Run `scripts/qualify-agent-quality.py` and `scripts/qualify-agent-prompts.py` against the final packaged backend with an explicitly selected provider/model, following the existing qualification documentation. Record human requirement-fidelity reviews and the exact installer/resource hashes alongside the automated report.
