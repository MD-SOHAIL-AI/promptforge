# Phase 2.5.5.2 - Patch Preflight UI Polish Plan

## Goal

Make read-only patch preflight results understandable in ForgeX without enabling patch apply.

Users should be able to see whether a patch is a safe candidate for a future apply phase, why it is blocked, which files would be created/modified/deleted, and which warnings need attention.

## Scope

- Add a reusable `PatchPreflightReport` UI component.
- Add frontend status mapping for preflight results.
- Add readable conflict labels.
- Improve Bridge Review preflight result display.
- Improve Patch History preflight result display.
- Use the active ForgeX workspace root only.
- Add output panel messages for preflight start/result.
- Keep Apply disabled.

## Non-Goals

- No patch apply.
- No active workspace writes.
- No rollback/apply records.
- No bridge routing.
- No AGY/Codex/Claude execution changes.
- No token, cookie, session, or credential reads.
- No destructive cleanup.

## UI Status Mapping

Frontend maps preflight results into:

- `Safe candidate`: `can_apply=true` and no conflicts
- `Blocked`: conflicts prevent apply
- `Warning`: warnings exist without blocking conflicts
- `Needs active workspace`: no target workspace root is available
- `Patch modified`: integrity is modified
- `Approval required`: review is not approved
- `Unknown`: incomplete or unexpected result

Even for `Safe candidate`, the UI must show:

```text
Apply is disabled in this build.
```

## Conflict Labels

Conflict types become readable labels:

- `patch_modified`: Patch file was modified after export.
- `patch_missing`: Patch file is missing.
- `review_missing`: Review session was not found.
- `review_not_approved`: Review is not approved yet.
- `review_expired`: Review expired. Create a new sandbox review.
- `provider_mismatch`: Patch provider does not match review provider.
- `path_unsafe`: Patch contains unsafe path.
- `ignored_path`: Patch targets ignored/build folder.
- `workspace_drift`: Workspace changed since review.
- `target_changed`: Target file changed after review.
- `target_missing`: Target file is missing.
- `delete_conflict`: Delete target changed or is unsafe.
- `parse_error`: Patch format could not be parsed.

## Workspace Root Handling

Patch History preflight uses `activeProject.project_path`, the active workspace selected/opened by ForgeX.

The UI must not:

- persist raw workspace paths for preflight
- ask users to type arbitrary paths
- send random user-entered paths

If no active workspace is open, the Preflight action is disabled with:

```text
Open the target workspace before running preflight.
```

## Output Messages

Frontend output messages should be short and content-free:

```text
[preflight] Started patch preflight
[preflight] Patch integrity: valid
[preflight] Result: blocked, 1 conflict
```

They must not include patch content, raw secrets, credentials, tokens, or cookies.

## Manual QA

1. Open ForgeX.
2. Open a workspace.
3. Go to Settings -> Models -> Bridge Safety.
4. Select an exported patch.
5. Click Preflight.
6. Confirm a readable safety report appears.
7. Confirm file create/modify/delete lists are shown.
8. Confirm conflict labels are readable.
9. Confirm output panel preflight messages appear.
10. Confirm Apply remains disabled.
11. Modify a target file.
12. Run Preflight again.
13. Confirm workspace drift or target changed appears.
14. Confirm no files are modified by preflight.

## Verification

Run:

```text
python -m compileall backend
python -m pytest
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend run build
npm.cmd run build:electron
npm.cmd run test:electron
```

There is no dedicated frontend unit test runner configured in this repository, so UI helper behavior is verified through TypeScript, production build, and manual QA.
