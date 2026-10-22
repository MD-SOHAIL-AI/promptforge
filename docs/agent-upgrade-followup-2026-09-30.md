# Agent upgrade follow-up — 2026-09-30

This follow-up builds on the durable-agent implementation and production-hardening reports in this directory. The shared checkout already contained extensive uncommitted work; no existing user changes were discarded.

## Changes

- Task revisions retire explicit replacements and narrow conflicting pin/timing assignments, preserve superseded requirements for audit, and remove old exclusions when a later instruction explicitly allows the feature. Cancelled build, simulation and unit-test checks no longer remain as acceptance gates.
- Requirement evidence rejects code quotes found only in comments. It remains source review evidence, not proof that the firmware performs the requested behavior.
- Model output reservations scale down near a shared job-budget limit to preserve room for subsequent repair and verification turns. Repair history now records model edits and the outcome of the next build.
- Workspace context reuses a bounded index for the same source revision. Symbol inspection marks source files listed in an existing compilation database while keeping results explicitly lexical and withholding compiler commands.
- The harness status and architecture document name the Python orchestrator as the run authority. The shadow harness is not promoted to execution authority.
- Next.js production builds use two workers after the default worker count exhausted this host's native Node memory during page generation.

## Local verification

| Check | Result |
| --- | --- |
| Full Python suite | 2,069 passed, four skipped for unavailable Windows symlink creation |
| Harness | 11 passed |
| Electron | 38 passed |
| Frontend state | 71 passed |
| Release scripts | 26 passed |
| Frontend type check and lint | Passed |
| Optimized frontend build | Passed with two Next.js workers |
| Browser conversation flows | 18 passed against the built production frontend |
| Source release-content scan | Passed |

The first frontend build compiled but its page-generation worker ran out of native memory. The build passed after worker concurrency was limited in `frontend/next.config.ts`. The first browser run's development server also ran out of memory. A production-server run then had one browser target crash while 17 tests passed; the failed case passed alone, and a subsequent complete production-server run passed all 18 tests.

## Qualification still required

The 30-scenario, three-repetition real-provider campaign and human request-fidelity review have not been run against this final source. A provider/model must be selected and configured before that campaign. No identified ESP32 serial port is currently available, so physical build/flash/serial sign-off remains open. Signing and clean Windows 10/11 machine evidence are also required before distribution. An earlier unsigned installer does not represent this source revision.

Compiler metadata improves source navigation but does not supply C++ semantic analysis. Source quotes and a passing build do not establish physical device behavior; acceptance reports retain separate simulation, unit-test and hardware evidence. These limitations must remain visible until corresponding evidence exists.
