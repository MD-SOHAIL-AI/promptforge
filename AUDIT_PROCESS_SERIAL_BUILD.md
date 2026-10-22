# ForgeX Adversarial Audit — Process / Serial / Build Execution Layer

Scope: `backend/main.py`, `backend/runtime/{subprocess_mgr,serial_runtime,process_owner,result}.py`,
`backend/services/{platformio_service,terminal_service,serial_service}.py`,
`backend/api/routes/{terminal,monitor,build,devices,diagnostics,execute}.py`,
`backend/tools/{serial_monitor,flash_firmware,board_detector}.py` (+ supporting: `local_security.py`,
`app.py`, `schemas/{execution,monitor}.py`, `build_firmware.py`, `generation_adapter.py`,
`observability/diagnostics.py`).

Legend: **Severity** = Critical / High / Medium / Low. **Category** = FIX (defect) / REDESIGN (wrong
shape) / IMPROVE (hardening/quality). Line numbers verified against the current tree.

---

## 0. Verified negatives (checked, no finding)

1. **No shell interpolation anywhere.** Zero occurrences of `shell=True`, `os.system`, `os.popen`,
   `create_subprocess_shell`, `subprocess.run`, or `shlex.split` in `backend/`. Every spawn is
   `asyncio.create_subprocess_exec` with an argv list: `subprocess_mgr.py:312`, `subprocess_mgr.py:372`,
   `subprocess_mgr.py:840` (taskkill helper), `terminal_service.py:116`. Classic OS-level command
   injection is not present.
2. **Output bounding works.** `MAX_OUTPUT_BYTES` (32 MB/stream, `subprocess_mgr.py:108`) is enforced on
   every drain path: concurrent (`:519-526`, `:919-931`), sequential (`:599`), timeout re-drain
   (`:755-759`), interleaved (`:653`).
3. **Environment is allowlist-based**, not inherited wholesale, for all `SubprocessManager` children
   (`subprocess_mgr.py:155-170`, built at `:944-960`) — but the allowlist itself is over-broad
   (PROC-2).
4. **Diagnostics is ingest-only.** `POST /diagnostics/events` (`diagnostics.py:24-33`) writes a JSON
   line and prints it (`observability/diagnostics.py:46-57`); no probes, no outbound requests, no
   command execution. No SSRF/probe abuse exists on this route.
5. **WebSockets authenticate before `accept()`**: `terminal.py:177-193`, `websocket.py:20`, via
   `websocket_is_authorized` (`local_security.py:48-60`) — subject to the no-token bypass in TERM-1.
6. **No CORS middleware is registered** (`app.py` contains no `CORSMiddleware`), so browsers cannot
   read cross-origin responses.
7. **Timeout escalation exists** for `run()`: SIGTERM → grace → SIGKILL with tree kill on the SIGKILL
   pass (`subprocess_mgr.py:763-779`). `cancel_owner` and `stream()` teardown use SIGKILL tree kill
   directly (`:413`, `:401-404`).

---

## 1. Command injection

### CMD-1 — MEDIUM — FIX
- **Location:** `backend/tools/flash_firmware.py:548-561` (`program_parts = ["program", str(path)]` …
  `" ".join(program_parts)` passed as OpenOCD `-c`)
- **Problem:** The artifact path is spliced unquoted into an OpenOCD `-c` *command script*. A path
  containing spaces corrupts the command, and a path containing `"`/`;` injects additional OpenOCD
  TCL commands (`program`, `reset`, `shutdown`, …) — a second-order interpreter injection that argv
  exec does not protect against, because OpenOCD itself is the interpreter.
- **Fix:** Pass the file via a braced/escaped TCL literal (`program {…path…}`), rejecting paths with
  `{`, `}`, newline; better, resolve the artifact first (see FLASH-1) and hand OpenOCD a
  containment-checked, quoted path. Add a test with a `space; shutdown` filename.

### CMD-2 — VERIFIED NEGATIVE (with cross-ref)
- No further injection sinks found. `platformio_service.py` builds argv lists only
  (`:355-356`, `:384-385`, `:446`); environment values are validated text
  (`_validate_environment_selection`, `:671-683`); `build_firmware._build_args` uses argv lists
  (`build_firmware.py:523-541`); `board_detector` only enumerates ports (`board_detector.py:106-135`).

---

## 2. Process safety

### PROC-1 — MEDIUM — FIX
- **Location:** `backend/runtime/subprocess_mgr.py:836-872` (Windows branch of `_kill_process_tree`),
  callers `:429-431` (`kill_all` first pass), `:769` (timeout SIGTERM pass); docstring claims
  `:10`, `:818-831`
- **Problem:** On Windows, the "graceful" SIGTERM branch calls `proc.terminate()` on the *direct
  child only*. PlatformIO/esptool spawn toolchains (`pio` → `gcc` → `ld`); when the parent is
  terminated its children are orphaned and keep running (still writing output files) until the later
  SIGKILL/taskkill pass — and forever if shutdown is aborted between passes. This contradicts the
  module's own "SIGTERM to process GROUP" contract.
- **Fix:** Use a **Job Object** (`CreateJobObject` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) assigned at
  spawn (`:312-324`, `:372-380`) so no child can outlive the backend, including on crash. Minimum
  fix: on Windows the graceful phase should run `taskkill /PID x /T` (without `/F`) to signal the
  tree, reserving `/T /F` for the kill pass.

### PROC-2 — MEDIUM — FIX
- **Location:** `backend/runtime/subprocess_mgr.py:155-170` (allowlist includes `PYTHONPATH` `:167`,
  `LD_LIBRARY_PATH` `:169`, `COMSPEC` `:160`, `SHELL` `:157`) vs. its own contract at `:948-951`
  ("PYTHONPATH entries that change import resolution" are named as blocked).
