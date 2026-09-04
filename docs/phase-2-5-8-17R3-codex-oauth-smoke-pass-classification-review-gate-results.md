# Phase 2.5.8.17R3 - Codex OAuth Smoke Pass Classification and Review Gate Results

## Root cause

The previous real smoke had a fully valid normalized one-file artifact but ended as `CODEX_OAUTH_SMOKE_UNKNOWN_SAFE_FAILURE`. The classifier evaluated an otherwise unclassified nonzero Codex process status before evaluating the validated sandbox artifact. Because the initial classification was not `CODEX_OAUTH_SMOKE_PASS`, the review gate correctly did not run.

## Fix

Known timeout, authentication, usage, quota, permission, and invocation failures still take precedence. After those checks, an exact validated one-file artifact with unchanged marker and active workspace maps to `CODEX_OAUTH_SMOKE_PASS`, even when raw byte counts differ only because of permitted newline normalization. Unknown nonzero process status is evaluated only after the safe artifact pass predicate.

A second fail-closed predicate verifies every sanitized execution, sandbox, content, workspace, credential, persistence, routing, and automation flag before the review subprocess runs. The backend adapter independently validates the same safe metadata before exposing a pass. Review metadata includes normalized validation mode, applied normalization, and strict prompt variant.

## Verification and real retry

The automated classifier, backend gate, review, UI, and safety regressions pass. The required shared status precheck reported `signed_out` and bridge readiness false on both status readings, so the one permitted real retry was not attempted.

- Exact normalized/nonzero regression: passed; 43-byte trailing-LF content maps to `CODEX_OAUTH_SMOKE_PASS` without requiring raw byte equality.
- Focused backend: 10 passed, 1,876 deselected.
- Full backend: 1,874 passed, 12 skipped; compileall passed.
- Codex Node tests: 73 passed.
- Frontend typecheck and production build: passed.
- Electron build and tests: 10 passed.
- Safety scan: 291 checks passed.

The stored previous real result remains `CODEX_OAUTH_SMOKE_UNKNOWN_SAFE_FAILURE` with no review. The implementation fix is complete, but phase completion remains blocked until the official CLI is signed in for the single guarded retry. Production routing, automatic apply, build, and flash remain disabled.
