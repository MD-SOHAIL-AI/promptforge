# ForgeX software hardening continuation — 20 October 2026

## Release decision

**NOT PRODUCTION-CERTIFIED.** This source archive includes all previous fixes from the `ForgeX_Continued_Fixes_2026-10-20.zip` handoff and the additional changes documented here. No live provider credentials, PlatformIO toolchain, Windows installer, or physical ESP32 were available for complete release qualification. Do not interpret a passing unit test as proof of those systems.

## New changes after the previous ZIP

1. **Cross-process workspace locking** (`backend/security/workspace_lock.py`). A per-user, stable OS file lock coordinates ForgeX writers across independent Python processes, using `flock` on POSIX and `msvcrt.locking` on Windows, with a bounded timeout and per-thread reentrancy. The lockfile remains outside the workspace so it is not accidentally compiled, hashed as project source, or committed. OS advisory locks do not prevent an unrelated native editor that does not participate in this protocol from writing.
2. **Managed project concurrency** (`backend/services/project_service.py`). Reads, writes, file-level revision compare-and-save, creation, deletion and project listing are coordinated under the same cross-process store lock. File edits also use the per-workspace lock shared with the AI ChangeSet publisher. Structural edits now compare a full baseline of file paths, content digests, file types, ownership, board/framework, metadata and creation time before overwriting the project.
3. **Interrupted project save recovery**. If the process dies after renaming the active project to its backup but before promoting the new stage, a later managed operation validates and restores the backup under lock. If the new project was fully published, it validates the new target before removing the stale backup. Invalid/unsafe backups fail closed.
4. **AI ChangeSet coordination** (`backend/changes/service.py`). Apply, undo and startup recovery acquire the same per-workspace interprocess lock, with baseline revalidation while holding it.
5. **External workspace operations** (`backend/api/routes/files.py`). Saves, compound rename/writes, file/directory creation and deletion acquire the shared workspace lock. A rename with content checks its expected revision **before** moving the file. Existing atomic file replacement and expected-revision requirements remain in effect.
6. **Source-specific memory invalidation** (`backend/agent_runtime/memory.py`, `orchestrator.py`, memory API route). Memories of applied changes now store per-file SHA-256 dependency hashes. A later external edit of an affected file invalidates the claim; an unrelated edit does not. Old memories with no captured dependency digests fail closed when their workspace revision differs. Verified facts are no longer simply keyed to an arbitrary whole-tree revision.
7. **Portable native-test mocks**. Corrected an outdated `shutil.which` monkeypatch in `tests/unit/test_agent_jobs_and_tools.py` that rejected the supported `path=` argument.
8. **Reproducible Windows verification** (`scripts/verify-software-only.ps1`). Runs backend, frontend, worker, Electron and source-release checks, produces a machine-readable result and fails closed on missing dependencies or test errors. No physical hardware is required for this script, but neither live-provider compatibility nor Windows packaging is established by it.
9. **Regression tests** (`tests/unit/test_2026_production_hardening.py`) cover cross-process lock exclusion, stale managed saves, crash recovery, corrupt backups, external revision conflicts, external create/delete and selective memory invalidation.

## Verification performed in this environment

| Verification | Result |
| --- | --- |
| New/related backend tests | **105 passed**, 0 failed |
| Full API test directory | **120 passed**, 0 failed |
| Wider backend unit suite | **1,388 passed**, 5 failed, 5 skipped; stopped at 5 failures |
| Five broad-unit failures | All require the missing compiled model-worker runtime (`MODEL_ENGINE_UNAVAILABLE`) |
| Frontend state tests | **95 passed**, 3 blocked by absent `react`/`esbuild` packages |
| `python -m compileall -q backend` | passed |
| `npm run scan:source-release` | passed |
| npm dependency install | failed (`npm` exit-handler error); local Node 22.16.0 is below declared >=22.19.0 |
| Real PlatformIO compilation | not performed; toolchain unavailable |
| Windows Electron installer and GUI E2E | not performed |
| Live provider API calls and real latency | not performed; no credentials |
| Physical ESP32 flash and serial monitor | excluded by user request |

The software changes are evidence-backed *within the tested areas*, not a blanket guarantee of every module. In particular a third-party native editor can still race a last-moment filesystem replacement, because it does not obey ForgeX locks. Live provider billing/cancellation behaviour cannot be certified offline. The prior source archive's unverified Windows and installer integration also remain unverified here.

## Qualification on Windows (no physical board required)

1. Extract the ZIP into a **new** directory. Back up your existing projects first.
2. Install Python according to project requirements, Node **>=22.19.0** and npm, and configure PlatformIO if planning a firmware compile.
3. Run from PowerShell: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-software-only.ps1`.
4. If a stage fails, inspect the output and `reports/forgex-software-check-*.json`. Do **not** treat a partial run as successful.
5. Separately exercise the packaged app on Windows and perform a real (non-flashing) PlatformIO compile for the board/framework combinations you deploy. Qualify live providers with test credentials and explicit spend limits.
6. Only ship once the installer, crash restarts, agent editing, live provider API, source/firmware provenance and full regression suite pass locally. The PowerShell script is a reproducibility aid, not a substitute for those gates.

## Design references

- PlatformIO build directory semantics: https://docs.platformio.org/en/latest/projectconf/sections/platformio/options/directory/build_dir.html
- OpenCode exact edits and permission model: https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/tool/edit.txt
- OpenCode concurrency discussion: https://github.com/anomalyco/opencode/issues/4079
- Python asyncio request deadline handling: https://docs.python.org/3/library/asyncio-task.html#timeouts

## Disposition

This archive is intended as a **testable hardening candidate**, not a signed Windows installer or a completed production certification. Original user archives are unchanged.
