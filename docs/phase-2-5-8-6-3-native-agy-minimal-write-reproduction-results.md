# Phase 2.5.8.6.3 Native AGY Minimal Write Reproduction Results

## Decision

The native reproduction is safely classified as `NATIVE_AUTH_BLOCKED`. AGY 1.0.14 was started exactly once with `agy -p <runtime instruction>` from the prepared ForgeX-managed trusted workspace, but it indicated that authentication/login was required and created no files. This run therefore does not establish whether an authenticated AGY process can write in non-interactive mode.

## Trusted workspace and CLI findings

Workspace preparation returned `PREPARED`. Structural verification confirmed the managed root and marker and returned `TRUST_STATUS_OPERATOR_ATTESTED_REQUIRED`, as expected because AGY exposes no machine-readable folder-trust status. The native command required explicit per-run trust attestation and revalidated managed containment, protected-root separation, active-workspace separation, and symbolic-link absence.

AGY was available as an application and reported version `1.0.14`. Top-level help was available. No `antigravity` alias was found. Help exposed `--print`/`-p` as the single-prompt non-interactive mode. The inspected `run`, `exec`, `agent`, `workspace`, `permissions`, and `trust` forms did not expose supported execution subcommands and fell back to top-level help. The selected invocation remained `agy -p <runtime instruction>`.

## Native result

| Check | Result |
| --- | --- |
| Classification | `NATIVE_AUTH_BLOCKED` |
| Real AGY execution count | 1 |
| Changed files | 0 total; 0 created; 0 modified; 0 deleted |
| Expected native file | Not created |
| Managed marker | Unchanged |
| Active throwaway workspace | Unchanged by relative hash comparison |
| Additional invocation variants | Not attempted; help confirmed no alternative and authentication blocking must not be forced |
| Patch/review/preflight | Not run; native-only runs create no authority |

The command also proved both fail-closed gates before the real run: missing confirmation and missing trust attestation each returned `NATIVE_UNSAFE_ABORTED` with zero executions.

## Comparison with ForgeX generic result

The prior ForgeX generic live run used the same stable managed cwd concept and the same `-p` instruction-delivery style. It completed as `no_changes_produced`, with 0 changed files and no review. The native attempt also produced 0 changed files, but its terminal classification differs: authentication was blocked before a useful write/no-write comparison could be made. The immediate blocker is AGY CLI session/authentication state in the native process, not demonstrated ForgeX diff failure. The evidence does not yet distinguish AGY non-interactive write behavior from a ForgeX invocation difference after authentication succeeds.

## Safety and tests

The native runner uses direct argv with `shell: false`, a managed trusted cwd, relative hash baselines, a separately tracked marker, and in-memory-only provider-output classification. It rejects the dangerous permission-bypass flag and contains no Codex, Claude, OpenCode, auto-apply, auto-build, or auto-flash path.

| Verification | Result |
| --- | --- |
| Focused Python AGY native/trust/live selector | 21 passed, 2 skipped, 1,579 deselected |
| JavaScript AGY QA tests | 33 passed |
| Backend compile | PASS |
| Unmodified full backend suite | 1 failed, 1,592 passed, 9 skipped; local timeout override was 300 seconds instead of the documented 180-second default |
| Process-scoped 180-second full backend suite | 1,593 passed, 9 skipped |
| Frontend typecheck | PASS |
| Frontend production build | PASS; 4 static pages |
| Electron build | PASS |
| Electron tests | 10 passed |
| Safety scan | 83 passed, 0 failed |

No local timeout configuration was changed.

## Status and next phase

Phase 2.5.8.6.3 is incomplete against the core question because authentication blocked the only native process before AGY demonstrated either a write or a normal zero-change completion. The minimum operator action is to authenticate through AGY's official flow for the same CLI context, without modifying ForgeX credentials or safety policy, then rerun the single guarded command.

Recommended next phase: **Phase 2.5.8.6.4 — AGY Official CLI Write-Mode and Session Investigation**. Production hardening and additional providers should remain deferred while AGY is a priority.

## Phase 2.5.8.6.4 session investigation update

Official AGY 1.0.14 local help exposes no machine-readable auth/status or standalone login command. A guarded auth-only command performed one fixed version probe, zero auth probes, and zero write runs, returning `NATIVE_AUTH_STATUS_UNAVAILABLE` with no workspace changes. The native write was not repeated because authentication readiness was not established; the prior `NATIVE_AUTH_BLOCKED` result remains authoritative.
