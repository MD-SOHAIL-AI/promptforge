# Phase 2.5.8.17A - Codex OAuth Status Environment Alignment Plan

## Objective

Make the UI status route, QA command, smoke precheck, and provider registry consume one shared status service. The service invokes only official Codex CLI version, login help, and login status commands with direct argv, `shell:false`, a neutral system-temp working directory, and an allowlisted Windows user environment.

## Safety boundary

The official Codex CLI owns authentication. ForgeX preserves normal Windows user variables needed by user tools, including `USERPROFILE`, `APPDATA`, and `LOCALAPPDATA`, but does not locate or inspect session storage. Secret-like environment names are excluded. Raw command output is parsed in memory only and is never persisted or returned by diagnostics.

Diagnostics and parity are status-only operations. They cannot launch login, execute a prompt, run a smoke, route Codex in production, or trigger apply, build, or flash.

## Verification

Use mocked Codex responses for automated tests. Run sanitized diagnostics, parity, and status before the backend, frontend, Electron, and safety suites. A real smoke is permitted only by the separate explicitly confirmed command and only if the final shared status is `signed_in`.
