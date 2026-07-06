# Phase 2.5.8.17 — Codex OAuth Sandboxed Smoke and Review Plan

## Scope

ForgeX asks the official Codex CLI for login status, requires `signed_in`, then permits one explicitly confirmed `codex exec` in a disposable direct child of `C:\forgex-codex-oauth-smoke`.

The direct argv is `["--ask-for-approval", "never", "exec", "--sandbox", "workspace-write", "--cd", sandboxPath, prompt]`. The runtime-only prompt requests exactly `CODEX_OAUTH_BRIDGE_SMOKE.txt`. ForgeX validates the exact content and one-created-file diff, verifies the marker and active workspace, then creates a persistent Bridge Review only for `CODEX_OAUTH_SMOKE_PASS`.

## Security boundary

- The official CLI owns OAuth and its browser flow.
- ForgeX does not read auth files, tokens, cookies, callback URLs, or account identity.
- Raw prompt, stdout, and stderr are not persisted or exposed by the API.
- Repository, active workspace, home, Desktop, OneDrive roots, filesystem root, symlinks, and reparse escapes are rejected.
- Production routing, apply, build, and flash remain disabled.
- AGY, Claude, OpenCode, and API providers are outside this command.

Automated tests use mocked Codex. Real execution is permitted only through `npm.cmd run qa:codex-oauth-bridge -- --standalone-smoke --confirm-real-codex`. No automatic retry is implemented; any future second run must require `--allow-second-codex-oauth-smoke`.
