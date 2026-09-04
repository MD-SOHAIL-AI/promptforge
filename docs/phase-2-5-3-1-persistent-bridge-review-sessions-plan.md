# Phase 2.5.3.1 Persistent Bridge Review Sessions Plan

## Objective

Persist bridge review snapshots and sessions before any bridge execution is enabled.

The persisted chain is:

```text
snapshot created
review session created
diff generated
approve or reject decision recorded
audit log appended
state survives backend restart
```

## Storage

ForgeX stores bridge review state in managed app state:

```text
.promptforge/state/bridge-snapshots.jsonl
.promptforge/state/bridge-reviews.jsonl
.promptforge/state/bridge-review-audit.jsonl
```

Snapshots persist metadata only:

- snapshot ID
- workspace root hash
- file hash
- file size
- file mtime

Snapshots do not persist full file content.

Review sessions persist capped review results:

- review ID
- provider ID
- workspace root hash
- status
- expiration time
- changed file metadata
- capped diff previews
- approval decision

## Expiration

Pending reviews expire after 24 hours. Expired reviews cannot be approved.

Cleanup removes expired review records only.

## API Updates

- `GET /models/bridges/reviews`
- `GET /models/bridges/reviews?status=pending`
- `GET /models/bridges/reviews?provider_id=codex_bridge`
- `POST /models/bridges/reviews/cleanup`

Existing snapshot, diff, detail, approve, and reject routes continue to work.

## Non-Goals

This phase does not add bridge prompt execution, bridge routing, bridge file editing, token storage, session parsing, or cloud sync.
