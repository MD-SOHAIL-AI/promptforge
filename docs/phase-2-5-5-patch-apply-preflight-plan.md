# Phase 2.5.5 - Patch Apply Preflight Plan

## Goal

Design the safe patch-apply system before ForgeX implements any active-workspace apply feature.

This phase is documentation only. It defines the required safety model, preflight API shape, UI flow, rollback plan, audit events, tests, and implementation sequence for a later apply phase.

## Non-Goals

- No patch apply implementation.
- No active workspace mutation.
- No bridge routing changes.
- No AGY routing changes.
- No Codex execution.
- No Claude execution.
- No token, cookie, credential, or session-file reads.
- No Google credential storage.
- No bridge prompts outside the existing sandbox boundary.
- No automatic apply.
- No destructive cleanup.
- No bypass around review approval.

Existing patch export, patch integrity, patch history, cleanup, and review-state behavior remain unchanged. Exported patch records continue to carry `apply_enabled: false` until a later implementation phase explicitly changes that behind a feature flag.

## Future Apply Pipeline

```text
User exports patch
User chooses Apply
ForgeX verifies patch integrity
ForgeX checks review approval state
ForgeX creates pre-apply snapshot
ForgeX checks active workspace drift
ForgeX performs dry-run patch simulation
ForgeX detects conflicts
ForgeX shows exact file changes
User confirms apply
ForgeX applies patch atomically where possible
ForgeX verifies resulting files
ForgeX creates rollback record
ForgeX logs audit event
```

The pipeline is intentionally two-step: preflight first, apply only after a clean report and a separate dangerous-action confirmation.

## Required Preflight Checks

### Patch Integrity

ForgeX must verify:

- patch exists in the managed patch directory
- patch SHA-256 matches the metadata sidecar/index
- patch has not been externally modified
- patch belongs to a known review
- `apply_enabled` is still `false` in the current implementation and must block real apply until a future phase changes the feature gate

### Review State

ForgeX must verify:

- review exists
- review is approved
- review is not expired
- review provider matches patch provider
- review changed-file list matches patch metadata

### Workspace Identity

ForgeX must verify:

- active workspace root is known
- active workspace root hash matches the expected review workspace hash where possible
- workspace is not missing
- workspace is not read-only
- workspace is not inside forbidden paths

### Path Containment

ForgeX must reject:

- absolute paths
- `../` paths
- paths escaping the workspace after canonical resolution
- symlink escapes
- writes into `.git`
- writes into `.pio`
- writes into `node_modules`
- writes into generated/build/cache folders such as `build`, `dist`, and `.next`
- dangerous binary overwrites without explicit future binary support

### Drift Detection

ForgeX must compare active workspace files against the review snapshot and patch metadata.

```text
unchanged -> safe candidate
modified -> conflict
missing -> conflict or create-only case
new unexpected file -> warning
```

Drift detection must run before dry-run simulation so the user can distinguish changed local state from patch syntax or apply conflicts.

### Dry-Run Patch Simulation

ForgeX must simulate apply before any real write. A dry-run response should return:

```json
{
  "can_apply": true,
  "conflicts": [],
  "files_to_create": [],
  "files_to_modify": [],
  "files_to_delete": [],
  "warnings": []
}
```

The dry run must not mutate files. If the underlying patch library cannot guarantee no writes, ForgeX must perform the simulation against a temporary workspace copy, not the active workspace.

## Conflict Model

Future preflight and apply APIs should use these conflict statuses:

- `clean`: no conflict for the file
- `workspace_drift`: workspace identity or snapshot no longer matches
- `patch_modified`: patch hash does not match recorded metadata
- `path_unsafe`: path fails containment policy
- `target_missing`: patch expects an existing file that is missing
- `target_changed`: target file changed after review was created
- `delete_conflict`: delete target changed, is missing unexpectedly, or requires stronger confirmation
- `binary_unsupported`: binary change cannot be safely simulated or applied
- `large_file_unsupported`: file exceeds supported preflight/apply size limits
- `unknown`: unexpected failure that must block apply

Example:

```json
{
  "can_apply": false,
  "conflicts": [
    {
      "path": "src/main.cpp",
      "type": "target_changed",
      "message": "File changed after review was created."
    }
  ]
}
```

Any conflict other than `clean` must block apply until a later phase defines an explicit user-resolvable workflow.

## Rollback Requirement

