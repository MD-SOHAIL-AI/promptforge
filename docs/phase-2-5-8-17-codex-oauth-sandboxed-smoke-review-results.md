# Phase 2.5.8.17 — Codex OAuth Sandboxed Smoke and Review Results

## Phase 2.5.8.17A status alignment update

The UI/backend and QA status implementations previously differed. Phase 2.5.8.17A routes the UI, QA, smoke precheck, and provider registry through one aligned status service using a safe user environment and neutral system-temp cwd. The current aligned result remains `signed_out`, so no smoke or review is attempted. Production routing remains disabled and no authentication data or raw CLI output is persisted.

## Current result

Implementation is complete, but the real smoke is blocked by the official CLI status gate. On 2026-07-03, `codex login status` was supported and sanitized status reported CLI detected, version `codex-cli 0.142.5`, auth `signed_out`, bridge ready `false`, and `CODEX_OAUTH_BRIDGE_LOGIN_REQUIRED`.

The user completed the official Codex browser login flow and saw its signed-in confirmation. ForgeX does not own that OAuth flow and independently requires the official CLI status result before execution.

The browser sign-in page is not treated as proof of CLI readiness. No browser URL, token, OAuth code, auth file, raw status output, raw prompt, stdout, stderr, or account identity was stored.

The real execution count is `0`; no smoke pass or Bridge Review is claimed. Production routing, automatic apply, build, and flash remain disabled. AGY, Claude, OpenCode, and API providers were not executed by this phase.

## Verification results

- Codex Node tests: 59 passed.
- Focused Python selection: 7 passed, 1848 deselected.
- Full backend: 1843 passed, 12 skipped.
- Backend compile: passed.
- Frontend typecheck and production build: passed.
- Electron build and 10 tests: passed.
- Safety scan: 265 checks passed.

Phase acceptance remains incomplete only because official CLI status is `signed_out`; the implementation and mocked regression gates pass.

## Phase 2.5.8.17B session parity update

Normal CMD was reported as signed in, but all executable status checks available to the ForgeX process reported signed out. Multiple launchers were detected without exposing their paths. The selected shared resolved runner remains status-only, and no smoke was attempted.

## Phase 2.5.8.17R2 content-validation update

A later signed-in retry executed once in the external sandbox and created only the expected file, but strict byte equality rejected its terminal LF. Sanitized inspection confirmed a normalization-only mismatch. Validation now permits only BOM/CRLF/terminal-LF normalization before exact logical comparison; prompt variant `strict_single_line_v2` reduces ambiguity.

Phase 2.5.8.17R3 fixes the remaining classifier ordering bug. Known process failures remain authoritative, while an otherwise unclassified nonzero exit cannot override a fully validated exact artifact. Review creation remains gated by a second complete safe-metadata predicate.
