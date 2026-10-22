# Verification and ESP32 firmware repair — 7 October 2026

Implemented the verification fixes in ForgeX and applied the repaired firmware project to
`C:\Users\diwan\OneDrive\Desktop\simple esp32 project`. All 17 applied files match the tested source hashes.

## Verification fixes

- Evidence can use `{path,start_line,end_line}`. ForgeX extracts the exact current source text, validates
  inclusive ranges (up to 80 lines / 4096 characters), and records its content hash. Existing exact quotes remain supported.
- Comment detection scans each span in full-file context. A comment followed by executable code is accepted;
  comments-only citations are rejected. Behavior still requires source evidence, and final completion still requires
  independent semantic review of the current revision.
- Verification instructions request authoritative requirement IDs in batches of at most eight, using a JSON array.
  The bounded structured-argument decoder was checked with valid encoded arrays, malformed JSON, wrong types,
  duplicate keys and non-finite values. Invalid arguments remain rejected.
- Recovery prompts after a successful build now identify missing/rejected evidence IDs. Corrected references
  count as progress; the bounded no-progress stop remains active. The task projection distinguishes evidence blockers
  from compiler/dependency blockers.
- Long specifications group complete instructions with headings, lists, diagrams and examples. The upgrade preserves
  all active text, unaffected IDs, superseded obligations and revision history; it runs on continuation without rewriting
  historical journals. Hardware/component and documentation deliverables can cite the relevant README sections.
- Provider conversation history is compacted to a conservative 64,000-character target while preserving the user task
  and complete tool exchanges. Generation reserves input budget for later review instead of raising the overall limit.
  Copied activity panels remain durable diagnostic data and are omitted from repeated generation prompts.
- An empty original folder can be inspected through its intact saved draft in the same project/session. Draft ownership,
  paths, file inventory and hashes are checked. Read-only tool permissions remain enforced, with explicit recovery guidance
  when a model requests an unavailable editing tool.
- Verification activity reports accepted, rejected and missing counts, with independent review shown as pending.
  A tool returning successfully no longer presents reference collection as completed requirement verification.

Changed runtime files: task_contract.py, tool_contracts.py, task_state.py, tool_runtime.py, router_planner.py,
autonomous_loop.py and orchestrator.py under backend/agent_runtime.

## Firmware fixes

- Detect short above-threshold ADC pulses immediately and require a full below-threshold quiet period to rearm.
- Sample the detector once per loop and dispatch the captured event. Preserve new sequences arriving after a timeout
  or during feedback. Remove blocking delays and serial-connection waits.
- Debounce and initialize the active-low button with INPUT_PULLUP. A long press enters learning once;
  releasing it does not clear the learning sequence.
- Use a five-second learning deadline and a two-second sequence-silence deadline, including no-knock/single-knock cases.
  Bound sequences to eleven knocks; reject overflow rather than recognizing truncated data.
- Correct Pattern C to three knocks / two intervals. Pattern A gets one short beep; Pattern C gets two separated
  nonblocking beeps and blue feedback. Explicit color selection cancels obsolete flashes.
- Store a versioned, bounded, checksummed pattern in one short-key NVS blob. Check write results, close handles per
  operation, validate corrupt records before allocating/committing, and retain the previous learned pattern on failed saves.
- Keep rollover-safe elapsed-time arithmetic, use an unsigned LED timer, and document GPIO mapping, circuitry,
  calibration, wiring, architecture, diagnostics and hardware testing limits.

## Validation

- Python runtime/API regression suite: **245 passed, 1 skipped**. The skipped Windows test requires unavailable
  symlink privileges. Two pre-existing FastAPI/Starlette deprecation warnings remain.
- C++ host checks: **passed**, compiling and executing the real firmware modules and application loop with hardware
  API stubs. Covered short pulses, ringing, tolerance boundaries, intervals, rollover, overflow, once-per-loop sampling,
  button bounce/short/long presses, learning timeout, NVS restart roundtrip, corrupt records and failed opens/writes.
- Final PlatformIO `esp32dev` firmware build: **passed**. RAM 22,024 / 327,680 bytes; flash 305,457 / 1,310,720 bytes.
- Firmware SHA-256: `fa31e482a15da6bd1eda91b1146e7b37b9fb283923878d94ada6c2acd6068c20`.
- Runtime diff whitespace check: **passed**.
- Reloaded live backend: **healthy**, with the tested source fingerprint loaded.
- Repaired ChangeSet: `changeset-ecf5cdaf087d450a87db35b86f809496` — applied with a transactional undo journal.
- Original saved draft `changeset-cc098de46bc24517ba3547fb5f62e62c` and historical failed runs remain preserved.
- Tested backend source fingerprint: `8e2461d922bcfa6db3df572779aa9b58e32b437c21260ff0cc140872b7bfe08a`.

No firmware was flashed. Physical knock recognition, calibration, circuit voltage protection and actual on-device
persistence still need ESP32 hardware checks. No new full production model job was used as validation; the fixes were
verified through the runtime/API regression suite and actual local firmware compilation/host execution.

Evidence: repair-manifest.json, esp32-smart-knock/.host-build/results.log and the repaired project alongside this report.
