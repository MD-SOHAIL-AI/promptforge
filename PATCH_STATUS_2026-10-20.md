# ForgeX integrated software patch — 20 October 2026

## Status: patched and selectively tested; NOT production-certified

This is a modified source release based on the exact uploaded ZIP. The original archive has not been modified.

**Changed files:** 24 existing source/test files plus one new regression suite.  **Selected Python tests:** 192 passed (verified after the final source changes). Python `compileall` passed. Frontend changed-file TypeScript syntax parsing passed; the full UI build/typecheck did not.

## Implemented corrections

- **Manual editor/data integrity:** SHA-256 baseline revisions on file reads and writes; managed save compare-and-write under persistence lock; 409 conflicts instead of clobbering newer changes; 428 when old clients omit a baseline; atomic saves for external files; Nexus save uses newest editor content; guard dirty tab close, file deletion/rename, project switching and refresh to preserve unsaved text.
- **Build records:** Unique build records instead of a shared project ID; serialize builds per project within the backend process; propagate build-history persistence errors; flash button disabled when metadata doesn't report a flash-ready firmware image.
- **Agent/project memory:** Reconcile conflicting decisions across sessions; newest-first memory retrieval; invalidate facts based on affected files rather than unrelated project edits; distinguish failed build attempts from verified builds.
- **Provider layer:** Fix provider/model mismatch on streaming and chat provider switch; enable Cerebras account model discovery; apply NVIDIA Nemotron options only to qualified Nemotron models; reject non-loopback HTTP endpoints when configuring or executing model calls; avoid assuming NVIDIA models are free.
- **Compiler and generation:** Treat absolute Windows/UNC compiler paths safely on non-Windows hosts; strip ANSI escape formatting before parsing diagnostics; make chunked PlatformIO configuration board/framework aware; handle cancelled generation with rollback; prevent rollback overwriting more recent edits; structural validation respects ESP32-S3 and ESP-IDF targets.
- **Regressions:** New `tests/unit/test_2026_review_fixes.py` exercises stale writes, scoped memory and cross-session choices, compiler diagnostics, serialized builds, HTTPS policy, generation rollback and ESP32-S3 config.

## Important residual work — DO NOT label this package production ready

1. **Immutable compilation input:** Builds are serialized within one backend process, but a compiler can still read mutable workspace files while users or other processes edit them. Build/flash provenance must be tied to a content-addressed snapshot and verified artifact digest.
2. **Cross-process consistency:** Locks are process-local; multiprocess or externally modified project files and AI ChangeSet commits need a shared durable compare-and-swap/transaction mechanism with revision validation across all writers.
3. **Provider reliability and streaming:** End-to-end total request budget across queue/retries, incremental agent stream, reliable cancel/recovery, diagnostics, per-provider real compatibility, and load tests remain outstanding.
4. **Artifact qualification:** ESP32-S3/C3 flash packaging, build-output sealing and real PlatformIO compilation/real-provider verification were NOT performed.
5. **Windows desktop and UI:** Full Next.js typecheck/build, Playwright against the real backend, Electron installer signing/startup, Windows-only path checks and crash/restart resilience have not been executed.
6. **User recovery:** In-editor three-way merge/diff interface for 409 conflicts, persistent manual undo/version history, comprehensive restore after failures, OS filesystem watchers and comprehensive rename/delete race handling need further engineering.
7. **Security/performance:** Full tool permissions/security assessment, durable operation journal and repeated cancellation/latency tests are incomplete.
8. **Hardware:** Physical ESP32 build/flash/serial runs are out of scope, as requested.

## Verification actually performed

- Selected Python tests: **192 passed**, 0 failed in that selection. File: `ForgeX_Selected_Python_Tests_2026-10-20.log` supplied separately.
- Python backend syntax: `python -m compileall -q backend` passed.
- Modified TypeScript sources: syntax/transpilation parsing, no reported syntax errors. **Not** a typecheck or production frontend build.
- Full Python suite did not complete; it timed out early. Additional model-engine integration tests cannot run without the built `dist/model-router/worker.cjs` and compatible Node/npm dependency installation. A later model-engine-focused run had 44 passing and 20 failing because the compiled worker was absent. These are **verification blocks**, not all confirmed new regressions.
- No external provider credentials were used, so actual latency and model access were not confirmed.

## How to use

Unzip the project into a separate folder (don't overlay the old project without a backup), use its existing README to install the exact pinned Python and Node dependencies, and run `python -m pytest -q tests/unit/test_2026_review_fixes.py` followed by the repository's full verification workflow. Run `npm ci` and `npm run build:model-router` on a machine with the required Node version before full model-engine integration tests. Validate desktop builds in Windows. Perform a full backup and migration rehearsal before testing this patch on working firmware projects.

## Changed original files

- `backend/agent_runtime/build_diagnostics.py`
- `backend/agent_runtime/chat_service.py`
- `backend/agent_runtime/memory.py`
- `backend/agent_runtime/orchestration_store.py`
- `backend/agent_runtime/orchestrator.py`
- `backend/api/routes/build.py`
- `backend/api/routes/files.py`
- `backend/api/schemas/files.py`
- `backend/model_router/providers/opencode.py`
- `backend/model_router/registry.py`
- `backend/model_router/router.py`
- `backend/services/build_operations.py`
- `backend/services/chunked_generation_writer.py`
- `backend/services/code_generation_service.py`
- `backend/services/generation_validators.py`
- `backend/services/platformio_service.py`
- `backend/services/project_service.py`
- `frontend/components/nexus/work-canvases.tsx`
- `frontend/hooks/use-promptforge-workspace.ts`
- `frontend/lib/api.ts`
- `frontend/types/index.ts`
- `tests/api/test_api_routes.py`
- `tests/api/test_redesign_build_operations.py`
- `tests/unit/test_agent_orchestrator_v2.py`

## External sources consulted

PlatformIO `pio run` documentation: https://docs.platformio.org/en/latest/core/userguide/cmd_run.html

SQLite WAL concurrency: https://www.sqlite.org/wal.html

FastAPI server-sent events documentation: https://fastapi.tiangolo.com/tutorial/server-sent-events/

These sources inform further design; merely citing them does not imply the remaining redesigns were implemented.
