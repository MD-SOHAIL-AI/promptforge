# ForgeX recent activity and complete repository review

Review date: **20 October 2026**. Times are IST.

## Assessment

**Hold release until the firmware artifact integration and recovery defects below are corrected.** ForgeX builds, its desktop and model-worker tests pass, and most Python tests pass. However, the latest real user task demonstrates a core integration failure: PlatformIO compiled the requested firmware successfully, while ForgeX reported that no successful build existed.

This review identifies **eight actionable issues: one P1 and seven P2**. Five have offline reproductions or direct runtime evidence; the two editor issues and verification-order issue are source-traced. Passing component tests have missed these boundaries.

No application fixes were made. Review scripts, logs, and generated check outputs were produced. No new paid provider requests or hardware operations were initiated.

## Scope and provenance

This folder is an extracted source snapshot without Git history. Commit authors, branches, and a chronological Git diff cannot be established. Evidence comes from:

- The 1,472-entry REVIEW_FILE_MANIFEST.txt and October 20 patch/continuation reports.
- Source tracing across desktop startup, local authentication, providers, agent builds, artifacts, editor persistence, workspace locking, ChangeSets, memory, recovery, and release checks.
- Read-only inspection of local runtime logs and the orchestration SQLite database.
- Fresh compilation, frontend/desktop/worker checks, all 2,739 collected Python tests, browser checks, and focused offline integration probes.

This is a broad repository and integration review with focused manual inspection. It does not claim manual examination of every line of the approximately 75,000-line backend or all vendored dependencies.

Before verification, 33 files referenced by the review manifest had different contents. Nine additional source/test/script files are outside that manifest, including build_snapshot.py, workspace_lock.py, the dependency installer, and the new regression suites. The inventory file additionally captures generated Next.js metadata during verification: [inventory](current-review-inventory.json).

## Recent activity

| Time on October 20 | Observed activity | Outcome |
| --- | --- | --- |
| Morning source handoffs | Added editor revisions, immutable build-input copies, durable firmware, provider deadlines, interprocess workspace locks, backup recovery, memory invalidation, and regression tests. | Historical pass counts cover different selections and environments; they are not current release certification. |
| 18:26–18:35 | Latest installer checked Python/PlatformIO, reused or restored npm dependencies, and built the worker/harness/Electron components. | Installer reported completion at 18:35:09. Dependencies and a worker bundle now exist, unlike the older handoff environment. |
| 18:37 | Electron launched the backend and loaded the renderer. | Subsequent successful API calls and agent execution confirm the application was running. |
| 18:44:24–18:44:30 | First ESP32 blink/serial task used OpenRouter model openai/gpt-oss-20b:free. | HTTP 404 explicitly said that free model variant was unavailable. No source files were generated. The visible failure became a generic runtime message. |
| 18:44:45–18:46:51 | Second attempt used OpenRouter model nvidia/nemotron-3-ultra-550b-a55b:free. | Created platformio.ini and src/main.cpp. PlatformIO compiled the ESP32 Arduino firmware successfully in about 60 seconds. The binary was 270,656 bytes. ForgeX nevertheless recorded BUILD_ERROR and retained an unverified recovery draft. |

The second run's saved build record simultaneously contains:

- status: SUCCESS
- exit_code: 0
- flash_ready: true
- success: false

Finding 1 explains this contradiction. The available evidence does not indicate that another compiler fix is needed for that particular sketch.

Both runs recorded fallback_used: false. No silent provider/model fallback occurred. The database contained no approval records when inspected. This review did not flash, upload, reset, or monitor a board.

The original backend log is .forgex/logs/live-runtime.backend-9e191923278e43a2b35acc9a1acde918.jsonl. Relevant run IDs end in f9bea11b3703469db118d86ade2da223 and fa6cc14c4fc94d8ebc1650ca04017f51. A bounded activity extract is saved in [recent activity](current-review-recent-activity.json).

## Findings in repair order

### 1. [P1] Successful isolated builds fail the agent's old artifact containment rule

**Locations:** backend/services/platformio_service.py:478; backend/agent_runtime/autonomous_loop.py:2030, :2124, :1647; backend/api/app.py:163.

PlatformIOService.build() now preserves successful firmware in a persistent artifact store and returns that new path. The normal desktop application enables this behavior. However, the agent's _build_once() still requires the returned firmware to reside inside its staged workspace before hashing it. The persistent store is outside that stage, so artifact.relative_to(self.workspace_root) raises ValueError. The firmware hash remains empty and build_passed becomes false. The later _refresh_verification() repeats the same obsolete containment rule.

**Impact:** A valid compile in the normal desktop configuration becomes a failed agent task. The agent may spend additional turns trying to repair working source. Recovery and downstream flash/simulation verification cannot use the build consistently. The latest real blink task demonstrates this failure.

