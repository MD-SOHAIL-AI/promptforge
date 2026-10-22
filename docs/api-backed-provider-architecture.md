# API-Backed Provider Architecture

## Phase 2.5.8.10A live-smoke accounting

The guarded QA summary now exposes only safe booleans and counts for provider flags, confirmation, authorization presence, outbound request attempt/count, ToolPlan validity, tool execution, diff counts, exact artifact checks, review creation, and downstream authority. The adapter sets outbound request count to one immediately before its single transport call and has no retry path.

Missing authorization returns before sandbox creation or transport. A successful future live response must still traverse the unchanged ForgeX schema, policy, sandbox, exact-diff, and review gates.

## Planner-only boundary

```text
Guarded QA entry
  -> disabled-by-default OpenAI adapter
  -> Responses API structured JSON
  -> strict ToolPlan parser
  -> ForgeX tool policy
  -> ForgeX sandbox executor
  -> exact diff and Bridge Review
  -> optional existing patch export / verify / preflight
  -> user approval boundary
```

The remote model is a planner. It receives task text, a sanitized relative manifest, allowed tool names, and policy metadata in memory. It receives no active-workspace root, shell, network tool, dependency installer, patch apply operation, build operation, or flash operation.

## OpenAI adapter

`OpenAIApiProvider` is a non-streaming spike using the Responses API. The requested default is `gpt-4.1-mini`, which supports the Responses endpoint and Structured Outputs. The model may be overridden through `FORGEX_OPENAI_MODEL` for an explicitly guarded smoke.

Normal execution is disabled. A request requires all of:

- `--confirm-real-api`
- `FORGEX_ENABLE_API_PROVIDER_SPIKE=1`
- `FORGEX_ENABLE_OPENAI_API_PROVIDER=1`
- API authorization supplied through `OPENAI_API_KEY` in the process environment

The adapter does not inspect credential files. It sends `store: false`, does not stream, caps response bytes and timeout, and records only provider ID, model ID, run ID, tool count, classification, and a safe error code.

## Canonical ToolPlan

```json
{
  "tool_calls": [
    {
      "tool": "write_file",
      "arguments": {
        "path": "FORGEX_API_PROVIDER_SMOKE.txt",
        "content": "ForgeX real API provider smoke completed.\n"
      }
    }
  ]
}
```

The root and nested objects reject additional properties. The smoke array contains exactly one call. ForgeX reparses and revalidates the response even when the API reports schema conformance. Markdown, code fences, unknown tools, forbidden authority requests, extra calls, unsafe paths, and non-exact content fail closed before tool execution.

## Events and persistence

API request, plan parse, runtime validation, tool execution, review creation, and terminal smoke events contain only types, sequence numbers, counts, and classifications. No request body, response body, task text, file content, authorization value, or absolute credential location is persisted.

The OpenAI adapter is not present in normal UI provider selection and is not production eligible.

## Phase 2.5.8.12 multi-provider planner layer

The product Agent Runtime now has a shared chat-completions-style planner adapter for Gemini, Groq, OpenRouter, OpenAI, and NVIDIA NIM. These providers are not file executors. They may only return `forgex.toolplan.v1` JSON, and ForgeX still owns the tool runtime, sandbox, diff, review, and apply boundary.

All providers are disabled by default and require the global API provider flag, provider-specific flag, key presence, model configuration, and explicit real-API confirmation for the guarded QA smoke. Detection reports sanitized booleans and does not call provider APIs.

The product UI can list provider status and select a planner, but it never displays key values, raw prompts, raw responses, headers, account identity, or credential paths.
