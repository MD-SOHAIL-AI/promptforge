# Phase 2.3.5: Code Generation Truth and Artifact Validation

## Previous Behavior

ForgeX could show Generation and Build as successful after the model returned a valid in-memory project object, even if the active workspace files were unchanged or stale. Build could then succeed against an older `platformio.ini` and `src/main.cpp`, which made the Forge panel look successful while the user saw no new code.

## New Status Model

Generation success now requires a verified artifact summary from the persisted workspace. For PlatformIO projects, ForgeX checks:

- `platformio.ini` exists in the active workspace
- `src/main.cpp` exists in the active workspace
- required files are inside the workspace root
- at least one required file was created or updated for the current execution, unless the mode is explicitly `modify_existing_project`
- the artifact summary task ID matches the current execution task

Advanced/complete project prompts also require README output.

## Artifact Summary

`GenerateCodeResult.metadata.artifact_summary` now includes:

- `task_id`
- `workspace_root`
- `generation_mode`
- `created_files`
- `updated_files`
- `unchanged_files`
- `required_files`
- `required_files_present`
- `platformio_ini_present`
- `main_cpp_present`
- `file_hashes`
- `raw_generated_file_count`
- `parsed_file_count`
- `generation_satisfied_prompt`

Progress events include the summary and a user-facing message like:

```text
Generation success: created: 3 files, updated: 0 files, unchanged: 0 files, workspace: <path>
```

## Stale Build Prevention

Build now refuses to run after a generation step unless the current execution has a verified artifact summary. It also checks that the build workspace matches the generation workspace. If validation fails, the user sees a blocked build message instead of a stale success:

```text
Build blocked: generation did not produce required files for this execution.
```

Build-only external project workflows still work through the active workspace resolver.

## Auto-Flash Behavior

The default AI workflow remains software-only:

```text
Planning -> Generation -> Build
```

Flash remains manual unless explicitly requested. A missing board is treated as a hardware readiness issue, not a generation/build failure.

## Frontend Changes

The workspace hook now clears previous logs at execution start, resets stages, and ignores stale socket events with a different task ID. The stage mapper also defensively refuses to mark generation complete if the backend ever sends an unvalidated artifact completion event.

## Tests and QA

Implemented targeted tests for:

- artifact summary on managed generation
- generation into an empty open folder
- unchanged stale required files rejected in open-folder generation
- existing workspace build resolution
- optional hardware no-device behavior

Verification run during implementation:

```text
python -m compileall backend
python -m pytest tests/unit/test_generate_code_handler.py tests/unit/test_workflow_adapters.py tests/integration/test_blink_led_workflow.py -q
npm.cmd --prefix frontend run typecheck
```

Full verification should also include:

```text
python -m pytest
npm.cmd --prefix frontend run build
npm.cmd run build:electron
npm.cmd run dev:desktop
```
