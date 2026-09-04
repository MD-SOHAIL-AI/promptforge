# Phase 2.5.8.17B - Codex CLI Session Parity Diagnosis Results

## Result

The sanitized parity command is implemented. It detected multiple Codex launchers and classified the observed process context as `CODEX_SESSION_PARITY_ALL_SIGNED_OUT`. The shared resolved runner and fixed Windows CMD runner both reported `signed_out`; direct argv could not execute the Windows shim. None of the three environment profiles produced a signed-in result.

The selected shared status path is `resolved_executable_runner` with `codex_safe_user_env`. The parser evaluates negative meanings first and classifies `Logged in using ChatGPT` and `Authenticated using ChatGPT` as `signed_in` in mocked tests.

Final ForgeX status is `signed_out`, bridge readiness is false, and the classification is `CODEX_OAUTH_BRIDGE_LOGIN_REQUIRED`. Smoke remains blocked. Production routing remains disabled.

## Safety result

No prompt execution, smoke, login launch, AGY, OpenCode, or API provider was run. No CLI session file, token, callback, device code, account identity, raw status output, full executable path, or environment value was read, printed, or persisted by ForgeX diagnostics.

## Verification

- Session parity: `CODEX_SESSION_PARITY_ALL_SIGNED_OUT`; three launcher entries detected.
- Final status: CLI `0.142.5`, `signed_out`, bridge ready false.
- Focused backend selection: 35 passed, 1,848 deselected.
- Full backend: 1,871 passed, 12 skipped; compileall passed.
- Codex Node tests: 65 passed.
- Frontend typecheck and production build: passed.
- Electron build and tests: 10 passed.
- Safety scan: 280 checks passed.

The operational signed-in acceptance condition is not met. Per the phase decision rule, the operator must re-establish the official CLI login in the same Windows process/user context and rerun status before Phase 2.5.8.17R.

## Phase 2.5.8.17R2 follow-up

The CLI subsequently became signed in and completed one guarded smoke execution. The remaining failure was isolated to a terminal-LF content mismatch, not authentication, routing, sandbox containment, or active-workspace mutation.
