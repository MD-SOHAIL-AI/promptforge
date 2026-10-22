# Phase 2.3.5 Plan: Code Generation Truth and Artifact Validation

## Scope

This stabilization phase makes generation and build status depend on verified workspace artifacts from the current execution. It does not add subscription bridges, autonomous agents, board registry, cloud sync, marketplace, or unrelated workflow features.

## Root Cause

The workflow validated the in-memory `GeneratedProject` returned by the generation service, then persisted files and marked generation successful without a post-write artifact check. In an open workspace, build resolution could then find an existing `platformio.ini` and `src/main.cpp` and successfully build stale files from a previous run. The UI received successful workflow events and displayed them as current truth even when the workspace files were unchanged.

## Backend Plan

1. Add a generation artifact summary after persistence:
   - workspace root
   - generation mode
   - created, updated, and unchanged files
   - required file presence
   - hashes for tracked files
   - current task ID
   - prompt-satisfaction flag
2. Require PlatformIO generation to verify `platformio.ini` and `src/main.cpp`.
3. Require README for advanced/README/complete project prompts.
4. Reject generation into open folders when required files are only stale unchanged files, except explicit `modify_existing_project`.
5. Attach artifact summary to `GenerateCodeResult.metadata`.
6. Block build when the current execution does not have a verified generation artifact.
7. Ensure build runs in the same workspace root that generation verified.
8. Keep build-only external project workflows intact.
9. Keep default AI workflow as Planning -> Generation -> Build, with flash manual unless explicitly requested.

## Frontend Plan

1. Clear previous logs when a new execution starts.
2. Reset stage state at execution start.
3. Ignore socket events whose task ID does not match the active execution.
4. Defensively treat generation-complete events with unvalidated artifacts as failed.
5. Continue showing optional hardware readiness separately from generation/build success.

## Tests

Add or update tests for:

- artifact summary on successful generation
- empty-folder generation creates required files in the active root
- stale unchanged required files do not count as current generation
- build uses the active workspace root
- optional no-device hardware behavior remains non-fatal
- frontend typecheck for event payload changes

## Manual QA

Use the advanced ESP32 Control Hub prompt in an empty folder and in a stale blink folder. Generation should show created/updated counts, build should use the same workspace, and ForgeX should not auto-flash by default.
