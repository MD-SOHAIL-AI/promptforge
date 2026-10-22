# ForgeX durable-agent implementation verification

Work resumed on September 27, 2026 in `D:\projects\ForgeX-Phase6-Final` after
the repository moved from `C:\Users\diwan\Downloads\ForgeX-Phase6-Final`.
The previous "Review and harden Forgex app" chat stopped during implementation
of the agent-quality plan. Its source edits were preserved and validated here.
See [the implementation runbook](agent-quality-implementation.md) for behavior,
tool limitations, recovery policy and qualification requirements.

## Relocation repairs

- Updated the home npm launcher and its repository template to use the D: folder.
- Regenerated the copied virtual environment's PlatformIO and Uvicorn launchers
  at their existing versions. Both launch successfully from D:; the development
  backend imports successfully with the repository Python interpreter.
- Replaced the embedded Python test runtime's old absolute repository import
  path with a relative `..\..` entry.
- Rebuilt the production frontend and staged a fresh bundled runtime. The new
  staging pointer uses D: rather than the copied C: build path.
- Excluded generated Playwright results from linting, fixing an enumeration
  race when browser tests refresh their results directory during lint.

Historical ChangeSets, checkpoints and journal records retain their original
identities and paths. They were not rewritten to bypass recovery validation.
A moved workspace still needs to satisfy current trust and checkpoint checks.

## Software checks

| Check | Observed result |
| --- | --- |
| Full Python 3.13.15 suite and fixture rerun after shared-budget fixes | 1,996 passed in the full run; its one fixture failure passed after correction; four skipped for unavailable Windows symlink creation |
| Recovery matrix | All 100 scenarios passed within the full Python suite |
| Harness | 11 passed |
| Frontend state | 67 passed after the failed-run timer fix |
| Electron | 35 passed after the closed-pipe logging fix |
| Release scripts, including agent-quality evidence validation | 24 passed |
| Browser conversations | Eleven passed, including output allowance exhaustion with request slots remaining, restored workspace reload/resize, failed-run timer replay, budget, trust, recovery and acceptance controls |
| Frontend type checking | Passed |
| Frontend lint | Passed with zero warnings after excluding generated results |
| Production frontend build | Passed |
| Desktop and frontend dependency audits | Zero known vulnerabilities |
| Hashed Python runtime dependency audit | No known vulnerabilities reported by OSV |
| Staged runtime scan | Passed for 4,033 managed files |
| Staged backend smoke | Authentication, per-user storage and session persistence across restart passed |
| Final packaged runtime, before and after desktop launch | Content scans passed for 4,033 managed files; backend restart smoke passed |
| Packaged desktop | Isolated profile, restricted PATH and authenticated settings/projects/sessions passed; screenshot inspected |
| Installer payload | Archive integrity passed for 4,108 files |
| Backend source provenance | Every packaged backend Python source hash matches the current repository source |

## Startup logging follow-up

A normal launch exposed `EPIPE: broken pipe, write` when the terminal that
launched Electron disappeared. Both startup status messages and forwarded child
process output could trigger Electron's native JavaScript error dialog. The
earlier desktop smoke redirected output to a file and did not reproduce a
closed pipe.

All desktop console writes now use a shared logger that handles synchronous
and asynchronous closed-pipe errors independently for stdout and stderr. Other
errors retain their existing failure behavior. The diagnostics file remains
independent of console output.

Five added logging regressions passed, including a child process whose parent
closes both pipes. All 35 Electron tests and 24 release-script tests passed.
The rebuilt packaged desktop also passed startup with both parent output pipes
closed, authenticated renderer API requests and an explicit check for native
error dialogs. That case is now required by the release runner and Windows CI.
The backend was unchanged during this logging correction; the affected desktop
checks were rerun after this fix. Subsequent backend verification is recorded
in the all-provider follow-up below.

The corrected app was reopened with the existing user profile. Canonical
desktop/installer files were refreshed from the verified build with matching
hashes. Follow-up logs use `test-results/closed-pipe-*.log`; the screenshot is
`test-results/packaged-desktop-closed-pipes.png`.

Browser tests use mocked transport. The bundled backend smoke starts actual
processes and makes HTTP requests. The full suite includes job persistence,
recovery blocking, shared budgets, tool prerequisites, context compaction,
source freshness and acceptance evidence. These checks do not certify arbitrary
model-generated firmware or physical hardware behavior.

Logs for this run are in `test-results/resume-*.log`. Four skipped test locations
in the Python output carry the old C: filename from copied Python bytecode;
the test files and backend used for this run are in the D: repository.

## Durable provider request follow-up

The saved run `agent-run-0b9a275436a94ddca97ec3e659575e9d` failed locally
before any provider HTTP request or workspace file changes. Its runtime log
reported `metadata.forgex_run_budget._persist contains a non-JSON-compatible
value`. The durable budget's persistence callback had crossed into the strict
JSON-only `LLMRequest` constructor. A stream recovery attempt hit the same bug
and the run surfaced the misleading `PROVIDER_ENVELOPE_INVALID` classification.

