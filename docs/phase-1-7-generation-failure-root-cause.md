# Phase 1.7 Generation Failure Root Cause

## Reproduction Steps

From the desktop UI, submit the existing AI assistant default prompt:

```text
Create embedded firmware for the active project and validate the build output.
```

The workflow starts, planning emits progress, then Generation fails before project files are created.

## Failure Location

The failure occurs in:

```text
backend/workflow/generate_code_handler.py
GenerateCodeHandler.execute()
  -> _validate_plan()
```

The exact logged exception from `.promptforge/desktop-dev.out.log` is:

```text
generate_code validation_failed ... phase=request_validation error_type=ValueError message=target_board must be resolved before generation
```

## Root Cause

The AI panel default prompt does not name a supported target board or framework. The rule-based planner therefore returns:

```text
target_board = UNKNOWN
framework = UNKNOWN
```

`GenerateCodeHandler._validate_plan()` correctly rejects generation for an unresolved board. This is not a file persistence failure and it happens before the LLM is called, so no raw LLM output is available for this failure case.

A second stability issue exists once the LLM is called: `CodeGenerationService.extract_files()` only accepts strict JSON or one full fenced JSON block. OpenRouter/free models commonly return explanatory text, fenced JSON with surrounding prose, or markdown file blocks, which causes `GeneratedOutputError` before validation.

## Chosen Fix

1. Update the AI panel default prompt to a concrete supported request:

```text
Create an ESP32 blink LED project using PlatformIO.
```

2. Make code-generation parsing defensive:
   - strict JSON
   - fenced JSON extraction
   - embedded JSON object extraction
   - markdown `file:path` blocks
   - heading-based file blocks

3. Improve prompt contract to require JSON and required files while still allowing the parser to recover when smaller/free models do not comply.

4. Emit clearer workflow failure messages in progress logs so the UI shows the actual generation error instead of a generic failure.

## Risks

- Defensive parsing must not accept unsafe paths. All recovered files still go through `GeneratedFile` path validation.
- Heading-based recovery is intentionally narrow and accepts only safe file-looking headings.
- If a prompt truly omits target board/framework, generation should still fail clearly rather than guessing silently.
