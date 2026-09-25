ForgeX repository review — 6 September 2026

The software checks pass, but I would not release this snapshot as hardened. The main blockers are hardware actions reachable through the build path, unrestricted build hooks, loss of agent-created managed-project files, incomplete artifact approval enforcement, and editor data-loss paths. These are integration failures that the current isolated tests do not cover.

This is a repository-wide review of the working tree, with targeted inspection of active execution paths and local reproductions. It is not a claim that every line or every possible provider/board combination has been verified. Git reports the supplied source as untracked, so the review is against the files on disk, not a commit or PR diff. No application source was changed.

The active architecture is Next.js/React Nexus V4 → Next.js API rewrites → FastAPI → persistent chat/agent sessions and orchestrator → model/tool loop → staged ChangeSets → PlatformIO/build/flash/serial services. Electron owns startup and local authentication. A legacy IDE UI and older workflow abstractions remain alongside this path. The important architectural problem is that different services disagree about what constitutes a project, a safe build, and an approved artifact.

Validation performed:

| Check | Result |
| --- | --- |
| Python suite | 1,455 passed; 4 skipped; 102.23 seconds |
| Frontend state tests | 63 passed |
| Release-script tests | 15 passed |
| Electron tests and TypeScript compilation | 28 passed |
| Frontend typecheck | Passed |
| Frontend lint, zero warnings | Passed |
| Next.js production build | Passed |
| Playwright conversations/UI tests | 4 passed; these use mocked transports |
| Source release-content scan | Passed |
| Root npm audit | 4 affected dependency entries: 2 high, 2 critical |
| Frontend npm audit | 9 affected dependency entries: 7 high, 2 moderate |
| Python requirements audit | Failed: 8 advisory records across pytest and Starlette, including duplicate advisory IDs |

The skipped Python tests exercise symlinks, which this host could not create. Tests do not establish real USB flashing, actual board compatibility, packaged installer behavior, or paid/live provider reliability. No hardware was flashed and no live model requests were made. Dependency-audit counts are registry results on the review date, not proof that every advisory is exploitable through ForgeX.

Findings are ordered by priority. P1 means fix before release; P2 means a concrete correctness or reliability problem to schedule next.

