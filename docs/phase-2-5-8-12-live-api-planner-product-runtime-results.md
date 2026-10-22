# Phase 2.5.8.12 — Multi API Provider Planner + Live Product Runtime Smoke Results

## Result

ForgeX now has a shared API planner adapter and product-runtime registry entries for Gemini, Groq, OpenRouter, OpenAI, and NVIDIA NIM. All are disabled by default and planner-only. The product Agent Runtime can select an API planner provider, validate its strict ToolPlan, execute the resulting `write_file` through `ForgeXToolRuntime`, capture a managed-sandbox diff, and create a persistent Bridge Review.

The automated proof uses mocked transports. No real API call was made during automated tests or safety verification.

## Providers

| Provider | Key env | Model env | Default model | Flag |
|---|---|---|---|---|
| Gemini | `GEMINI_API_KEY` | `FORGEX_GEMINI_MODEL` | `gemini-2.5-flash` | `FORGEX_ENABLE_GEMINI_PROVIDER` |
| Groq | `GROQ_API_KEY` | `FORGEX_GROQ_MODEL` | `llama-3.1-8b-instant` | `FORGEX_ENABLE_GROQ_PROVIDER` |
| OpenRouter | `OPENROUTER_API_KEY` | `FORGEX_OPENROUTER_MODEL` | `openrouter/auto` | `FORGEX_ENABLE_OPENROUTER_PROVIDER` |
| OpenAI | `OPENAI_API_KEY` | `FORGEX_OPENAI_MODEL` | `gpt-4.1-mini` | `FORGEX_ENABLE_OPENAI_PROVIDER` |
| NVIDIA NIM | `NVIDIA_API_KEY` or `NIM_API_KEY` | `FORGEX_NVIDIA_NIM_MODEL` | operator-configured | `FORGEX_ENABLE_NVIDIA_NIM_PROVIDER` |

All provider entries report only sanitized state: key present yes/no, model configured yes/no, routeable yes/no, and classification.

## Product runtime integration

- `GET /agent-runtime/providers` now exposes fake and API planner statuses.
- `POST /agent-runtime/runs` accepts `provider_id` for `fake_planner`, `gemini`, `groq`, `openrouter`, `openai`, and `nvidia_nim`.
- API providers still require flags, key presence, model configuration, and confirmation before outbound transport.
- The Agent Runtime UI now offers provider selection and displays sanitized provider status.

## Verification performed

- Focused backend selector: 72 passed.
- Backend compileall: passed.
- Full backend: 1,747 passed, 10 skipped.
- API provider detection: passed; `network_attempted=false`.
- Guarded Gemini product smoke without confirmation: `API_PROVIDER_CONFIRMATION_REQUIRED`, outbound request count `0`.
- Guarded Gemini product smoke with flags and no key: `API_PROVIDER_KEY_MISSING`, outbound request count `0`.
- Fake product runtime smoke: `TOOL_RUNTIME_PASS`, 1 created, 0 modified, 0 deleted, review created, active workspace unchanged.
- Fake tool runtime smoke: `TOOL_RUNTIME_PASS`, 1 created, 0 modified, 0 deleted, review created, active workspace unchanged.
- Frontend typecheck: passed.
- Frontend production build: passed; 4 static pages generated.
- Electron build: passed.
- Electron tests: 10 passed.
- Safety scan: 184 passed.

## Real smoke status

No live provider smoke was attempted in this run because no operator-provided key/model confirmation was supplied. This is intentional: real outbound calls remain guarded by feature flags, provider-specific flags, key presence, model configuration, and `--confirm-real-api`.

## Safety result

- Active workspace writes remain denied.
- Sandbox writes go through `ForgeXToolRuntime`.
- Shell, network-as-model-tool, dependency install, build, flash, and apply authority remain denied.
- AGY and Codex remain paused.
- OpenCode remains reference-only and was not added as a provider.
- Raw prompt, raw response, API keys, headers, and credential paths are not persisted.

## Remaining limitation

The multi-provider layer is proven by mocked transports. A real provider smoke is still pending operator key/model setup.

## Phase 2.5.8.12C Codex bridge note

The later Codex subscription bridge work is a separate QA-only CLI-auth path. It does not execute or enable any API planner, does not change API-provider flags, and does not route through the normal product Agent Runtime. Even after an exact Codex sandbox pass, API planner policy remains unchanged and Codex production routing stays disabled.

The 12C retry returned `CODEX_NATIVE_UNKNOWN_SAFE_FAILURE`; no Codex review or product route was created. API planner validation remains the recommended continuation.

## Recommended next phase

If a real provider key is available: Phase 2.5.8.13 — API Provider Review Pipeline Hardening.

If provider keys/model access are still blocked: Phase 2.5.8.12B — Operator API Key Setup and Provider Availability Matrix.
