# ForgeX production-beta hardening

This implementation targets Windows 10/11 x64 and the ESP32 DevKit V1
(`esp32dev`) beta. Python remains the authoritative agent runtime. The
TypeScript harness is optional shadow tooling, enabled with
`FORGEX_ENABLE_SHADOW_HARNESS=1`. Other boards and operating systems
require separate release qualification.

## Implemented changes

| Area | Behavior and release purpose |
| --- | --- |
| Durable journal | Payload, identity, sequence, chain and head validation; persistent quarantine; migration backup; paged projections use a validated read snapshot. Restart never repairs corrupted evidence by rewriting checksums. |
| Safe recovery | Sealed checkpoints carry staged changes, observations, plan and consumed budgets. Resume requires explicit confirmation and creates a linked continuation. A durable reservation prevents duplicate retries and resumes. Changed workspaces and uncertain effects block recovery. |
| Side effects | Apply and flash have durable intent/outcome records. Interrupted started effects become unknown. Uncertain apply/flash operations are never automatically replayed. Flash holds the session lease and consumes a one-time approval. |
| Cancellation | Cancel and lease loss fence future capabilities, ignore late runtime output and clean up owned processes. Windows Job Objects contain descendants and kill them when the backend dies. Process waits and streaming cleanup are bounded. |
| Serial | Finite reconnects, exclusive port ownership, cancellation-safe late opens, identity checks on re-enumeration and reusable monitoring generators. OpenOCD paths use validated quoting. |
| Agent limits | Shared physical model-request budgets cover retries, repair calls and child planners. Checkpoints preserve consumed tool/time/model budgets. Untrusted project, memory, skill, compiler and device text cannot grant privileges. |
| Recovery UI | Review and confirm resume; display blocked recovery for unknown hardware outcomes. Continuations reconnect to the new run. Recovered runs must collect fresh verification evidence. |
| Desktop storage | Packaged application resources are separated from per-user state. Legacy data migration copies and verifies files, uses SQLite backup for WAL consistency, preserves the source and survives interrupted publication. |
| Distribution | Pinned embedded Python, hashed Python dependency lock, npm locks, bundled PlatformIO entry point, standalone frontend and harness, CycloneDX inventories, file-hash manifest and packaged-content scanner. |
| Release gates | Automated regression checks, bundled backend startup/authentication/restart smoke, signed NSIS packaging and installer-hash-bound hardware and clean-machine reports. Unsigned packages are explicitly test-only. |
| Incident regressions | Five redacted requests captured from the local run history, including the 959-line specification and smoke-sensor exclusions. Diagnostic restrictions are explicit host configuration; ordinary prompt words cannot enable them. |
| Request fidelity | Major sensor/display/cloud alternatives are grouped into one persisted clarification. Every write request carries a project/session/workspace-bound contract. Finalization requires current source evidence for every requirement group; edits invalidate stale evidence. |
| Failure settlement | Structured failure categories retain the original error and recovery action. Terminal events settle tools and timers. Missing historical terminal events are reconciled once after a SQLite backup; interrupted effects remain blocked. |
| Firmware repair | Registry IDs must resolve exactly, or unqualified names uniquely. Dependency lookup failures are retried without poisoning the cache. Repeated diagnostic groups stop after an initial failure plus three repairs, including across continuation. Ordinary source edits preserve PlatformIO's incremental cache. |
| Operations | Backend logs identify the process and source fingerprint. Authenticated diagnostic exports omit prompts, source, credentials and serial text. Only old, unreferenced managed stage folders are pruned; durable checkpoint references and user projects are preserved. |
| Startup liveness | Health and runtime identity requests return promptly while a shared, bounded PlatformIO probe runs in the background. Cancelling one waiter cannot cancel another's probe; repeated polls use cached readiness. Shutdown cancels the probe before stopping owned tools. |

## Latest incident and acceptance criteria

The recent requests failed at tool-plan validation after successful provider
responses. The old runtime inferred diagnostic mode from any occurrence of
`smoke`, including an excluded future sensor. It then misreported the policy
rejection as a provider outage and could omit the terminal event. Those three
boundaries now have regression coverage. Existing failures require an explicit
retry after restarting with the updated backend; unknown apply/flash outcomes
continue to require inspection.

`requirement_report` distinguishes exact source references from independent model
review of requirement fidelity. Completion requires review of every requirement
against the final task/source revision. The reviewer shares the parent's durable
model budget. Neither model review nor compilation proves physical device behavior.
The Evidence workspace keeps these levels separate.

ESP32 uploads require a sealed manifest of bootloader, partition table, optional
OTA initialization, and application bytes with explicit offsets and hashes.
Approval binds the entire manifest. Confirmation never rebuilds firmware or calls
PlatformIO's upload target. Windows sharing locks prevent changing those images
while the uploader reads them. Other board profiles remain unqualified.

Before beta distribution, replay all five captured requests and their follow-up
edits on the installed candidate with a real selected provider and PlatformIO
6.2.0. The runner checks real firmware hashes, fresh build/source evidence and
the installed resource manifest against the candidate. Configure the normal
local authentication token in `FORGEX_LOCAL_TOKEN`, then run:

```powershell
python scripts/qualify-agent-prompts.py --installer path/to/installer.exe --workspace path/to/isolated-qualification --provider your-provider --model your-model --output agent-prompts.json
```