**Reproduction:** An offline probe uses the real PlatformIOService.build() snapshot/persistence path with a fake compiler, followed by the real agent _build_once(). It produces compiler status SUCCESS, one preserved firmware image, and agent_build_passed: false.

**Correction:** Verify persistent firmware through the host artifact-store contract and bind the verified digest to captured source and environment. Use that contract consistently in refresh, recovery, simulation, and flash preparation. Keep fail-closed checks; simply allowing arbitrary external file paths would weaken safety. Add an integration regression using the service and agent together with an artifact store configured.

Evidence: [probe results](current-review-probes.json), [reproduction script](review-probes-2026-10-20.py), and the actual saved run.

### 2. [P2] Interrupted-save recovery is unreachable through normal project discovery

**Locations:** backend/services/project_service.py:295, :323, :442; backend/api/dependencies.py:45.

Named project operations invoke backup recovery. Listing calls _with_store_lock() without a project name, so recovery does not run. Listing then ignores hidden directories. If a process dies after renaming firmware to .firmware.backup but before publishing the replacement, the only remaining copy is hidden. API lookup resolves IDs through that list before calling the named loader.

**Impact:** The project disappears from the UI and ID-based requests return PROJECT_NOT_FOUND. The backup remains recoverable; this is not proof of permanent data loss.

**Reproduction:** Create a managed project, move its directory to the expected backup name, then use a fresh service to list and resolve it. Zero projects are listed and resolution returns PROJECT_NOT_FOUND. A direct load_project('firmware') succeeds and restores it, showing that recovery exists but discovery cannot reach it.

**Correction:** Reconcile valid pending backups under the correct locks before discovery, or retain enough durable identity to recover by ID. Test restart recovery through public listing/opening APIs.

Evidence: [probe results](current-review-probes.json).

### 3. [P2] Build snapshots follow Windows junctions outside the project

**Location:** backend/services/build_snapshot.py:39–56.

Snapshot traversal checks is_symlink() but does not reject Windows reparse points/junctions. On this Windows host a junction reports is_symlink() == false, and os.walk(..., followlinks=False) traverses it. The snapshot copies the external target under a project-relative name.

**Impact:** The claimed project containment boundary fails on the supported Windows platform. External content can enter the build snapshot and its provenance despite being outside the selected project. This does not establish a new arbitrary-code execution path; PlatformIO execution already has a separate documented trust boundary.

**Reproduction:** A junction from a review fixture project to a sibling fixture directory was copied successfully, including a file containing 'external fixture'. No private content was used. Junction creation worked without the symlink privilege unavailable to the symlink regression.

**Correction:** Reuse component/reparse-point validation and bounded reading from backend/security/workspace_io.py:11. Check actual descendant containment and defend against path replacement during capture. Add Windows junction coverage.

Evidence: [junction probe](current-review-junction-probe.json); fixture under .cache/review-junction/.

### 4. [P2] Typing during a successful save leaves an obsolete editor baseline

**Location:** frontend/hooks/use-promptforge-workspace.ts:633–646.

A save response updates the revision only if the tab's current content still equals the submitted content. If the user types during the request, newer text is correctly retained, but the old revision is retained too. The disk revision has already advanced. The next save therefore sends an obsolete baseline and receives 409.

**Impact:** Ordinary editing creates a false concurrent-edit conflict. Further saves continue to fail until the baseline is refreshed. Reopening an already open tab only selects it at :569; file-list refreshes do not repair a dirty baseline. Closing the tab asks the user to discard unsaved content, leaving an awkward recovery path.

**Correction:** Advance the confirmed disk revision after a matching successful save while keeping newer text and dirty state. Serialize or version overlapping saves so late responses cannot restore older baselines. Add a delayed-save interaction regression.

Evidence level: source-traced; no browser reproduction added for this case.

### 5. [P2] Newly auto-opened generated files omit their revision and cannot save

**Locations:** frontend/hooks/use-promptforge-workspace.ts:1170–1176; frontend/lib/workspace-helpers.ts:89; backend/api/routes/files.py:215.

refreshAfterWorkflow() reads src/main.cpp with a revision. It preserves that revision when replacing an existing clean tab. The branch that appends a new tab omits response.revision, and toFile() does not set it. saveFile() later submits expected_revision: undefined, which the hardened API rejects with 428.

**Impact:** In the legacy workflow-completion path at :1326, a generated main file appears editable but rejects its first save. The normal openFile() path handles revisions correctly, so ordinary opening tests can miss this branch.

**Correction:** Retain the read revision whenever creating a tab from a file-content response. Check cached-open and delayed-load paths for the same invariant. Test generation completion followed by editing/saving a previously unopened main file.

