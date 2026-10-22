# Phase 2.5.8.6.2 — scoped AGY workspace trust results

## Status

Implementation, trusted-workspace bootstrap, attested readiness, and one real execution are complete. The real AGY run failed safely as `no_changes_produced`; the expected review artifact and patch pipeline were therefore not created. Phase 2.5.8.6.2 is not complete.

## Implemented

- Added the QA-only `AGYTrustedWorkspaceMode`, disabled by default.
- Added explicit prepare and verify commands for one stable ForgeX-managed workspace.
- Added marker, direct-child containment, protected-root, recursive symlink, active-workspace separation, and safe-reset enforcement.
- Added an exclusive ownership-token lock with conservative stale recovery.
- Integrated the stable workspace into the existing generic AGY router without a legacy fallback.
- Kept AGY cwd inside the managed copy and retained the existing review/patch/preflight path.
- Excluded internal marker/prompt names from reviews.
- Added per-run trust attestation and retained real-execution confirmation.
- Kept raw instructions and provider output out of persisted records and public events.
- Kept apply, build, flash, Codex, Claude, and OpenCode disabled.

## Trust bootstrap

Preparation returned `PREPARED` and executed no AGY edit. Structural verification confirmed the managed root and marker, then correctly returned `TRUST_STATUS_OPERATOR_ATTESTED_REQUIRED`; ForgeX does not pretend to detect AGY folder trust.

The current validation shell reported AGY not installed and authentication not attested. No real AGY process was launched, no run or review was created, and patch export/verify/preflight were not run. The active workspace remained unchanged.

## Automated verification completed so far

| Command | Result |
| --- | --- |
| Focused AGY trust/live selector | 15 passed, 2 skipped, 1,579 deselected |
| AGY JavaScript QA tests | 15 passed |
| AGY cutover/trust/live focused set | 38 passed, 2 skipped |
| Final trusted-runner focused set | 96 passed, 2 skipped |
| Backend compile | PASS |
| Full backend suite with documented default timeout | 1,587 passed, 9 skipped |
| Frontend typecheck | PASS |
| Frontend production build | PASS; 4 static pages |
| Electron build | PASS |
| Electron tests | 10 passed |
| Safety scan | 74 passed, 0 failed |
| Trust prepare | PREPARED; zero provider executions |
| Trust verify | `TRUST_STATUS_OPERATOR_ATTESTED_REQUIRED` |

The focused selector's two skips are Windows symlink tests where the host did not grant symlink creation. Equivalent containment logic remains covered by non-symlink tests and the JavaScript junction test passed. The first unscoped full-suite run had one pre-existing environment-sensitive failure because the local LLM timeout override was 300 seconds while the test asserts the documented 180-second default. A process-only 180-second override produced the clean full-suite result above; no local setting was changed.

Check-only without flags returned `BLOCKED_FLAGS_MISSING`. Check-only with all seven live flags, authentication attestation, and per-run trust attestation returned `BLOCKED_AGY_NOT_INSTALLED`. Both checks executed zero providers. The smoke artifact is absent from both active and managed workspaces.

## Remaining completion steps

1. Make the official AGY command available to the validation shell and complete official authentication.
2. Manually open and trust only the prepared ForgeX-managed workspace.
3. Start the gated loopback backend with all seven QA/live flags.
4. Run check-only with per-run trust attestation.
5. Run one explicitly confirmed positive smoke and record run/review IDs and exact change counts.
6. Export and verify the patch and run preflight without apply.

## Final trusted-workspace validation (July 2026)

| Check | Result |
| --- | --- |
| AGY detected/version | Yes; `1.0.14` |
| QA backend live gates | Enabled and ready on loopback |
| Trusted workspace prepare | PASS (`PREPARED`); zero provider executions |
| Trusted workspace verify | Structural PASS; operator attested per run |
| Attested check-only | `READY`; zero provider executions |
| Real positive smoke | Safe failure: `no_changes_produced` |
| Real execution count | 1 |
| Run ID | `bridge-run-d46552c3ef6e489f98d6850c971288c7` |
| Review ID | None |
| Changed files | 0 changed; 0 created; 0 modified; 0 deleted |
| Expected filename | Not created: `AGY_GENERIC_SMOKE.txt` |
| Active workspace safety | PASS; zero relative hash differences |
| Patch export / verify / preflight | Not run; no review authority exists |
| Automatic apply / build / flash | No / no / no |
| Legacy fallback | No |
| Safety scan | 74 passed, 0 failed |

The smoke artifact was absent from both active and managed workspaces, and `platformio.ini` was unchanged. Backend compilation, frontend typecheck, frontend production build, Electron build, and all 10 Electron tests passed. The exact unmodified backend suite reported 1 failed, 1,586 passed, and 9 skipped because the local timeout override is 300 seconds while the test asserts the documented 180-second default. With `PROMPTFORGE_LLM_TIMEOUT_SECONDS=180` scoped to the test process, 1,587 tests passed and 9 were skipped; no local setting was changed.

Codex, Claude, and OpenCode execution remained disabled. Phase 2.5.8.6.2 remains incomplete until a real trusted run creates the expected one-file review and enables read-only patch export, integrity verification, and preflight validation. Phase 2.5.8.7 is not recommended until that positive-artifact proof passes.

## Phase 2.5.8.6.3 native follow-up

The stable workspace was prepared and structurally verified again. A dedicated native command then started exactly one AGY 1.0.14 process with direct `-p` argv from that managed cwd after explicit confirmation and per-run trust attestation. The process was classified `NATIVE_AUTH_BLOCKED`, created no files, and left both the managed marker and active throwaway workspace unchanged. No invocation matrix, generic rerun, review, or patch operation followed because authentication blocking must not be bypassed.

## Phase 2.5.8.6.4 auth-only follow-up

The workspace was prepared and structurally verified without a provider edit. AGY local help exposed no auth/status/login or dedicated write subcommand. The auth-only guard used one bounded version probe and no write instruction, returned `NATIVE_AUTH_STATUS_UNAVAILABLE`, and observed zero managed or active workspace changes. No native or generic write was authorized because authentication readiness remained unproven.

After those checks pass, the recommended next phase is Phase 2.5.8.7 — AGY Production Hardening: Cancellation, Timeout, Restart, Long-Run, and Recovery QA.
