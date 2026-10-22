# Phase 2.3.1 - Native IDE Dialog Stabilization

## Root Cause

ForgeX renderer code still used browser-native dialogs for IDE operations. In the desktop renderer, `prompt()` is not supported, so file and folder creation could crash with:

```text
Runtime Error
prompt() is not supported.
```

The confirmed crash path was:

```text
use-promptforge-workspace.createEntry()
file explorer create action
browser-native input dialog
```

## Dialogs Added

Reusable ForgeX IDE dialogs were added under `frontend/components/ide/dialogs/`:

- `forgex-dialog-provider.tsx`
- `input-dialog.tsx`
- `confirm-dialog.tsx`
- `message-dialog.tsx`

The provider exposes:

- `dialogs.input(...)`
- `dialogs.confirmAction(...)`
- `dialogs.message(...)`

Dialogs are theme-aware, centered, keyboard accessible, close on Escape, and show inline validation errors.

## Files Changed

Created:

- `frontend/components/ide/dialogs/forgex-dialog-provider.tsx`
- `frontend/components/ide/dialogs/input-dialog.tsx`
- `frontend/components/ide/dialogs/confirm-dialog.tsx`
- `frontend/components/ide/dialogs/message-dialog.tsx`
- `frontend/scripts/check-native-dialogs.mjs`
- `docs/phase-2-3-1-native-dialog-stabilization.md`

Modified:

- `frontend/app/page.tsx`
- `frontend/hooks/use-promptforge-workspace.ts`
- `frontend/components/ide/forgex-shell.tsx`
- `frontend/components/ide/model-settings-panel.tsx`
- `frontend/components/settings/security-settings.tsx`

## Browser-Native Dialogs Removed

Removed browser-native dialog usage from renderer source for:

- File creation
- Folder creation
- Rename
- Delete project entry
- Delete project
- Flash firmware confirmation
- Clear API key confirmation
- Reset security/settings confirmation
- Unsupported Open Folder/Open Project fallback messages

Source checks for frontend TypeScript and TSX files now pass with no matches for native prompt, confirm, or alert calls.

## File And Folder Behavior

Create file now opens a ForgeX input dialog, validates the requested path, calls the existing create API, refreshes the explorer, and opens the created file.

Create folder now opens the same ForgeX input dialog pattern, validates the folder path, calls the existing create API, and refreshes the explorer.

Validation rejects:

- Empty paths
- Parent directory segments
- Current directory segments
- Absolute POSIX paths
- Windows drive-root paths

Rename now uses a ForgeX input dialog and calls the existing update API. Operation failures are shown through a ForgeX message dialog.

Delete now uses a ForgeX danger confirmation dialog before calling the existing delete API.

## Flash Behavior

Flash now requires a ForgeX hardware confirmation dialog before the flash API is called. The dialog includes:

- Project
- Board
- Port
- Firmware/build artifact when available
- A hardware modification warning

If no board is selected or detected, ForgeX shows a message dialog instead of silently doing nothing.

## Settings Behavior

Settings confirmations were moved to ForgeX confirm dialogs for:

- Clear API key
- Reset settings/security preferences

No raw API keys are exposed by the dialog changes.

## Verification Results

Automated checks run:

```text
python -m compileall backend
python -m pytest
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm run build:electron
node frontend\scripts\check-native-dialogs.mjs
```

Results:

- Backend compile passed.
- Backend tests passed: 1131 passed, 3 skipped.
- Frontend typecheck passed.
- Frontend production build passed.
- Electron build passed.
- Native dialog source check passed.
- `npm run dev:desktop` started successfully and Electron reached `Renderer did-finish-load`.

Manual UI checks:

- Create file dialog opened in ForgeX.
- Empty/unsafe path validation displayed inline.
- `../bad.cpp` was rejected.
- `src/test.cpp` was created and appeared in Explorer.
- Create folder dialog opened in ForgeX.
- `include` folder appeared in Explorer.
- Flash with no detected device showed a ForgeX message dialog.
- No `prompt() is not supported` crash occurred.

Screenshots:

- `docs/screenshots/phase-2-3-1/create-file-validation-fixed.png`
- `docs/screenshots/phase-2-3-1/created-file-folder-fixed.png`

## Remaining Limitations

The hardware flash confirmation modal could not be fully exercised against a real connected board in this environment because no serial board was detected. The no-device path was verified visually, and the board-present code path now uses the ForgeX hardware confirmation dialog before invoking flash.

The broader worktree contains preexisting and generated Phase 2 artifacts unrelated to this stabilization phase; they were not reverted.
