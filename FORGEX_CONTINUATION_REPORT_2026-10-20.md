# ForgeX continuation — 20 October 2026

## Delivery status

This is a **complete ForgeX source archive** containing the existing integrated patch plus the additional changes below. It is **not a certified production release**. The source code contains no bundled pre-built Windows installer, Node model-worker bundle, or PlatformIO toolchain.

### Changes in this continuation (over the preceding patched source)

1. **Isolated desktop compilation:** When the desktop service has an artifact store, each PlatformIO build reads a private byte-copied project snapshot; it no longer directly compiles mutable Monaco editor files. The snapshot excludes previous `.pio` outputs, detects symlinks, checks file count and total size, and verifies file hashes after capture. A concurrent live file edit cannot alter captured bytes. Files change in the live project after the snapshot, and the build-operation revision check still marks old results stale.
2. **Persistent firmware output:** Successful firmware files are copied from the temporary build into the persistent artifact store with SHA-256 recorded in metadata. Unsupported or failed flash manifests remain non-flash-ready. On artifact-storage failure, the result cannot advertise usable firmware output.
3. **Bounded provider requests:** One absolute request timeout now encompasses waiting for a provider slot, execution and retry backoff, for both non-streaming and streaming model calls. Explicit earlier agent/turn deadlines continue to take precedence. The timeout returns a typed `PROVIDER_REQUEST_DEADLINE_EXCEEDED` error and cancels the pending call.
4. **Generation rollback regression:** Direct generated-file persistence and project manifest updates now participate in generation transaction hash accounting and atomic writes. A rollback cannot silently overwrite a changed file, and rollback conflicts are returned as explicit failure context instead of masking the original generation error.
5. **Memory/context compaction:** Preserves high-priority actionable interface-repair diagnostics at the end of compacted context instead of burying them behind a general source-contract summary.
6. **External file editing:** Performs an additional file revision check immediately before atomic replacement, narrowing the lost-update window after fsync. The existing process-local lock is still not a fully OS-atomic compare-and-swap for arbitrary third-party editors.
7. **Eight new regression tests** for snapshot isolation, symlinks, stale build output exclusion, artifact persistence, tamper detection, retry deadline, and streaming cancellation.

### Validation on this environment

- **143 passed, 0 failed** in the final selected regression suite (new tests, prior patch regressions, model router, PlatformIO service, generation, recovery context, API routes).
- `python -m compileall -q backend` — passed.
- `node scripts/scan-release-content.mjs --source-tree .` — passed.
- An earlier broad backend run stopped after **1,372 passed, 12 failed, 5 skipped**. Two confirmed regressions from that run were fixed afterward and their targeted tests pass. Five other failures need the absent compiled model worker. Five rely on outdated test-mock signatures. The *entire* broad suite has not been rerun green.
- No `npm ci`, production Next.js typecheck, Electron Windows packaging or real provider qualification was completed here; this environment has Node 22.16, whereas the project requires Node >=22.19. No real PlatformIO firmware compilation or physical ESP32 testing was performed.

### Remaining release blockers

1. **Cross-process revision coordination / rollback and external editors:** This patch reduces races but does not provide fully durable compare-and-swap against other native editors, AI ChangeSet writers, or multiple backend processes. Manual merge UI and durable undo history are outstanding.
2. **Windows host validation:** Test the local FastAPI lifecycle, Electron/Next.js frontend, packaged binaries, CLI workers, user installation and crash-restart recovery on Windows 10/11.
3. **Model-worker dependencies:** Build `dist/model-router/worker.cjs`, run router/harness integration tests, then qualify live provider compatibility, real retry behaviour, cancellation and usage billing with test credentials.
4. **Real PlatformIO regression:** Snapshot compilation is new and can affect projects that intentionally reference absolute paths, local dependencies, or custom directory settings. Perform real builds on Arduino ESP32, ESP32-S3, ESP32-C3 and ESP-IDF, and validate diagnostic path mapping. The source copy does **not** sandbox arbitrary toolchain execution.
5. **Lifecycle and data durability:** Test backups/migrations, cross-process build-operation leases, power-loss recovery, security permission boundaries and performance under many concurrent sessions.
6. **Flash qualification:** The current flash adapter remains restricted to supported upload layouts. Do not infer flash readiness solely from successful compilation.

### Suggested Windows smoke test (no hardware required)

Open a new terminal in the extracted top-level directory, use Python and Node versions listed in the README, and run:

```
python -m pytest -q tests/unit/test_2026_continuation.py tests/unit/test_2026_review_fixes.py
npm ci
npm --prefix frontend ci
npm run build:model-router
npm run typecheck:frontend
npm run build:frontend
npm run test:model-router
npm run test:electron
```

Install PlatformIO before attempting a real build. Make a backup of existing firmware projects and use a copied workspace for the first run. This package is a source build, not an installer.

### Technical references informing the approach

- PlatformIO build directories: https://docs.platformio.org/en/latest/projectconf/sections/platformio/options/directory/build_dir.html
- PlatformIO workspace directories: https://docs.platformio.org/en/stable/projectconf/sections/platformio/options/directory/workspace_dir.html
- Python asyncio deadlines: https://docs.python.org/3/library/asyncio-task.html#timeouts
- OpenAI agent sandbox and approvals: https://openai.com/index/running-codex-safely/
- OpenCode agents and permissions: https://github.com/anomalyco/opencode

These references informed the design; they are **not proof** of Windows or live-provider compatibility.
