# ForgeX audit remediation

This records changes against the original [code audit](../../audit/2026-10-05/ForgeX-Complete-Code-Audit.md).
The original audit and its evidence remain a snapshot of the pre-fix checkout.

## Scope

Fix the 14 reproduced audit defects and closely related safety/lifecycle defects.
The existing dirty checkout is preserved. No live firmware upload, credential
disclosure, release publication, or architectural rewrite is part of these fixes.
MCP, new OAuth bridges, semantic-loop consolidation and physical/release
qualification are additional engineering work, not completed by this bug-fix pass.

## Confirmed defects addressed

| ID | Status | Change and implementation |
| --- | --- | --- |
| FX-001 | Mitigated | [Native tests](../../backend/services/platformio_service.py) require an injected isolated runner. Without one, the host executor returns `NATIVE_TEST_SANDBOX_UNAVAILABLE` without running project code. |
| FX-002 | Fixed | [Skill reads](../../backend/agent_runtime/skills.py) check every workspace descendant for Windows reparse points, including Python 3.10. |
| FX-003 | Fixed | [Workspace hints](../../backend/agent_runtime/workspace_index.py) have file/byte/time budgets; [safe traversal](../../backend/security/workspace_io.py) is shared by snapshots, imports, archives and embedded context. |
| FX-004 | Fixed | [Legacy `/execute`](../../backend/api/routes/execute.py) receives a managed copy, emits reviewed ChangeSets and preserves the active project. Applying changes remains explicit. |
| FX-005 | Fixed | [Instruction and skill reads](../../backend/security/workspace_io.py) are bounded, identity checked and screened for high-confidence secrets. Context assembly also screens memory, skill labels and embedded profile data. |
| FX-006 | Fixed | [Verified build memory](../../backend/agent_runtime/orchestrator.py) uses host-selected structured build fields. Model summaries are excluded. A successful compile does not establish physical hardware verification. |
| FX-007 | Fixed | [Apply/undo](../../backend/changes/service.py) have durable write-ahead journals and startup recovery. Divergent manual edits become visible conflicts. Committed metadata is published before in-memory state. |
| FX-008 | Fixed | [Terminal cleanup](../../backend/services/terminal_service.py) has finite deadlines and cleans pending pumps after exit. Windows jobs and POSIX process groups cover descendants; ConPTY output and scheduled deliveries are bounded. |
| FX-009 | Fixed | [Default provider attempts](../../backend/model_router/router.py) use the endpoint circuit. Its six-failure threshold permits the physical retry allowance; server cooldowns can open it earlier. No provider/model switch was added. |
| FX-010 | Fixed | [Harness publishers](../../harness/src/event-store.ts) use per-session SQLite locks released by process death. JSONL remains the event format. |
| FX-011 | Fixed | [Heartbeat regression](../../tests/unit/test_agent_orchestrator_v2.py) waits for observed renewals instead of a short wall-clock sleep. |
| FX-012 | Fixed | [.env.example](../../.env.example) and [README](../../README.md) no longer advertise unsupported Codex OAuth or account-specific cloud model discovery. |
| FX-013 | Mitigated | The vulnerable SAP SDK is removed and [explicitly quarantined](../../backend/model_router/data/provider-quarantine.json). Python and Node block it before dispatch. Root npm advisory check reports zero vulnerabilities. |
| FX-014 | Fixed | [Worker boundaries](../../backend/model_router/providers/opencode.py) now bound spawn, stdin write, reap and stderr cleanup, including direct provider calls. |

Related changes: development Electron retains its sandbox; port parsing rejects
trailing junk; terminal WebSocket input has the same service limit as REST;
agent request limits count actual streamed bytes, including chunked requests.
Conversation browser tests now own their Next server process tree, finish with
exit status zero, and restore generated type/config metadata after shutdown.

## Verification

Environment: Windows, repository Python 3.10.6, Node 24.14.0 and npm 11.9.0.
Test counts combine independent groups and targeted reruns, not one monolithic
run. The ledger de-duplicates test identities and retains the latest outcome.

| Check | Observed result | Evidence |
| --- | --- | --- |
| Full Python unit group | 2,126 passed, 4 skipped | [unit log](logs/python-unit.log), [JUnit](logs/python-unit.xml) |
| API, integration and benchmark groups | 124 passed | [log](logs/python-other-final.log), [JUnit](logs/python-other-final.xml) |
| Later regressions | 101/102 initially passed; the remaining new fixture expected six attempts without allowing the long-backoff handoff. Adding `Retry-After: 0` exercised all six attempts and verified next-request circuit rejection; both provider tests passed. | [regression log](logs/regressions-complete.log), [provider rerun](logs/provider-final.log) |
| Final context/tool group | 198 passed; two existing dependency deprecation warnings | [log](logs/context-final.log), [JUnit](logs/context-final.xml) |
| Unique Python outcomes across groups/reruns | **2,258 passed, 4 skipped, 0 remaining failures**; repeated tests counted once, with their latest outcome | [verification ledger](verification.json) |
| Node: model-router, Harness, Electron and release scripts | 109 passed | [log](logs/node-tests-final.log) |
| Frontend state tests | 79 passed | [log](logs/ui-state-tests.log) |
| Browser suite | 52 passed; command exited 0 and its owned server was stopped | [log](logs/browser-tests.log) |
| Browser lifecycle after metadata restoration change | One existing browser case passed, command exited 0, and hashes confirmed exact restoration of both generated type/config files | [log](logs/browser-lifecycle-final.log) |
| Python syntax | 390 files compiled in memory; zero errors | [verification ledger](verification.json) |
| Frontend typecheck / lint | Both passed; lint produced zero warnings | [typecheck](logs/frontend-typecheck.log), [lint](logs/frontend-lint.log) |
| Frontend production build | Compilation, static generation and build traces completed | [build log](logs/frontend-production-build.log) |
| Model-router / Harness / Electron builds | Passed, final command exited 0 | [build log](logs/electron-harness-router-build-final.log) |
| Source release-content scanner | Passed | [log](logs/source-release-scan.log) |
| Python installed dependency consistency | `pip check` passed | [log](logs/python-dependencies.log) |
| Root npm vulnerability audit | 0 vulnerabilities, checked through the network after SDK removal | Verification tool result; recorded in [ledger](verification.json) |

