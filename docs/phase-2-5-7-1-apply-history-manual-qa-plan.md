# Phase 2.5.7.1 Apply History Manual QA Plan

Phase 2.5.7.1 stabilizes safe patch apply by making applied patch records visible in Settings and by documenting real apply/rollback QA for throwaway workspaces.

Scope:

```text
Patch Apply History UI
Patch Apply Detail UI
Rollback snapshot linkage from apply records
Restore preflight and RESTORE-confirmed restore from apply details
Automated apply-history and apply-restore integration tests
Manual QA script for real desktop validation
```

Non-goals:

```text
AGY routing
Codex execution
Claude execution
bridge prompt execution outside existing AGY sandbox mode
auto-build after apply
auto-flash after apply
binary, rename, chmod, or submodule patch support
weakened path checks
```

Implementation plan:

1. Add `PatchApplyDetail` UI component.
2. Load `GET /models/bridges/patch-applies` into Settings -> Models -> Bridge Safety.
3. Show compact apply stats and recent apply rows.
4. Show apply detail with metadata and file result table.
5. Link apply records to rollback snapshots.
6. Require restore preflight plus exact `RESTORE` confirmation before restore.
7. Add integration tests for apply history/detail and apply-restore e2e.
8. Document manual QA against `forgex-apply-test/`.
9. Run backend, frontend, and Electron verification.

