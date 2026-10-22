# Phase 2.5.7.1 Apply History Manual QA

ForgeX now exposes patch apply records in Settings -> Models -> Bridge Safety.

The Patch Apply History section shows:

```text
Total applies
Applied
Failed
Failed rolled back
Rollback available
```

Recent apply rows include Details, Open rollback snapshot, Restore Preflight, and Restore Snapshot actions. Restore Snapshot remains disabled until rollback restore is enabled and restore preflight passes. Restore still requires typed `RESTORE` confirmation.

The apply detail panel shows apply metadata and a file result table with relative paths, operations, statuses, hashes, and messages. It does not display raw workspace paths, patch content, or file content.

Automated validation now covers apply history, apply detail file results, rollback snapshot linkage, restore of applied modify/create/delete patches, unrelated file preservation, and audit safety.

Manual desktop QA is documented in `docs/phase-2-5-7-1-real-apply-rollback-manual-qa.md`.

