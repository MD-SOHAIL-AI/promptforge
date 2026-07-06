# Phase 2.5.8.9 ForgeX-Owned Tool Runtime and API-Backed Provider Design Plan

## Objective

Replace local CLI write-provider routing with a ForgeX-owned execution boundary. Providers may propose structured tool calls; ForgeX alone validates and executes filesystem operations in a managed sandbox.

## Work plan

1. Preserve AGY and Codex as paused, non-routeable evidence records; keep Claude disabled and OpenCode reference-only.
2. Add strict tool-call, tool-plan, runtime-classification, and sanitized-event contracts.
3. Add sandbox-only path, content, extension, size, credential-read, symlink, reparse, and permission policy checks.
4. Implement `list_files`, `read_file`, `write_file`, and `edit_file_simple`. Keep `create_review` as an orchestration-owned operation after diff validation.
5. Add a fake in-process provider returning one exact `write_file` call.
6. Execute the plan against a disposable copied sandbox; compare active-workspace and marker baselines; require exactly one expected diff.
7. Create an existing Bridge Review candidate with no apply, build, or flash authority.
8. Keep patch export, integrity verification, and preflight services unchanged and callable only after a review exists.
9. Add focused tests, a guarded npm smoke command, safety-scan checks, and architecture/results documentation.

Real provider adapters, API keys, networking, shell execution, dependency installation, direct apply, build, and flash are out of scope.
