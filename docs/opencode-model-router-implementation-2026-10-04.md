# OpenCode model router implementation

Implemented on 2026-10-04 in ForgeX. The supplied `opencode-dev (2).zip` was reviewed as reference source. Instructions inside the archive were treated as document contents.

## Source and review findings

The archive contains OpenCode 1.18.34. Its model stack uses Effect-based native request compilation, provider-specific wire protocols, validated stream parsers, a shared error taxonomy, Models.dev model metadata, and AI SDK fallbacks for other provider packages. Copying only its provider names or retry constants would leave ForgeX's former HTTP implementations in charge.

ForgeX now bundles the actual MIT-licensed `packages/llm` engine and its schema dependencies under `third_party/opencode`. `provenance.json` records the archive SHA-256 and original source hashes. All vendored files match those hashes except one documented change to `route/executor.ts`: it exposes `singleAttemptLayer` to disable native HTTP retries for ForgeX. The original OpenCode layer remains available. Its native stream parsing, request validation, protocol lowering, signing, and error decoding remain upstream code.

The pinned [Models.dev](https://models.dev/) snapshot contains **226 providers and 8,393 provider-model entries**. All are registered. ForgeX retains `gemini`, `nvidia_nim`, and `ollama` compatibility identifiers, yielding 229 built-in provider identifiers. Saved provider keys, custom providers, explicit model selections, model caches, and routes remain usable. Models are listed with their catalog capabilities and context limits; catalog presence does not imply an account has access. Embedding, audio, and other non-text entries are present in the catalog, but ForgeX's generation endpoint continues to enforce text-generation capabilities.

## Execution

`ModelRouterService` installs `OpenCodeProvider` for every default and custom provider. The Python adapter starts `dist/model-router/worker.cjs` for one attempt and exchanges bounded NDJSON over stdin/stdout. Secrets travel through stdin, not command-line arguments or temporary files. The child uses Electron's embedded Node runtime in desktop releases and Node from PATH in source checkouts. Each attempt has process startup overhead; cancellation and deadlines kill and reap the owned child.

Native routes cover OpenAI Responses, Chat Completions, Anthropic Messages, Gemini, Bedrock Converse, Azure, OpenRouter, xAI, Copilot, and Cloudflare. Other catalog packages use their provider SDKs through the same bounded transport. OpenCode's AI SDK 6 is the default. Salad Cloud's current provider implements the v4 model interface, so that adapter uses a separately pinned AI SDK 7 runtime. Both set `maxRetries: 0`. The transport also prevents a provider SDK from silently repeating the same POST inside an attempt.

Text, reasoning, validated tool calls, finish reasons, usage, provider request IDs, and typed errors become ForgeX canonical events. Tool results retain their original text and call IDs. OpenAI-compatible reasoning continuation is preserved. An unfinished stream fails instead of becoming a successful partial answer. Managed agent streams remain private until an attempt completes, so retries cannot duplicate tool execution. Public streams retry only before any event has been exposed.

## Retry and timeout behavior

The session policy follows the supplied OpenCode retry implementation:

- Five retries after the initial attempt, for six attempts total, unless the caller's shared attempt budget or deadline permits fewer.
- The selected provider and model stay fixed throughout recovery.
- Initial backoff is 2 seconds, doubles per failure, and adds positive jitter of up to 25%.
- `retry-after-ms`, numeric `Retry-After`, and HTTP-date `Retry-After` are honored. Headerless backoff is capped at 30 seconds. Header-driven delays retain OpenCode's signed 32-bit timer limit.
- Rate limits, transient server failures, and transport failures can retry. Authentication, configuration, context overflow, content policy, exhausted quota, invalid tools, and host budget failures remain terminal.

ForgeX retains its durable-job constraints: waits longer than 30 seconds, long stalled attempts, and attempts that cannot fit the remaining parent deadline return to persisted job recovery. Provider reset hints are retained in canonical error details. They are never shortened to force an immediate retry. The old circuit's two-failure threshold no longer cuts off default OpenCode attempts; admission control remains active.

The standalone transport defaults to OpenCode's 300-second header/chunk windows when used directly. ForgeX supplies its explicit transport policy: 120 seconds to headers/first semantic event, 60 seconds between body reads, and 300 seconds total per attempt. Parent job/turn deadlines can shorten this. Cancellation reaches both network reads and the worker process. Incoming requests, response bytes, event lines, and managed stream buffers are bounded. Secret-bearing diagnostics and third-party console output are suppressed or redacted.

## Configuration and maintenance

Models settings includes secure provider options JSON. The API accepts it as `options` on `/models/providers/{provider_id}/configure`. API keys and option values use the operating-system credential store; provider-list responses expose only configuration status and environment-variable names. Passing `options: null` clears saved options. Examples:

| Provider | Example options / authentication |
| --- | --- |
| Azure | `{"resourceName":"my-resource"}` plus API key; `baseURL` can specify a gateway. |
| Azure Cognitive Services | `resourceName` resolves its Cognitive Services endpoint. |
| Bedrock | `{"region":"us-west-2","profile":"my-profile"}`; AWS profiles, environment credentials, web identity, container credentials, explicit credentials, or Bedrock bearer keys. AWS access keys are never mistaken for bearer tokens. |
| Vertex | `{"project":"my-project","location":"us-central1"}` plus Google application credentials. |
| Cloudflare Gateway | `{"accountId":"my-account","gatewayId":"my-gateway"}` plus gateway token. |
| Snowflake | `{"account":"my-account"}` plus PAT/token. Token limits and split streaming role fields are normalized. |
| Copilot | Provider bearer token; optional `enterpriseUrl` routes an enterprise account. Anthropic shim requests use bearer authentication and Copilot headers. |
| SAP AI Core | `AICORE_SERVICE_KEY` environment variable, or its JSON string in the secure API-key field. |

The catalog loads offline. Refresh it deliberately with `python scripts/refresh-model-catalog.py`, then run the engine tests and rebuild. Refresh fails if it encounters an uninstalled provider package. Every catalog SDK and model-specific SDK override is checked by the engine tests. New unsaved Gemini configurations use `gemini-2.5-flash`; explicit saved IDs remain authoritative.

Run `npm ci` and `npm run build:electron` after pulling these source changes. `build:harness` includes the engine build and generates harness provider descriptors. Desktop staging includes the engine, catalog, upstream license/provenance, and generated `THIRD_PARTY_NOTICES.md` for bundled npm dependencies. The source-release manifest requires the new source files.

## Verification and limits

Verification completed in this workspace:

- Full Python suite: **2,229 passed**, four Windows symlink tests skipped.
- Final router/backend/API subset: **93 passed**, including canonical SDK reset hints and bounded reasoning-only readiness probes.
- Bundled engine: **17 passed**, exercising native protocols, SDK 6 and SDK 7 streams, HTTP errors and reset hints, embedded OpenRouter errors, timeouts, tool/reasoning history, Bedrock SigV4, Copilot/Snowflake adaptations, internal-retry prevention, and credential redaction.
- Harness: **11 passed**; Electron: **43 passed**; UI state: **79 passed**; frontend type checking and lint passed.
- Standalone package smoke: native, SDK 6, and SDK 7 requests succeeded from an isolated directory without `node_modules`.
- Original OpenCode file hashes, the documented executor adaptation, and required source-release files were verified.

These are local protocol/transport tests. Provider account permissions, region availability, OAuth login/refresh workflows, and real billing behavior still require account-specific live qualification. The settings supports provider credentials; it does not implement OpenCode's interactive account-login UI. Local Ollama/LM Studio discovery remains available; remote model listings use the offline catalog plus saved discoveries and explicit IDs.

The dependency audit's resource-consumption and HTTP-parser findings were fixed with compatible provider-utils/Undici overrides. It still reports 11 affected packages, all from one SAP dependency chain ending in `node-forge`. [GHSA-86w9-cpqp-85rv](https://github.com/advisories/GHSA-86w9-cpqp-85rv) affects versions through 1.4.0 and lists no patched version. SAP's Java-keystore/signature path needs an upstream fix or a reviewed replacement before a security-clean production release. No production deployment or installer release was performed.
