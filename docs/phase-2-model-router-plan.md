# Phase 2 Model Router Plan

## Current LLM Architecture

ForgeX currently builds code generation around `CodeGenerationService`, which accepts an injected `LLMService`. `backend/services/llm_service.py` already contains provider-specific HTTP implementations for OpenRouter, OpenAI, Anthropic, and Gemini. Application startup in `backend/api/app.py` creates a single LLM service from environment variables and passes it into `CodeGenerationService`.

Generation flow today:

```text
Execute route
  -> execute_prompt / execute_task
  -> GenerateCodeHandler
  -> CodeGenerationService
  -> LLMService
```

## Target Router Architecture

Phase 2 adds a provider router without removing the existing `LLMService` contract:

```text
ForgeX Code Generation / AI Request
  -> ModelRouterService
  -> Provider Registry
  -> Selected Provider Adapter
  -> OpenRouter / OpenAI / Gemini / Anthropic / Ollama / LM Studio
```

`ModelRouterService` will implement the existing `LLMService` interface so `CodeGenerationService` can keep its retry, parsing, and validation behavior.

## Files To Modify

- `backend/api/app.py`
- `backend/api/routes/__init__.py`
- `backend/services/code_generation_service.py` only if needed for router compatibility
- `backend/services/llm_service.py` for local-provider enum compatibility
- `frontend/lib/api.ts`
- `frontend/types/index.ts`
- `frontend/components/ide/top-command-bar.tsx`
- `frontend/components/ide/forgex-shell.tsx`

## Files To Add

- `backend/model_router/`
- `backend/api/routes/models.py`
- `frontend/components/ide/model-settings-panel.tsx`
- `docs/phase-2-model-router.md`
- `docs/model-router-subscription-bridges.md`
- Router and storage tests under `tests/unit/` and `tests/api/`

## Provider List

- OpenRouter (`openrouter`), OpenAI-compatible, default provider
- OpenAI API (`openai`)
- Gemini API (`gemini`)
- Anthropic API (`anthropic`)
- LM Studio (`lmstudio`), local OpenAI-compatible endpoint
- Ollama (`ollama`), local endpoint

## Auth Storage Plan

Provider settings are stored in a local JSON settings file under user app data by default:

```text
%APPDATA%/ForgeX/model-router/settings.json
```

Tests and advanced local runs may override this with `FORGEX_MODEL_ROUTER_SETTINGS_PATH`. API keys are never returned by API responses. Responses may include only masked keys such as `sk-...abcd`. Environment variables remain supported as fallback when no saved key exists.

Priority:

```text
saved provider setting -> environment variable -> disabled provider
```

## Integration Plan With CodeGenerationService

`create_app()` creates a `ModelRouterService` by default and passes it into `CodeGenerationService`. If a caller injects a custom `llm_service` or `code_generation_service`, that behavior remains supported for tests and existing integrations.

## Risks

- Provider network health checks can be slow or unavailable; route APIs return structured errors and do not block startup.
- Existing tests expect a single `LLMProvider` enum list; local provider values require test updates.
- Local provider model listing depends on Ollama or LM Studio being running.

## Rollback Plan

Set `PROMPTFORGE_LLM_PROVIDER`, provider key, and `PROMPTFORGE_MODEL` and instantiate the legacy service path if needed. The existing provider classes remain in `backend/services/llm_service.py`, and `CodeGenerationService` still depends only on the `LLMService` contract.
