# Live-log reliability implementation — 2 October 2026

This implements the approved incident plan described in
[the full retained-log review](live-log-review-2026-10-02.md). The incident had
both upstream provider failures and ForgeX defects. These changes address the
host defects and preserve work during an upstream outage.

## Resulting behavior

- Both agent runtimes accept partition CSV files through the shared policy.
  Other rejected extensions return a structured reason and the allowed types.
- Before an ESP32 build, the selected environments' inherited partition settings
  resolve through the same resolver used for project discovery. Arbitrary parent
  sections, later-parent precedence and references are supported. An explicit
  custom or installed built-in table is checked with the installed Espressif
  partition utility, including inferred offsets, alignment, overlaps and flash
  capacity. Missing inputs and unavailable tools have distinct error codes.
  Default tables remain subject to the canonical PlatformIO build.
- Firmware generation into an empty folder takes the build workflow when the
  host requires a build. A model's interpretation cannot remove that requirement.
  Trust, missing tools and exhausted budgets keep the result as an unverified
  draft. Applying files and flashing retain their separate authorization gates.
- Successful writes seal immutable checkpoints before the next effect. Stop
  fences new capabilities, drains with bounded waits and retains confirmed
  changes before committing its terminal projection. Concurrent Stop requests
  produce one settlement. An unsettled operation blocks recovery and preserves
  its live staging folder for outcome review; late workers cannot change the
  terminal outcome. Count-based cleanup no longer deletes referenced drafts.
- Journal events and the associated run projection retain a sealed resume
  boundary. Recovery can rebuild a missing derived checkpoint index after an
  interruption. Older cancelled projections are repaired only when their saved
  workspace, managed stage and revisions validate. Unverifiable effects are not
  replayed. Discard is a separate, explicit API and review action.
- Planner deadlines use the smaller of the configured turn limit and one third
  of remaining runtime after the verification reserve: 120 seconds for firmware
  builds and at least 45 seconds otherwise. The shared physical-attempt budget,
  cumulative job budget, admission and circuit layers remain authoritative.
  Persisted outage cooldowns use 30/60/120 seconds with jitter, respect a longer
  provider Retry-After and retain the capped automatic recovery count. Queue
  pressure, policy, cancellation, budget and transport failures remain distinct.
  Diagnostics distinguish dispatch from receipt of a provider response.
- A missing folder stops dependent profile, trust, session and file refreshes.
  The interface offers Locate, Open another folder and Remove from recents.
  Locate preserves the project ID; removing a recent registration leaves the
  folder contents alone. A stale session clears its remembered selection and
  subscriptions. Focus, visibility and file-change refreshes coalesce, with
  guards against stale responses. Saved files open in staged review.
- Backend processes and desktop launches write separate rotating JSONL files.
  The live tail and run export merge them with legacy logs, tolerate malformed
  records and handle rotation. Exports bound the number of sources and bytes
  read and omit prompt/source text. Credentials remain redacted while numeric
  token counts remain observable. Logs carry actual app version, process and
  launch identities, and backend source fingerprints.

