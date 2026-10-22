# Phase 2.5.8.13 — AGY Scratch Project Import Provider Results

## Implementation result

ForgeX now has a disabled-by-default `agy_scratch_import` manual-artifact provider. It does not launch AGY or any other model provider. One exact user-selected folder is validated and copied to a marked ForgeX-managed import sandbox; an exact safe diff produces a persistent Bridge Review while the active workspace remains unchanged.

Implemented surfaces:

- `POST /agent-runtime/agy-scratch-import`
- `npm.cmd run qa:agy-scratch-project-import -- --source "<exact-folder>"`
- Agent Runtime panel section labeled **AGY Scratch Import**
- Persistent review metadata with source folder name and relative file tree only

## Safety result

- No scratch-root discovery, newest-folder selection, or provider-output link trust exists.
- Source containment, root rejection, brain/sensitive-root blocking, and link/reparse checks fail closed.
- Limits are 200 files, 5 MiB total, 512 KiB per file, and depth eight.
- Secret patterns, generated/dependency directories, unsupported types, and binary content are rejected.
- Validated bytes are copied only beneath `.promptforge/agy-import-sandboxes`.
- Review creation occurs only after exact diff and active-workspace validation.
- Apply, build, and flash remain manual and were not invoked.

## Verification

The explicitly selected `esp32_blink` folder beneath the AGY scratch root was imported successfully. ForgeX enumerated only that selected project tree. It did not list the scratch root or choose a folder automatically.

| Field | Result |
| --- | --- |
| AGY executed by ForgeX | no |
| Import source type | user-selected exact AGY scratch folder |
| Source inside scratch root | yes |
| Full scratch scan | no |
| Newest-folder selection | no |
| Source folder name | `esp32_blink` |
| File count | 8 |
| Total bytes | 10,614 |
| Blocked files | 0 |
| Classification | `AGY_SCRATCH_IMPORT_PASS` |
| Managed sandbox created | yes |
| Created / modified / deleted | 8 / 0 / 0 |
| Review created | yes |
| Review ID | `bridge-review-997d01c92cc94b44b3c93c76846c8c2c` |
| Active workspace unchanged | yes |
| Auto-apply / build / flash | no / no / no |
| Full source path persisted | no |
| UI integration | yes |

The persisted provider state now reports `review_eligible=true`, `production_eligible=false`, `routeable=false`, and `qa_only=false`. Review eligibility reflects the completed safe import; it does not grant planner routing or automatic apply authority.

## Test results

| Verification | Result |
| --- | --- |
| Import service and API tests | 47 passed, 2 link-permission skips |
| Required focused selector | 62 passed, 2 skipped, 1,748 deselected |
| Backend compileall | passed |
| Full backend | 1,800 passed, 12 skipped |
| Safety scan | 217 passed |
| Frontend typecheck | passed |
| Frontend production build | passed; 4 static pages |
| Electron build | passed |
| Electron tests | 10 passed |
| Exact-path QA import | `AGY_SCRATCH_IMPORT_PASS` |

No AGY, Codex, Claude, OpenCode, or API provider was executed. The recommended next phase is **Phase 2.5.8.14 — AGY Scratch Import Review Hardening**.

## Phase 2.5.8.14 preservation note

The exact-source manual import remains the fallback and continues to use the same validation and review service. Assisted AGY generation does not replace or weaken it; a missing expected assisted folder creates no review and directs the user back to this explicit-path flow.

The Phase 2.5.8.14 real assisted comparison ultimately passed with three expected PlatformIO files and a persistent `agy_scratch_runner` review. The manual `esp32_blink` regression also passed again with eight files and an unchanged active workspace.
