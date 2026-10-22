# Phase 2.4.3 Plan: Incremental Chunked Writes

## Scope

This phase improves Phase 2.4.2 chunked generation so validated files are written as soon as each file passes validation, per-file telemetry is persisted, and Forge surfaces compact generation progress. It preserves one-shot generation, artifact validation, no-auto-flash behavior, external project import, and build/flash/monitor APIs.

## Incremental Write Plan

1. Add a safe chunked writer that writes only under the active workspace root.
2. Record previous and new file hashes for each generated file.
3. Use atomic replace for writes where feasible.
4. Add per-file status metadata:
   - path
   - created/updated
   - previous/new hash
   - bytes written
   - provider/model
   - repair/fallback flags
   - validation errors
5. Keep previous valid files on disk when a later file fails.
6. Mark failed chunked runs as incomplete and prevent build.

## Diagnostics Plan

1. Add a local JSONL diagnostics store under app data.
2. Persist generation attempts and chunked generation run summaries.
3. Expose compact APIs:
   - `GET /models/generation-attempts`
   - `GET /models/chunked-generation-runs`
   - `POST /models/generation-diagnostics/clear`
4. Avoid API keys and cap raw output previews.

## UI Plan

1. Add a compact `Generation Progress` panel to Forge.
2. Show chunked mode, file counts, current file, and per-file statuses.
3. Keep it expandable and small by default.
4. Add generation diagnostics loading to the existing workspace hook for Settings/Models consumers.

## Build Gating Plan

Build remains blocked unless:

- all required files are written
- all required files validate
- chunked run status is success
- workspace root matches the active execution
- Phase 2.3.5 artifact summary is valid

## Tests

Add coverage for safe writes, incomplete chunked runs, persisted diagnostics, API responses, build blocking, per-file repair/fallback metadata, and frontend type handling.
