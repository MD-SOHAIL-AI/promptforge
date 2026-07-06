# Phase 2.5.8.17B - Codex CLI Session Parity Diagnosis Plan

## Objective

Align ForgeX's sanitized official Codex CLI status with Windows CMD without inspecting CLI-owned session storage. Normal CMD was known to report the signed-in meaning `Logged in using ChatGPT`, while ForgeX previously reported `signed_out`.

## Implementation

- Add `--session-parity` with shared, direct argv, safely resolved launcher, and fixed Windows CMD status-only runners.
- Compare `minimal_safe_env`, `codex_safe_user_env`, and `inherited_minus_secrets_env` without printing names or values from the inherited environment.
- Report only parser flags, classifications, counts, launcher kind, and a one-way launcher-path hash.
- Harden parsing with negative phrases evaluated before signed-in phrases.
- Keep the shared UI, QA, smoke precheck, and provider-registry status authority on one selected runner.
- Keep smoke, prompt execution, production routing, automatic apply, build, and flash outside this phase.

## Safety boundary

Diagnostics may run only version/help/status commands in a neutral temporary directory. They do not launch login, execute prompts, inspect CLI session storage, retain raw process output, or expose executable paths or environment values.
