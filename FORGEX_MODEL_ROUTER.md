# ForgeX Model Router Architecture

Generated: 2026-06-17

## Goal

Add a provider abstraction and routing layer without breaking the current `LLMService` provider clients.

Supported providers:

- OpenAI
- Anthropic
- Google Gemini
- OpenRouter
- Ollama
- LM Studio

## Architecture

```text
Agent/Service Request
  -> ModelRouter
    -> TaskClassifier
    -> ProviderRegistry
    -> ModelRegistry
    -> RoutingPolicy
    -> BudgetPolicy
    -> FallbackPolicy
    -> TelemetryRecorder
  -> ProviderClient
  -> LLMResponse
```

## Core Interfaces

```text
ProviderRegistry
  register(provider)
  list()
  health(provider_id)

ModelRegistry
  list(provider_id)
  refresh(provider_id)
  get(model_id)

RoutingPolicy
  choose(task_type, constraints)

FallbackPolicy
  next_route(failure, previous_routes)

UsageTracker
  record(tokens, cost, latency, success, task_type)

BenchmarkService
  run(model_id, benchmark_suite)
```

## Task-Based Routing

Default policy:

| Task | Preferred | Fallback | Local Option |
| --- | --- | --- | --- |
| Coding | Anthropic Claude | OpenAI GPT | LM Studio/Ollama coding model |
| Reasoning | OpenAI GPT | Anthropic Claude | local reasoning model |
| Planning | Gemini | OpenAI GPT | local planner model |
| Fast classification | OpenRouter cheap/fast model | OpenAI mini | Ollama |
| Documentation | OpenAI or Gemini | Anthropic | local instruct |
| Serial log triage | fast local or cheap remote | Claude | Ollama |
| Privacy-sensitive local tasks | Ollama/LM Studio | none unless user allows | Ollama/LM Studio |

## Provider Support

Reuse existing:

- `OpenAIService`
- `AnthropicService`
- `GeminiService`
- `OpenRouterService`

Add:

- `OllamaService`: local HTTP API.
- `LMStudioService`: OpenAI-compatible local API.

## Data Model

Tables:

- `providers(provider_id, type, display_name, base_url, auth_ref, enabled, local, created_at, updated_at)`
- `models(model_id, provider_id, display_name, context_window, input_cost, output_cost, supports_tools, supports_vision, supports_json, enabled)`
- `model_routes(route_id, task_type, primary_model_id, fallback_model_ids, local_allowed, max_cost_usd, max_latency_ms)`
- `model_usage(usage_id, model_id, provider_id, task_type, input_tokens, output_tokens, cost_usd, latency_ms, success, error_code, created_at)`
- `model_benchmarks(benchmark_id, model_id, suite, score, latency_ms, cost_usd, created_at)`

## Request Contract

```text
ModelRequest
  task_type
  prompt
  system_prompt
  context
  privacy_level
  max_tokens
  temperature
  require_json
  allow_remote
  budget
  latency_target
```

## Response Contract

```text
ModelResponse
  content
  provider_id
  model_id
  route_id
  token_usage
  cost_estimate
  latency_ms
  fallback_used
  benchmark_profile
```

## Fallback Rules

Fallback on:

- rate limit
- timeout
- transient 5xx
- provider unavailable
- model unavailable
- malformed JSON when strict JSON requested, after one repair attempt

Do not fallback automatically when:

- user selected exact model and disabled fallback
- request is marked local-only
- provider returns authentication error
- policy budget would be exceeded

## Cost And Token Tracking

Every generation records:

- provider
- model
- task type
- prompt/completion tokens
- cost estimate
- latency
- success/failure
- agent session
- project/workspace

The UI should expose daily/monthly spend, per-provider usage, per-task usage, and current route decisions.

## V1 Migration

1. Leave `LLMService` and provider classes intact.
2. Add `ModelRouterService` that implements the same `generate()` shape used by `CodeGenerationService`.
3. Inject router into `CodeGenerationService`.
4. Migrate env vars to DB-backed provider settings while keeping env fallback.
5. Add settings UI and provider health checks.

