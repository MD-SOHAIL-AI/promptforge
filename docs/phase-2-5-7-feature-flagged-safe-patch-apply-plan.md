# Phase 2.5.7 Feature-Flagged Safe Patch Apply Plan

Phase 2.5.7 introduces the first real ForgeX bridge patch apply path. It is disabled by default and only available when both development flags are set:

```text
FORGEX_ENABLE_PATCH_APPLY=1
FORGEX_ENABLE_ROLLBACK_RESTORE=1
```

The apply path is intentionally narrow. It applies only exported ForgeX unified patches for approved bridge reviews, requires exact `APPLY` confirmation, reruns patch preflight immediately before mutation, creates a fresh rollback snapshot, stages all target outputs before writing, verifies final hashes, and persists apply metadata under `.promptforge/state/patch-applies`.

Implementation checkpoints:

1. Add patch apply models, result persistence, and audit lifecycle.
2. Add a strict unified diff parser/apply engine for create, modify, and delete only.
3. Add a feature-flagged service that verifies patch integrity, review approval, fresh preflight, rollback snapshot creation, staging, writes, hash verification, and automatic rollback on partial failure.
4. Add the apply API plus apply history/detail routes.
5. Extend bridge safety status with patch apply and rollback restore flag state.
6. Add UI controls that show `Apply disabled` by default and require typed `APPLY` confirmation when enabled.
7. Add tests for flags, confirmation, safety rejects, core operations, metadata, audit, and rollback-on-failure.

Out of scope:

```text
AGY routing
Codex execution
Claude execution
bridge prompt execution outside sandbox
auto-build
auto-flash
token/cookie/session access
```
