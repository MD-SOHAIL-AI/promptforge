# ForgeX V2 Phase 2.4.3 - Incremental Chunked Writes

## Summary

Phase 2.4.3 makes chunked generation recovery-safe. In chunked mode, each generated file is validated and written to the active workspace immediately instead of waiting for the workflow handler to write every file at the end.

## Incremental Write Behavior

The chunked pipeline now resolves the active workspace root from execution context metadata or `project_path`. If the root exists, `ChunkedGenerationWriter` writes each validated file atomically inside that root.

Per file:

- Validate generated content.
- Resolve the relative path inside the workspace.
- Reject absolute paths, `..` paths, and paths escaping the workspace.
- Reject symlink targets.
- Hash the previous file if one exists.
- Write through a temporary file and replace the target.
- Record created/updated status, hashes, byte count, provider, model, repair, and fallback metadata.

If a later file fails, already validated files remain on disk and the run is recorded as `incomplete`.

## File Safety Rules

Generated file paths must be project-relative. ForgeX never writes paths outside the active workspace. Parent directories are created only after containment checks pass.

## Telemetry Storage

Generation diagnostics are persisted in local app data:

- `generation-attempts.jsonl`
- `chunked-generation-runs.jsonl`

The records include execution ID, task ID, workspace root, strategy, provider/model, status, required files, generated/failed/pending files, per-file statuses, repair count, fallback count, warning count, and build flags. Raw API keys are never stored.

## APIs

New/extended diagnostics APIs:

- `GET /models/generation-attempts`
- `GET /models/chunked-generation-runs`
- `GET /models/chunked-generation-runs?execution_id=...`
- `GET /models/chunked-generation-runs?project_id=...`
- `POST /models/generation-diagnostics/clear`

## Forge UI

The Forge panel now has an expandable Generation Progress section for chunked generation. It shows mode, file count, current/pending file, repairs, fallbacks, warnings, and per-file status.

Settings -> Models now shows recent chunked generation diagnostics with filters for success, failed, incomplete, repair used, and fallback used.

## Build Gating

Build remains gated by artifact validation. For incremental chunked generation, the artifact summary trusts per-file write records from the chunked writer and still verifies that required files exist in the active workspace.

Incomplete chunked generation blocks build.

## Known Limitations

Progress is reported through workflow generation report events. Fine-grained live streaming events for every individual file write are represented in the final chunked report, not yet delivered as independent websocket events during the model call.
