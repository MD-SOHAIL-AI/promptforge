# Verification permission recovery — 2026-10-06

## Live evidence

ForgeX was launched from this checkout and its Electron renderer was inspected and operated using a loopback browser automation connection. The normal desktop automation helper was unavailable (`failed to connect native pipe`, OS error 2); browser automation controlled the actual Electron page instead.

The selected project was `external-new-folder-17e0f79e27`, at `C:\Users\diwan\OneDrive\Desktop\New folder`. The saved run `agent-run-2f0412b3c7b04e69a020a072b0514f67` reported:

- status: `failed`
- classification: `BUILD_TOOL_UNAVAILABLE`
- build result: `success: false`, `terminal: true`, `error_code: AGENT_PROJECT_TRUST_REQUIRED`
- saved ChangeSet: `changeset-2d45afc5cbb24cccb9e2cd7d3d679f1d`
- local verification eligibility: blocked by `AGENT_PROJECT_TRUST_REQUIRED`

The project trust GET returned `trusted: false`. This failure occurred at the local build permission gate, before a compiler was launched. Earlier provider timeouts remain separate historical events.

The app's **Inspect saved work** action opened the staged changes successfully. The live recovery controls now expose **Inspect saved work** and **Verify locally** for the old failed task. **Verify locally** remains disabled while the selected project is untrusted. Existing historical run records were preserved.

## Changes

- `backend/agent_runtime/autonomous_loop.py`: preserve the trust-required classification, return a blocked build result with `execution_attempted: false`, emit `BUILD_BLOCKED`, and recheck trust before preflight and compiler launch. Permission failures are excluded from compiler-failure caching. Local draft verification also publishes its permission blocker.
- `backend/agent_runtime/orchestrator.py`: retain blocked build stage/run status, leave the active task node waiting, preserve the recovery draft, and describe the permission action needed for recovery.
- `backend/agent_runtime/jobs.py`: persist the job's local build permission blocker without scheduling provider retries.
- `backend/agent_runtime/task_state.py`: make the next step permission followed by local verification.
- `backend/api/routes/agent_runtime.py`: preserve the blocked build status in subsequent turn context; close completed-with-warning run streams; disable compression/buffering for all three Agent SSE routes; reconcile the saved session with committed recovery links on reopen.
- `backend/agent_runtime/autonomous_loop.py` and the build-evidence API: retain the actual environment selected by the builder; accept historical builds whose environment is recorded in metadata or their sealed artifact manifest. Source revision and target checks still reject stale or mismatched artifacts.
- `backend/agent_runtime/activity_stream.py`: terminate warning completions, paused runs and skipped nodes, with frozen duration and truthful warning states.
- `frontend/lib/activity-stream.ts`: normalize old journal root records from explicit runtime outcomes, preserving successful build evidence and stopping historical retry notifications from restarting timers.
- `backend/agent_runtime/session_run_projector.py`, `session_store.py`, and `backend/api/app.py`: follow committed, bidirectional recovery links within the same project/session, project the completed result idempotently, and guard against overwriting a newer task.
- `frontend/hooks/use-forge-agent-session.ts` and `frontend/types/index.ts`: follow a resumed event only when it replaces the currently observed run; historical replay cannot move the UI back to an obsolete recovery attempt.
- `frontend/components/nexus/agent-job-controls.tsx`: expose recovery controls for failed and timed out jobs; use actual verification eligibility to display the permission blocker, including legacy saved runs.
- `frontend/e2e/recovery-controls.spec.ts`: cover recovery controls on legacy failed drafts and confirm a permission change does not submit model work.

## Permission-fix verification checkpoint

