# Phase 2.5.3 Bridge Diff Review Foundation Plan

## Goal

Build the shared review and approval foundation required before any Tool Bridge can execute prompts or modify files.

This phase does not run Codex, Claude Code, AGY, or any bridge prompt. It does not allow bridge file editing.

## Backend Components

- `backend/bridges/review_models.py`
- `backend/bridges/diff_service.py`
- `backend/bridges/audit_log.py`

Core concepts:

- `BridgeWorkspaceSnapshot`
- `BridgeChangedFile`
- `BridgeFileDiff`
- `BridgeReviewSession`
- `BridgeApprovalDecision`
- `BridgeAuditEntry`

## Snapshot Design

Snapshots scan only the active workspace root and record:

- relative path
- SHA-256 hash
- size
- mtime

Small text content is retained in backend memory only so unified diffs can be generated later. It is not returned through the public API.

Ignored folders:

- `.git`
- `.pio`
- `node_modules`
- `dist`
- `build`
- `.next`

## Diff Design

The diff service compares a stored snapshot against the current workspace and detects:

- created files
- modified files
- deleted files

Diff previews are unified text diffs with caps. Binary and large files are marked as unsupported preview.

## Path Containment

Rejected paths include:

- absolute paths
- `../` escapes
- paths outside the workspace root

The service resolves paths against the workspace root and fails closed when containment cannot be proven.

## Approval Lifecycle

APIs:

- `POST /models/bridges/reviews/snapshot`
- `POST /models/bridges/reviews/diff`
- `GET /models/bridges/reviews/{review_id}`
- `POST /models/bridges/reviews/{review_id}/approve`
- `POST /models/bridges/reviews/{review_id}/reject`

Approve/reject only records local review state and audit entries. It does not run bridges and does not apply or revert files.

## Audit Logging

Audit events are stored under app-managed state, not project folders. Entries include provider ID, review ID, workspace hash, changed-file count, approval state, and timestamp.

The audit log must not store raw workspace paths, credentials, tokens, cookies, huge diffs, or raw bridge output.

## UI

Settings -> Models includes Bridge Safety readiness:

- Execution: Disabled
- Review system: Ready
- Diff approval: Required
- Workspace containment: Enabled
- Audit logging: Enabled

A reusable `BridgeReviewPanel` component displays changed files and unified diff previews for future integration.
