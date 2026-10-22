# ForgeX V2 Phase 2.4.4 - Live Chunked Generation Events

## Summary

Phase 2.4.4 streams generation progress while the model is still producing chunked output. The Forge panel no longer waits for the final generation report to show per-file status.

## Event Architecture

`CodeGenerationService` accepts an optional generation event callback on `CodeGenerationRequest`. It emits transport-neutral payloads with lowercase `event_type` values.

`GenerateCodeHandler` passes that callback into the request.

`main.py` bridges callback payloads to the existing workflow progress callback and converts event types to WebSocket event names such as `FILE_WRITTEN`.

The API progress hub remains the only WebSocket transport layer. Model providers and generation helpers do not know about WebSockets.

## Event Types

Implemented live events:

- `GENERATION_STRATEGY_SELECTED`
- `GENERATION_STARTED`
- `GENERATION_FAILED`
- `REQUIREMENTS_EXTRACTION_STARTED`
- `REQUIREMENTS_EXTRACTED`
- `MANIFEST_GENERATION_STARTED`
- `MANIFEST_CREATED`
- `FILE_GENERATION_STARTED`
- `FILE_GENERATION_REPAIR_STARTED`
- `FILE_GENERATION_FALLBACK_STARTED`
- `FILE_GENERATION_VALIDATED`
- `FILE_WRITTEN`
- `FILE_FAILED`
- `GENERATION_INCOMPLETE`
- `GENERATION_COMPLETED`
- `BUILD_BLOCKED` is reserved in the schema for build-gating progress.

Each payload includes task/execution IDs, generation mode, strategy, timestamp, message, and provider/model data when available. File events include `file_path`, attempt, repair/fallback, and write metadata.

## File Write Ordering

`FILE_WRITTEN` is emitted only after `ChunkedGenerationWriter.write_file(...)` succeeds. Failed writes emit `FILE_FAILED` and `GENERATION_INCOMPLETE`.

## Frontend Mapping

The workspace hook reduces live events into the existing `generationProgress` state. The Forge generation progress card updates while work is happening:

- manifest file list initializes pending rows
- file generation marks a row as generating
- repair/fallback marks the same row with the active state
- validation marks the row validated
- write marks the row written and increments written count
- incomplete generation shows failed and pending files

The Output tab shows readable lines prefixed with `[generation]`.

## Tests

Coverage includes live event ordering for chunked generation, requirements and manifest events, file generation/validation/write events, repair events, incomplete events, and one-shot basic generation events.

## Limitations

Settings -> Models diagnostics still focuses on persisted completed/incomplete runs. It does not yet subscribe to active live generation events.