The shared HTTP adapter now retains budget and attempt-diagnostic objects at
the local accounting boundary and removes them from service request metadata.
Budget reservations are still persisted before each provider attempt. Four
regressions exercise the actual OpenAI/OpenRouter adapters and HTTP services,
with streaming and complete responses, through a durable planner budget.
Those tests and the related planner, normalization, OpenRouter and budget tests
passed: 63 tests. Another 133 durable-job, continuation and provider regression
tests passed. A minimal live probe of the user's selected OpenRouter
Nemotron model also succeeded with HTTP 200, with both physical attempts
accounted for and persisted. It did not replay the user's firmware task.

Terminal activity merging now freezes unfinished timers at the recorded run
end, and the feed handles previously settled records whose duration is absent.
This fixes the screenshot's reasoning counter continuing for minutes after a
four-second failure. All 67 frontend state tests and nine browser tests passed.
The browser replay test advances the clock by a minute and confirms the
reasoning row remains at 2.7 seconds. Follow-up evidence is
in `test-results/provider-*.log` and `test-results/provider-live-probe.json`.
The initial full 1,910-test Python result preceded this adapter correction;
the focused tests explicitly exercised the previously untested durable callback
through the service boundary. The later full suite includes these regressions.

The corrected frontend and backend were freshly staged and packaged. The
bundled backend restart smoke and desktop smoke with closed parent output pipes
passed; resource scans passed for 4,033 files and installer archive integrity
passed for 4,108 files. The app was reopened with the existing profile and both
the backend health endpoint and frontend returned success. The original failed
runs remain historical records; the live probe did not alter their budgets or
retry the user's firmware task. The previous canonical app build was preserved
as `.forgex/installers/win-unpacked-before-provider-fix`.

## All-provider and live-runtime follow-up

The packaged application's live log is in
`%APPDATA%\forgex-desktop\backend\logs\live-runtime.jsonl`. Recent NVIDIA
Nemotron requests returned HTTP 200. Transient HTTP 503 responses were retried
successfully; the final failures occurred in tool execution and plan validation:

- At 11:49:30 UTC, run `agent-run-d5695eead54f486a807a7c114908fef3`
  rejected a file write with `capability_expired`. Its durable job allowed
  900 seconds, but the tool grant used the default 300-second lifetime.
- At 11:51:57 UTC, resumed run `agent-run-8a40b76ff8274a44b8a3337e98097d9a`
  stopped at `AGENT_RUNTIME_MAX_TURNS` after several turns without file progress.
  Generated files remained in an unverified recovery draft.
- At 11:53:07 UTC, run `agent-run-d2735a56f290443f93db8864d2f0d3bc`
  failed with `provider_path_unsafe` after both the initial response and its
  repair were rejected. This corresponds to the screenshot's interrupted
  ten-step plan. The old log did not record the exact rejected path.

New tool grants now last for the remaining authorized runtime, retaining their
workspace scope, call limits and revocation checks. Resume and retry prompts
identify recovered staged files explicitly: the empty active-project baseline
does not mean the saved draft is empty. Native tool calls use the same argument
normalization as JSON tool plans. Harmless `./` prefixes are normalized; unsafe
paths remain rejected. A bounded repair request includes the rejected tool/path
as data and asks for a project-relative forward-slash path. Rejections now emit
bounded path diagnostics, and runtime stops retain actual provider-attempt
diagnostics instead of reporting zero requests after successful HTTP calls.
The stalled-turn guard remains in place.

Durable request-boundary coverage now exercises streaming and complete responses
for all ten built-in providers: OpenAI, OpenRouter, DeepSeek, Groq, Cerebras,
NVIDIA NIM, LM Studio, Anthropic, Gemini and Ollama. It also covers the three
custom protocols: OpenAI Chat Completions, OpenAI Responses and Anthropic
Messages. All 26 cases use real adapters/services with mocked HTTP transport,
check persistence before the request, and verify that accounting callbacks stay
local. Ollama now records attempts consistently through the shared accounting
helpers. This coverage does not certify every provider's credentials or live
availability.

The focused runtime/provider regression group passed 127 tests with one Windows
symlink skip. It includes permission lifetime, recovered draft context, unsafe
path repair and terminal diagnostics. The full backend suite then passed 1,943
tests with four Windows symlink skips and two dependency deprecation warnings
in 672.53 seconds. Logs use `test-results/all-provider-*.log`.
The original firmware jobs were not replayed and their budgets were not reset;
their interrupted plan and unverified drafts remain historical records.

Reopening the existing profile also logged a renderer failure:
`No group found for id "_R_1dbtb_"`. The resizable panel library could not resolve
the generated group identity. The conversation group, both panels and resize
handle now have explicit stable IDs, and the two panels have explicit order.
This avoids relying on generated IDs across hydration and saved-view restoration.
A new browser case checks restoring the Agent destination after reload, reopening
the dock, keyboard resizing and returning to Chat without a page error. All ten
browser cases and 67 frontend state tests passed; frontend type checking, lint
and production build passed. The desktop smoke additionally opens the Agent
workspace, reloads its saved destination and reopens the dock, and rejects the
renderer error-boundary view. Follow-up logs use `test-results/workspace-panel-*.log`.
The packaged desktop passed that expanded check with closed parent output pipes.
The reload/resize browser case also passed against the packaged production
frontend. The app was reopened with the existing user profile; backend health,
frontend HTTP and PlatformIO readiness passed, and the current launch log
contained no renderer failure. Earlier canonical builds were preserved as
`win-unpacked-before-all-provider-fix` and `win-unpacked-before-panel-fix`.

