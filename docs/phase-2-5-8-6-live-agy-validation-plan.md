# Phase 2.5.8.6 Live AGY Validation Plan

## Objective

Validate one explicitly authorized AGY process through the generic coordinator, managed sandbox, sanitized SSE boundary, review, patch export/integrity/preflight pipeline, and unchanged active throwaway workspace.

## Gates

1. `--check-only` must use locator and metadata probes only; it must never launch AGY.
2. The exact `workspace/forgex-apply-test` directory, non-symlink containment, and `QA_NOT_REAL_PROJECT.txt` marker are mandatory.
3. All six live flags, safe AGY installation/version detection, authentication attestation, live-gated loopback backend, and optional required Agent UI must pass.
4. Real execution additionally requires `--confirm-real-agy` and prints the sandbox/no-apply warning before submission.
5. Cancellation and timeout are separate confirmed modes and never run implicitly.

## Live validation

Before execution, record relative file names and SHA-256 hashes. After execution, require one generic-run delta, one compatibility-run/process delta, one sandbox delta, unchanged workspace hashes, sanitized ordered SSE with one terminal event, and no active-workspace smoke artifact.

On successful completion, validate the review contains only `AGY_GENERIC_SMOKE.txt`, export and verify the patch, run read-only preflight, and keep apply disabled. Never fetch or record patch content.

## Stop condition

If readiness is not `READY`, do not request confirmation and do not execute AGY. Record the exact safe blocker and complete only the non-executing verification/documentation work.

