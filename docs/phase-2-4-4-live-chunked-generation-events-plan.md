# Phase 2.4.4 Plan: Live Chunked Generation Events

## Scope

This phase streams live code-generation progress while chunked generation is running. It does not change artifact validation, build gating, no-auto-flash behavior, model routing, or external project import.

## Backend Plan

1. Add a lightweight generation progress event callback to `CodeGenerationRequest`.
2. Emit events from `CodeGenerationService` at real state transitions:
   - strategy selected
   - requirements extraction start/completion
   - manifest generation start/completion
   - file generation, repair, fallback
   - file validation
   - file write after actual disk write
   - incomplete/completed generation
3. Add an async callback bridge in `GenerateCodeHandler`.
4. Forward callback payloads from `main.py` through the existing workflow progress callback.
5. Extend the WebSocket event enum with `GENERATION_*` event names.

## Frontend Plan

1. Extend execution event types for live generation events.
2. Update the workspace hook to reduce live generation events into `generationProgress`.
3. Add readable Output lines such as:
   - `[generation] Strategy selected: chunked`
   - `[generation] Generating src/main.cpp`
   - `[generation] Written src/main.cpp`
4. Keep the existing Forge progress panel compact and update it live.

## Tests

Add/extend tests for:

- strategy selected event
- requirements and manifest events
- file generation/validation/write events
- write event only after disk write
- repair/fallback events
- incomplete event
- one-shot basic events
- frontend typecheck

## Limitations

This phase streams generation progress through the existing workflow WebSocket stream. Settings diagnostics remain persisted-run based; active-run status in Settings is not a primary target.
