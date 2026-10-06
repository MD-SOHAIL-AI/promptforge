# ForgeX implementation verification

This is the earlier beta-hardening verification baseline. The subsequent
durable-job and agent-quality implementation is described in
[the current implementation runbook](agent-quality-implementation.md) and
validated in [the current verification report](agent-quality-verification.md).
The historical hashes and counts below do not certify those subsequent changes.
The installer filename is now reused for the new candidate; consult its current
evidence record and the current report for the latest artifact hash.

Software hardening and Windows beta packaging are implemented. Distribution
sign-off remains incomplete until the final signed installer passes recorded
prompt qualification, request-fidelity review, physical ESP32 and clean-machine
gates. This report covers the local workspace;
it does not assert that every possible defect has been eliminated.

## Checks completed

| Check | Result |
| --- | --- |
| Software gates | All seven individual software gates passed |
| Python 3.13 suite | 1,786 passed; four skipped for Windows symlink privileges |
| Final compatibility changes | 63 targeted reliability, continuation, orchestrator and API regressions passed after the full suite; includes legacy retry and correction of pending choices |
| Harness regressions | 11 passed |
| Frontend state regressions | 65 passed |
| Electron regressions | 30 passed; repeated after packaged Python invocation changes |
| Browser regressions | Seven passed, including confirmed resume and unknown hardware outcome blocking |
| Release-script regressions | 22 passed |
| Startup/API/toolchain regressions | 107 passed after shared background readiness probes |
| Frontend type checking and lint | Passed; zero lint warnings |
| Production frontend build | Passed after the final recovery wording update |
| Desktop npm audit | Zero known vulnerabilities |
| Frontend npm audit | Zero known vulnerabilities |
| Python runtime audit | Zero known vulnerabilities in the hashed runtime lock (OSV) |
| Bundled backend | Startup, authentication, per-user storage and session persistence across restart passed |
| Real packaged desktop | Final rebuilt candidate passed with a fresh profile and restricted PATH; renderer settings, projects and sessions requests returned HTTP 200; unauthenticated backend settings returned HTTP 401 |
| Filesystem/process hardening | Windows junction rejection, process deadlines, output-pressure stream closure and child cleanup after backend death passed |
| Final installer payload | Rebuilt candidate passed archive integrity for 4,103 files; embedded manifest matches the staged and unpacked runtime; post-smoke scan passed for 4,028 managed files |

The browser conversation tests use mocked transport. The bundled backend and
desktop checks exercise actual processes and HTTP requests separately. No
physical firmware build, flash or serial evidence is included in these results.
The Windows junction regression passes without symlink privileges and covers
the reparse-point boundary despite the four skipped symlink tests.

## Reviewable outputs

- [Implementation and release runbook](production-beta-hardening.md)
- [Clean-machine evidence template](release-clean-machine-template.json)
- Installer candidate: `.forgex/installers/ForgeX-0.1.0-windows-x64-setup.exe`
- Installer metadata: adjacent `.exe.evidence.json`
- Packaged UI screenshot: `test-results/packaged-desktop.png`
- Bundled file manifest and dependency inventories: `.forgex/installers/win-unpacked/resources/forgex/`

The local installer is unsigned and test-only. Its evidence record identifies
the exact hash. Release evidence must be regenerated for the final signed
installer; the test candidate cannot pass `verify:release`.

Final local test candidate verified on September 27, 2026:

- Installer SHA-256: `f270c096b7379297f9c64cb5830a8c093ace42224f54e77128dc3d2cfca424d4`
- Resource manifest SHA-256: `1ebd8c21eac38938d7c8c2cd614f1d94584e43dafcf9eafd9b41fd4c8a27d65d`

The adjacent evidence JSON records these hashes and supersedes the earlier
candidate. The installer uses the ForgeX icon and preserves
user data on uninstall. An existing desktop needs a full restart to load the
new backend; source changes do not update an already running process.

An interrupted compression run exposed Electron Builder's reuse of incomplete
intermediate archives. The packaging runner now discards that specific generated
cache file before building. The final candidate was rebuilt from validated
resources and its embedded payload passed archive integrity checking.

The desktop smoke initially failed during its first renderer evaluation, then
passed on retry. The test now waits for the page document to finish loading
before evaluating renderer APIs; that final check passed. This test-script
change does not alter the packaged application.

## Remaining release gates

| Priority | Required input/work | Completion evidence |
| --- | --- | --- |
| Release blocker | Code-signing certificate supplied through the local environment/CI secrets; build with `npm run package:desktop` | Valid Authenticode signature and signed installer hash |
| Release blocker | Real selected provider configured in the installed candidate | All five recorded requests and follow-ups pass real builds; each has recorded requirement-fidelity review |
| Release blocker | ESP32 DevKit V1 attached on an identified COM port; use the final bundled runtime | 20 consecutive verified builds and 10 unique nonce upload/serial cycles bound to that signed installer |
| Release blocker | Clean Windows 10/11 x64 machines | Install without system Python/Node; upgrade/uninstall preserve data; USB recovery; offline model recovery; explicit resume after backend crash |
| Final distribution gate | Combine the completed reports | `npm run verify:release -- installer.exe hardware.json clean-machines.json agent-prompts.json` passes |

The selected provider has no usable credential in this environment, so the
recorded prompt qualification and human request-fidelity reviews have not been
run. No physical ESP32, signing credential or clean-machine evidence was
available. Those gates remain open; the local test installer is not approved
for beta distribution.

## Additional engineering beyond this beta

Python remains authoritative and the harness remains in shadow mode. Full OS
filesystem/network isolation for hostile code, retirement of durable checkpoints,
wider board qualification and additional operating systems remain
separate work. The current file and process controls must not be represented
as a complete OS sandbox. Do not promote the harness or expand the supported
release scope based only on these local results.

Existing workspace edits were preserved. No source commit, deployment or
distribution was performed.
