# Phase 2.5.8.7 Codex Headless Provider Investigation Plan

## Goal

Determine whether the locally installed Codex CLI can run non-interactively in a ForgeX-managed disposable sandbox, create only `CODEX_GENERIC_SMOKE.txt`, and supply the existing review/patch/preflight pipeline without changing the active workspace.

## Architecture under test

```text
ForgeX UI
↓
Generic Bridge API
↓
Provider adapter
↓
Managed disposable sandbox
↓
Provider execution
↓
Diff capture
↓
Review artifact
↓
Patch export / verify / preflight
↓
No auto-apply
```

The native write gate precedes adapter work. A generic Codex adapter, generic live QA, review creation, and patch processing are prohibited unless the native classification is `CODEX_NATIVE_WRITE_PASS`.

## Investigation sequence

1. Read the generic architecture, bridge security model, and latest AGY results without editing them.
2. Run bounded local-only Codex detection and help/version probes from a ForgeX-managed probe directory.
3. Record only the bounded version, help availability, supported invocation classifications, and safe invocation summary.
4. Create one unique child under `.promptforge/codex-sandboxes`, containing only the required marker.
5. Require `--confirm-real-codex`, revalidate containment and link safety, snapshot relative hashes, and run direct argv with `shell:false`.
6. Classify the in-memory process result and exact filesystem delta. Persist or print no raw prompt, stdout, stderr, executable path, credential path, token, or account identity.
7. Continue to a feature-gated adapter and read-only review/patch pipeline only after an exact one-file pass.
8. Run fake-Codex tests, the safety scan, focused/full backend tests, frontend checks, and Electron checks.

## Fixed execution policy

The locally documented mode is `codex exec`. The guarded argv uses Codex `workspace-write`, top-level approval policy `never`, `--ephemeral`, `--ignore-user-config`, and `--skip-git-repo-check`. It never uses full-access sandboxing, additional writable directories, web search, output files, JSON transcript persistence, hook-trust bypass, or approval/sandbox bypass.

The managed sandbox must be a direct run child, must not overlap the repository or active workspace, must not equal the filesystem root, home, Desktop, or OneDrive root, and must contain no link/reparse escape. The prompt and process output remain memory-only.

## Stop conditions

Any missing confirmation, invalid version, unsupported invocation, authentication/permission denial, timeout, invalid content, extra change, marker mutation, or active-workspace mutation fails closed. Without a native pass, no adapter, review, patch export, patch verify, preflight, apply, build, or flash action is permitted.
