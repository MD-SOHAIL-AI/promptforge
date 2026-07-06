# Phase 2.5.8.12C — Codex CLI Subscription Bridge Integration Plan

## Objective

Validate Codex as an experimental, QA-only CLI-auth bridge. Codex owns official CLI authentication and model execution. ForgeX owns creation of an external disposable sandbox, hash-based diff capture, exact-output validation, persistent review creation, and any later user-approved patch workflow.

## Safety boundary

- Use direct argv with `shell: false` and the exact shape `--ask-for-approval never exec --sandbox workspace-write --cd <sandbox> <prompt>`.
- Create only direct children of `C:\forgex-codex-sandboxes` with a ForgeX marker and no link/reparse escape.
- Never run in the repository, active workspace, home, Desktop, OneDrive root, or filesystem root.
- Delegate authentication to the official Codex CLI. ForgeX does not inspect, copy, display, refresh, or persist authentication material.
- Keep the runtime instruction and process output memory-only. Persist only classification, expected-file metadata, counts, integrity booleans, and a review after an exact pass.
- Never auto-apply, build, or flash. Keep product routing disabled.
- Permit one real `codex exec` for the gated retry. A comparison attempt requires a separate future invocation and explicit `--allow-second-codex-retry` authority.

## Validation sequence

1. Verify both `--confirm-real-codex` and `--subscription-bridge-retry`.
2. Verify this phase document attests the manual standalone pass.
3. Resolve the official CLI and create the external marked sandbox.
4. Detect the bounded printable CLI version.
5. Capture active-workspace and sandbox baselines.
6. Execute one safe Codex request with the working argv order.
7. Classify quota, usage, auth, permission, invocation, timeout, no-change, extra-change, content, and exact-pass outcomes separately.
8. Create a persistent Bridge Review only for the exact one-file pass.
9. Run focused, backend, safety, frontend, and Electron verification.

Production eligibility and product routing remain false regardless of the QA result.
