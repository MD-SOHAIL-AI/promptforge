# Phase 2.1 Model Router UX Hardening Plan

## Current Phase 2 State

ForgeX has a working `ModelRouterService`, provider registry, provider adapters, settings storage, model routes API, usage API, and an IDE Models tab. Code generation uses the router through the existing `LLMService` contract. The Electron blank-screen crash was stabilized with defensive shell guards and an IDE error boundary.

## UI/UX Gaps

- Model settings are centered on a single selected provider instead of showing router state at a glance.
- Per-task routes exist in the backend but are not editable as a compact list.
- Provider health and local provider offline states are not friendly enough.
- Model discovery is not visible; users cannot refresh provider models from the UI.
- Usage tracking is stored but not presented.
- The top-bar indicator needs a stronger "not configured/provider error/local offline" fallback.

## Backend API Gaps

- Provider list responses do not include cached health metadata, last check time, last error, or cached model count.
- Provider model listing does not cache discovered models.
- Settings storage does not persist model discovery or provider health snapshots.
- Health messages need local-provider-specific hints for Ollama and LM Studio.

## Planned Changes

- Extend backend provider payloads with `health_status`, `last_checked_at`, `last_error`, `masked_api_key`, `api_key_masked`, and `models_cached`.
- Persist provider health snapshots and discovered models in backend-only local settings storage.
- Keep `/models/providers`, `/models/providers/{provider_id}/health`, `/models/providers/{provider_id}/models`, `/models/routes`, `/models/usage`, and `/models/test` contracts additive and key-safe.
- Improve local provider health messages:
  - Ollama: "Ollama not running. Start Ollama at http://localhost:11434."
  - LM Studio: "LM Studio server not running. Start the local server at http://localhost:1234/v1."
- Replace the Models tab with compact sections:
  - Active Route Summary
  - Providers
  - Task Routes
  - Usage
- Add model refresh and manual model entry support.
- Add route save controls for each task type with provider/model/fallback fields.

## Files To Modify

- `backend/model_router/models.py`
- `backend/model_router/registry.py`
- `backend/model_router/storage.py`
- `backend/model_router/providers/*.py`
- `backend/api/routes/models.py`
- `frontend/components/ide/model-settings-panel.tsx`
- `frontend/components/ide/forgex-shell.tsx`
- `frontend/components/ide/top-command-bar.tsx`
- `frontend/lib/api.ts`
- `frontend/types/index.ts`
- `tests/api/test_model_routes.py`
- `tests/unit/test_model_provider_storage.py`
- `tests/unit/test_model_router.py`

## Risks

- Provider health checks can be slow or unavailable; the UI must show failure states without blocking shell rendering.
- OpenRouter/OpenAI model listing may fail depending on API keys and provider policy; manual model entry remains required.
- Existing user settings files may not contain new metadata keys; all reads must be tolerant.

## Rollback Plan

The router service and existing provider adapters remain intact. If the 2.1 UI or metadata additions cause issues, the Models tab can fall back to the Phase 2 single-provider configuration flow while keeping the backend route and provider settings data unchanged. Since all API additions are additive, existing generation/build/flash/monitor workflows can continue using the current saved routes and environment variables.