1. **P1 — A build can invoke upload through project configuration.**

   Location: [backend/tools/build_firmware.py:519](../backend/tools/build_firmware.py#L519), with validation in [backend/services/platformio_service.py:295](../backend/services/platformio_service.py#L295).

   `_build_args()` invokes `pio run` without an explicit build-only target. The argument guard rejects upload targets in `extra_args`, but does not reject `targets=upload` inside an environment in `platformio.ini`. I reproduced successful validation of that configuration and confirmed the generated argv leaves the target unspecified. The installed PlatformIO CLI then selects the configured target. A normal build, including an agent build before final review, can consequently attempt an upload without passing ForgeX's flash approval flow. PlatformIO documents [environment default targets](https://docs.platformio.org/en/latest/core/userguide/cmd_run.html).

   Fix: force a trusted compile-only target and validate the effective configuration, including inherited target settings. Add a test that a build request with `targets=upload` never reaches upload processing. This must be addressed separately from script execution below.

2. **P1 — Agent-authored build hooks execute outside the staged-file boundary.**

   Location: [backend/agent_runtime/autonomous_loop.py:544](../backend/agent_runtime/autonomous_loop.py#L544), [backend/agent_runtime/tool_policy.py:76](../backend/agent_runtime/tool_policy.py#L76), and [backend/agent_runtime/orchestrator.py:834](../backend/agent_runtime/orchestrator.py#L834).

   The write policy permits `.ini` and `.py`; project validation accepts `extra_scripts=pre:hook.py`; the autonomous loop builds this stage before the final ChangeSet risk/reviewer decision. The subprocess runs with the user's host privileges. Copying files to a stage does not restrict script filesystem, process, network, or device access. I verified that the policy and project validator accept both files; I did not execute a malicious hook. The installed PlatformIO builder invokes these scripts, consistent with [PlatformIO's extra_scripts documentation](https://docs.platformio.org/en/latest/projectconf/sections/env/options/advanced/extra_scripts.html).

   Fix: enforce a build trust boundary before execution. Reject unapproved executable build configuration/hooks and resolve indirect configuration and package behavior, or execute builds inside an actual OS sandbox with limited filesystem/network/device access. A post-build review cannot prevent effects that already occurred.

3. **P1 — Saving a managed project deletes files added by an applied ChangeSet.**

   Location: [backend/services/project_service.py:390](../backend/services/project_service.py#L390), [backend/services/project_service.py:292](../backend/services/project_service.py#L292), and [backend/changes/service.py:195](../backend/changes/service.py#L195).

   ChangeSet apply writes new files without updating `.promptforge-project.json`. `ProjectService` loads only the manifest's file inventory and saves by replacing the whole project directory. Reproduction: create a managed project, apply a ChangeSet adding `helper.cpp`, load the project, then save it. The added file exists after apply, is missing from the loaded project, and is deleted by save. The managed-project explorer also uses that incomplete inventory. External imported projects use a different file path and are not the same case.

   Fix: unify project inventory and mutation semantics. Reconcile ChangeSets with the managed manifest atomically, and make normal file saves preserve unrelated project files. Add a cross-service apply → list/read → editor-save regression test.

4. **P1 — Flashing can rebuild after checking the approved firmware hash.**

   Location: [backend/services/flash_service.py:97](../backend/services/flash_service.py#L97) and [backend/tools/flash_firmware.py:500](../backend/tools/flash_firmware.py#L500).

   The flash service checks the rebuilt artifact against `expected_artifact_hash`, but the ESP32 flasher then invokes `pio run --target upload` on the mutable project. That command can build again; it is not a command bound to the checked bytes. Source changes or build hooks between validation and upload can change what is written. The approval also does not hash the complete set of bootloader/partition/application images that the project upload can use. This is a code-path finding; no hardware reproduction was attempted.

   Fix: approve and retain an immutable upload artifact manifest containing every image, offset, environment and hash. Upload those exact files without rebuilding or executing project hooks after the final integrity check.

5. **P1 — An unavailable diff preview is classified as a safe localized edit.**

   Location: [backend/agent_runtime/risk.py:68](../backend/agent_runtime/risk.py#L68), [backend/agent_runtime/risk.py:94](../backend/agent_runtime/risk.py#L94), and [backend/changes/service.py:585](../backend/changes/service.py#L585).

   Risk scoring consumes the display preview, treating `None` as empty text. It does not fail closed when content is binary, too large, or otherwise unpreviewable. I replaced a source file larger than 512,000 bytes with a short source file containing a security-sensitive marker. The ChangeSet had no preview, yet assessment returned score 0, `low`, no reviewer, and `scoped_auto_apply_allowed=True`. Truncated previews also conceal the omitted portion from marker inspection.

   Fix: assess complete bounded content independently of the UI preview. Require explicit review when analysis is incomplete; include changed byte/line counts and preview completeness in policy inputs.

6. **P1 — An in-flight editor save can overwrite newer edits or another project's tab.**

   Location: [frontend/hooks/use-promptforge-workspace.ts:561](../frontend/hooks/use-promptforge-workspace.ts#L561).

   `saveFile()` captures content, awaits the request, then unconditionally replaces the matching path with the response and clears `dirty`. If the user types while the request is pending, those newer edits disappear. If they switch projects and open the same relative path, the response can replace that project's tab because this update checks only the path, not project identity. The nearby `openFile()` success path already has a project guard; the save path lacks one.

   Fix: bind save completion to project ID, tab identity and captured content revision. Mark only the submitted revision saved, preserve subsequent edits, and ignore stale completions after project/tab changes. Verify with delayed-response UI tests.

7. **P1 — The current dependencies fail the repository's release audit gates.**

   Location: [package.json:32](../package.json#L32), [frontend/package.json:24](../frontend/package.json#L24), the corresponding lockfiles, and [.github/workflows/production-beta.yml:63](../.github/workflows/production-beta.yml#L63).

   Root audit affected entries: `concurrently`, `electron`, `extract-zip`, `shell-quote`. Frontend: `brace-expansion`, `browserslist`, `dompurify`, `js-yaml`, `monaco-editor`, `nanoid`, `next`, `postcss`, `sharp`. Both audits report high-or-higher severities and therefore fail the workflow's `--audit-level=high` gates. Some findings are transitive or development-only; this finding establishes a release gate failure, not universal application exploitability.

   Python audit also failed: resolution of `requirements.txt` produced affected `pytest==9.0.2` and `starlette==0.50.0`, with 8 advisory records (6 distinct IDs). Starlette is transitive through FastAPI. The requirements file pins direct dependencies only; it is not a complete transitive lock. The audit tool was installed in a temporary directory rather than added to the project's environment.

   Fix: review the registry advisories and update compatible direct/transitive dependencies and locks, including the Electron runtime. Rerun audit, desktop startup, production build and UI checks after those changes; do not blindly force major upgrades.

8. **P2 — Cancelling one run kills subprocesses belonging to other work.**

   Location: [backend/api/app.py:511](../backend/api/app.py#L511), [backend/api/app.py:564](../backend/api/app.py#L564), and [backend/api/routes/execute.py:239](../backend/api/routes/execute.py#L239).

   Cancellation callbacks discard the run argument and invoke the shared manager's `kill_all()`. Standalone builds, flash operations and other agent/workflow runs share that manager. When operations overlap, stopping one run can terminate unrelated work, including an upload. `kill_all()` also takes another global process snapshot after its grace period, potentially catching work started during cancellation.

   Fix: track subprocess ownership by run/task and cancel only that owner's process trees. Reserve `kill_all()` for application shutdown. Add a concurrent two-run cancellation test.

9. **P2 — Closing tabs or switching projects silently loses unsaved buffers.**

   Location: [frontend/hooks/use-promptforge-workspace.ts:544](../frontend/hooks/use-promptforge-workspace.ts#L544) and [frontend/hooks/use-promptforge-workspace.ts:734](../frontend/hooks/use-promptforge-workspace.ts#L734).

   `closeTab()` removes a tab without checking `dirty`; `selectProject()` clears every tab. Nexus directly wires these callbacks into its editor and project controls. The local-storage persistence stores paths, not unsaved content. A user can lose typed work with one ordinary navigation action.

   Fix: centralize Save / Discard / Cancel handling for dirty buffers, or persist drafts with explicit recovery. Cover tab close, project switch and application close.

10. **P2 — Flashing ignores the executable resolver used by successful builds.**

    Location: [backend/tools/flash_firmware.py:515](../backend/tools/flash_firmware.py#L515) versus [backend/services/platformio_service.py:506](../backend/services/platformio_service.py#L506).

    Builds resolve the repository virtual environment or configured PlatformIO executable. The ESP32 flash backend hardcodes `pio` for its probe and upload. On this review machine, the resolver returns `.venv\Scripts\pio.exe` while `shutil.which('pio')` returns `None`. The documented setup that does not activate the virtual environment can build but cannot start this upload command.

    Fix: inject the same resolved executable into build and upload paths; test with a repo-local installation and a PATH containing no `pio`.

11. **P2 — Undo trusts backup bytes without checking the recorded original hash.**

    Location: [backend/changes/service.py:314](../backend/changes/service.py#L314).

    Undo verifies the current target against the applied hash but restores the backup without comparing it to `previous_hash`. I applied a modification, changed `__undo__/main.cpp`, and invoked undo. The corrupted bytes were restored successfully, the ChangeSet became undone, and its stage was removed. This can turn backup corruption into silent source corruption.

    Fix: validate every backup hash before changing any target, reject missing/mismatched backups, and retain recovery data on failure.

12. **P2 — Autonomous runs lose project-local skills during staging.**

    Location: [backend/agent_runtime/orchestrator.py:759](../backend/agent_runtime/orchestrator.py#L759) and [backend/agent_runtime/autonomous_loop.py:89](../backend/agent_runtime/autonomous_loop.py#L89).

    The autonomous stage copies only the safe snapshot inventory, which excludes hidden paths and `.forgex`. The loop constructs `SkillRegistry` using the stage as its root. I created `.forgex/skills/custom-review/SKILL.md`: discovery finds it in the original workspace but not in the stage produced by the orchestrator's copy procedure. Built-in skills remain available, so ordinary tests can miss this.

    Fix: pass an explicitly read-only skill/context registry rooted in the original workspace, or copy validated skill resources separately from mutable source. Keep hidden user state out of the writable stage.

13. **P2 — Dependency files can displace application firmware from hardware analysis.**

    Location: [backend/agent_runtime/embedded_context.py:124](../backend/agent_runtime/embedded_context.py#L124).

    The inspector recursively enumerates every C/C++ source, sorts paths and reads only the first 80. It does not exclude `.pio`, build outputs or dependency folders. In a local fixture with 81 `.pio/libdeps` sources and a GPIO definition in `src/main.cpp`, the application pin disappeared from the graph. This is directly relevant when inspecting a built workspace; a freshly copied autonomous stage excludes `.pio` and is a different case. Enumeration and full-file reads also occur before their intended limits take effect.

    Fix: prune generated/dependency directories before traversal, prioritize application source, and bound both traversal and bytes read. Test the same project before and after dependency installation.

The strongest existing protections are worth preserving: desktop session authentication, renderer context isolation, scoped file-tool policies, ChangeSet conflict/tamper checks, OS-backed Windows credential storage, durable session/event storage, and substantial automated coverage. The required changes are principally at the seams between these components.

Recommended implementation order: close the build/hardware bypasses; make managed project and ChangeSet persistence agree; preserve editor revisions; bind approval to immutable upload bytes; fail closed on incomplete change analysis; resolve dependency gates; then address cancellation, executable resolution, recovery and context fidelity. Add cross-component regressions for the reproduced cases rather than more tests that only mirror individual methods.

The companion [reproduction script](review_reproductions_2026_09_06.py) checks seven findings using temporary fixtures. Run `.venv\Scripts\python.exe docs/review_reproductions_2026_09_06.py` from the repository root. It does not launch a build, upload firmware, or execute project hooks. Its assertions intentionally confirm the reviewed defects; after fixes, those assertions should fail and be replaced by regression tests for the corrected behavior.
