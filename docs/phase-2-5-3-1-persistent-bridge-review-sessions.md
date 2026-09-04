# Phase 2.5.3.1 Persistent Bridge Review Sessions

## Summary

Bridge review state now survives backend restarts. ForgeX persists snapshot metadata, review sessions, review decisions, and audit entries while keeping bridge execution disabled.

## Persistence Approach

Persistence is handled by `BridgeReviewStore`, a JSONL-backed local store under ForgeX managed app state:

```text
.promptforge/state/bridge-snapshots.jsonl
.promptforge/state/bridge-reviews.jsonl
```

The store loads snapshots and reviews when `BridgeDiffService` starts.

## Snapshot Persistence

Snapshot records store metadata only:

- snapshot ID
- workspace root hash
- file hash
- file size
- mtime

Raw workspace paths and full file contents are not stored in snapshot records.

## Review Persistence

Review records store:

- review ID
- provider ID
- workspace root hash
- status
- created and expiration timestamps
- changed file metadata
- capped diff previews
- approval decision

Approved and rejected reviews remain queryable after restart.

## Expiration

Pending reviews expire after 24 hours. Expired reviews cannot be approved or rejected.

Cleanup removes expired review records only:

```text
POST /models/bridges/reviews/cleanup
```

## Audit Logging

Audit entries are appended for:

- `snapshot_created`
- `diff_generated`
- `review_created`
- `review_approved`
- `review_rejected`
- `review_expired`

Audit entries use workspace hashes, not raw workspace paths.

## UI

Settings -> Models -> Bridge Safety now shows pending review count, expired review count, audit logging enabled, and persistence enabled.

## Execution Boundary

Bridge execution remains disabled. This phase does not run Codex, Claude Code, or AGY, and does not allow bridge tools to edit files.
