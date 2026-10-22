# Phase 2.5.5.5 - Bridge Patch Safety E2E QA

## Summary

Phase 2.5.5.5 adds end-to-end QA coverage for the bridge patch safety chain. It does not add patch apply or real rollback restore.

Automated tests now prove:

- approved review to exported patch to preflight to rollback snapshot to restore preflight
- `apply_enabled=false` throughout patch preflight
- `restore_enabled=false` throughout restore preflight
- active workspace files remain unchanged by safety operations
- blocked paths fail closed
- audit logs avoid raw workspace paths and patch/file contents
- patch cleanup and rollback cleanup stay in their own storage areas
- Windows-style unsafe paths are rejected

## Automated Tests

The QA layer lives in:

```text
tests/integration/bridge_patch_safety_helpers.py
tests/integration/test_bridge_patch_safety_flow.py
tests/integration/test_bridge_patch_safety_blocked_paths.py
tests/integration/test_bridge_patch_audit_safety.py
tests/integration/test_bridge_patch_cleanup_boundaries.py
tests/integration/test_bridge_patch_windows_paths.py
```

The tests use temporary workspaces and service-level ForgeX bridge components. They do not require AGY, internet access, PlatformIO builds, Codex, Claude, or real bridge execution.

## Happy Path

The happy-path test creates a small workspace:

```text
workspace/
  README.md
  platformio.ini
  src/main.cpp
```

It then creates and approves a bridge review, exports and verifies a patch, runs patch preflight, creates a rollback snapshot, and runs restore preflight.

Assertions:

- patch preflight returns `can_apply=true`
- patch preflight returns `apply_enabled=false`
- rollback snapshot is created under managed app state
- restore preflight returns `can_restore=true`
- restore preflight returns `restore_enabled=false`
- active workspace hashes do not change

## Blocked Paths

The blocked-path QA covers:

- review not approved
- patch modified after export
- active workspace drift after export
- missing rollback backup file
- unsafe `../` patch path
- ignored `.git`, `.pio`, and `node_modules` patch paths

These cases fail before apply or restore. Rollback snapshot creation requires a passing patch preflight.

## Audit Safety

The audit safety test verifies lifecycle events:

```text
review_created
review_approved
bridge_patch_exported
bridge_patch_verified
patch_preflight_started
patch_preflight_completed
rollback_snapshot_requested
rollback_snapshot_created
rollback_restore_preflight_started
rollback_restore_preflight_completed
```

The serialized audit log must not contain raw workspace paths, patch content, file contents, tokens, cookies, session data, or Google credential text.

## Cleanup Safety

Cleanup tests verify:

- patch cleanup removes old patch files and patch metadata only
- patch cleanup does not remove review sessions
- patch cleanup does not remove rollback snapshots
- rollback cleanup removes old rollback snapshots only
- rollback cleanup does not remove exported patches
- cleanup does not modify the active workspace

## Windows Path Safety

The path QA verifies rejection for:

```text
C:\Users\evil.txt
C:Users\evil.txt
..\evil.txt
..\..\secret.txt
.git\config
.PIO\build\firmware.bin
node_modules\pkg\index.js
dist\bundle.js
build\firmware.bin
.next\cache\entry
.forgex\state.json
```

The shared path safety helper now treats Windows drive forms as unsafe and treats ignored folder names case-insensitively.

## Manual QA

Manual UI QA remains recommended:

1. Start ForgeX with `FORGEX_ENABLE_AGY_BRIDGE=1`.
2. Open a small test workspace.
3. Run an AGY sandbox test.
4. Confirm a sandbox review is created.
5. Approve the review.
6. Export the patch.
7. Verify patch integrity.
8. Run patch preflight.
9. Create rollback snapshot.
10. Run restore preflight.
11. Confirm Apply is disabled.
12. Confirm Restore is disabled.
13. Confirm active workspace files are unchanged.
14. Confirm patch history still works.
15. Confirm rollback snapshot list still works.
16. Confirm output logs are readable.
17. Confirm audit logs do not include raw workspace paths or patch content.

## Remaining Boundary

This phase does not implement:

- patch apply
- rollback restore
- apply records
- restore records
- bridge routing
- AGY/Codex/Claude execution changes

Future apply/restore phases must keep these QA tests passing and add new tests for explicit confirmation, transactional writes, restore execution, and failure rollback.
