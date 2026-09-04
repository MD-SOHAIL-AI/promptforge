# Phase 2.5.8.6.3 Native AGY Minimal Write Reproduction Plan

## Goal

Determine whether AGY 1.0.14 can create `AGY_NATIVE_SMOKE.txt` through its official non-interactive CLI when the process working directory is the prepared ForgeX-managed trusted workspace. The native run remains separate from the generic coordinator and does not grant review, patch, apply, build, or flash authority.

## Existing blocker

ForgeX can launch AGY and preserve safety, but AGY has not produced filesystem changes under the generic trusted-workspace path. Readiness, containment, stable working-directory preparation, explicit trust attestation, direct process execution, zero-change classification, and active-workspace isolation have been proven; a positive file artifact has not.

## Method

1. Prepare and structurally verify the stable managed workspace using the existing trust commands.
2. Inspect only AGY version and help output. Use `agy -p <runtime instruction>` unless help exposes a safer supported non-interactive edit command.
3. Reset the stable workspace from the marked QA throwaway workspace and validate marker, containment, protected-root separation, active-workspace separation, and absence of symbolic links.
4. Record relative file hashes for the managed and active workspaces, tracking the internal marker separately and confirming both smoke filenames are absent.
5. Require `--confirm-native-agy` and `--trusted-workspace-attested`, then start one AGY process with direct argv, `shell: false`, and the trusted workspace as cwd.
6. Reduce provider output in memory to a bounded classification. Persist neither the runtime instruction nor provider output.
7. Compare relative hashes, validate the expected file if present, and report counts only.
8. Attempt additional official variants only after a safe failure, only when help confirms support, and never after authentication or permission blocking.

## Safety boundary

The command refuses missing confirmation, missing attestation, an absent marker, an unexpected managed location, protected roots, the active workspace, the repository root, symbolic links, and unsafe argv. It never uses a permission bypass, interactive TUI, another provider, automatic apply, build, or flash. Native-only output cannot enter the patch pipeline.

## Verification

Run focused native/trust/live tests, backend compilation and full tests, frontend typecheck/build, Electron build/tests, and the bridge safety scan. A ForgeX generic rerun and read-only patch validation occur only after `NATIVE_WRITE_PASS`.