175 Python tests passed:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_verification_permission_recovery.py tests\unit\test_agent_verification_freshness.py tests\unit\test_task_state.py tests\unit\test_agent_orchestrator_v2.py tests\unit\test_agent_jobs_and_tools.py tests\unit\test_recovery_actions.py tests\api\test_agent_jobs_api.py -q
```

These include permission-blocked generation, trust revoked during dependency preflight, preserving the draft, rejecting local verification before trust, verifying locally without resolving a model, and idempotent verification. Two dependency deprecation warnings were emitted.

5 browser tests passed against the running frontend, with isolated mocked project/API fixtures:

```powershell
$env:FORGEX_BROWSER_BASE_URL='http://127.0.0.1:3000'
npx playwright test e2e/recovery-controls.spec.ts --reporter=line
```

Frontend typecheck passed:

```powershell
npm --prefix frontend run typecheck
```

The first browser run had a test locator mismatch on a span containing both the heading and value; the locator was corrected, and the complete five-test file was rerun successfully.

## Completed live verification

After the user explicitly asked the assistant to enable the selected project's build permission, the checkbox was enabled through the actual app. The trust GET confirmed `trusted: true`, and **Verify locally** became enabled. This explicit user instruction took precedence over the skill's default restriction on security controls.

The app submitted local verification of the real saved draft. Result:

- run: `agent-run-b9c0ec32c9034cc2b242efada2ba67fd`
- status: `completed_with_warning`
- classification: `DRAFT_BUILD_VERIFIED`
- `build_verified: true`
- PlatformIO result: `SUCCESS`, `PlatformIO build succeeded: firmware.bin`
- firmware size: 272,096 bytes
- firmware SHA-256: `98c2692e6cde5c47b077e852d1d2e47219004ab39be616ddeb28f6da4ac0883c`
- sealed review draft: `changeset-fcdaec2ab25341918222c5c712a9a9d3`
- `active_workspace_unchanged: true`
- `hardware_verified: false`

The shared job's model requests remained at 11, cumulative model input at 435,299 bytes, and output at 13,939 tokens. Tool calls increased from 9 to 10 for the build. Thus this local verification did not make a provider request. The current backend's live log recorded the successful build at `2026-10-06T01:20:02.763928Z`.

The warning is intentional: compilation alone does not verify task requirements or physical hardware behavior. No ChangeSet apply, hardware flash, reset, or serial connection was initiated. Source changes remain staged for review.

Live evidence: `live-verification.json`, `live-recovery-controls.png`, `live-build-passed.png`, and desktop logs at the repository root. The final app restart uses `forgex-completed-desktop.stdout.log` / `.stderr.log`.

## Additional live failures reproduced and fixed

1. **Passed build displayed as stale.** The verification call used the default build environment (`None`), while the builder actually selected `esp32dev`. That missing value overwrote the environment in run evidence. The API now derives historical target binding from the builder metadata or sealed manifest, and new builds preserve the builder-selected value. The existing firmware is now `passed`, scoped to the saved draft, with matching source revision and environment.
2. **Activity falsely displayed “Build interrupted.”** The durable journal contained successful build completion at sequence 213 and run completion at sequence 218. The session feed only received the cumulative build-output snapshot at sequence 191. A read of the session stream after sequence 190 received zero bytes in 14 seconds; the finite run stream delivered all events when it closed. The Next.js proxy was gzip-compressing these Agent streams. After adding `Cache-Control: no-cache, no-transform` and `X-Accel-Buffering: no`, the same live session request immediately delivered sequences 191–218 with no content encoding. The actual UI now shows `BUILD.COMPLETED`, `COMPLETED`, exit code 0.
3. **Historical timers continued for hundreds of minutes.** Old journal records projected `completed_with_warning` and `paused_model_unavailable` as running. A later `runtime.retried` snapshot restarted the old paused root after the frontend had settled it. Backend projection and frontend compatibility normalization now freeze these outcomes. The old paused run displays its recorded 3m23s duration; the local verification displays 1m23s.
4. **Reopening selected an obsolete failed run.** The user-facing session still pointed to the original provider-timeout run, while its durable recovery chain led through the failed draft to the completed local verification. The projector previously updated only sessions already pointing to the exact run being persisted. Reconciliation now follows validated recovery links, persists the latest pointer/result, and ignores obsolete replay notifications. This repaired derived conversation state from the existing journal; historical execution records were not rewritten.

The compression middleware's documented `no-transform` behavior is also confirmed in the installed `frontend/node_modules/next/dist/compiled/compression/index.js`. See [Express compression documentation](https://expressjs.com/en/resources/middleware/compression/) and [Next.js compression configuration](https://nextjs.org/docs/pages/api-reference/config/next-config-js/compress).

## Verification after the additional fixes

| Checkpoint | Result |
| --- | --- |
| Activity projection + SSE headers/closure + build target/freshness + permission recovery | 36 Python tests passed |
| Orchestrator + recovery actions + transport + activity + initial session recovery projection + build API | 41 Python tests passed |
| Final session pointer guards + permission recovery + jobs API + Chat API | 12 Python tests passed |
| Activity state, rendering and SSE observation policy | 20 Node tests passed |
| Recovery controls, including reopening and ignoring historical replacements | 6 browser tests passed |
| Frontend TypeScript check | Passed |
| Changed tracked files: `git diff --check` | Passed; Git emitted line-ending notices |

These groups overlap and must not be summed as unique tests. The earlier 175-test result above was obtained at the permission-fix checkpoint, before the additional changes. Python emitted two dependency deprecation warnings; Node emitted module-format warnings. A typecheck caught the missing `source_run_id` event type; the type was added and typecheck rerun successfully.

The new warning/retry and skipped-node regression cases failed against the old implementation (2 Node failures and 3 Python failures), then passed after the fix. Tests also cover recovery-chain session isolation, idempotent restored messages and an interleaved newer task that must retain its active pointer.

No additional provider request, compilation, firmware apply or hardware operation was needed to repair and recheck the status/reopen failures. The real PlatformIO compilation remains the successful result described above. The final app evidence is saved alongside this report.

## Final app check

At `2026-10-06T01:49:02.080Z`, after restarting the app with the final backend changes, the session's active run was the completed local verification (`agent-run-b9c0ec32c9034cc2b242efada2ba67fd`), project trust was true, and build evidence was `passed` for `esp32dev` in the saved draft. Both source revisions were `workspace:2e9ceacc9546db1d9afa9e9c`. The visible activity card showed `BUILD.COMPLETED`, `COMPLETED`, exit code 0 and zero compiler warnings. The green Build panel showed “Firmware compiled successfully.” Model usage remained at 11 requests. ForgeX was left open on this result.

See `live-final-status.json` and `live-final-build-passed.png`. Task requirements and physical hardware remain unverified; source changes remain staged.
