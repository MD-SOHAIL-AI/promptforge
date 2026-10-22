# Phase 2.4: Code Generation Reliability

## Summary

Phase 2.4 adds a reliability layer before Phase 2.3.5 artifact validation. ForgeX now records generation attempts, repairs invalid model output once, can fall back to a stronger route when available, and reports what happened to the user without weakening disk validation or build gating.

## Generation Attempt Model

`CodeGenerationService` records each generation call as a `GenerationAttempt` with:

- attempt ID
- execution/task ID
- provider and model
- route ID
- prompt hash
- attempt number and type
- start and completion timestamps
- latency
- parse and validation status
- parsed file count
- required-file presence
- error code/message
- raw output preview
- repair/fallback flags

Attempts are stored in memory and exposed through:

```text
GET /models/generation-attempts
```

The endpoint supports `task_id`, `execution_id`, and `limit` query parameters.

## Structured Contract

The generation prompt now requires strict project-file JSON:

```json
{
  "project_name": "project-name",
  "files": [
    {
      "path": "platformio.ini",
      "content": "..."
    }
  ]
}
```

The prompt explicitly forbids explanations outside file content, absolute paths, `../` paths, and omitted placeholders. PlatformIO projects must include `platformio.ini` and `src/main.cpp`; advanced/complete prompts must include README output.

## Repair Behavior

If parsing or deterministic project validation fails, ForgeX makes one repair attempt with a validation-aware prompt. The repair prompt includes the previous error and restates the required files/path rules.

Repair triggers include:

- missing `platformio.ini`
- missing `src/main.cpp`
- unsafe file paths
- missing README for advanced/README prompts
- unsupported or malformed project manifests

LLM authentication, configuration, timeout, or provider errors are not repaired as content errors.

## Fallback Behavior

If repair fails and fallback is enabled, ForgeX tries a fallback generation attempt.

Fallback uses either:

- an injected fallback `LLMService`, or
- the existing `ModelRouterService` fallback candidates.

For router-backed generation, fallback attempts pass metadata to skip the failed primary provider. Fallback is not used when disabled or when the request is local-only.

## Quality Checks

Advanced ESP32 prompts now get lightweight warnings when generated code looks too small or does not contain requested feature signals such as:

- WebServer
- WiFi AP usage
- Preferences
- README

These are warnings, not hard failures. Simple blink prompts do not receive advanced-project warnings.

## Reporting

Successful generation progress events now include:

- provider/model
- attempt count
- repair/fallback flags
- artifact summary
- generation report
- warnings

Forge recent output shows compact messages such as:

```text
Generation report: openrouter / openai/gpt-oss-120b:free; attempts: 2; repair: yes; fallback: no
Generation initial failed: PlatformIO project is missing required files: src/main.cpp
Generation repair succeeded
```

Failed generation includes attempt metadata in the backend error details and user-facing guidance:

```text
Try a stronger model or enable fallback.
```

## Preserved Behavior

- Phase 2.3.5 artifact validation remains strict.
- Build still requires verified current-execution artifacts.
- Default workflows still do not auto-flash.
- External project build/import behavior is preserved.
- API keys are not logged or exposed.

## Tests

Added/updated coverage for:

- initial valid generation
- missing `platformio.ini` repair
- missing `src/main.cpp` repair
- unsafe path repair
- repair failure with actionable error
- fallback used when enabled
- fallback disabled behavior
- advanced prompt README repair
- advanced warning behavior
- simple blink no-warning behavior
- generation attempt telemetry
- router skip-primary fallback metadata
- generation attempts API endpoint

## Limitations

Attempt storage is in-memory for now. Quality checks are deliberately lightweight string/signal checks, not semantic firmware analysis. Fallback depends on configured router/provider availability and is skipped for local-only requests.
