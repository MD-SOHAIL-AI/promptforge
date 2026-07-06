# Phase 2.4 Plan: Code Generation Reliability

## Scope

This phase strengthens code generation reliability after artifact validation. It keeps Phase 2.3.5 strict disk validation intact and does not add subscription bridges, autonomous agents, board registry, cloud sync, marketplace, or unrelated architecture.

## Goals

1. Track every generation attempt with provider/model, timing, parse and validation outcome, and failure metadata.
2. Strengthen the structured output contract so models return project files only.
3. Run one validation-aware repair attempt when the first generation output is invalid.
4. Use existing model-router fallback behavior when available and allowed.
5. Add lightweight quality warnings for advanced ESP32 prompts.
6. Surface a compact generation report to Forge and output logs.
7. Preserve strict artifact validation and verified-build gating from Phase 2.3.5.

## Backend Plan

1. Add immutable generation attempt/report models in `code_generation_service.py`.
2. Record attempt metadata in memory on `CodeGenerationService`.
3. Split one model call into a helper that can be used for initial, repair, and fallback attempts.
4. Generate a repair prompt from parse or project-validation failure details.
5. Try one repair call with the same service when parse/project validation fails.
6. Add a fallback hook that can use an injected fallback LLM service when configured by the app/router layer.
7. Include attempt report metadata in `GeneratedProject.metadata`.
8. Preserve provider auth/budget failures as hard failures without fallback.
9. Extend progress payloads with generation report data.

## Frontend Plan

1. Read the backend generation report from progress payloads.
2. Show compact report text in recent output and stage details.
3. Keep stage truth bound to current task ID.
4. Defensively handle repair/fallback metadata without requiring new UI architecture.

## Tests

Add/update tests for:

- valid initial JSON generation
- missing `platformio.ini` repair
- missing `src/main.cpp` repair
- unsafe paths rejected and repaired
- repair success/failure
- fallback used and disabled cases
- advanced prompt warnings
- telemetry attempt records
- existing artifact validation and no-auto-flash behavior

## Manual QA

Run simple blink, advanced ESP32 control hub, and simulated bad-output tests. Generation should validate, repair once, fallback when configured, report attempts, and build only verified artifacts.
