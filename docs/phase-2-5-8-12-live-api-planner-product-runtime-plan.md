# Phase 2.5.8.12 — Multi API Provider Planner + Live Product Runtime Smoke Plan

## Goal

Add disabled-by-default API-backed planner providers for Gemini, Groq, OpenRouter, OpenAI, and NVIDIA NIM, then route them through the Phase 2.5.8.11 product Agent Runtime.

The provider is a planner only. ForgeX owns tool execution, sandboxing, path validation, diff capture, review creation, and the apply boundary.

## Provider registry

The registry exposes:

- `fake_planner`
- `gemini`
- `groq`
- `openrouter`
- `openai`
- `nvidia_nim`
- paused/reference local providers: `agy`, `codex`, `opencode`

API providers are disabled by default, non-production, and require:

- `FORGEX_ENABLE_AGENT_RUNTIME=1`
- `FORGEX_ENABLE_API_PROVIDERS=1`
- `FORGEX_ENABLE_GENERIC_BRIDGE_API=1`
- `FORGEX_ENABLE_GENERIC_BRIDGE_ROUTING=1`
- provider-specific enable flag
- provider API key in the process environment
- configured model or safe default
- explicit `--confirm-real-api` for QA smoke

## ToolPlan contract

The product runtime uses `forgex.toolplan.v1`:

```json
{
  "version": "forgex.toolplan.v1",
  "summary": "short safe summary",
  "tool_calls": [
    {
      "id": "call_1",
      "type": "write_file",
      "path": "API_PROVIDER_SMOKE.txt",
      "content": "ForgeX API planner product runtime smoke completed."
    }
  ],
  "final": false
}
```

For provider smoke, ForgeX accepts exactly one `write_file` call to `API_PROVIDER_SMOKE.txt` with exact content. Markdown, prose, unknown tools, extra calls, absolute paths, parent traversal, hidden/auth paths, shell/network/build/flash/apply requests, and unsafe content fail closed.

## QA commands

- `npm.cmd run qa:api-providers-detect`
- `npm.cmd run qa:api-product-runtime-smoke -- --provider gemini --confirm-real-api`
- `npm.cmd run qa:api-product-runtime-smoke -- --provider groq --confirm-real-api`
- `npm.cmd run qa:api-product-runtime-smoke -- --provider openrouter --confirm-real-api`
- `npm.cmd run qa:api-product-runtime-smoke -- --provider openai --confirm-real-api`
- `npm.cmd run qa:api-product-runtime-smoke -- --provider nvidia_nim --confirm-real-api`

Detection does not call APIs. Smoke is guarded and emits sanitized booleans, counts, and classifications only.

## Non-goals

- No local CLI provider execution.
- No OpenCode provider.
- No model-owned filesystem writes.
- No shell/network/install tools exposed to the model.
- No auto-apply, auto-build, or auto-flash.
- No raw prompt, raw response, request body, response body, API key, or credential path persistence.
