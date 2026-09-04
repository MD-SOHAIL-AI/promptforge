# Phase 2.4.2 Plan: Context-Aware Chunked Generation

## Problem

One-shot generation asks the selected model to produce an entire embedded project in one response. Long prompts and smaller/free models can exceed practical context or output limits, causing summaries, missing files, tiny blink sketches, or truncated source.

## Scope

This phase adds context-aware generation strategy selection and a chunked generation path. It preserves Phase 2.3.5 artifact validation, Phase 2.4 repair/fallback behavior, external project import, build/flash/monitor APIs, and the no-auto-flash default.

## Strategy Selection

Add a lightweight estimator using:

- prompt token estimate: `len(text) // 4`
- selected model ID
- task type
- expected file count
- expected output estimate

Strategies:

- `one_shot`: simple prompts such as blink projects
- `chunked`: long/advanced/multi-file projects or small-output models
- `repair`: validation-aware repair from Phase 2.4
- `fallback`: provider/model fallback from Phase 2.4

## Chunked Pipeline

For chunked generation:

1. Extract a compact requirement summary.
2. Generate a project manifest without file contents.
3. Generate files one at a time.
4. Validate each generated file.
5. Repair only the failed file.
6. Use fallback only for the failed file when available.
7. Assemble a canonical `GeneratedProject`.
8. Let existing artifact validation and build gating decide final success.

## File Validation

Per-file validators check:

- safe relative path
- non-empty content
- requested path match
- forbidden feature signals
- PlatformIO build settings
- advanced `src/main.cpp` feature signals
- README sections
- likely truncation signals

## Progress and Reporting

Chunked reports include:

- generation mode
- requirement extraction status
- manifest status
- per-file generation status
- provider/model per file
- repair/fallback flags
- failed/pending files

Existing Forge output logs render these events as compact progress messages.

## Tests

Add tests for strategy selection, chunked summary/manifest/file generation, file repair/fallback, truncation repair, advanced blink rejection, artifact summary metadata, and one-shot compatibility.
