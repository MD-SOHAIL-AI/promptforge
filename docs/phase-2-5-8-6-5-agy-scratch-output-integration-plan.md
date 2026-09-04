# Phase 2.5.8.6.5 AGY Scratch Output Integration Plan

## Goal

Determine whether ForgeX can import one run-specific AGY scratch artifact into a review-only path without enumerating provider scratch data or changing the active workspace.

The corrected root cause is that AGY non-interactive mode can respond and create files, but writes into its own scratch area instead of the ForgeX-managed working directory. ForgeX correctly reported zero workspace changes because it monitored the safe managed workspace while AGY wrote elsewhere.

## Phase scope

This phase supports only a smoke text artifact. Unified diff import remains deferred until the smoke path passes. Arbitrary project files are not accepted.

Each request creates an in-memory artifact model containing provider, run ID, nonce, exact filename, `.txt` extension, 16 KiB size limit, content marker, creation time, and `scratch_smoke` classification. The exact filename binds both run ID and nonce. The content must contain the exact marker and matching run ID and nonce.

## Safety boundary

- Resolve the provider scratch root from the current user's home directory and a fixed home-relative suffix. Test overrides exist only as explicit function arguments.
- Require the scratch root to pre-exist as a real directory. Do not create, enumerate, or recursively scan it.
- Generate the expected filename before AGY starts and reject a pre-existing exact artifact.
- Invoke only AGY with direct `-p` argv, `shell: false`, a bounded timeout, and the existing managed trusted workspace as cwd.
- Require explicit real-run confirmation and per-run trusted-workspace attestation.
- Reject the repository and active workspace as provider cwd.
- Ignore stdout file references and retain raw instructions and process output only in memory.
- After exit, inspect only the exact expected path. Require `.txt`, a regular non-link file, at most 16 KiB, realpath containment, exact content, and a matching nonce.
- Require unchanged active workspace, unchanged managed workspace, and unchanged trusted marker.
- Create a metadata-only Bridge Review record only after `SCRATCH_IMPORT_PASS`. It has no changed files and therefore no patch export or apply authority.
- Do not apply, build, flash, read credentials, or execute another provider.

## Classification

The command emits exactly one of the phase classifications: pass, missing, nonce mismatch, invalid content, too large, unsafe path, symlink blocked, provider error, timeout, unsafe pre-execution abort, or unknown safe failure.

## Verification plan

Use fake AGY execution for automated success and failure cases. Then run the focused Python selector, JavaScript AGY tests, safety scan, complete backend suite, frontend checks, Electron checks, and finally one explicitly guarded real smoke. The optional diff path must not run unless smoke passes.