- **Problem:** `PYTHONPATH`/`LD_LIBRARY_PATH` are inherited into *Python-based* children (esptool,
  PlatformIO) — an attacker who can influence the backend process environment controls import
  resolution inside every spawned tool (arbitrary code in the child), and `COMSPEC`/`SHELL` redirect
  any child that consults an interpreter variable. This is the exact class the allowlist claims to
  stop, and it undermines PROC-1's containment.
- **Fix:** Remove `PYTHONPATH`, `LD_LIBRARY_PATH`, `COMSPEC`, `SHELL` from the default allowlist; keep
  them only behind an explicit per-`ProcessConfig` opt-in with a comment justifying each caller
  (esptool's need can be met by resolving the interpreter path explicitly).

### PROC-3 — MEDIUM — FIX (latent)
- **Location:** `backend/runtime/subprocess_mgr.py:321-322` (pipes → `DEVNULL` when
  `capture_output=False`), `:498-506` (reader tasks), `:904-905` (`_read_bounded(None)` returns
  immediately), `:552` (unbounded `await proc.wait()`)
- **Problem:** With `capture_output=False`, both reader tasks finish instantly, `pending` is empty,
  the timeout branch at `:506` is skipped, and `proc.wait()` at `:552` runs with **no timeout** —
  `timeout_s` is silently unenforced. No current caller passes `capture_output=False` (verified by
  grep), but it is public API on `ProcessConfig:145`.
- **Fix:** Enforce the deadline independently of the readers: wrap `proc.wait()` in
  `asyncio.wait_for(config.timeout_s - elapsed)` and route through `_handle_timeout`, or start a
  watchdog task before draining.

### PROC-4 — LOW — FIX
- **Location:** `backend/runtime/subprocess_mgr.py:274` (set created), `:409` (grows), never pruned
- **Problem:** `_cancelled_owners` grows without bound over the process lifetime; entries are also
  consulted at `:294`, `:329`, `:358`, `:387`, so a stale owner permanently cancels any future
  operation reusing that id.
- **Fix:** Prune an owner when its execution completes and it has no active processes (hook into
  `cancel_owner` completion / a done-callback), or store owners in a bounded LRU keyed by task id.

### PROC-5 — LOW — FIX
- **Location:** `backend/runtime/subprocess_mgr.py:585` → `:722` (`_drain_sequential` calls
  `_terminate_for_timeout`, then `_handle_timeout` calls it again)
- **Problem:** The sequential timeout path terminates the tree twice, paying SIGTERM + grace
  (`_TIMEOUT_GRACE_S`) twice — up to double the intended teardown latency, and `signal_sent` reported
  to the caller can differ from the signal actually decisive.
- **Fix:** Pass the already-obtained `signal_sent` (and partial bytes) into `_handle_timeout`, or drop
  the first call and let `_handle_timeout` own termination.

### PROC-6 — LOW — IMPROVE
- **Location:** `backend/runtime/subprocess_mgr.py:342-404` (`stream()`)
- **Problem:** `stream()` never applies `config.timeout_s`; a child that never produces EOF runs
  until the caller abandons the generator (teardown then correctly SIGKILLs the tree at `:401-404`).
  The method currently has no callers (grep) — latent API hazard.
- **Fix:** Wrap the yield loop in `asyncio.timeout(config.timeout_s)` and document/implement the
  timeout contract, or delete the unused API.

### PROC-7 — LOW — IMPROVE
- **Location:** `backend/runtime/subprocess_mgr.py:108` (32 MB/stream), semaphore at `:293`
- **Problem:** The memory ceiling is `semaphore_width × 64 MB` (stdout+stderr) plus another up-to-32
  MB read per stream in `_handle_timeout` (`:728`, `:737`); there is no global budget, so a burst of
  concurrent noisy builds can still spike memory substantially.
- **Fix:** Make the per-stream cap configurable per `ProcessConfig` and derive a process-wide budget
  from available memory; build commands can use a smaller cap than diagnostics capture.

---

## 3. Serial safety

### SER-1 — HIGH — FIX
- **Location:** `backend/runtime/serial_runtime.py:537-544` (`finally` **yields** the partial line at
  `:542` and the "monitoring ended" observation at `:544`); consumer
  `backend/tools/serial_monitor.py:217-220` (`await stream.aclose()` in its own `finally`)
- **Problem:** Yielding inside `finally` during generator close raises
  `RuntimeError: async generator ignored GeneratorExit` — **empirically reproduced** with a minimal
  repro. Two consequences: (a) `SerialMonitor.consume()`'s early exits (SUCCESS_PATTERN / TIMEOUT /
  RECONNECT_FAILED) surface the RuntimeError through `observe()`'s `except Exception`
  (`serial_monitor.py:239`), misreporting a successful pattern match as an ERROR outcome; (b) the
  exception fires *before* `self._monitor_task = None` at `:543`, so the monitor-task registration
  leaks and the next `monitor()` raises `SerialRuntimeAlreadyRunningError` (`:520-523`) until
  `disconnect()` clears it (`:396-404`). Tests miss this because `FakeRuntime` has no yield-in-finally
  (`tests/unit/test_serial_monitor.py:72-94`).
- **Fix:** Never yield in `finally`: emit the "monitoring ended" observation *before* teardown in the
  normal path (or push it into the ring buffer), move `_monitor_task = None` to the top of `finally`,
  and add a real-generator test that calls `aclose()` after a pattern match.

