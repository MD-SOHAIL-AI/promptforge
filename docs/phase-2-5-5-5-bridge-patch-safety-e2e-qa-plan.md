# Phase 2.5.5.5 - Bridge Patch Safety E2E QA Plan

## Objective

Prove the complete bridge patch safety chain before any real apply or restore implementation begins.

The chain under test is:

```text
review session
review approval
patch export
patch integrity verification
patch preflight
rollback snapshot creation
restore preflight
audit safety
cleanup safety
no active workspace writes
```

Apply remains disabled. Restore remains disabled.

## Automated QA Matrix

### Happy Path

- create a temporary ForgeX workspace with `README.md`, `platformio.ini`, and `src/main.cpp`
- create a deterministic bridge review without running AGY
- approve the review
- export and verify a patch
- run patch preflight
- create rollback snapshot
- run restore preflight
- assert `apply_enabled=false`
- assert `restore_enabled=false`
- assert active workspace hashes are unchanged
- assert required audit events exist

### Blocked Paths

- unapproved review blocks patch preflight and rollback snapshot
- modified patch blocks integrity and snapshot creation
- active workspace drift blocks preflight and snapshot creation
- missing rollback backup blocks restore preflight
- unsafe and ignored patch paths block preflight and snapshot creation

### Audit Safety

Audit logs must include lifecycle events without storing:

- raw workspace paths
- patch content
- file contents
- tokens
- cookies
- session data
- Google credentials

### Cleanup Safety

- patch cleanup removes old patch files only
- patch cleanup does not remove reviews or rollback snapshots
- rollback cleanup removes old rollback snapshots only
- rollback cleanup does not remove patches
- cleanup does not touch active workspace files

### Windows Path Safety

Reject:

- drive-qualified absolute paths such as `C:\Users\evil.txt`
- drive-relative paths such as `C:Users\evil.txt`
- backslash `..` escapes
- mixed-case ignored folders such as `.PIO\build`
- ignored folders using backslashes

Allow normalized safe relative paths for inspection only. Passing preflight still returns `apply_enabled=false`.

## Implementation Notes

The automated QA uses service-level fixtures and does not call AGY, Codex, Claude, PlatformIO, the network, or any bridge prompt runner.

Temporary test workspaces are created under the pytest temp directory. Active workspace file hashes are captured before and after safety operations. Any unexpected active workspace write fails the test.

## Out Of Scope

- patch apply
- rollback restore
- AGY routing
- Codex execution
- Claude execution
- bridge prompt execution outside the existing sandbox feature
- user credential inspection
