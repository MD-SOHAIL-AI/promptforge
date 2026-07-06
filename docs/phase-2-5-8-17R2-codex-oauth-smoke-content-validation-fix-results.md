# Phase 2.5.8.17R2 - Codex OAuth Smoke Content Validation Fix Results

## Prior failure inspection

The previous real run created exactly the expected file but returned `CODEX_OAUTH_SMOKE_CONTENT_INVALID`. Metadata-only inspection completed as `CODEX_OAUTH_SMOKE_INSPECT_PASS`. Sanitized diagnostics showed a single LF at EOF, no BOM, no leading or trailing spaces, and a normalized logical-content match. No raw file content or process output was printed or persisted.

## Fix

Validation now removes only a UTF-8 BOM, converts CRLF to LF, and removes terminal LF characters before an exact case- and punctuation-sensitive comparison. Extra words, text lines, markdown, quotes, and arbitrary whitespace remain invalid. The runtime-only instruction uses `strict_single_line_v2`.

The review validator uses the same normalization policy and records `normalized_single_line` plus the prompt variant. Review creation remains restricted to a validated one-file pass with unchanged marker and active workspace.

## Retry and verification

The shared precheck reported `signed_out` and bridge readiness false on both required status checks. Therefore the one permitted real retry was not attempted and no review was created. This preserves the explicit signed-in precondition and leaves the previous sanitized smoke record available for inspection.

- Inspect: `CODEX_OAUTH_SMOKE_INSPECT_PASS`; mismatch kind `trailing_newline`; normalized match true.
- Focused backend: 8 passed, 1,876 deselected.
- Full backend: 1,872 passed, 12 skipped; compileall passed.
- Codex Node tests: 71 passed.
- Frontend typecheck and production build: passed.
- Electron build and tests: 10 passed.
- Safety scan: passed.

Production routing, apply, build, and flash remain disabled. Phase completion is blocked only on re-establishing the official CLI signed-in state and consuming the single guarded retry.

## Phase 2.5.8.17R3 correction

A subsequent signed-in run produced a valid normalized artifact but still ended as `CODEX_OAUTH_SMOKE_UNKNOWN_SAFE_FAILURE`. R3 identified the cause as classifier ordering: an unclassified nonzero process status was considered before the validated artifact predicate. The ordering and review gate are now fail-closed around explicit safe metadata.
