# Free-model timeout coverage — 7 October 2026

ForgeX now applies the provider timeout fixes to free-model requests across its shared transport. Coverage includes models identified by provider metadata, zero token prices in the pinned catalog, `:free` IDs, and the `openrouter/free` router. Newly discovered free models do not require another hardcoded model list.

## What changed

- Added a registry lookup for the model's free timing hint. Cached provider metadata takes precedence over pinned prices, including an explicit `free: false` value.
- Passed that hint through the agent planner, shared OpenCode transport, and legacy OpenAI-compatible transport. Cached reasoning capabilities also reach the shared timeout policy.
- Extended idle allowances for Groq, Cerebras, Gemini/Google, Mistral, Cohere, and Cloudflare Workers AI. Their free, trial, or included usage can share model IDs with paid usage, so token prices alone cannot identify the account tier.
- Kept explicit request policies and parent turn/job deadlines authoritative. Policy metadata stays in ForgeX and is not sent as a billing tier to providers.

These changes extend the first-event, meaningful-stream-progress, deadline classification, and bounded recovery fixes documented in the [earlier provider timeout review](../provider-timeouts/REVIEW.md).

## Allowances

The values below are maximum transport allowances, not promised provider response times. Parent deadlines can end a request earlier.

| Request type | First meaningful event | Idle after meaningful progress | Total attempt |
| --- | ---: | ---: | ---: |
| General free model or free/trial provider family | 120 s | 120 s | 300 s |
| OpenRouter free model | 120 s | 120 s | 300 s |
| NVIDIA/NIM generation | 120 s | 120 s | 300 s |
| Reasoning model / DeepSeek, outside the preceding cases | 180 s | 120 s | 600 s |
| Ollama / LM Studio | 180 s | 120 s | 600 s |

Review requests retain their existing policy and shorter outer deadline. API rate limits, admission limits, retry counts, saved model selections, and billing labels were not changed.

## Provider website checks

The official pages support treating free access as model metadata or account eligibility rather than relying only on an ID suffix:

| Provider | Relevant evidence |
| --- | --- |
| OpenRouter | Its [free-model collection](https://openrouter.ai/collections/free-models) lists current free options and the `openrouter/free` router. |
| Groq | [Rate-limit documentation](https://console.groq.com/docs/rate-limits) distinguishes free and developer plans using shared model IDs. |
| Gemini | [Pricing](https://ai.google.dev/gemini-api/docs/pricing) distinguishes free-tier and paid-tier availability for models. |
| Cerebras | Current [pricing](https://www.cerebras.ai/pricing) and [inference information](https://www.cerebras.ai/inference) describe trial/credit access. This does not establish unlimited ongoing free API access. |
| Mistral | [Usage-limit documentation](https://docs.mistral.ai/admin/billing-usage/usage-limits) describes Free mode and its included usage. |
| Cohere | [Pricing documentation](https://docs.cohere.com/docs/how-does-cohere-pricing-work) describes limited free trial usage. |
| Cloudflare Workers AI | [Pricing documentation](https://developers.cloudflare.com/workers-ai/platform/pricing/) describes included usage on free and paid plans. |

These eligibility facts inform the transport allowance. They do not guarantee a particular account's access, price, model availability, or response latency.

## Validation

- **607 zero-token-price text-model entries across 76 pinned catalog providers:** every entry received the intended bounded allowance; zero coverage gaps. See [coverage.json](coverage.json) and its reproducible [coverage script](check_coverage.py). Zero token prices can also represent subscription-included usage. This is policy coverage, not proof that every catalog entry is currently callable or free for an account.
- **299 broader regression tests passed**, covering provider transport, recovery, routing, storage, admission, circuit handling, streaming, durable boundaries, and model/outage API routes. Two dependency deprecation warnings remain. See [python-tests.log](python-tests.log).
- **70 focused checks passed after the final provider-family expansion**, including metadata-only free models, explicit overrides, cached paid metadata, account free tiers, local models, and unchanged routes. See [focused-tests.log](focused-tests.log). These overlap the broader run and must not be added together as distinct tests.
- **Isolated backend startup passed:** healthy status, engine ready, PlatformIO available, and local authentication required. See [startup-validation.json](startup-validation.json).
- `git diff --check` passed.

No external generation requests were sent for this extension, and production state was not used. The startup check used temporary state and an in-memory credential store. Per-model response-time probabilities were not re-estimated; the earlier review contains the historical live-log timing analysis and its sampling limits.

The fixes are in the workspace source. An installed desktop release was not packaged or deployed during this extension.