### SER-2 — MEDIUM — FIX
- **Location:** `backend/main.py:652` (workflow injects the shared `serial_runtime`, no lock) vs.
  `backend/api/routes/monitor.py:39-61` (`/monitor/start` under `monitor_lock`, builds a *second*
  runtime via `serial_service_factory`); `serial_runtime.py:653` (`exclusive=True`, POSIX-only effect)
- **Problem:** Two independent `SerialRuntime` instances can open the same COM port concurrently —
  `pyserial`'s `exclusive=True` is a POSIX flock and is a no-op on Windows, where COM ports permit
  multiple opens. Both readers then consume the byte stream, splitting/corrupting output for both the
  workflow monitor and the UI monitor, and one close can break the other mid-read.
- **Fix:** Introduce a process-wide port-ownership registry (keyed by normalized port name) taken in
  `SerialRuntime.connect()` and released on close; route *all* openers (workflow-injected runtime,
  `/monitor/start` factory) through it, and fail the second open with `PORT_ALREADY_OWNED`.

### SER-3 — MEDIUM — FIX
- **Location:** `backend/runtime/serial_runtime.py:601-635` (`_await_open` runs
  `asyncio.to_thread(self._open_port_sync, …)` at `:616` **without** `_lifecycle_lock`),
  `:665-667` (assignment `self._port = s`), vs. `disconnect()` `:391-409`
- **Problem:** `disconnect()` can complete between the state check and the in-flight open: it closes
  `self._port` (still `None`), sets state `DISCONNECTED` (`:407`), returns — then `_open_port_sync`
  finishes and assigns a live handle (`:666-667`). Result: state says DISCONNECTED while a COM port
  stays open forever (handle leak; port reported busy to every later caller until process exit).
- **Fix:** After the open completes, re-check state/`_stop_event` under `_lifecycle_lock`; if a stop
  raced in, close the fresh handle immediately and raise. Better: hold `_lifecycle_lock` across
  open+state transition with state `CONNECTING` visible to `disconnect()`.

### SER-4 — MEDIUM — FIX
- **Location:** `backend/tools/serial_monitor.py:123-134` (`if runtime is not None` bypasses the
  `SerialConfig` built from `config.port/baudrate/...`); injection site `backend/main.py:652`
- **Problem:** When the shared runtime is injected (the production workflow path), the monitor
  config's port, baudrate, timeouts, line-length and buffer limits are silently ignored — the
  observation window reads from whatever port/baud the shared runtime happens to be connected with
  (possibly a different board), producing wrong-device data with no signal to the caller.
- **Fix:** If an injected runtime's active configuration disagrees with `SerialMonitorConfig`
  (port/baud), either raise `ValueError` at construction or reconfigure/disconnect+reconnect it
  explicitly; never silently drop the config.

### SER-5 — MEDIUM — FIX
- **Location:** `backend/runtime/serial_runtime.py:995-997`
  (`candidate = old_port or _detect_port(...)` with the `:996-997` detect fallback)
- **Problem:** The auto-detect fallback is dead code: it only runs when `old_port` is falsy, and in
  that case line 995 already called it. A device that re-enumerates on a different COM port (routine
  on Windows after USB glitch) is probed on the stale port every attempt until the budget is
  exhausted → state `FAILED` (`:965-980`) despite the device being present.
- **Fix:** Probe `old_port` first; if that open fails, fall back to `_detect_port()` *within the same
  attempt* before `continue` (`:1003-1007`), and update `old_port` on success.

### SER-6 — LOW — FIX
- **Location:** `backend/runtime/serial_runtime.py:181` (`max_reconnect_attempts: int = 10  # 0 =
  unlimited`), validation `:212-215`, loop `:965-966`
- **Problem:** `0` meaning "unlimited" is a footgun: any config with 0 loops on a wedged port
  forever at `reconnect_delay`, pinning the runtime in `RECONNECTING` and blocking observers until an
  external `disconnect()`.
- **Fix:** Interpret `0` as "no reconnect" (or enforce a wall-clock budget alongside the attempt
  count); keep unlimited behind an explicit sentinel like `-1` with a hard documented ceiling.

### SER-7 — LOW — IMPROVE
- **Location:** `backend/api/schemas/monitor.py:14-26` (port: non-empty, NUL-free, ≤256 chars;
  `baudrate > 0`), `backend/tools/serial_monitor.py:79-99`
- **Problem:** Port accepts any string (`"../../../x"`, `"\x1b[31m"`, 256-char junk) and any
  positive baud (including absurd values) — all reach pyserial, producing late, low-context
  exceptions instead of deterministic 422s; log lines echo the raw value.
- **Fix:** Validate port against `^(COM\d+|/dev/\w+)$`-style patterns (via `serial.tools.list_ports`
  allowlist when possible) and restrict baud to the standard rate list; mirror in
  `SerialMonitorConfig.__post_init__`.

### SER-8 — LOW — IMPROVE
- **Location:** `backend/runtime/serial_runtime.py:963` and `:1012`
- **Problem:** `_reconnect_attempts` is incremented at loop entry *and again* on success, so metrics
  over-count every successful reconnect by one (misleading telemetry used to judge device health).
- **Fix:** Increment in exactly one place (on attempt start), and don't re-increment on success.

---

## 4. Flash / artifact safety

### FLASH-1 — MEDIUM — FIX
- **Location:** `backend/tools/flash_firmware.py:280` (`Path(artifact.path).expanduser()` — no
  `resolve()`, no containment), validation `:317-377` (exists / is_file / size / suffix only)
- **Problem:** The artifact path is validated for shape but never constrained to the build/workspace
  root and never resolved (symlink/junction tricks survive). Any code path that reaches
  `FirmwareFlasher.flash()` with an attacker-influenced `BuildArtifact.path` (artifacts are also
  constructed in `main.py:935`, `build_adapter.py:113`, `agent_runtime/autonomous_loop.py:931`) can
  flash an arbitrary file to the attached device.
