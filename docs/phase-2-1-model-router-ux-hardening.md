# Phase 2.1 Model Router UX Hardening

Phase 2.1 hardens the Model Router user experience without adding new provider types or subscription bridges.

## What Changed

- The Models tab now has compact sections for:
  - Active Route Summary
  - Providers
  - Task Routes
  - Usage
- Provider cards show configuration, enabled state, health status, default model, base URL, last checked time, cached model count, and friendly errors.
- Provider actions support save, test connection, refresh models, and clearing a saved API key with confirmation.
- Per-task route rows support provider, model, fallback, local-only, and save controls.
- Recent model usage is shown as a compact list with task type, provider/model, latency, success/failure, tokens, and error code when present.
- The top-bar model indicator now reports `Not configured`, `Provider error`, or `Local offline` when route/provider state is not ready.

## Backend Additions

Provider responses now include additive metadata:

- `health_status`
- `last_checked_at`
- `last_error`
- `masked_api_key`
- `models_cached`

Provider health checks persist the last checked status in backend-only local settings storage. Model discovery persists discovered or fallback model IDs in the same backend-only store. Raw API keys are still never returned by API responses.

## Local Provider UX

Ollama and LM Studio health failures now return local-provider guidance:

- `Ollama not running. Start Ollama at http://localhost:11434.`
- `LM Studio server not running. Start the local server at http://localhost:1234/v1.`

Model refresh remains tolerant: when a provider model endpoint fails, the API returns fallback known models and a message instructing the user to enter a model ID manually.

## Generation Errors

Router configuration errors now point users to the Models panel. Invalid model output errors now explain that the selected model did not return a valid project and recommend trying another model or enabling fallback.

## Limits

No Codex, Claude Code, Gemini CLI, Copilot, agent runtime, board registry, cloud sync, marketplace, or billing features were added.
