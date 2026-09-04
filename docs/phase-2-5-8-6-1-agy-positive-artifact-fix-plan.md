# Phase 2.5.8.6.1 AGY Positive Artifact Fix Plan

## Objective

Prove that one real AGY process can create exactly one proposed file inside a ForgeX-managed throwaway sandbox, produce one review, and enter patch export, integrity verification, and read-only preflight without changing the active workspace.

## Safety boundary

- AGY is the only executable provider.
- Execution requires all live gates and explicit confirmation.
- The managed sandbox is the process working directory.
- The instruction remains runtime-only and is represented diagnostically only by length and SHA-256.
- Provider output is reduced to a safe classification and is not persisted.
- No shell, arbitrary command, executable, environment, or working directory is accepted.
- No automatic apply, build, flash, restore, rollback, or legacy fallback is permitted.

## Diagnostic plan

Record count-only diagnostics for sandbox entry, working-directory identity, throwaway marker presence, instruction delivery, exit classification, provider-output classification, pre/post file counts, diff count, ignored-file count, and review count.

Run diff collection only after process termination. Permit three bounded scans separated by 100 milliseconds, then classify a successful process with zero changes as `no_changes_produced`. Such a run creates no review/apply authority.

## Live decision rule

Use the documented `agy -p` headless mode. Fixed alternative flags may be evaluated only from local help and official documentation. Any mode that requires project/trust interaction, a global permission change, or `--dangerously-skip-permissions` is rejected.

The phase passes only if the expected file appears as one created review entry, the active workspace remains unchanged, and patch export, verification, and preflight succeed.