Partition handling follows the primary references for
[PlatformIO ESP32 partition configuration](https://docs.platformio.org/en/latest/platforms/espressif32.html),
[PlatformIO inheritance](https://docs.platformio.org/en/latest/projectconf/sections/env/options/advanced/extends.html),
and the [Espressif partition utility](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-guides/partition-tables.html).
Provider-body failures follow [OpenRouter's documented error behavior](https://openrouter.ai/docs/api/reference/errors-and-debugging).
Process-specific files address the shared-file limitation explained in the
[Python logging cookbook](https://docs.python.org/3/howto/logging-cookbook.html).

## Verification

The final source backend suite passed **2,146 tests**, with four skips because
Windows symlink creation is unavailable and two dependency deprecation warnings.
Evidence: `.forgex/reliability-final-backend-tests.log`.

The production-beta gates passed all seven checks: backend, harness (11),
frontend state (71), Electron (39), frontend type checking, lint with zero
warnings, and browser conversations (34). The new browser checks cover missing
folders, stale sessions and explicit draft discard. The Electron concurrency
check runs two desktop writers and a Python backend writer together, including
rotation and redaction. Release-script checks passed 31 tests.
Evidence: `.forgex/reliability-release.log` and
`.forgex/reliability-release-script-tests.log`.

Desktop and frontend npm audits found zero vulnerabilities. The pinned Python
runtime dependency audit, evaluated using Python 3.13, found no known
vulnerabilities. Network access was needed for the online advisory services.

The original release invocation stopped at the sandboxed npm audit after all
seven beta gates passed. The audits were rerun with approved network access;
the remaining packaging stages continue from those verified gates.

## Package qualification

The unsigned **ForgeX 0.1.1 Windows x64 installer** is available at
`.forgex/installers/ForgeX-0.1.1-windows-x64-setup.exe`.

| Check | Result and evidence |
| --- | --- |
| Package inventory | 5,087 manifest-bound resource files passed the content scan, before and after packaging. All 212 backend source files match the current workspace. |
| Backend startup/restart | Both staged and final bundles passed authenticated API, isolated storage, provider tool catalog, environment profile and session persistence checks. |
| Durable recovery | Both bundles passed concurrent Stop, injected provider outage, restart, Resume and explicit Discard. Three files survived each interruption. Final-package Stop completed in 1.84 seconds. Provider/model selection and consumed request budget remained unchanged. |
| Real ESP32 compile | Bundled Python 3.13.15 and PlatformIO 6.2.0 compiled the inherited custom CSV fixture with installed espressif32 7.0.1. The installed utility validated the layout; bootloader, partition table, OTA data and application images passed sealed-upload-set checks. |
| Desktop | Actual packaged launches passed normal and closed-output-pipe checks, with restricted PATH, isolated profiles, authenticated renderer API, restored Agent workspace and screenshots. Every instance log record matched app version, source fingerprint and the shared desktop launch identity. |
| Installer signature | `NotSigned`, as expected for the unsigned test target. Distribution qualification remains gated. |

Evidence is retained in `.forgex/reliability-package-checks.log`,
`.forgex/reliability-package-smoke/staged/live-log-reliability-smoke.json`,
`.forgex/reliability-package-smoke/final/live-log-reliability-smoke.json`,
`.forgex/reliability-esp32-build/esp32-build-smoke.json`,
`.forgex/reliability-desktop-smoke.log`,
`.forgex/reliability-desktop-closed-pipes.log` and the installer's `.evidence.json`.

- Installer SHA-256: `b2837e36c21e5b1b7dc4c20f436242b335ba231f76ea908f166b129d49e242e5`.
- Resource manifest SHA-256: `efafb41e7b40bd09983514334b340fe5dce4fec970afef16e1d332216eb5994d`.
- Development source fingerprint: `3257be374a38340e1a2074d6d4c7752325ebf3bff7b16af7abb4be9787ec2513`.
- Packaged backend fingerprint: `368d8e2ce68dfc8e2c780b0cebb3a1aed10e654452b78da80d20ee88fb45d9d9`.
- Compiled application SHA-256: `07d889de592f8e8c203e14f6e4ec11d715aaa770e0d5800453ed4b78aa7a72e2`.

The development fingerprint includes additional dependency input files; the
packaged manifest binds the actual runtime inventory. Provider fault outcomes
in the recovery smoke are injected. The smoke uses the actual bundled backend,
SQLite journal, managed stages, ChangeSets and recovery methods; it does not
certify live provider availability. These checks did not resume or discard the
operator's existing jobs.

## Rollback and release limits

Version 0.1.1 is an unsigned test build. The existing 0.1.0 installer remains
available under `.forgex/installers`; retained candidate bundles also remain
available. Stop the application before replacing its executable. Back up the
desktop data directory, then reinstall the previous binary without deleting
application data. The database additions are compatible; avoid resuming new
recovery records through an older binary until its recovery behavior is checked.

Publisher signing, clean-machine qualification, live-model prompt qualification
and physical ESP32 hardware sign-off remain the existing distribution gates.
No physical flash or serial operation is performed by these software checks.