Evidence level: source-traced.

### 6. [P2] A later parent deadline replaces a shorter request deadline

**Locations:** backend/model_router/runtime.py:58–64; backend/model_router/router.py:329–336.

Both deadline expressions choose the parent deadline whenever one exists, instead of taking the minimum of that deadline and the request's total allowance. A longer parent budget disables the shorter logical-request cap around admission, retries, and backoff. Per-attempt adapter timeouts do not restore the total logical-request cap across attempts.

**Reproduction:** With a 30 ms request policy, a 500 ms parent allowance, and a provider finishing after 100 ms, ModelRuntime.generate() returns successfully after about 106 ms. Existing regressions test an earlier parent deadline and miss the opposite direction.

**Correction:** Compute the request deadline once at logical admission and take its minimum with any parent deadline, for streaming and buffered calls. Cover queue waits and retry backoff as well as execution.

Evidence: [probe results](current-review-probes.json).

### 7. [P2] Clean-source verification tests the worker before building it

**Locations:** scripts/verify-software-only.ps1:34–35; scripts/verify-beta.mjs:7–8; .github/workflows/production-beta.yml.

Source releases intentionally omit dist/. Both verification entry points start with Python tests. Real adapter tests such as tests/unit/test_opencode_engine.py:68 require dist/model-router/worker.cjs through OpenCodeProvider at backend/model_router/providers/opencode.py:199. The worker build runs later. npm ci has no root lifecycle script that creates the worker first.

**Impact:** A fresh extraction or clean CI checkout can fail with MODEL_ENGINE_UNAVAILABLE before reaching the step that would create its prerequisite. Reused development output masks this defect. Prior handoffs documented the same missing-worker verification failures.

**Correction:** Build the worker before worker-dependent Python tests in both verifiers and CI. Validate the order from a clean extraction without build output.

Evidence level: source-traced prerequisite/order defect. The existing worker was retained during review; it was not removed from a running workspace to demonstrate this.

### 8. [P2] An explicitly unavailable free model becomes a generic runtime failure

**Locations:** model-router/src/engine.mjs:383–392; backend/model_router/providers/opencode.py:370; backend/agent_runtime/run_reporting.py:254.

The worker recognizes retirement only for HTTP 410. In the first actual task, OpenRouter returned HTTP 404 explicitly stating that the selected free variant was unavailable. Normalization returned PROVIDER_REQUEST_FAILED, bypassing the registry's retirement handling. The visible run message became 'ForgeX could not continue the Agent runtime.'

**Impact:** Users cannot readily tell that the selected model variant is the cause or that they need to explicitly select another model. Retrying that same slug can repeatedly fail. Switching to a paid alternative must remain a deliberate user choice.

**Reproduction:** The compiled normalizeError() function returns category 'provider' and code PROVIDER_REQUEST_FAILED for the observed 404 shape.

**Correction:** Recognize authoritative provider codes/bodies for unavailable models and preserve a sanitized, actionable cause. Do not classify all 404s as retirement; an incorrect endpoint can also return 404. Retain the prohibition on silent paid-model substitution.

Evidence: [normalization probe](current-review-model-retirement-probe.json), plus the first saved run.

## Fresh verification

Environment: Windows; Python **3.13.15**; Node **24.20.0**. Existing installed dependencies were used. Worker/harness/Electron output was rebuilt during the checks.

| Check | Result | Evidence |
| --- | --- | --- |
| Full Python suite | **2,733 passed, 2 failed, 4 skipped**, 2,739 collected; 23m 47s | [log](current-review-pytest.log), [JUnit](current-review-pytest.xml) |
| Python dependency compatibility | Passed: pip check found no broken requirements | Executed directly |
| Backend compileall | Passed | Executed directly |
| Frontend typecheck | Passed | [log](current-review-typecheck.log) |
| Frontend lint, zero warnings | Passed | [log](current-review-lint.log) |
| Production Next.js build | Passed, including lint/type validation | [log](current-review-frontend-build.log) |
| Electron build/tests | **45 passed** | [log](current-review-electron.log) |
| Harness build/tests | **12 passed** | [log](current-review-harness.log) |
| Model router/worker tests | **49 passed** | [log](current-review-model-router.log) |
| Release-script tests | **46 passed** | [log](current-review-release-scripts.log) |
| Frontend state tests | Final run **104 passed**; initial concurrent run had one timing failure | [final](current-review-ui-state-final.log), [initial](current-review-ui-state.log) |
| Read/local-session recovery | **5 passed** | [log](current-review-read-recovery.log) |
| Browser scenarios | **66 distinct scenarios passed, 1 production-only scenario skipped**, across initial and continuation runs; no single uninterrupted green full run | [initial](current-review-browser.log), [isolated recovery](current-review-browser-recovery.log), [remaining 31 pass/1 skip](current-review-browser-remaining.log) |
| Independent Windows lock probe | Child acquired lock; competing process excluded; child exited normally | [result](current-review-lock-probe.json), [script](review-lock-probe-2026-10-20.py) |
| Additional offline integration probes | Reproduced findings 1, 2, 3, 6, 8 | Evidence linked above |
| Source release scan | **Blocked**: EPERM entering a live .promptforge/artifacts directory | [log](current-review-source-scan.log) |
| Current npm advisory audits | **Blocked**: requests to npm's advisory endpoint failed; vulnerability status not established | [root output](current-review-npm-audit-root.json), [frontend output](current-review-npm-audit-frontend.json) |
| Python vulnerability audit | Not run: pip_audit is not installed | No package installation attempted |
| Signed installer/clean-machine checks | Not run | Build success does not establish installer qualification |
| New live-provider or physical-board qualification | Not performed | Existing activity was inspected; no new paid calls or hardware actions |

