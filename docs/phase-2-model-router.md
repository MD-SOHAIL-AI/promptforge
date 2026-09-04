# Phase 2 Model Router

ForgeX now routes AI generation through a backend `ModelRouterService`. OpenRouter is the default provider for startup-friendly BYOK use, with OpenAI, Gemini, Anthropic, LM Studio, and Ollama available as optional routes.

The router preserves the existing code-generation flow by implementing the existing `LLMService` contract. Code generation still performs the same prompt construction, retry handling, manifest extraction, and project validation.

## Provider Settings

Provider configuration is stored in the user's app-data directory by default:

```text
%APPDATA%/ForgeX/model-router/settings.json
```

`FORGEX_MODEL_ROUTER_SETTINGS_PATH` can override the settings file location for tests or local development. API keys are never returned by backend APIs; only masked values are exposed.

## Routing Defaults

Default task routes point to OpenRouter:

- `code_generation`
- `planning`
- `debugging`
- `documentation`
- `general_chat`
- `serial_analysis`

Fallback order:

```text
openrouter -> openai -> gemini -> anthropic -> lmstudio -> ollama
```

Local-only fallback order:

```text
lmstudio -> ollama
```

## API Surface

- `GET /models/providers`
- `POST /models/providers/{provider_id}/configure`
- `POST /models/providers/{provider_id}/health`
- `GET /models/providers/{provider_id}/models`
- `GET /models/routes`
- `POST /models/routes`
- `POST /models/test`
- `GET /models/usage`

## Limits

Phase 2 does not include autonomous agents, cloud sync, marketplace, billing, or subscription bridges.
