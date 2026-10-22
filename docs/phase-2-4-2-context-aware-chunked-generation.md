# Phase 2.4.2: Context-Aware Chunked Generation

## Why This Was Needed

Small/free models can have enough context to understand a long embedded prompt but not enough practical output budget to produce a complete multi-file PlatformIO project in one response. They often summarize, omit files, truncate `src/main.cpp`, or fall back to a tiny blink sketch.

## Strategy Selection

ForgeX now estimates generation pressure before calling the model:

- prompt token estimate: `len(text) // 4`
- selected model capability estimate
- expected file count
- expected output size
- advanced/multi-file prompt signals

Simple prompts such as ESP32 blink remain `one_shot`. Long, advanced, dashboard, README, FreeRTOS, WebServer, or multi-file prompts select `chunked`.

## Chunked Pipeline

Chunked generation runs:

1. Requirement extraction JSON
2. Project manifest JSON
3. Per-file generation
4. Per-file validation
5. Per-file repair
6. Per-file fallback when enabled
7. Whole-project validation
8. Existing Phase 2.3.5 artifact validation
9. Build only after verified artifacts

Required advanced ESP32 files are:

- `platformio.ini`
- `include/config.h`
- `src/main.cpp`
- `README.md`

## File Validation

Each file is validated before project assembly:

- safe relative path
- non-empty content
- forbidden feature checks
- `platformio.ini` requires `espressif32`, `esp32dev`, and `arduino`
- advanced `src/main.cpp` requires `WiFi.softAP`, `WebServer`, `Preferences`, FreeRTOS task usage, `Serial`, and REST endpoints
- README requires overview, features, build, upload, serial commands, and API endpoints
- truncation signals are detected for C++ and Markdown

## Repair And Fallback

If one file fails validation, ForgeX repairs only that file. If repair fails and fallback is allowed, fallback is attempted for that file only. The whole project is not regenerated unless the pipeline cannot recover.

## Reporting

Generation reports now include:

- `generation_mode`
- strategy reasons
- requirement summary
- manifest
- per-file statuses
- failed files
- pending files
- provider/model per file
- repair/fallback flags

Forge recent output shows compact chunked progress:

```text
Generation mode: Chunked; files generated: 4/4
src/main.cpp written after repair via fallback
```

## Artifact Validation

Phase 2.3.5 artifact validation remains the final gate. Build still runs only after required files are present, current, and verified for the active workspace/current execution.

## Limitations

Chunked generation currently assembles files in memory before persistence because the existing generation handler owns workspace writes. The artifact summary records per-file status, but true on-disk incremental writes should be a later handler-level improvement. Model capability estimates are heuristic, not exact tokenizer-backed limits.
