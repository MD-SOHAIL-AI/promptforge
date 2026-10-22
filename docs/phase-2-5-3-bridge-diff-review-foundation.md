# Phase 2.5.3 Bridge Diff Review Foundation

## Summary

ForgeX now has the safety foundation needed before bridge execution can be considered. It can snapshot a workspace, detect local file changes, generate capped diff previews, create review sessions, record approve/reject decisions, and append local audit entries.

Bridge execution remains disabled.

## Snapshot Behavior

Snapshots are limited to the requested workspace root. Heavy generated folders are ignored:

```text
.git
.pio
node_modules
dist
build
.next
```

Snapshot API responses expose hashes, size, and mtime only. Text content used for diff generation remains backend-local and in memory.

## Diff Behavior

The diff service detects:

- created files
- modified files
- deleted files

Unified diff previews are capped. Binary or large files are marked as unsupported preview instead of being dumped into the UI or audit log.

## Review API Behavior

Review routes:

- `POST /models/bridges/reviews/snapshot`
- `POST /models/bridges/reviews/diff`
- `GET /models/bridges/reviews`
- `GET /models/bridges/reviews/{review_id}`
- `POST /models/bridges/reviews/{review_id}/approve`
- `POST /models/bridges/reviews/{review_id}/reject`
- `POST /models/bridges/reviews/cleanup`

Approve/reject records decisions only. No bridge command runs, no file change is applied, and no bridge-generated patch is accepted automatically.

Phase 2.5.3.1 persists snapshot metadata and review sessions in ForgeX managed app state so review status survives backend restarts.

## Audit Log Behavior

Audit logs are stored in ForgeX-managed app state:

```text
.promptforge/state/bridge-review-audit.jsonl
```

Audit entries store a workspace hash rather than the raw workspace path. They do not include tokens, cookies, API keys, full diffs, or raw bridge output.

## UI

Settings -> Models now shows Bridge Safety readiness. A reusable `BridgeReviewPanel` component is available for future bridge execution UI integration.

## Execution Boundary

This phase does not implement:

- Codex prompt execution
- Claude Code prompt execution
- AGY prompt execution
- bridge file editing
- bridge routing
- diff apply/revert

Future bridge execution must go through this review and approval layer plus explicit workspace guards and user consent.