- **Fix:** `path = artifact_path.resolve()` then require
  `path.is_relative_to(build_root.resolve())` (reject symlinks/junctions), plus reject non-regular
  files; enforce the same containment at `FirmwareFlasher.flash()` entry, not only in callers.

### FLASH-2 — MEDIUM — FIX
- **Location:** `backend/tools/flash_firmware.py:523` (`offset = "0x0" if _is_merged_esp_image(path)
  else "0x10000"`) with `:609-611` (filename token match: `merged|factory|full-flash|full_flash`)
- **Problem:** The esptool write offset — a device-damage-critical value — is guessed from *filename
  tokens*. A non-merged image named `app_factory.bin` is written at `0x0` (over the bootloader/partition
  table), and a genuinely merged image without the token is written at `0x10000`, both producing a
  bricked or corrupted device with no pre-write check to catch it.
- **Fix:** Detect merged images by content (ESP image magic `0xE9` at offset 0 *and* at `0x10000`, or
  explicit metadata recorded by the builder), require an explicit offset in `FlashConfig` when the
  board family allows several, and refuse to flash on ambiguity rather than guess.

### FLASH-3 — MEDIUM — FIX
- **Location:** `backend/tools/flash_firmware.py:273-378` (`_validate_inputs`), verify handling
  `:246`, `:549`
- **Problem:** There is **no binary-content verification before write**: no ESP image-magic /
  chip-revision header check, no size-vs-flash-size check, no board/chip-vs-image cross-check.
  `config.verify` only adds a post-write read-back (`verify` arg), so a wrong-but-well-formed file is
  fully written to the device before anything can fail.
- **Fix:** Pre-flight in `_validate_inputs`: parse the image header (ESP: magic byte + chip id at
  `0x10002` for merged images), compare against `board.board_type`, and check
  `stat().st_size <= flash_size` (from board profile); return the existing
  `InvalidFirmwareError` classification on mismatch.

### FLASH-4 — LOW — FIX
- **Location:** `backend/tools/flash_firmware.py:614-628` (`_stm32_target_config`, regex over
  artifact path/board text, default `target/stm32f4x.cfg` at `:628`)
- **Problem:** The OpenOCD target script is inferred from free text and **defaults to STM32F4** for
  any unmatched board; selecting the wrong target config makes OpenOCD use the wrong flash
  algorithm/arguments — a device-damage risk for non-F4 STM32 parts.
- **Fix:** Map `board.board_type` → target config explicitly and fail closed
  (`_classified_failure` with `wrong target`) when the mapping is absent; delete the regex guess.

### FLASH-5 — verified-good (context)
Port must match the *detected* board (`:306-316`), suffix allowlist is per board type
(`:366-377`), post-build size drift is rejected (`:354-364`), empty files rejected (`:346-353`),
`verify` defaults to `True` (`:96`). These are real controls — the gaps are pre-write content and
path containment (FLASH-1/3).

---

## 5. Terminal PTY policy + WebSocket auth

### TERM-1 — HIGH — FIX
- **Location:** `backend/api/app.py:676` (`if local_api_token and request.url.path != "/health"`),
  `backend/api/local_security.py:26-35` (host validation itself only runs `if expected`),
  `:48-60` (WS path), token sourcing `app.py:136-145`
- **Problem:** When `FORGEX_LOCAL_TOKEN` is unset — the normal case for any non-desktop run
  (token is only *required* when `FORGEX_DESKTOP=1`, `app.py:137-143`) — **all authentication and
  Host checks are skipped**, and Origin checking degrades to `strict=False`, which permits
  `Origin: null`/`file://` (`local_security.py:117-121`). Any local process (or local page) can then
  call `/terminal/sessions` (+ `/input`) for an arbitrary PowerShell/cmd, `/execute` (see EXE-1),
  `/monitor/start`, `/build`, `/devices`, `/diagnostics/events` with no credential. Because these
  routes do not call `require_local_http_request` themselves (verified: only `agent_runtime`,
  `changes`, `chat`, `harness`, `model_gateway`, `diagnostics` do), the middleware is the *only*
  gate.
- **Fix:** Fail closed: always run `_validate_host` + strict `_validate_origin`, and when no token is
  configured either (a) mint an ephemeral per-launch token at startup and expose it to the desktop
  launcher, or (b) hard-disable the dangerous route classes (terminal, execute, build, flash,
  monitor) with `503 LOCAL_TOKEN_REQUIRED`. Document the chosen threat model explicitly.

### TERM-2 — MEDIUM — REDESIGN
- **Location:** `backend/services/terminal_service.py:551-578` (unrestricted `powershell.exe`/`cmd.exe`
  profiles), `backend/api/routes/terminal.py:63-81` (start), `:106-114` (20 KB arbitrary input),
  `:175-275` (WS: any `session_id` attachable)
- **Problem:** The terminal is a full interactive shell by design, and there is **no session
  ownership**: any authorized client can attach to, read replay of, and type into *any* session
  (`subscribe(session_id, after)` at `:195`, `write` at `:237`), and sessions are not bound to the
  project that created them. Combined with TERM-1 this is unauthenticated local command execution;
  even with a token, one compromised renderer owns every shell.
- **Fix:** (a) Bind sessions to a per-creator capability token minted at `start` and required on
  input/WS; (b) add a restricted profile (allowlisted commands scoped to the workspace) as the
  default, keeping full-shell behind an explicit setting; (c) audit-log every input write with
  session + project id.

