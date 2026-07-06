# Phase 2.5.8.17A - Codex OAuth Status Environment Alignment Results

## Result

The earlier status mismatch was reproduced: the QA command had a separate Node parser while the UI/backend used Python bridge detection. Status handling is now consolidated behind `CodexStatusService`. The QA command calls the sanitized service wrapper; the UI route, smoke precheck, and provider registry consume the same service result.

The service uses a neutral system-temp child directory, direct argv, `shell:false`, and the `codex_safe_user_env` allowlist. Normal Windows user-tool variables are preserved so the official CLI can locate its own user session. Secret-like names are excluded. ForgeX does not inspect authentication storage, persist raw output, or expose account details.

## Observed aligned status

On 2026-07-03, the aligned runner detected `codex-cli 0.142.5` and consistently classified the current session as `signed_out`. OAuth bridge readiness is false, production routing remains disabled, and no real smoke was attempted. The operator should run official `codex login`, or `codex login --device-auth`, then use Check Status Again.

## Verification record

- Status diagnostics: `CODEX_STATUS_DIAGNOSTICS_ALL_SIGNED_OUT`; all three runners detected Codex and reported `signed_out`.
- Status parity: `CODEX_STATUS_PARITY_PASS`; login and smoke were not launched.
- Focused status/security tests: 29 passed, 1848 deselected.
- Shared status/login unit tests: 27 passed.
- Full backend tests: 1865 passed, 12 skipped.
- Backend compileall: passed.
- Frontend typecheck and production build: passed.
- Electron build and tests: 10 passed.
- Codex Node tests: 59 passed.
- Safety scan: 273 checks passed.
- Direct in-app browser inspection: unavailable because no in-app browser was attached; UI source assertions, typecheck, production build, API tests, and Electron tests passed.

The final aligned status is `signed_out`, so the real smoke was not invoked. No review was created. Production routing, auto-apply, auto-build, and auto-flash remain disabled.

## Phase 2.5.8.17B follow-up

The new four-runner session parity diagnostic found multiple launchers but no signed-in result in the ForgeX process context, including the fixed Windows CMD status-only runner. The hardened parser correctly recognizes the known normal-CMD signed-in phrase in mocked coverage. The final shared runner is `resolved_executable_runner` with `codex_safe_user_env`; current status remains `signed_out`, so smoke remains blocked.
