# Phase 1.6 External Project Import Plan

## Current Limitation

ForgeX can open generated, backend-managed projects and can match a selected native folder only when that folder is already known to the managed project service. Selecting an arbitrary embedded project folder from the file manager does not inspect or register that folder, so PlatformIO projects outside the ForgeX workspace cannot be opened in the explorer or used with existing build, flash, and monitor workflows.

## Proposed Backend Import Flow

Add `POST /projects/import` to accept a filesystem path from Electron/native folder selection.

The route will:

1. Resolve and validate the selected directory.
2. Reject unsafe choices such as missing paths, files, system roots, and user home root folders.
3. Require `platformio.ini` at the selected root.
4. Parse `platformio.ini` with Python `configparser`.
5. Extract PlatformIO environments, including environment name, platform, board, framework, monitor speed, upload speed, and library dependencies.
6. Choose an active environment from the first environment with a board, falling back to the first environment.
7. Register the project by reference without copying source files.
8. Return import metadata suitable for project selection and explorer refresh.

## Proposed Frontend Flow

Existing Electron IPC/preload folder selection remains the native entry point.

`Open Folder`, `Open Project`, and `Import Project` will share the backend import flow for selected paths:

1. Show status logs: `Inspecting folder...`, `Detected PlatformIO project`, `Registered external project`, `Opening workspace...`.
2. Call `POST /projects/import`.
3. Refresh projects.
4. Select the returned project id.
5. Load files through existing explorer APIs.
6. Show a clear unsupported-folder message when no `platformio.ini` exists.

## Files To Modify

- `backend/api/app.py`
- `backend/api/dependencies.py`
- `backend/api/routes/projects.py`
- `backend/api/routes/files.py`
- `backend/api/schemas/project_import.py`
- `backend/services/project_import_service.py`
- `frontend/lib/api.ts`
- `frontend/types/index.ts`
- `frontend/hooks/use-promptforge-workspace.ts`
- `frontend/components/ide/forgex-shell.tsx`
- `frontend/components/ide/top-command-bar.tsx`
- `frontend/components/ide/device-tools-panel.tsx`

Tests will be added or adjusted under `tests/api` and `tests/unit` where practical.

## Project Registry Approach

External projects will be registered in:

```text
workspace/external-projects.json
```

The registry will store:

- `project_id`
- `name`
- `path`
- `external: true`
- `project_type: platformio`
- `created_at`
- `last_opened_at`
- selected board/framework/platform summary
- parsed PlatformIO environment metadata

Generated ForgeX projects remain owned by `ProjectService`. External projects are resolved by the new import service only when the generated project service does not contain the requested project id.

## Security And Path Sandboxing Plan

All external project roots will be normalized with `Path.resolve()`.

File operations for external projects will:

- Resolve requested workspace paths relative to the registered external root.
- Reject absolute paths.
- Reject `..` traversal.
- Reject resolved paths outside the registered root.
- Skip generated/build/dependency directories like `.pio`, `node_modules`, `.git`, and common build artifacts from explorer listings.
- Avoid following symlinked entries that escape the project root.

Build and flash will continue to use the existing PlatformIO service and existing route contracts. No arbitrary shell commands will be executed from project files.

## Rollback Plan

The change is isolated behind the new import service and `POST /projects/import`.

Rollback can be done by:

1. Removing the import route and frontend import calls.
2. Removing the external project resolver fallback from `backend/api/dependencies.py`.
3. Removing external-file branches from `backend/api/routes/files.py`.
4. Deleting `workspace/external-projects.json` if it was created.

Generated ForgeX projects and existing build, flash, monitor, and file APIs should continue to work throughout because their original project service path remains the primary resolver.