### TERM-3 — LOW — FIX
- **Location:** `backend/services/terminal_service.py:122` (`env=os.environ.copy()` in the pipe
  driver; ConPTY inherits the parent env by construction)
- **Problem:** Every terminal child inherits the backend's full environment, including
  `FORGEX_LOCAL_TOKEN` read at `app.py:136` — any shell command (including LLM-suggested ones) can
  read and exfiltrate the API token that guards the whole backend.
- **Fix:** Copy `os.environ` minus secret-bearing keys (`FORGEX_LOCAL_TOKEN`, `FORGEX_HARNESS_TOKEN`,
  provider API keys — reuse the redaction key regex from `observability/diagnostics.py:102`).

### TERM-4 — LOW — FIX
- **Location:** `backend/services/terminal_service.py:331` (insert), `:396` (pop only on `stop`),
  `:281` (`max_events` exists but no session cap/TTL)
- **Problem:** Sessions that exit naturally are never removed from `_sessions` unless the client
  sends `DELETE` — no TTL, no max-session cap — so the dict (each with ≤2000 buffered events) grows
  unboundedly across a long-lived backend, and stale ids remain attachable.
- **Fix:** Prune closed sessions with no subscribers after a TTL (or on every `start()`), and enforce
  `max_sessions` with `429` at `terminal.py:67-81`.

### TERM-5 — LOW — IMPROVE
- **Location:** `backend/services/terminal_service.py:265` (`return self._exit_code or 0`),
  `:243`
- **Problem:** `or 0` collapses `None`/unset to "success" — the exact anti-pattern the codebase
  already fixed elsewhere (`subprocess_mgr.py:553-556` "[FIX-P2] Explicit None check"). Currently
  unreachable in practice because `:243` coerces before `_exited.set()`, but it will silently mask a
  missing exit status.
- **Fix:** `return 0 if self._exit_code is None else self._exit_code` and treat unexpected `None` as
  `-1`.

---

## 6. Diagnostics

### DIAG-0 — VERIFIED NEGATIVE
`POST /diagnostics/events` (`diagnostics.py:24-33`) only validates + ingests; `emit_diagnostic_event`
writes a JSONL line and prints (`observability/diagnostics.py:46-58`). No probes, no outbound
requests, no command execution — no probe-abuse/SSRF surface here.

### DIAG-1 — LOW — FIX
- **Location:** `backend/api/routes/diagnostics.py:27-33`
  (`emit_diagnostic_event(component=…, event=…, level=…, message=…, **body.fields)`)
- **Problem:** A `fields` payload containing `component`, `event`, `level`, or `message` raises
  `TypeError: got multiple values for keyword argument` inside the route → unhandled 500 on an
  ingest endpoint (reachable with `extra="allow"`-style dicts; `fields` is `dict[str, Any]` at
  `:21`).
- **Fix:** Merge explicitly — reject reserved keys with `422` in a `field_validator`, or build
  `payload = {"component": …, …, "fields": body.fields}` instead of `**`-expansion.

### DIAG-2 — LOW — FIX
- **Location:** `backend/observability/diagnostics.py:117-123` (`_sanitize_string` strips NUL +
  secrets only), `:152-167` (`_print_condensed` prints `event`/`message` raw)
- **Problem:** Renderer-supplied `component`/`event`/`message` are length-limited but keep ANSI/
  control sequences and carry no provenance marker — a local caller can forge convincing
  `component=auth` / `event=login_success` lines and inject terminal escapes that repaint or hide
  prior log output during incident review.
- **Fix:** Strip `\x1b…m`-style CSI sequences (and all C0 controls except `\n`/`\t`) in
  `_sanitize_string`, and stamp ingested events with `source=renderer` (forced, overriding any
  client-supplied field) in `_event_payload` (`:65-85`).

### DIAG-3 — LOW — FIX
- **Location:** `backend/api/routes/diagnostics.py:21` (`fields: dict[str, Any]`, no size/count cap);
  per-value caps only at `observability/diagnostics.py:88-108` (depth 8, 16 KB/str, 100 seq items)
- **Problem:** Unbounded *key count* means an arbitrarily large `fields` dict (thousands of 16 KB
  values) is redacted, JSON-serialized, appended to the live log and printed — a cheap
  CPU/disk-flood amplification against the log pipeline, plus unbounded log-line size.
- **Fix:** Cap `fields` (e.g., ≤32 keys, overall serialized ≤64 KB) at the schema with `422`, and
  drop-or-truncate overflow with an explicit marker.

---

## 7. execute_prompt / workspace_root / races

### EXE-1 — CRITICAL — FIX  *(known issue — full propagation documented)*
- **Location (root cause):** `backend/api/schemas/execution.py:16` (`workspace_root: str | None`,
  1–4096 chars) and `:49-57` (validator checks **only** non-empty + NUL-free — no path semantics).
- **Propagation chain (verified end-to-end):**
  1. `backend/api/routes/execute.py:75-76` → `_active_workspace_from_root()` `:288-303` resolves
     *any* absolute path with no containment check; `_apply_execute_overrides()` `:314-321`
     re-resolves and **overwrites** `rootPath`/`platformioIniPath` even when a `project_id` was
     supplied (`:77-84`).
  2. `backend/main.py:99` stores it as `Task.metadata["active_workspace"]`;
     `_context_from_task` reads it back at `:1048` and feeds
     `ExecutionContext.project_path` at `:1076`.
  3. `backend/workflow/adapters/generation_adapter.py:447-467` (`_workspace_project_path`) resolves
     `context.project_path` → `BuildConfig.project_dir` (`:263-291`).
  4. `backend/tools/build_firmware.py:215-221` runs `platformio run` with
     `cwd=str(project.root)` — **arbitrary working directory**.