Before applying in a future phase, ForgeX must create rollback data under:

```text
.promptforge/state/patch-rollback/<apply_id>/
```

Rollback data must be created after preflight succeeds and before active workspace writes begin. If rollback snapshot creation fails, apply must not start.

Rollback metadata should follow:

```json
{
  "apply_id": "...",
  "patch_id": "...",
  "review_id": "...",
  "created_at": "...",
  "workspace_root_hash": "...",
  "files_backup": [
    {
      "path": "src/main.cpp",
      "previous_hash": "...",
      "backup_path": "..."
    }
  ]
}
```

Rollback rules:

- do not store secrets unnecessarily
- use relative paths only
- do not include ignored folders unless touched by the patch
- support restoring modified files
- support restoring deleted files
- support removing newly created files
- fail safely if the workspace changed again after apply

## Future API Plan

Planned routes:

```text
POST /models/bridges/patches/{patch_id}/preflight
POST /models/bridges/patches/{patch_id}/apply
POST /models/bridges/patches/{patch_id}/rollback
GET /models/bridges/patch-applies/{apply_id}
GET /models/bridges/patch-applies
```

Preflight response:

```json
{
  "patch_id": "...",
  "can_apply": false,
  "integrity_status": "valid",
  "review_status": "approved",
  "conflicts": [],
  "warnings": [],
  "files_to_create": [],
  "files_to_modify": [],
  "files_to_delete": []
}
```

Apply response:

```json
{
  "apply_id": "...",
  "status": "applied",
  "files_changed": 3,
  "rollback_available": true
}
```

Rollback response:

```json
{
  "apply_id": "...",
  "status": "rolled_back",
  "files_restored": 3
}
```

The apply route must remain unavailable or return a disabled/feature-gated response until the later safe-apply implementation phase.

## Future UI Plan

Future `BridgeReviewPanel` and Patch History actions:

```text
Preflight Check
Apply Patch
Rollback
```

Current implementation must keep apply controls disabled.

Future UX flow:

```text
1. User clicks Preflight.
2. ForgeX shows safety report.
3. If clean, Apply button becomes available.
4. User confirms dangerous action.
5. ForgeX applies patch.
6. ForgeX shows changed files.
7. Rollback button appears.
```

Required warning text:

```text
Applying a patch will modify your active workspace.
ForgeX will create a rollback snapshot before applying.
Review all changes carefully.
```

## Future Audit Events

ForgeX should record:

- `patch_preflight_started`
- `patch_preflight_completed`
- `patch_preflight_failed`
- `patch_apply_requested`
- `patch_apply_confirmed`
- `patch_apply_started`
- `patch_apply_completed`
- `patch_apply_failed`
- `patch_rollback_requested`
- `patch_rollback_completed`
- `patch_rollback_failed`

Audit metadata may include patch ID, review ID, provider ID, integrity status, conflict count, changed-file count, workspace hash, apply ID, and rollback availability.

Audit must never store:

- full patch content
- tokens
- cookies
- raw secrets
- full workspace paths

## Future Test Plan

### Preflight

1. valid patch passes preflight
2. modified patch fails integrity
3. missing patch fails
4. expired review fails
5. unapproved review fails
6. unsafe path fails
7. workspace drift fails
8. ignored folder write fails

### Apply

1. clean patch applies
2. active workspace snapshot created
3. created files are created
4. modified files are modified
5. deleted files are deleted only after explicit confirmation
6. partial apply rolls back or fails safely

### Rollback

1. rollback restores modified files
2. rollback restores deleted files
3. rollback removes newly created files
4. rollback fails safely if current workspace changed again

## Future Implementation Sequence

```text
Phase 2.5.5.1 - Patch Preflight API
Phase 2.5.5.2 - Patch Preflight UI
Phase 2.5.5.3 - Safe Apply Implementation Behind Feature Flag
Phase 2.5.5.4 - Rollback Implementation
Phase 2.5.5.5 - AGY Sandbox -> Review -> Apply End-to-End QA
```

## Acceptance Criteria

- Patch apply safety model is documented.
- Preflight checks are documented.
- Conflict model is documented.
- Rollback plan is documented.
- Future API design is documented.
- Future UI plan is documented.
- Future audit plan is documented.
- Future test plan is documented.
- No apply implementation is added.
- Active workspace apply remains disabled.
- Existing patch export and history behavior remains unchanged.