New regressions cover real process death after each of three apply and undo
mutations, death after durable metadata commit, failed metadata publication,
post-crash manual edits, Python 3.10 junction traversal, oversized and secret
context, stalled worker writes/reaping, stalled terminal drivers, real Windows
pipe/ConPTY descendants, verified-memory provenance, chunked API bodies,
staged legacy writes, quarantined SDK dispatch, default-adapter circuits and
Harness writer death. See [Python regressions](../../tests/unit/test_audit_remediation.py),
[durability](../../tests/unit/test_audit_remediation_durability.py),
[providers](../../tests/unit/test_audit_remediation_provider.py),
[terminal ownership](../../tests/unit/test_terminal_process_ownership.py),
[API regressions](../../tests/api/test_audit_remediation_api.py),
[Harness crash recovery](../../harness/tests/crash-recovery.test.mjs), and
[port parsing](../../electron/port-validation.test.mjs).

Earlier development runs exposed an introduced undo preflight ordering issue,
an API fixture that exited before its background task finished, and mistakes in
new fixture construction. These were corrected and the affected groups rerun.
Logs are retained; the outcome count uses the latest result for each test.

## Remaining qualifications

Native tests are unavailable by default until a real security sandbox is supplied.
SAP remains unavailable until a safe SDK/adapter is qualified. Production readiness
still requires real provider accounts, physical-board flash/serial cycles, supported
OS and installer qualification, signing, upgrade/uninstall and crash-recovery runs.
No live provider/device/installer qualification is claimed by mocked tests.

## Open engineering work

The original audit's missing integrations and refactoring recommendations are
still open; addressing reproduced defects does not complete them.

| Work | Remaining scope |
| --- | --- |
| Native test sandbox | Supply and qualify a real runner that confines arbitrary compiled project code, devices and network access. The production fallback stays disabled. |
| SAP provider | Qualify a patched SDK or safe adapter; retain the visible quarantine until then. |
| Canonical semantic agent loop | Consolidate the two Python loops incrementally. Both still exist beneath the orchestrator; legacy semantic execution remains distinct despite its repaired staging boundary. |
| MCP | Implement discovery, transport lifecycle and tool registration through the existing policy/scheduler/executor path. MCP is currently missing. |
| Account OAuth | Codex and Google consumer-subscription OAuth bridges are missing. Gemini API-key authentication and Vertex authentication do not establish consumer subscription entitlement. |
| Harness consolidation | Shadow mode remains optional. SQLite fixes publisher ownership, not partial JSONL-tail recovery or production cutover. |
| Maintainability | The large orchestrator, tool runtime and frontend hooks still need the incremental decomposition described in the audit. |
| Release qualification | Live provider behavior, physical ESP32 cycles, OS/installer qualification, signing, upgrade/uninstall and power-loss behavior remain unverified. |

## Limits of the fixes

- Context screening recognizes high-confidence secret patterns; it cannot identify every arbitrary secret. Filesystem checks reduce unsafe traversal but do not create an OS sandbox against a hostile process racing path changes.
- Workspace indexing is byte/time bounded. ChangeSet snapshots still hash regular files to establish exact identities; the shared walker bounds entry count, not total snapshot bytes.
- ChangeSet tests establish recovery after **process death**. Multi-file publication is not power-loss atomic, and Windows directory durability remains subject to the filesystem. Concurrent desktop processes sharing ChangeSet state are not newly qualified.
- ConPTY joins its job immediately after spawn; the shell creation-to-assignment window is not proven race-free. Real ownership tests cover ordinary spawned descendants. The terminal is human controlled, not a security sandbox.
- Worker boundary cleanup is finite. Killing an unresponsive child is best effort; a deadline does not prove every OS failure can be reaped successfully.
- Browser tests exercise the actual frontend with test backend fixtures. They do not establish real account or device qualification. Electron was compiled and its tests passed; a full interactive desktop/installer qualification was not performed.
- The npm result covers installed root npm dependencies. `pip check` checks dependency consistency, not Python security advisories; a Python advisory scan remains open.
- Installed `ini@7.0.0` declares Node `^22.22.2 || ^24.15.0 || >=26.0.0`, which excludes this machine's Node 24.14.0 despite the observed successful builds/tests. Requalification on a version admitted by every installed dependency is still required; no global Node installation was changed.

The [verification ledger](verification.json) lists source deltas relative to the
audit snapshot. Pre-existing user changes were preserved. No commit, release,
firmware upload or architectural rewrite was performed.

## Subsequent live incident

The 19:28 IST OpenRouter run exposed a canonical-versus-wire tool-history
conversion bug. It was reproduced, fixed in both engine execution paths, and
verified with real planner/worker/tool continuations and malformed-history
checks. See [the incident and fix report](openrouter-tool-history/README.md).
That follow-up passed 252 affected Python tests and 133 Node tests; the combined
latest Python outcome count is now 2,262 passed and four skipped.