## Shared budget follow-up

The next live NVIDIA run, `agent-run-252a3c6efada49dab46d3101a759116b`,
stopped at 12:33:28 UTC with `AGENT_PROVIDER_BUDGET_EXCEEDED`. Its shared job
had reserved all 245,760 output tokens across 60 attempts. Sixty request slots,
53 tool calls and roughly 345 seconds remained, but those allowances could not
override the exhausted output allowance. The preceding missing `src/utils/logger.h`
read was a recoverable tool observation; it was not the terminal provider error.

The task controls now display output reservations and remaining context bytes
alongside request, tool and time allowances, explain that all limits apply
together, and disable continuation while the job is paused for budget. The
extension action states what it adds and does not silently resume the task.
Recovery messaging explains that budget must be added before continuing.

Related shared-runtime fixes preserve budget classification even when the limit
is reached during model-response repair, avoid counting a locally blocked
reservation as an outbound request, retain the last actual HTTP diagnostics,
and carry the planner's failure origin, turn and response details into the public
run projection. Input accounting now includes canonical conversation history and
tool schemas, rather than only the current prompt and system instruction.
Conservative output reservations and monotonic durable accounting remain in
place; saved jobs are not reset or refunded.

Provider coverage now has 78 mocked-transport cases: streaming and complete
responses for all ten built-in providers and three custom protocols, each with
successful accounting, an already exhausted allowance, and exhaustion during
same-model repair. The focused group passed 223 tests; the added public-projection
regression also passed after repairing its test fixture. All 11 browser cases,
67 frontend state tests, type checking, lint and production frontend build passed.
Logs use `test-results/budget-*.log`. No firmware task was replayed and no user
budget extension was performed.
The full backend run finished with 1,996 passes, four Windows symlink skips and
one failure in the newly added projection test's setup: its fixture omitted
`staging_root`. That test was corrected and passed in isolation and again with
`pytest --lf`; no production-code change was needed for this fixture repair.
This validates all 1,997 backend cases across the full run and targeted rerun.

Subsequent live attempts on the same NVIDIA model encountered HTTP 503
(`Service temporarily overloaded`) after bounded retries, then a successful
HTTP response whose ToolPlan content remained invalid after same-model repair
(`MODEL_OUTPUT_NOT_JSON`). These are separate from local budget exhaustion;
the generated files remain an unverified draft. No malformed response is
executed and no different provider is selected automatically.

The live drafts also exposed a shared reporting omission: `_abort_stage`
saved the ChangeSet but returned zero created/modified/deleted counts. Failure
results now derive these counts from the saved draft. The bounded-turn
regression checks that all 14 saved files are counted while the active workspace
remains unchanged.
The affected runtime and durable-job modules passed 145 tests with one Windows
symlink skip after this reporting correction (`test-results/budget-saved-draft-counts.log`).

## Desktop candidate

The fresh [unsigned test installer](../.forgex/installers/ForgeX-0.1.0-windows-x64-setup.exe)
passed the checks above. Its [adjacent evidence record](../.forgex/installers/ForgeX-0.1.0-windows-x64-setup.exe.evidence.json)
contains the current verification counts and artifact hashes:

- Installer SHA-256: `5721cbdbc708b548a33d4c981e4d6decd06f714da5b957c359ac3ccd62bf2fdb`
- Resource manifest SHA-256: `2158b7e6a7d53ae62d199fe6d854c1d1c790df5f88e3a317602d96a75198120b`
- Desktop screenshot: `test-results/all-provider-workspace-desktop.png`.

The filename is reused for the latest local candidate. The earlier hash and
counts in [the historical beta verification report](production-beta-verification.md)
predate these durable-job changes and do not identify this rebuilt installer.
The archive test returned success; its trailing-data warning is expected for
the NSIS executable wrapper. Fully restart an existing ForgeX process to load
a rebuilt development runtime, or install this candidate for local testing.

## Remaining distribution gates

The same final signed installer must receive all of the following evidence:

1. A valid Windows code-signing signature.
2. Real-provider qualification of all 30 agent-quality scenarios three times:
   at least 86 of 90 attempts pass, every historical incident repetition passes,
   and human requirement-fidelity reviews are recorded. The five recorded
   prompts and follow-ups must also pass their separate qualification gate.
3. Physical ESP32 evidence: 20 verified builds and ten unique-nonce
   upload/serial cycles using the bundled toolchain.
4. Clean Windows 10/11 installation, upgrade, offline and recovery evidence.
5. Successful combined `verify:release` for those exact signed installer hashes.

Those external sign-offs were not performed in this continuation. Local software
validation and an unsigned candidate do not authorize beta distribution.
Source changes remain uncommitted; no deployment or distribution was performed.
