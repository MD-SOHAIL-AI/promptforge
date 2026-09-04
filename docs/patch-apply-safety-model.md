# Patch Apply Safety Model

## Purpose

This document defines the future ForgeX patch-apply safety model. It is a design document only. ForgeX must not apply exported patches to the active workspace until a later phase implements and tests the preflight, apply, and rollback system.

## Safety Principles

- Apply is never automatic.
- Apply requires a previously exported patch.
- Apply requires valid patch integrity metadata.
- Apply requires an approved, unexpired review.
- Apply requires path containment checks before any workspace write.
- Apply requires active workspace drift detection.
- Apply requires a dry-run simulation.
- Apply requires a rollback snapshot before writing.
- Apply requires explicit user confirmation after preflight.
- Apply writes must be atomic where possible and verified afterward.

## Future Pipeline

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

## State Gates

### Patch Gate

The patch must:

- exist under ForgeX-managed patch storage
- match its SHA-256 metadata
- belong to a known review
- have changed-file metadata that matches the review
- remain `apply_enabled: false` in the current phase

The final item deliberately blocks real apply now. A later phase must explicitly introduce and test a feature flag before apply can write to the active workspace.

### Review Gate

The review must:

- exist
- be approved
- not be expired
- have the same provider ID as the patch
- have the same changed-file list as the patch metadata

Rejected, pending, expired, missing, or provider-mismatched reviews must block apply.

### Workspace Gate

The active workspace must:

- resolve to a known canonical root
- exist on disk
- be writable
- not be inside forbidden system or credential paths
- match the review workspace hash when a hash is available

If workspace identity cannot be established with sufficient confidence, ForgeX must fail closed.

### Path Gate

Every patch path must be normalized and resolved relative to the active workspace root before simulation or apply.

Reject:

- absolute paths
- drive-qualified paths
- UNC paths
- `../` escapes
- symlink escapes
- writes into `.git`
- writes into `.pio`
- writes into `node_modules`
- writes into `build`, `dist`, `.next`, cache, or generated output folders
- unsupported binary overwrites

Path checks must run against the parsed patch metadata and the final resolved filesystem path.

## Drift Detection

ForgeX must compare each touched target path with the review snapshot.

```text
unchanged -> safe candidate
modified -> conflict
missing -> conflict unless the patch is create-only
new unexpected file -> warning
```

Drift conflicts must be reported before apply. ForgeX must not silently merge or overwrite active workspace changes.

## Dry-Run Simulation

Dry run must produce a complete safety report:

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

The simulation must not mutate the active workspace. If a patch engine cannot guarantee read-only simulation, ForgeX must copy the relevant workspace files to managed temporary storage and simulate there.

## Atomicity Requirement

Future apply should prefer atomic file replacement:

- write modified content to a temporary file in the same directory
- fsync where practical
- replace the target file atomically where the platform supports it
- verify resulting hashes/content expectations

If a multi-file patch cannot be applied atomically as a single transaction, ForgeX must use rollback data to restore previous state after any failure.

## Current Boundary

This model does not enable apply. Existing export/history flows remain inspection-only. Apply buttons and routes must stay disabled until a future implementation phase adds the preflight API, UI, feature flag, tests, and rollback behavior.

## Phase 2.5.8.1 Generic Artifact Boundary

Generic bridge artifacts are references into the existing review and patch pipeline, not a second patch store. Patch and diff references must identify the existing review and pipeline-owned artifact record. Their creation does not approve, preflight, apply, build, flash, restore, or bypass integrity and drift checks.

The generic run store persists artifact metadata only. Patch bytes, file bytes, and raw storage paths remain outside public schemas and under existing managed storage containment.

## Phase 2.5.8.2 AGY Review Reference Boundary

The AGY adapter maps a successful legacy `review_ready` result to a generic review reference by reusing the existing review ID. It does not export or copy patch content and does not call preflight, apply, build, flash, rollback, or restore services.

The existing review record remains authoritative. A generic reference cannot approve a review or bypass expiry, integrity, workspace identity, drift, containment, preflight, confirmation, feature-flag, rollback, or verification gates. Failed, timed-out, interrupted, blocked, and cancelled adapter outcomes create no apply authority.

## Phase 2.5.8.3 Coordinator Artifact Boundary

The coordinator validates and associates provider-returned references before allowing `completed`. It checks run and sandbox ownership, managed-storage containment, type-specific review/pipeline identifiers, and duplicate IDs. It never reads embedded provider content because embedded content is not part of the contract.

Association remains metadata only. The coordinator has no dependency on apply or restore services and cannot approve reviews, change feature flags, or bypass integrity, drift, preflight, confirmation, rollback, or result verification.

## Phase 2.5.8.4 Cutover Review Parity

Generic AGY cutover still creates reviews through the existing runner and `BridgeDiffService`. The compatibility response reuses that review ID and changed-file count. No patch bytes are embedded in generic lifecycle records.

Cutover does not export, approve, apply, build, flash, restore, or roll back automatically. Existing patch integrity, review expiry/approval, drift, containment, preflight, confirmation, feature flags, snapshots, and result verification remain authoritative.

## Phase 2.5.5.1 Read-Only Preflight

ForgeX now implements the first read-only part of this model:

- load trusted patch metadata
- verify patch file existence and SHA-256
- check approved, unexpired review state
- check provider and changed-file metadata consistency
- validate active workspace identity when a root is available
- reject unsafe, escaping, symlink, and ignored paths
- detect target drift from review baseline hashes
- parse supported `diff --git a/<path> b/<path>` headers
- return create/modify/delete lists, conflicts, and warnings
- record preflight audit events

The API does not apply patches, write active workspace files, create rollback data, create apply records, run bridge prompts, or enable AGY/Codex/Claude execution.

