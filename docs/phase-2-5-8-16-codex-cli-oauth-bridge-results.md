# Phase 2.5.8.16 — Codex CLI OAuth Bridge

## Phase 2.5.8.17 follow-up

The bridge now has a separate QA-only sandbox smoke path. It rechecks official CLI status and requires `signed_in` before confirmed execution. On 2026-07-03 the recheck reported `signed_out`, so no real smoke was attempted and no review was created. ForgeX still does not read OAuth tokens, auth files, or browser URLs, and production routing remains disabled.

## Outcome

ForgeX now exposes a QA-only **Codex CLI OAuth Bridge** for official CLI detection, login guidance, and sanitized auth classification.

The official Codex CLI remains the OAuth client and session owner. ForgeX does not read credential stores, accept tokens, implement an OAuth callback, or call private Codex services.

## Identity and boundary

| Field | Value |
| --- | --- |
| `provider_id` | `codex_cli_oauth_bridge` |
| `provider_kind` | `local_cli` |
| `auth_mode` | `official_codex_cli_oauth` |
| `execution_mode` | `login_helper` |
| `workspace_mode` | `none_for_login` |
| `production_eligible` | `false` |
| `qa_only` | `true` |

Only direct argv processes with `shell=False` are used. Codex probes run with the OS temporary directory as their working directory, never the ForgeX repository or active project.

## Safe probes

- `codex --version`
- `codex login --help`
- `codex login status`, only when the installed CLI advertises it

Raw login-status output is not returned, logged, or persisted. It is reduced in memory to authenticated, unauthenticated, unknown, or failed. API-key and agent-identity modes are not treated as ChatGPT OAuth.

The UI presents `codex login` as a copyable command and can launch it only after an explicit confirmation. The official CLI owns the browser flow. ForgeX does not intercept the browser callback or store its result; login launch is never treated as authentication, and the user must request a separate status check.

The launcher uses direct argv with `shell=False`, discards raw process output, and runs from a unique OS temporary directory rather than the repository or active workspace. Production routing remains disabled.

## Classifications

Detection:

- `CODEX_CLI_DETECTED`
- `CODEX_CLI_NOT_FOUND`
- `CODEX_CLI_VERSION_UNSUPPORTED`
- `CODEX_CLI_DETECTION_FAILED`

OAuth status:

- `CODEX_OAUTH_AUTHENTICATED`
- `CODEX_OAUTH_NOT_AUTHENTICATED`
- `CODEX_OAUTH_STATUS_UNKNOWN`
- `CODEX_OAUTH_STATUS_FAILED`

Standalone smoke readiness is informational only. This phase does not run Codex prompts, edit a workspace, create a diff, apply a patch, build, or flash.

## Verification

- Expanded bridge/provider backend and API tests: 62 passed.
- Backend bridge modules compile successfully.
- Frontend TypeScript check passed.
- ForgeX bridge safety scan passed.
- Standalone detection helper returned only sanitized metadata and classifications.
