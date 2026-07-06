# Phase 2.5.8.6.5 AGY Scratch Output Integration Results

## Decision

Phase 2.5.8.6.5 is complete as a safe negative investigation result. The import mechanism is structurally safe under fake-provider tests, but the single guarded real AGY 1.0.14 smoke returned `SCRATCH_ARTIFACT_MISSING`. The expected nonce-bound artifact was not present at the exact expected scratch path after AGY exited. ForgeX did not enumerate scratch or inspect any alternative file.

Because the real smoke did not pass, AGY scratch output is not reliable enough to enable as a review provider path. No optional diff run was authorized, and no review was created.

## Corrected root cause

AGY non-interactive mode can respond and can create files, but the manual test showed that it writes into Antigravity CLI scratch instead of the ForgeX-managed workspace. ForgeX saw zero workspace changes because it watched the correct safe workspace while AGY wrote elsewhere.

## Implemented boundary

- Smoke text artifacts only; no arbitrary files and no unified diff execution in this phase.
- Exact run-ID-and-nonce filename generation before provider execution.
- Fixed home-relative scratch-root resolution with test-only dependency override.
- No scratch enumeration, recursion, newest-file selection, or stdout-link authority.
- Regular-file, extension, 16 KiB size, nonce, exact-content, realpath-containment, and link/reparse defenses.
- Direct AGY argv with managed trusted cwd, bounded timeout, and both explicit gates.
- Active workspace, managed workspace, and marker integrity checks.
- Metadata-only `agy_scratch` / `scratch_smoke` Bridge Review records with no changed files or patch authority.
- No raw instruction or raw provider output persistence.
- No apply, build, flash, Codex, Claude, or OpenCode path.

## Results

| Check | Result |
| --- | --- |
| AGY version | `1.0.14` |
| Scratch root method | Current user home plus fixed provider-relative suffix; no persisted absolute path |
| Exact nonce artifact requested | Yes |
| Real AGY execution count | 1 |
| Classification | `SCRATCH_ARTIFACT_MISSING` |
| Exact artifact found | No |
| Nonce verified | No; artifact absent |
| Size verified | No; artifact absent |
| Symlink/reparse defense | Implemented and enforced before any artifact read |
| Artifact realpath containment | Not established; artifact absent |
| Active workspace | Unchanged |
| Managed workspace | Unchanged |
| Trusted marker | Unchanged |
| Review artifact | Not created |
| Apply / build / flash | Not run / not run / not run |
| Optional diff artifact | Not tested because smoke did not pass |
| Safety scan | 106 passed, 0 failed |

## Test results

| Command | Result |
| --- | --- |
| Focused Python AGY selector | 19 passed, 2 skipped, 1,584 deselected |
| JavaScript AGY QA suite | 69 passed, 1 skipped |
| Backend compile | PASS |
| Unmodified full backend suite | 1 failed, 1,595 passed, 9 skipped; local timeout override was 300 seconds while the test asserts 180 seconds |
| Process-scoped 180-second backend suite | 1,596 passed, 9 skipped |
| Frontend typecheck | PASS |
| Frontend production build | PASS; 4 static pages |
| Electron build | PASS |
| Electron tests | 10 passed |

The skipped AGY tests require Windows symlink creation privileges unavailable to this host. Equivalent rejection logic is covered by realpath equality, containment checks, root-link rejection, and the existing trusted-workspace link tests.

## Core question and recommendation

ForgeX can constrain a scratch import to one exact nonce-bound artifact without reading unrelated scratch files or touching the active workspace. However, the real run did not produce that exact artifact, so this phase did not prove that AGY can reliably supply the review input. The safe conclusion is outcome B: do not integrate the scratch-backed provider path on current evidence.

Pause AGY integration. The next investigation should be the Codex provider path, with no Codex execution added or performed by this phase. AGY scratch-backed review hardening should resume only if a later official AGY behavior or interface reliably creates the exact requested artifact.

Phase 2.5.8.7 subsequently began the Codex path. AGY remains paused and was not executed during that investigation.