`apply_enabled` remains `false` even when preflight returns `can_apply: true`.

## Phase 2.5.5.2 Preflight Presentation

ForgeX now renders the read-only preflight result as a safety report in the UI. The report makes backend safety checks understandable without adding any apply behavior.

The UI presents:

- Safe candidate, Blocked, Warning, Needs active workspace, Patch modified, Approval required, or Unknown
- readable conflict labels
- files to create, modify, and delete
- conflicts and warnings
- explicit `Apply disabled` messaging

Patch History preflight uses only the active ForgeX workspace root. The UI does not persist raw workspace paths and does not ask users to enter arbitrary paths.

This presentation phase does not write active workspace files, create rollback/apply records, execute bridge prompts, or enable AGY/Codex/Claude execution.

## Phase 2.5.5.3 Rollback Snapshot Foundation

ForgeX now implements rollback snapshot creation after a patch passes read-only preflight. Snapshot creation copies current versions of files touched by a future apply into ForgeX app state:

```text
.promptforge/state/patch-rollback/<rollback_id>/
```

The snapshot records relative paths, previous hashes, backup paths, file sizes, mtimes, patch ID, review ID, provider ID, and workspace root hash.

Snapshot creation:

- requires `can_apply=true` from preflight
- keeps `restore_enabled=false`
- does not create apply records
- does not restore files
- does not apply patches
- does not write active workspace files

Apply remains disabled.

## Phase 2.5.5.4 Rollback Restore Preflight

ForgeX now checks whether a rollback snapshot can be safely restored later. Restore preflight verifies snapshot metadata, backup file integrity, workspace identity, path containment, symlink containment, ignored-folder policy, and current workspace drift.

Restore preflight:

- does not restore files
- does not delete files
- does not apply patches
- does not write active workspace files
- always returns `restore_enabled=false`

Restore remains disabled. Apply remains disabled.

## Phase 2.5.5.5 End-to-End Safety QA

ForgeX now has deterministic integration QA for the complete bridge patch safety chain before apply or restore exists:

- review approval
- patch export and integrity verification
- patch preflight
- rollback snapshot creation
- restore preflight
- audit safety
- patch and rollback cleanup boundaries
- Windows path safety
- active workspace no-write assertions

The QA layer does not call AGY, Codex, Claude, PlatformIO, the network, or bridge prompt execution. It uses temporary workspaces and service-level ForgeX components.

Path safety was tightened so Windows drive-qualified paths, drive-relative paths, backslash `..` escapes, and mixed-case ignored folders such as `.PIO` are rejected consistently.

Apply remains disabled. Restore remains disabled.

## Phase 2.5.6 Feature-Flagged Rollback Restore

ForgeX now has the first active-workspace rollback restore write path behind:

```text
FORGEX_ENABLE_ROLLBACK_RESTORE=1
```

Restore remains disabled by default. When enabled for development testing, restore requires exact user confirmation `RESTORE`, reruns restore preflight internally, restores only paths listed in rollback metadata, verifies backup hashes before writing, verifies restored file hashes afterward, and records restore result metadata.

Patch apply is still not implemented. `apply_enabled` remains `false` in restore results and bridge safety status.
# Phase 2.5.7 Safe Apply Addendum

Patch apply is disabled by default and requires both `FORGEX_ENABLE_PATCH_APPLY=1` and `FORGEX_ENABLE_ROLLBACK_RESTORE=1`.

Apply requires exact typed `APPLY` confirmation, a valid exported patch SHA-256, an approved and unexpired review, a fresh passing preflight, and a fresh rollback snapshot created immediately before any workspace write.

The apply engine supports only ForgeX-generated unified text diffs for create, modify, and explicit file delete. It rejects binary patches, renames, chmod/mode-only changes, submodule patches, large unsupported files, unsafe paths, ignored folders, symlink escapes, ambiguous hunks, and context mismatches.

Apply writes only paths listed by preflight and never recursively deletes directories. Create and modify use a temp file plus atomic replace. Final hashes are verified. If a post-mutation write or verification fails, ForgeX attempts automatic rollback from the fresh snapshot and records the result.

Apply metadata and audit events must not include patch content, file content, raw workspace paths, tokens, cookies, sessions, or credentials.
# Phase 2.5.7.1 Apply History And Restore Validation

Apply records are visible through metadata-only history and detail views. The UI displays IDs, status, file counts, relative paths, hashes, and safe messages only.

Rollback snapshots linked to successful applies retain post-apply hashes where applicable. Restore preflight uses those hashes to validate that applied files have not drifted before allowing rollback restore. This preserves the ability to restore real applied patches without accepting unrelated post-apply edits.

# Phase 2.5.7.2 Desktop QA Status

The Phase 2.5.7.2 throwaway workspace is `workspace/forgex-apply-test/`. Desktop QA was attempted with both apply and rollback restore feature flags enabled, but this session could not inspect or control the Electron desktop window. The required human desktop pass remains blocked and is documented in `docs/phase-2-5-7-2-desktop-manual-qa-results.md`.

No safety model changes were made: apply still requires feature flags, exact confirmation, fresh preflight, rollback snapshot creation before writing, safe listed paths only, hash verification, and rollback on post-mutation failure.
# Generic Agent review handoff (Phase 2.5.8.5)

A successful generic run may return only a validated review identifier and safe changed-file counts. Patch and file content do not cross the generic run API. The Agent panel hands the review identifier to the existing bridge Review UI; it does not approve, export, preflight, apply, restore, build, or flash.

The active workspace remains unchanged during Agent execution. Apply continues to require the existing review approval, patch integrity, preflight, rollback snapshot, feature flags, and explicit confirmation. Generic completion creates no automatic transition into Apply and adds no Apply control to the run panel.