### Failed and unstable checks

The new symlink regression fails during fixture setup with **WinError 1314**, because this process lacks symlink-creation privilege. Four older symlink tests skip for the same capability. This is a portability issue in the test, not evidence that its rejection assertion failed. The independently demonstrated junction defect is a separate product finding.

The multiprocessing lock regression fails because its child does not signal within five seconds. An isolated rerun also failed. A separate minimal process probe acquired the same locking mechanism in approximately 0.49 seconds, demonstrated competing-writer exclusion, and exited normally. The pytest child-startup failure remains unresolved; it is neither a passing regression nor proof that OS locking is broken.

The initial Python attempt encountered sandbox write denial in the ordinary host lock directory and was stopped. The completed run set LOCALAPPDATA only for the test process to .cache/review-localappdata. Existing test fixtures also isolate application state and use in-memory credentials. The live desktop environment was not changed.

The initial state-suite failure involved a 50 ms proxy header deadline under concurrent load. Isolated reruns and the final full state suite passed. It is retained as test timing instability, not a confirmed production transport defect.

The initial browser run passed 34 scenarios, then its 600-second runner deadline interrupted the next recovery case. That case passed alone. The remaining selection passed 31 scenarios and skipped the production-only CSP case. All 67 scenarios are accounted for, but there was no uninterrupted full-suite pass. These UI tests use API fixtures and do not certify a real provider/hardware flow.

Source-scan local exclusions include .forgex but omit .promptforge at scripts/scan-release-content.mjs:43, causing traversal into live runtime state in a used developer folder. A clean staged-source scan still needs to be established. npm audit output files include stderr diagnostics as well as the failed response; their .json suffix does not mean they contain a successful machine-readable advisory report.

## Architecture, security, and maintainability

Sound controls are present and exercised: token-authenticated local APIs, Host/Origin restrictions, authenticated WebSockets, Electron sandbox/context isolation, credential separation, deterministic model selection, staged ChangeSets, revision conflicts, durable events, and host-owned flash approvals. The latest failed task preserved recovery work rather than declaring verified success.

Workspace locks improve cooperative consistency but cannot prevent an unrelated editor from writing. Source revisions remain necessary. Build copies do not sandbox PlatformIO or project scripts. The flash-manifest adapter deliberately supports a narrow ESP32 Arduino layout; compiling another board does not establish flash readiness.

The principal weakness is **contract drift between individually tested layers**. Snapshot tests establish that the service returns persistent firmware, while agent tests generally place their fake firmware inside the stage. Both can pass while the combined desktop flow fails. Backend file-revision enforcement similarly became stricter without updating every editor transition.

Large implementation units make these invariants harder to inspect: autonomous_loop.py is about 157 KB, orchestrator.py about 190 KB, and use-promptforge-workspace.ts about 65 KB. Extract artifact verification, editor revision transitions, and recovery discovery into explicit shared contracts and integration checks. The confirmed issues do not require a wholesale rewrite.

Historical reports contain different pass counts from different source snapshots and environments. Keep one current verification record tied to hashes, runtime versions, dependencies, and the exact artifact being qualified. The old review manifest no longer enumerates the entire current source tree.

## Recommended next changes

1. Repair persistent-artifact verification first. Locally verify the existing blink recovery draft before spending model requests to regenerate working source.
2. Repair backup discovery and both editor revision transitions. Cover public-API restart recovery and typing during saves.
3. Reject Windows junctions in snapshots, correct total deadline selection, and make unavailable-model errors actionable.
4. Correct clean-source gate order, resolve Windows regression capability/startup failures, and qualify the final candidate's advisory status, packaged installer, providers, and target boards.

**Disposition:** A useful, substantially tested development candidate. Production certification is not supported while these integration defects and remaining qualification gaps are open.

