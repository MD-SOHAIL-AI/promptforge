# ForgeX failure fixes — 7 October 2026

This implements the non-rate-limit fixes from [the live-log review](REPORT.md). API rate-limit handling is unchanged. The original evidence and saved runs remain available.

## Changes

- **Retired models:** HTTP 410 now produces `MODEL_RETIRED`, a permanent provider/model failure. Confirmed retirement is remembered for the specific endpoint. The NVIDIA-hosted Qwen model from this incident is rejected locally before generation; self-hosted endpoints remain separate. Retired models appear unavailable in the picker and cannot be saved as an active task route. Recovery requires an explicitly selected usable model; ForgeX does not switch providers automatically. Old generic 410 failures receive the corrected recovery projection when loaded.
- **Model catalog:** the active NVIDIA adapter now fetches the provider's `/v1/models` endpoint. A successful provider catalog supersedes stale starter/catalog entries, while saved retired selections remain visible as unavailable. Responses distinguish provider discovery, cache and fallback and include the last discovery timestamp. The frontend now recognizes the API's `provider` source as a live catalog.
- **Truncated generation:** production agent planning starts with 8,192 output tokens rather than 4,096, bounded by the model and remaining task budget. Truncation increases the allowance, up to 16,384. Later generation turns and restart recovery retain that allowance and the instruction to produce small complete actions. A model change resets the old model's recovery hint. Incomplete function arguments remain rejected before tool execution.
- **NVIDIA streaming:** native generation has a 120-second idle allowance, while finite turn, repair and job deadlines still apply. Review requests retain their existing 60-second idle allowance.
- **Requirements:** mixed paragraphs are partitioned into positive requirements and prohibitions. Ending a specification with “Do not flash or open serial” no longer excludes the specification, documentation or compile checks. Explicit continuation repairs old affected contracts while preserving existing requirement IDs, exclusions, subsequent guidance and revision history. Sealed historical events are retained.
- **Project readiness:** empty or malformed `platformio.ini` files return readiness information with configuration warnings. Generation can remain available while builds are blocked until configuration is valid. The profile endpoint returns a structured 422 instead of an uncaught parser exception.

## Verification

- Broad backend suite: **325 passed** across planner, model router, real local worker, storage, requirements, orchestration, durable recovery and API checks.
- Final focused backend rerun: **42 passed**, including the additional legacy generic-410 recovery case and endpoint-scoped retirement persistence.
- UI state suite: **83 passed**.
- Model-engine suite: **44 passed**.
- Frontend TypeScript, ESLint and production build checks passed. Desktop/model-engine components were rebuilt.
- The real backend was started with isolated state and passed `/health`: `healthy`, `engine_ready=true`, PlatformIO available. See [startup validation](startup-validation.json).
- Read-only validation against all five incident runs restores the omitted requirements and compile checks. The affected runs move from 0–2 requirements to 11–16 requirements while preserving the historical revision. See [saved-run recovery validation](recovery-validation.json).

Provider failures were exercised with local or mocked responses. These checks sent no paid model generations and did not resume production runs, apply firmware, flash hardware or alter the external project. The saved ESP32 draft still needs continuation, completion and verification.

Restart ForgeX to load the rebuilt code. For the retired-model run, select another configured model and use **Continue with …** to retain the saved work and task budget.
