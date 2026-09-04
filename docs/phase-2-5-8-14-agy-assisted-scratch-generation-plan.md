# Phase 2.5.8.14 — AGY Assisted Scratch Generation Plan

## Objective

Allow an explicit ForgeX action to start the official AGY CLI as a generator while preserving the Phase 2.5.8.13 manual import boundary. AGY never receives the repository or active workspace as its cwd and never receives direct active-project write authority.

```text
explicit assisted-run action
  -> AGY runs in a marked disposable external cwd
  -> ForgeX checks one precomputed expected scratch folder
  -> existing scratch import validator captures safe text files
  -> managed import sandbox receives validated bytes
  -> exact diff creates a persistent review
  -> user-controlled apply remains separate
```

## Provider modes

- AGY direct workspace editor: paused.
- `agy_scratch_import`: manual artifact import behind `FORGEX_ENABLE_AGY_SCRATCH_IMPORT`.
- `agy_scratch_runner`: experimental local CLI generator behind `FORGEX_ENABLE_AGY_ASSISTED_RUNNER`.

The assisted provider is `local_cli_generator` / `assisted_scratch_generation` / `agy_scratch_import`. It remains non-production and cannot route as a normal planner.

## Execution boundary

Each run precomputes a random run ID, nonce, and exact expected scratch folder name. AGY receives a fixed `esp32-platformio-blink` template instruction in memory only. Direct argv is `agy -p <runtime-instruction>` with `shell:false`, a bounded timeout, and cwd under `C:\forgex-agy-runs\<run_id>`.

The invocation root must be outside the repository, active workspace, home, Desktop, OneDrive, and filesystem roots. The run directory begins with a ForgeX marker. Active-workspace and invocation-workspace integrity are checked around AGY execution.

ForgeX checks only the exact precomputed scratch path. It does not enumerate the scratch root, select recent output, or interpret stdout as import authority. A missing exact folder produces `AGY_ASSISTED_EXPECTED_FOLDER_MISSING`, creates no review, and exposes the existing manual import fallback.

## Import and review reuse

The assisted runner calls `AGYScratchProjectImportService.import_project` with hardened review context. It inherits the 200-file, 5 MiB total, 512 KiB per-file, depth-eight, text-only, secret-blocking, directory-blocking, and link/reparse rules.

Assisted reviews identify `agy_scratch_runner`, the fixed template, expected-source mode, relative file tree, byte and created-file counts, detected project type, manual-review warning, and active-workspace integrity. Apply, build, and flash remain separate manual operations.
