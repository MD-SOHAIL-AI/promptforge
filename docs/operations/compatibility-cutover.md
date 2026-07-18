# Compatibility Cutover Operations and Rollback Plan

## Inventory and dry run

1. Back up domain-state.db with DatabaseMaintenance and verify quick_check.
2. Run the caller audit and archive docs/releases/compatibility-cutover-audit.json with the release evidence.
3. Run LegacyWorkflowMigrator in dry-run mode against coding-workflow-runs.jsonl and coding-workflow-events.jsonl.
4. Compare discovered run/event counts, terminal states, review references, profile bindings, and event sequences.
5. Do not alter the JSONL files during dry run.

## Migration

Run the migrator with the exact published fallback profile version selected for legacy runs. Migration is transactional per run and idempotent. It records the migration source, preserves safe summaries and event ordering, binds the exact profile version, imports review references as hashed artifacts, and marks migrated outbox rows published to avoid duplicate historical fan-out.

After migration, replay every migrated run from SQLite and compare its projected terminal state, event count, review reference, and safe summary with the JSONL source. Keep JSONL read-only during a full release cycle.

## Removal gates

Remove a compatibility caller only when:

- caller inventory is zero outside migration/audit tooling;
- source data is migrated and verified;
- unified UI/runtime parity covers success, repair, every waiting state, cancellation, timeout, restart, approval expiry, device mismatch, and hardware disconnection;
- the full backend and frontend regression suites pass;
- the rollback snapshot and prior release artifact are available.

## Rollback

If a cutover regression occurs:

1. Stop new workflow command acceptance.
2. Preserve the current SQLite database and WAL/SHM files for investigation.
3. Restore the verified pre-cutover SQLite backup.
4. Deploy the prior application release.
5. Re-enable the compatibility reader only; never merge two writable authorities.
6. Replay the outbox from the restored sequence and verify client resynchronization.
7. Record affected idempotency keys and do not repeat external apply/flash commands without fresh approval.

JSONL sources are never deleted by the migration tool. Deletion requires a separate release after the rollback window closes.
