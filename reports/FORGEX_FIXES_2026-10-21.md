# ForgeX review fixes — 21 October 2026

All eight findings in [the original review](FORGEX_COMPLETE_REVIEW_2026-10-20.md) have been addressed in this workspace. Automated checks and a real ESP32 compilation passed. The additional Computer Use project-generation test requested by the user remains pending because the Computer Use tool denies access to ForgeX/Electron and Microsoft Edge, including after the user's explicit approval.

## Changes

| Original finding | Result |
| --- | --- |
| Persistent build images were rejected outside the disposable stage | The agent accepts host-owned, content-addressed BIN/ELF artifacts from the configured store, verifies their bytes on every refresh, and rejects arbitrary external paths or altered content. PlatformIO retains both BIN and available ELF output. Simulation and crash decoding use the verified artifacts. |
| Interrupted project backups were invisible to project listing and ID lookup | Listing discovers valid backup names and restores them under the existing store/workspace lock order. Invalid backups fail closed. |
| Build snapshots followed Windows directory junctions | Snapshot traversal checks directories and files for reparse points. Bounded, checked reads capture independent source bytes; a final pass rejects changes during capture. A real Windows junction regression test passes. |
| Typing during an in-flight save left the revision stale | Per-file writes are serialized. An acknowledged write advances the disk revision while preserving any newer editor buffer as dirty. Subsequent saves use the acknowledged revision. Failed writes are not replayed automatically. |
| Auto-opened generated files lacked a revision | Generated file tabs retain the revision returned by the file-content API. |
| A longer parent deadline overrode a shorter request timeout | Both normal and streaming model requests use the earlier deadline. Cancellation tests cover both deadline orderings. |
| Verification ran worker-dependent Python tests before building the worker | The beta and software-only verification paths build the model-router worker/harness first. The PowerShell verifier also selects the repository Python environment. |
| Unavailable free models produced a generic error | Structured missing-model responses and explicit OpenRouter retirement responses return a safe, nonretryable `MODEL_RETIRED` / `model_unavailable` result. Generic endpoint errors remain distinct. No automatic model or paid-provider substitution was added. |

The build verification also compares the captured source's canonical revision with the pre-build revision. This rejects a build made from different source even when an intervening edit is subsequently reverted. Regression coverage includes that edit/revert case and post-build source/artifact changes.

Source-tree release scanning excludes local `.promptforge` runtime state while strict release staging continues to reject that state. Two existing Python fixtures were made independent of test order and Windows symlink privileges; the Windows cross-process lock test now imports a lightweight worker rather than the entire application test module.

## Dependency updates

The online audits uncovered additional affected pins. Updates were installed and recorded in both npm lockfiles and the hashed Python runtime lock:

- Next.js `15.5.28`, Sharp `0.35.5`.
- Overrides for `follow-redirects` `1.16.1`, `shell-quote` `1.11.0`, `postcss-selector-parser` `7.1.6`, and `source-map-js` `1.2.2`.
- Certifi `2026.10.19`, with wheel/source hashes from [PyPI release metadata](https://pypi.org/pypi/certifi/2026.10.19/json). The downloaded wheel's SHA-256 matches the recorded wheel hash. This addresses [GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2).

The lint configuration remains on `eslint-config-next` `15.5.26`: the corresponding `@next/eslint-plugin-next` `15.5.28` was unavailable from the registry. Lint, type validation, production compilation, and the browser suite pass with the installed configuration. Tailwind remains on the existing major version; the patched selector parser was checked by the production build and browser suite.

## Verification evidence

| Check | Result | Evidence |
| --- | --- | --- |
| Full Python suite during fixes | 2,744 passed, 5 skipped; no failures | [Log](fixes-python.log), [JUnit](fixes-python.xml) |
| Final focused Python regressions after the captured-source revision guard | 94 passed, 1 skipped | [Log](fixes-targeted-python-final.log) |
| Desktop, harness, model-router, release-script JavaScript tests | 155 passed | [Log](fixes-javascript-final.log) |
| UI state tests | 106 passed | [Log](fixes-ui-state-final.log) |
| Browser suite after dependency updates | 67 passed, 1 skipped | [Log](fixes-browser-full.log) |
| Delayed editor-save browser regression | Passed; also included in the full browser suite | [Focused log](fixes-editor-browser.log), [test](../frontend/e2e/editor-save.spec.ts) |
| Production frontend build | Passed on Next.js 15.5.28 | [Log](fixes-frontend-build-final.log) |
| Frontend lint | Passed with zero warnings | [Log](fixes-lint-final.log) |
| Frontend typecheck | Passed | [Log](fixes-typecheck-final.log) |
| Source release-content scan | Passed | [Log](fixes-source-scan-final.log) |
| Root npm audit | Zero reported vulnerabilities | [JSON](fixes-npm-audit-root-final.json) |
| Frontend npm audit | Zero reported vulnerabilities | [JSON](fixes-npm-audit-frontend-final.json) |
| Hashed Python runtime lock audit | Zero known vulnerabilities | [JSON](fixes-pip-audit-final.json), [log](fixes-pip-audit-final.log) |
| Installed Python dependency consistency | Passed | [Log](fixes-pip-check.log) |

The full Python run preceded the final added edit/revert guard regression. The final focused run covers the resulting implementation. One focused test skips Windows symbolic-link creation when the account lacks that privilege; separate Windows junction coverage runs and passes. The browser skip is the production-CSP test, which explicitly requires a packaged production-server qualification environment.

### Real ESP32 build

[The local build probe](verify-fixed-build-2026-10-21.py) invokes the real PlatformIO service and agent verification boundary for an `esp32dev` Arduino project. It does not use a mocked compiler or a model-generated project.

- Compilation succeeded; firmware BIN size: 270,624 bytes.
- Agent `build_passed`: true; verification refresh: true.
- Durable BIN and ELF paths and hashes were produced.
- Evidence: [result JSON](fixes-real-build.json), [compiler log](fixes-real-build-final.log).

This establishes compilation and software artifact verification. It does not establish on-device behavior. No flashing, uploading, reset, serial session, or hardware signoff was performed.

## Requested Computer Use test — awaiting app access

ForgeX was started with the patched backend, rebuilt model-router/Electron code, and the successful production frontend build. The intended test is a new project, preserving existing projects, with this distinct prompt:

> Create a new ESP32 Arduino PlatformIO project named esp32-button-debounce-demo using board esp32dev. Use GPIO4 as an INPUT_PULLUP button and GPIO2 as an LED. Debounce the button with a 35 ms millis-based state machine. Each stable press toggles the LED and increments a press counter. At Serial 115200, report each press and print uptime plus free heap every five seconds. Keep the loop nonblocking and do not use delay. Include pin wiring and build instructions. Compile the project, but do not flash, upload, reset a device, or open a serial monitor. Preserve other projects.

Computer Use returned `Computer Use was not approved to use electron` when selecting ForgeX and `Computer Use was not approved to use Microsoft Edge` for the browser alternative. After the user replied “yes please,” a fresh ForgeX access attempt returned the same denial. No Computer Use screenshot, prompt submission, model run, or new project result is claimed. App access must be enabled before this test can continue.

The user subsequently authorized changing the configuration directly. On 21 October at 13:38 local time, `electron.exe` was added to `[computer_use.windows].always_allowed_app_ids` in `C:\Users\diwan\.codex\config.toml`. The original file is backed up beside it as `config.toml.bak-computer-use-20261021-133852`. TOML parsing and a complete comparison confirmed that all unrelated settings were preserved. A fresh access attempt, and another after resetting/reinitializing the Computer Use JavaScript session, still returned the same denial. The desktop host needs to reload its configuration; the ESP32 Computer Use test remains pending, and successful access after a host restart has not been verified.

On the next continuation, the saved allow-list was confirmed unchanged and another fresh ForgeX selection was denied. Read-only process inspection showed that the ChatGPT desktop processes were still running from 20 October, preceding the configuration change; some Codex processes had restarted, but the desktop host had not. A full quit and relaunch of the ChatGPT desktop app remains the next diagnostic step. No additional permissions were broadened.

When access is available, verify through the UI that the new project is separate, the selected model is explicit, the prompt generates the requested source, compilation succeeds, generated files can be opened and saved, and the resulting build evidence points to the current source. Record the model, run/project identifiers, build output, and any provider error. Do not substitute a paid model automatically.

## Reviewable change artifacts

This folder is an extracted source snapshot without Git metadata. Original copies are retained in `.cache/fixes-2026-10-21/before/`. The source/config changes are available as [a unified diff](fixes-2026-10-21.diff) and [a SHA-256 manifest](fixes-2026-10-21-manifest.json). Generated Next.js type/config edits were restored to their pre-task contents.

The checks above are software verification, not a signed release, packaged-app certification, or hardware qualification.