The workspace must be isolated from user projects. Provider usage is real.
Review the generated implementation against each request and document behavioral
checks in the hardware report. A failed case blocks release; do not change the
fixture or fabricate evidence to pass the gate. The report is bound to the
fixture, manifest and installer hashes; rebuilt installers need new evidence.
The runner exits successfully for passing automated checks but leaves release
`passed` false. Record each case's `review.passed`, reviewer and evidence after
checking both generated versions against the original requirements, including
excluded features. Set the overall release result true only after every case
passes that review. The distribution gate requires these review records as
well as real compilation evidence.

## Verification

Run from the repository root with Node 22.19+ and a full Python 3.13 installation:

```powershell
python -m pip install --require-hashes -r requirements-runtime.lock
python -m pip install pytest==9.0.2 pytest-asyncio==1.4.0 pytest-cov==7.0.0 pip-audit setuptools==84.0.0
npm ci
npm ci --prefix frontend
npm run test:release-scripts
npm run verify:beta
npm run build:frontend
python -m pip_audit -r requirements-runtime.lock --require-hashes
npm audit --audit-level=moderate
npm audit --prefix frontend --audit-level=moderate
npm run prepare:release
npm run scan:package
python scripts/smoke-packaged.py
```

`FORGEX_PYTHON` selects the Python interpreter for `verify:beta`.
`FORGEX_RELEASE_PYTHON` selects the full Python interpreter for the packaging
runner. Browser tests use `.next-e2e`, so they do not overwrite the production
standalone build. They mock the transport; the separate bundled-backend smoke
checks actual HTTP authentication and restart persistence. Neither substitutes
for real firmware or clean-machine testing.

After packaging, `python scripts/smoke-desktop.py` launches the actual desktop
with an isolated profile and a restricted PATH, verifies authenticated renderer
requests against the bundled backend and saves a UI screenshot. The packaging
runner and Windows CI include this check. Ports 8000, 3000 and 8765 must be free;
the smoke test refuses to terminate an existing service. It checks the local
machine and does not count as clean-machine qualification.

The regressions include journal corruption across restart, more than 1,000
events, checkpoint validation and duplicate recovery, uncertain-effect blocking,
process deadlines without captured output, stream closure under output pressure,
Windows descendant cleanup after backend death, Windows junction rejection,
serial generator restart, migration and request-budget exhaustion.

## Installer and signing

Configure the certificate path in `CSC_LINK` and provide its password through
`CSC_KEY_PASSWORD` in the local environment or CI secret storage. Do not commit
either value. Run `npm run package:desktop`. This runs verification, audits,
frontend build, staging, packaged scanning, bundled smoke and NSIS packaging.
It validates Authenticode and writes an installer SHA-256 evidence record.

`npm run package:test` creates an unsigned test candidate. It is rejected by
the distribution evidence gate and must not be presented as a signed release.
The installer uses per-user installation and preserves application data on
uninstall. Runtime data belongs under Electron's per-user `userData` directory;
settings use the existing per-user settings service. Upgrade migration keeps
the original data intact. Back up both settings and runtime data before any
manual data repair.

## Required external release evidence

1. **Signing:** supply a real Windows code-signing certificate and successfully
   verify the final installer's Authenticode signature.
2. **ESP32:** use the final signed installer and its bundled PlatformIO runtime;
   run 20 consecutive builds and 10 upload/serial cycles with unique nonces.
   Keep firmware hashes, upload exit codes and matching serial evidence.
3. **Clean machines:** test Windows 10 and Windows 11 x64, including installation
   without system Python/Node, upgrade and uninstall data preservation, USB
   unplug/reconnect, offline model recovery and backend-crash explicit resume.
   Record evidence paths, operator and completion timestamp.
4. **Agent qualification:** replay the five recorded prompts and follow-up edits
   using the installed candidate and a real provider, as described above.
5. **Distribution gate:** bind all reports to the exact installer hash and run:

```powershell
npm run verify:release -- path/to/installer.exe hardware-signoff.json clean-machines.json agent-prompts.json
```

The gate rejects unsigned candidates, mismatched installer hashes, incomplete
build/cycle evidence, reused nonces, missing machine scenarios and missing,
mocked or stale recorded-prompt results. The starter
machine report is [release-clean-machine-template.json](release-clean-machine-template.json).
It deliberately starts with failed/unexecuted cases. Replace them only with
observed results and actual evidence. The hardware sign-off script accepts
`--installer`, `--project`, `--port`, `--pio` and `--report`; when `--pio` points
to `forgex-pio.py`, set `FORGEX_PLATFORMIO_PYTHON` to the installed bundled
`python.exe`.

## Operational response and boundaries

For `AGENT_JOURNAL_QUARANTINED`, preserve the SQLite database and associated
data before investigation. Do not edit checksums or force a run lease. For an
unknown apply or flash outcome, inspect actual project/device state and start
a fresh verified run after reconciliation; do not retry the uncertain effect.
Review any migration failure before switching data roots.

Checkpoint copies reject links/junctions and limit each snapshot to 10,000
files and 256 MiB. This is file validation, not an OS security sandbox.
Windows Job Objects provide process ownership and cleanup, not filesystem or
network isolation. Arbitrary harness terminal execution remains disabled
without verified isolation. Promoting the harness to authoritative execution,
adding fully untrusted build execution, retiring durable checkpoints, wider board
support and additional platforms need separate engineering and qualification.

Release readiness depends on the actual final signed artifact and external
evidence above. Passing automated checks alone does not establish production
readiness.
