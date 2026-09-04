# Phase 2.5.8.7 Codex Headless Provider Investigation Results

## Decision

Phase 2.5.8.7 is complete as an honest negative investigation result. Codex CLI is installed and exposes an official non-interactive `exec` mode, but the corrected guarded real write attempt was blocked by workspace/permission policy. It created no files and left the managed marker and active throwaway workspace unchanged.

The result is success-definition outcome **B: Codex is installed but blocked by auth/session/permission state**. The observed blocker was permission/workspace policy, not authentication. Codex is not yet suitable for provider hardening on this evidence.

AGY remains paused because its managed-workspace write and exact nonce scratch artifact paths were unreliable. AGY, Claude, and OpenCode were not executed in this phase.

## Phase questions

| Question | Answer |
| --- | --- |
| Q1. Is Codex CLI installed and visible in the validation shell? | Yes. The npm Windows shim required an in-memory native launcher resolution so ForgeX could retain `shell:false`. |
| Q2. What version is detected? | `codex-cli 0.142.5`. |
| Q3. Does Codex expose an official non-interactive/headless mode? | Yes: local help documents `codex exec` as “Run Codex non-interactively.” |
| Q4. Can Codex run in a ForgeX-managed sandbox as cwd? | Yes. Both real attempts used unique managed sandbox children as cwd; no write ran from the repository or active workspace. |
| Q5. Can Codex create exactly one expected file? | Not in this validation. The corrected attempt was permission-blocked and created zero files. |
| Q6. Does the active workspace remain unchanged? | Yes, by relative file/hash comparison after both attempts. |
| Q7. Can ForgeX detect the sandbox diff? | Yes. It detected zero real changes and fake-Codex tests cover exact pass, extra, invalid, modified, and deleted cases. |
| Q8. Can ForgeX create a normal review from Codex output? | Not tested because native write did not pass; no review authority was created. |
| Q9. Can patch export / verify / preflight run without apply? | Not run because no Codex review existed. Apply/build/flash were never run. |
| Q10. Is Codex suitable for ForgeX provider hardening? | No, not until the scoped permission/workspace blocker is resolved without weakening the sandbox. |

## Local help findings

Raw help was inspected in memory and not persisted. Top-level help exposed `exec`, and `exec --help` exposed `--sandbox`, `--ephemeral`, `--ignore-user-config`, and `--skip-git-repo-check`. Top-level help exposed `--ask-for-approval`. The first real process used the approval option in the wrong subcommand position and exited before agent work; this was classified `CODEX_NATIVE_INVOCATION_UNSUPPORTED`. The corrected ordering reached Codex and returned `CODEX_NATIVE_PERMISSION_BLOCKED`.

| variant | help_confirmed | auth_requirement_known | write_capable | safe_to_test | reason |
| --- | --- | --- | --- | --- | --- |
| `codex exec` | yes | no | yes | yes | Official local non-interactive mode with workspace-write policy |
| `codex run` | no | no | no | no | Not supported by local help |
| `codex agent` | no | no | no | no | Not supported by local help |
| `codex -p` / `--print` | no | no | no | no | `-p` is a profile option, not print/headless mode |

No login flow ran. No Codex auth/config/token file was read by ForgeX. The CLI was allowed to use its normal existing authentication boundary; ForgeX did not inspect it.

## Sandbox and native result

Each real attempt used a unique direct child of `.promptforge/codex-sandboxes` containing only `README_FORGEX_CODEX_SANDBOX.txt` before execution. The directory was disjoint from the repository root and active workspace, protected-root checked, recursively link checked, and snapshot by relative paths and SHA-256 hashes.

| Result | Value |
| --- | --- |
| Real native process count | 2 |
| Attempt 1 | `CODEX_NATIVE_INVOCATION_UNSUPPORTED`; zero changes |
| Attempt 2 / final classification | `CODEX_NATIVE_PERMISSION_BLOCKED`; zero changes |
| Created / modified / deleted | 0 / 0 / 0 |
| `CODEX_GENERIC_SMOKE.txt` | Not created |
| Extra changes | No |
| Marker unchanged | Yes |
| Active workspace unchanged | Yes |
| Raw prompt/output persisted | No |

## Generic and patch gates

The required native pass did not occur. Therefore no Codex generic adapter was added, no generic live command was added or run, no review was created, and patch export, integrity verification, and preflight were not run. Auto-apply, auto-build, and auto-flash remained disabled.

## Verification

| Command | Result |
| --- | --- |
| `npm.cmd run qa:codex-detect` | PASS; installed, version/help/headless mode detected |
| `npm.cmd run test:codex` | 27 passed |
| Focused Python selector | 64 passed, 1,541 deselected |
| `npm.cmd run qa:safety-scan` | 121 passed, 0 failed |
| `python -m compileall backend` | PASS |
| Unmodified `python -m pytest` | 1 failed, 1,595 passed, 9 skipped; inherited timeout was 300 seconds while the test asserts the documented 180-second default |
| Process-scoped 180-second `python -m pytest` | 1,596 passed, 9 skipped |
| Frontend typecheck | PASS |
| Frontend production build | PASS; 4 static pages generated |
| Electron build | PASS |
| Electron tests | 10 passed |

## Recommendation

Recommended next phase: **Phase 2.5.8.8 — Provider Strategy Decision: API-backed provider vs local CLI provider**.

The remaining limitation is a scoped Codex workspace/permission denial under the safe `workspace-write` and no-bypass policy. Do not solve it with danger-full-access, additional writable roots, or approval/sandbox bypasses. A future local-CLI retry should require an official scoped mechanism that preserves the managed cwd and exact one-file boundary.

## Phase 2.5.8.12C follow-up

A later standalone smoke passed with the official CLI-auth path and established the working argv order: global `--ask-for-approval never`, then `exec`, then `--sandbox workspace-write`, then `--cd` and the final instruction. This identifies the earlier ForgeX delta as command ordering, extra exec flags, and/or the nested OneDrive-backed sandbox location—not a proven Codex authentication failure.

Phase 2.5.8.12C moves only the gated retry to direct children of `C:\forgex-codex-sandboxes`, removes `--ignore-user-config`, `--ephemeral`, and `--skip-git-repo-check`, and retains all no-bypass, exact-diff, no-active-workspace, and no-auto-apply rules. The original 2.5.8.7 result remains the historical classification for that run.

The single 12C retry ultimately returned `CODEX_NATIVE_UNKNOWN_SAFE_FAILURE` with zero changes, an unchanged marker, and an unchanged active workspace. It did not produce evidence for permission, authentication, or usage/quota reclassification, and no review was created.