- **Problem:** A client-supplied directory becomes the build/generation root. Impact ladder:
  (a) read/write outside the workspace — generation mode `generate_into_open_folder` writes files
  into that root; (b) PlatformIO executes project `extra_scripts` (Python) from the project dir, so
  pointing `workspace_root` at an attacker-crafted directory containing a `platformio.ini` with
  `extra_scripts = pre:evil.py` yields **arbitrary code execution as the backend user**; (c) combined
  with TERM-1 (no token in non-desktop mode) this is unauthenticated local RCE/file-write.
- **Fix:** In the schema validator (or a shared resolver called from both `execute.py:76` and
  `main.py:_context_from_task` as defense-in-depth): `root = Path(value).expanduser().resolve()`,
  then require `root.is_relative_to(configured_workspace_root)` **or** membership in the registered
  project registry; reject symlinks/junction escapes after `resolve()`; reject when
  `platformio.ini`-bearing roots are outside the allowlist. Additionally require the resolved
  `project_dir` in `build_firmware._validate_project` (`:452`) to pass the same containment.

### EXE-2 — MEDIUM — FIX
- **Location:** `backend/main.py:351-416` (`for attempt in range(1, 4)` repair loop: LLM generation +
  build per attempt), entry `backend/api/routes/execute.py:99-105` (`hub.begin`), cancel-only escape
  `:221-245`
- **Problem:** There is no overall wall-clock deadline on `execute_prompt`: three attempts each
  combining an LLM call and a build (600 s timeout class) can pin the task id in `hub.begin`, occupy
  the subprocess semaphore, and stream progress for tens of minutes; nothing auto-expires a hung or
  abandoned execution — only manual `/execute/{task_id}/cancel`.
- **Fix:** Wrap the repair loop (or `execute_prompt` as a whole) in `asyncio.timeout(total_budget)`
  with a per-attempt budget; on expiry emit `WORKFLOW_FAILED` with `deadline_exceeded` and release
  `hub.begin` state; add an idle-TTL sweep for registered task ids.

### EXE-3 — LOW — FIX
- **Location:** `backend/api/routes/execute.py:205-211` (task created, *then* registered) vs.
  cleanup `:186-190` (pops only if the entry already exists)
- **Problem:** If `run_workflow` finishes/fails before the registration at `:207`, its `finally` finds
  no entry and pops nothing; the subsequent insert leaves a permanently stale `_active_executions`
  entry — memory leak, and `cancel_workflow` then reports `404` (`:236-237`) without ever removing it.
- **Fix:** Insert the entry (or a placeholder) *before* `create_task`, or have `finally` remove by
  task-id unconditionally and re-check `task.done()` after insert.

### EXE-4 — LOW — IMPROVE
- **Location:** `backend/api/routes/execute.py:292` (`f"workspace-{abs(hash(str(root).casefold()))}"`)
- **Problem:** `hash()` is process-randomized (`PYTHONHASHSEED`), so synthetic workspace ids change
  across restarts — any persisted correlation keyed on them breaks silently.
- **Fix:** Use a stable digest (`sha256(str(root).casefold())[:16]`).

### EXE-5 — LOW — FIX
- **Location:** `backend/runtime/result.py:294`, `:301-305` (bound only), `:315`
  (`api_dict` exposes `raw_output`) vs. redaction that exists only on the workflow path
  (`backend/main.py:521-526`) and in diagnostics (`observability/diagnostics.py:117-123`)
- **Problem:** `FailureResult.raw_output` (up to 4096 chars of toolchain stdout/stderr) is returned
  **unredacted** through `api_dict()` and persisted to disk via `build.py:55-59`
  (`save_artifact("build_report.json", api_dict)`), so any secret that leaked into compiler/tool
  output (env-derived tokens echoed by verbose tools) lands in API responses and stored reports.
- **Fix:** Apply the existing redaction regex in `FailureResult.__post_init__` (where truncation
  already runs, `:300-306`) so every consumer inherits it; keep `raw_output` internal-only if a
  verbose flag is needed.

### EXE-6 — verified-good (context)
Task-id format is validated at both schema (`schemas/execution.py:31-38`) and route
(`execute.py:359-364`); `hub.begin` prevents id reuse (`:99-105`); build command logging
(`main.py:613-618`) logs only the project dir, and diagnostics redaction covers the verbose paths.

---

## 8. Architecture — split seams for the oversized files

| File | Lines | Seam | New units (responsibility) | Why it matters to this audit |
|---|---|---|---|---|
| `backend/main.py` | ~1288 | 5-way | `workflow/executor.py` (`execute_prompt`/`execute_task` orchestration, `:80-311`); `workflow/repair.py` (`_repair_build_failures` + failure/diagnostics helpers `:313-533`); `workflow/invocations.py` (builders `:588-679`); `workflow/validators.py` (`_validate_*` `:830-1038`); `workflow/context.py` (`_context_from_task` `:1039+`) | Isolates the EXE-1 propagation point (`_context_from_task`) and EXE-2 loop into one testable module; validators become a chokepoint where containment checks (EXE-1) can be enforced once. |
| `backend/runtime/subprocess_mgr.py` | 963 | 4-way | `runtime/process/config.py` (`ProcessConfig` + env allowlist + `_build_env` `:139-170`, `:944+`); `runtime/process/spawn.py` (`run`/`stream` `:278-404`); `runtime/process/drain.py` (strategies `:456-698` + `_read_bounded`); `runtime/process/kill.py` (tree/terminate/timeout `:700-885`) with `SubprocessManager` as facade | Kill policy (PROC-1 Job Objects) and env policy (PROC-2 allowlist) evolve independently of drain code; the Windows-vs-POSIX divergence lives in one file. |
| `backend/runtime/serial_runtime.py` | ~1096 | 4-way | `runtime/serial/config.py` (models/validation `:150-230`); `runtime/serial/port.py` (open/close/detect `:595-677`); `runtime/serial/monitor_loop.py` (`:683-900`); `runtime/serial/reconnect.py` (FSM `:920-1040`) behind the `SerialRuntime` facade holding `_lifecycle_lock` | Makes the SER-3 lock scope and SER-5 probe strategy single-module fixes; config module gives SER-6/SER-7 a real validation boundary. |
| `backend/services/platformio_service.py` | 781 | 4-way | `services/pio/models.py` (`:107-195`); `services/pio/validation.py` (`validate_project`, `_validate_environment_selection`, `_validate_source_directory` `:273-686`); `services/pio/executable.py` (`resolve_platformio_executable` `:503+`); `services/pio/client.py` (`build/clean/install/_run` `:296-466`) | Separates "what may run" (validation, the containment chokepoint for EXE-1's build side) from "how it runs" (argv + timeout). |
| `backend/services/terminal_service.py` | 603 | 3-way | `services/terminal/drivers.py` (`_PipeTerminalDriver`/`_ConPtyTerminalDriver` `:107-275`); `services/terminal/profiles.py` (`_terminal_profiles` + capability detection `:551-603`); `services/terminal/session.py` (`TerminalService` `:278+`) | TERM-2 (session ownership/reaping) becomes a session-manager concern; TERM-3 (env stripping) and TERM-5 (exit-code) are driver-local. |
| `backend/runtime/result.py` | 949 | 3-way | `runtime/result/base.py` (`Result`/`FailureResult` + contracts `:203-330`); `runtime/result/tool.py` (`BuildResult`/`FlashResult`/`ObserveResult`/`SimulationResult` `:332-592`); `runtime/result/execution.py` (`ExecutionResult` + builder `:594-949`) | EXE-5 redaction belongs in `FailureResult.__post_init__`; `api_dict` contracts get dedicated serialization tests per family. |

Cross-cutting seam worth noting: **authentication** — `local_security.py` is the right shape, but
route coverage is implicit (only some routers call it; `app.py:676` is the real gate). Making
`require_local_http_request` an explicit router-level dependency (`Depends`) for `terminal`,
`execute`, `build`, `monitor`, `devices` would make TERM-1's fail-closed behavior structural rather
than a middleware condition.

---

## Severity-sorted findings table

| # | Severity | Category | Finding | Location | Fix in one line |
|---|---|---|---|---|---|
| EXE-1 | **Critical** | FIX | Unbounded `workspace_root` → arbitrary cwd for `pio run` (+ project `extra_scripts` RCE) and out-of-workspace file writes; fully propagated schema→route→context→build | `schemas/execution.py:16,49-57`; `execute.py:75-84,288-330`; `main.py:99,1048,1076`; `generation_adapter.py:447-467`; `build_firmware.py:215-221` | `resolve()` + enforce containment under workspace/registered-project root at schema **and** at `_context_from_task`/`_validate_project` |
| TERM-1 | High | FIX | No `FORGEX_LOCAL_TOKEN` (all non-desktop runs) ⇒ Host+auth checks skipped entirely; `/terminal` becomes an unauthenticated local shell | `app.py:676`, `app.py:136-145`; `local_security.py:26-35,48-60` | Always validate Host/strict Origin; mint ephemeral token at startup or hard-disable terminal/execute/build routes without one |
| SER-1 | High | FIX | `monitor()` yields in `finally` → `RuntimeError` on `aclose()`: success-pattern outcomes misreported as ERROR + `_monitor_task` leak → `AlreadyRunningError` | `serial_runtime.py:537-544`; `serial_monitor.py:217-220,239` | No yields in `finally`; clear `_monitor_task` first; add real-generator `aclose()` test |
| SER-2 | Medium | FIX | Two `SerialRuntime` instances can open one COM port (workflow injects shared runtime unlocked; `/monitor/start` builds a second; `exclusive=True` is POSIX-only) | `main.py:652`; `monitor.py:39-61`; `serial_runtime.py:653` | Process-wide port-ownership registry; second open fails `PORT_ALREADY_OWNED` |
| SER-3 | Medium | FIX | Open/disconnect race leaks a live COM handle while state = `DISCONNECTED` | `serial_runtime.py:601-635,665-667` vs `:391-409` | Re-check stop/state under `_lifecycle_lock` after open; close on lost race |
| SER-4 | Medium | FIX | Injected runtime ignores `SerialMonitorConfig` port/baud/limits — silent wrong-device reads | `serial_monitor.py:123-134`; `main.py:652` | Raise or reconfigure when injected runtime ≠ config |
| SER-5 | Medium | FIX | Reconnect auto-detect fallback is dead code → stale-port death loop after USB re-enumeration → `FAILED` | `serial_runtime.py:995-997` | Probe old port, fall back to `_detect_port()` in the same attempt |
| PROC-1 | Medium | FIX | Windows "graceful" kill terminates only the direct child; toolchain grandchildren orphaned (contradicts tree-kill contract) | `subprocess_mgr.py:836-872`, `:429-431`, `:10` | Job Object per spawned process; or `taskkill /T` (no `/F`) on the graceful pass |
| PROC-2 | Medium | FIX | Allowlist ships `PYTHONPATH`/`LD_LIBRARY_PATH`/`COMSPEC`/`SHELL` — import-path hijack into Python-based children; contradicts own docstring | `subprocess_mgr.py:155-170` vs `:948-951` | Drop those four; per-config opt-in only |
| PROC-3 | Medium | FIX (latent) | `capture_output=False` makes both readers finish instantly ⇒ timeout branch skipped ⇒ unbounded `proc.wait()` | `subprocess_mgr.py:321-322,498-506,552,904-905` | Enforce deadline on `proc.wait()` independently of readers |
| FLASH-1 | Medium | FIX | Artifact path never resolved/contained to build root before flashing | `flash_firmware.py:280,317-377` | `resolve()` + `is_relative_to(build_root)` at flash entry |
| FLASH-2 | Medium | FIX | esptool offset guessed from **filename tokens** — wrong offset can overwrite bootloader (device damage) | `flash_firmware.py:523,609-611` | Content-based merged-image detection; refuse ambiguity |
| FLASH-3 | Medium | FIX | No pre-write binary/chip verification (magic/chip-id/size-vs-flash); `verify` is post-write only | `flash_firmware.py:273-378,246,549` | Parse image header + compare to board; reject on mismatch before writing |
| CMD-1 | Medium | FIX | OpenOCD `-c` script embeds unquoted artifact path → OpenOCD command-language injection/breakage | `flash_firmware.py:548-561` | Braced/escaped TCL path literal, reject `{}`/newline |
| TERM-2 | Medium | REDESIGN | Unrestricted shell profiles + no session ownership (any client attaches to any `session_id`) | `terminal_service.py:551-578`; `terminal.py:88-172,175-275` | Per-session capability token, restricted default profile, input audit log |
| EXE-2 | Medium | FIX | No overall deadline: 3×(LLM+600 s build) repair loop; only manual cancel; `hub.begin` pinned | `main.py:351-416`; `execute.py:99,221-245` | `asyncio.timeout(total_budget)` + task-id TTL sweep |
| SER-6 | Low | FIX | `max_reconnect_attempts=0` = unlimited ⇒ infinite reconnect on wedged port | `serial_runtime.py:181,212-215,965-966` | 0 = no reconnect; explicit sentinel + wall-clock budget |
| SER-7 | Low | IMPROVE | Port/baud validation is only NUL/empty/`>0` — junk reaches pyserial, echoed into logs | `schemas/monitor.py:14-26`; `serial_monitor.py:79-99` | Port pattern/`list_ports` allowlist + standard baud list |
| SER-8 | Low | IMPROVE | `_reconnect_attempts` double-incremented on success (misleading telemetry) | `serial_runtime.py:963,1012` | Increment once, on attempt start |
| PROC-4 | Low | FIX | `_cancelled_owners` grows forever, never pruned | `subprocess_mgr.py:274,409` | Prune when execution ends and no active procs |
| PROC-5 | Low | FIX | Sequential timeout path terminates twice (double SIGTERM + grace) | `subprocess_mgr.py:585→722,763-779` | Pass `signal_sent` through; terminate once |
| PROC-6 | Low | IMPROVE | `stream()` ignores `timeout_s` entirely (no callers — latent) | `subprocess_mgr.py:342-404` | `asyncio.timeout` around the yield loop or remove API |
| PROC-7 | Low | IMPROVE | No global output-memory budget (32 MB × streams × concurrency, +timeout re-drain) | `subprocess_mgr.py:108,293,728,737` | Per-config cap + process-wide budget |
| FLASH-4 | Low | FIX | STM32 OpenOCD target regex-guessed, defaults to F4 for unmatched boards | `flash_firmware.py:614-628` | Explicit `board_type → target` map, fail closed |
| TERM-3 | Low | FIX | Terminal children inherit full env incl. `FORGEX_LOCAL_TOKEN` → token exfiltration | `terminal_service.py:122`; `app.py:136` | Strip secret env keys before spawn |
| TERM-4 | Low | FIX | Closed terminal sessions never reaped (pop only on DELETE; no TTL/cap) | `terminal_service.py:331,396` | TTL-prune closed, unsubscribed sessions; `max_sessions` cap |
| TERM-5 | Low | IMPROVE | ConPTY `wait` returns `_exit_code or 0` masking `None` as success | `terminal_service.py:265,243` | Explicit `None` handling (as [FIX-P2] elsewhere) |
| DIAG-1 | Low | FIX | `**body.fields` collides with reserved kwargs → unhandled 500 | `diagnostics.py:27-33` | Reject reserved keys (`422`) or merge via dict |
| DIAG-2 | Low | FIX | Log spoofing + ANSI/control-char injection through renderer ingest | `diagnostics.py:16-33`; `observability/diagnostics.py:117-123,152-167` | Strip ANSI/C0 controls; force `source=renderer` |
| DIAG-3 | Low | FIX | `fields` dict unbounded (key count) → log/CPU flood | `diagnostics.py:21`; `observability/diagnostics.py:88-108` | Cap keys (≤32) and serialized size (≤64 KB) |
| EXE-3 | Low | FIX | Register-after-`create_task` race leaks stale `_active_executions` entries | `execute.py:205-211` vs `:186-190`, `:234-237` | Insert placeholder before `create_task`; pop unconditionally in `finally` |
| EXE-4 | Low | IMPROVE | Workspace id from `abs(hash(...))` is per-process randomized | `execute.py:292` | Stable `sha256` digest |
| EXE-5 | Low | FIX | `FailureResult.raw_output` exposed via `api_dict` unredacted; persisted in `build_report.json` | `result.py:294,300-315`; `build.py:55-59`; redaction exists at `main.py:521-526` | Redact in `FailureResult.__post_init__` |

Counts: 1 Critical, 2 High, 14 Medium, 17 Low (34 findings), plus 7 verified negatives and 6
architecture seams.
