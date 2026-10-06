import { Cause, Effect, Layer, Stream } from "effect";
import { FetchHttpClient } from "effect/unstable/http";
import * as LLM from "../../third_party/opencode/packages/llm/src/llm.ts";
import { LLMClient } from "../../third_party/opencode/packages/llm/src/route/client.ts";
import { RequestExecutor } from "../../third_party/opencode/packages/llm/src/route/executor.ts";
import { ToolCallPart, ToolResultPart } from "../../third_party/opencode/packages/llm/src/schema/messages.ts";
import * as Providers from "../../third_party/opencode/packages/llm/src/providers/index.ts";
import { isContextOverflow } from "../../third_party/opencode/packages/llm/src/provider-error.ts";
import { jsonSchema, Output, streamText, tool } from "ai";
import quarantinedSDKs from "../../backend/model_router/data/provider-quarantine.json";

const sdkFactories = {
  "@ai-sdk/amazon-bedrock/mantle": () => import("@ai-sdk/amazon-bedrock/mantle").then(m => m.createBedrockMantle),
  "@ai-sdk/cohere": () => import("@ai-sdk/cohere").then(m => m.createCohere),
  "@ai-sdk/mistral": () => import("@ai-sdk/mistral").then(m => m.createMistral),
  "@ai-sdk/perplexity": () => import("@ai-sdk/perplexity").then(m => m.createPerplexity),
  "@ai-sdk/alibaba": () => import("@ai-sdk/alibaba").then(m => m.createAlibaba),
  "@ai-sdk/deepinfra": () => import("@ai-sdk/deepinfra").then(m => m.createDeepInfra),
  "@ai-sdk/groq": () => import("@ai-sdk/groq").then(m => m.createGroq),
  "@ai-sdk/cerebras": () => import("@ai-sdk/cerebras").then(m => m.createCerebras),
  "@ai-sdk/togetherai": () => import("@ai-sdk/togetherai").then(m => m.createTogetherAI),
  "@ai-sdk/google-vertex": () => import("@ai-sdk/google-vertex").then(m => m.createVertex),
  "@ai-sdk/google-vertex/anthropic": () => import("@ai-sdk/google-vertex/anthropic").then(m => m.createVertexAnthropic),
  "@ai-sdk/gateway": () => import("@ai-sdk/gateway").then(m => m.createGateway),
  "@ai-sdk/vercel": () => import("@ai-sdk/vercel").then(m => m.createVercel),
  "gitlab-ai-provider": () => import("gitlab-ai-provider").then(m => m.createGitLab),
  "venice-ai-sdk-provider": () => import("venice-ai-sdk-provider").then(m => m.createVenice),
  "@saladtechnologies-oss/ai-sdk-provider": () => import("@saladtechnologies-oss/ai-sdk-provider").then(m => m.createSaladCloud),
  "watsonx-ai-provider": () => import("watsonx-ai-provider").then(m => m.createWatsonx),
  "@aihubmix/ai-sdk-provider": () => import("@aihubmix/ai-sdk-provider").then(m => m.createAihubmix),
  "merge-gateway-ai-sdk-provider": () => import("merge-gateway-ai-sdk-provider").then(m => m.createMergeGateway),
  "@qvac/ai-sdk-provider": () => import("@qvac/ai-sdk-provider").then(m => m.createQvac),
};

export const supportedSDKs = [...Object.keys(sdkFactories), "@ai-sdk/openai", "@ai-sdk/openai-compatible", "@ai-sdk/anthropic", "@ai-sdk/azure", "@ai-sdk/google", "@ai-sdk/amazon-bedrock", "@openrouter/ai-sdk-provider", "@ai-sdk/xai", "@ai-sdk/github-copilot", "ai-gateway-provider"];
export { quarantinedSDKs };

// OpenCode's provider.ts region-specific Bedrock model loader. Existing
// cross-region profiles and ARNs remain authoritative.
export function bedrockModelID(id, region = "us-east-1") {
  if (/^(?:arn:|global\.|us\.|eu\.|jp\.|apac\.|au\.)/.test(id)) return id;
  if (region.startsWith("us-") && !region.startsWith("us-gov") && /nova-(?:micro|lite|pro|premier|2)|claude|deepseek\.r1/.test(id)) return "us." + id;
  if (/^eu-(?:west-[123]|north-1|central-1|south-[12])$/.test(region) && /claude|nova-lite|nova-micro|llama3|pixtral/.test(id)) return "eu." + id;
  if (["ap-southeast-2", "ap-southeast-4"].includes(region) && /anthropic\.claude-(?:sonnet-4-5|haiku)/.test(id)) return "au." + id;
  if (region.startsWith("ap-") && /claude|nova-lite|nova-micro|nova-pro/.test(id)) return (region === "ap-northeast-1" ? "jp." : "apac.") + id;
  return id;
}

export class TransportTimeout extends Error {
  constructor(stage, ms) { super(`Provider ${stage} timed out after ${ms}ms`); this.stage = stage; this.name = "ProviderTimeoutError"; }
}

// Adapted from OpenCode provider.ts timeoutFetch/wrapSSE. All body reads have
// idle deadlines, including Bedrock's binary event stream and error bodies.
export function timeoutFetch(policy = {}, outerSignal, observe = () => {}) {
  return async (input, init = {}) => {
    const abort = new AbortController();
    const headersMs = policy.header_ms ?? 300_000;
    const idleMs = policy.idle_ms ?? 300_000;
    let timer = setTimeout(() => { observe({ type: "transport_error", stage: "response_headers" }); abort.abort(new TransportTimeout("response_headers", headersMs)); }, headersMs);
    const signal = AbortSignal.any([abort.signal, ...(outerSignal ? [outerSignal] : []), ...(init.signal ? [init.signal] : [])]);
    let res;
    try {
      res = await fetch(input, { ...init, signal });
    } finally { clearTimeout(timer); }
    observe({ type: "transport", status: res.status, request_id: res.headers.get("x-request-id") ?? res.headers.get("request-id") ?? res.headers.get("x-amzn-requestid") ?? res.headers.get("cf-ray") });
    if (!res.body) return res;
    const reader = res.body.getReader();
    let bytes = 0;
    const body = new ReadableStream({
      async pull(controller) {
        timer = setTimeout(() => { observe({ type: "transport_error", stage: "stream_idle" }); abort.abort(new TransportTimeout("stream_idle", idleMs)); }, idleMs);
        try {
          const part = await reader.read();
          if (part.done) { controller.close(); return; }
          bytes += part.value.byteLength;
          if (bytes > (policy.max_response_bytes ?? 16_777_216)) throw new Error("Provider response exceeds limit");
          observe({ type: "transport_chunk" });
          controller.enqueue(part.value);
        } catch (error) { controller.error(signal.aborted ? signal.reason : error); await reader.cancel().catch(() => {}); }
        finally { clearTimeout(timer); }
      },
      async cancel(reason) { clearTimeout(timer); abort.abort(reason); await reader.cancel(reason).catch(() => {}); },
    });
    return new Response(body, { status: res.status, statusText: res.statusText, headers: res.headers });
  };
}

// Provider adapters from OpenCode's Copilot and Snowflake loaders. Keep these
// around the bounded fetch so custom authentication cannot bypass deadlines.
export function providerFetch(input, upstream) {
  const posts = new Map();
  async function send(url, init) {
    const method = init.method ?? (url instanceof Request ? url.method : "GET");
    const key = url instanceof Request ? url.url : String(url);
    if (method.toUpperCase() === "POST" && posts.has(key)) {
      const previous = posts.get(key);
      const error = new Error("Provider SDK requested an internal HTTP retry; ForgeX owns recovery");
      error.statusCode = previous?.status;
      error.responseHeaders = previous ? Object.fromEntries(previous.headers) : undefined;
      error.isRetryable = !previous || previous.status >= 500 || previous.status === 429;
      throw error;
    }
    if (method.toUpperCase() === "POST") posts.set(key, undefined);
    const response = await upstream(url, init);
    if (method.toUpperCase() === "POST") posts.set(key, response);
    return response;
  }
  return async (url, init = {}) => {
    let body;
    if (typeof init.body === "string") {
      try { body = JSON.parse(init.body); } catch { /* Not a JSON request. */ }
    }
    if (input.provider_id.startsWith("github-copilot")) {
      const headers = new Headers(init.headers);
      headers.delete("x-api-key");
      headers.set("Authorization", `Bearer ${input.api_key}`);
      headers.set("X-GitHub-Api-Version", "2026-06-01");
      const messages = input.messages ?? [];
      headers.set("x-initiator", messages.some(message => ["assistant", "tool"].includes(message.role)) ? "agent" : "user");
      if (messages.some(message => Array.isArray(message.content) && message.content.some(part => part.type === "image_url"))) headers.set("Copilot-Vision-Request", "true");
      if (input.npm === "@ai-sdk/anthropic") headers.set("anthropic-beta", "interleaved-thinking-2025-05-14");
      if (body && input.model_id.includes("gpt")) {
        delete body.max_tokens; delete body.max_completion_tokens; delete body.max_output_tokens;
      }
      init = { ...init, headers, ...(body ? { body: JSON.stringify(body) } : {}) };
    }
    if (input.provider_id !== "snowflake-cortex") return send(url, init);
    if (body && "max_tokens" in body) {
      body.max_completion_tokens = body.max_tokens; delete body.max_tokens;
      init = { ...init, body: JSON.stringify(body) };
    }
    const response = await send(url, init);
    if (response.status === 400) {
      const text = await response.text();
      if (text.toLowerCase().includes("conversation complete")) {
        return new Response('data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n', { status: 200, headers: { "Content-Type": "text/event-stream" } });
      }
      return new Response(text, { status: response.status, statusText: response.statusText, headers: response.headers });
    }
    if (!response.body || !response.headers.get("content-type")?.includes("text/event-stream")) return response;
    const decoder = new TextDecoder();
    const encoder = new TextEncoder();
    let pending = "";
    const transform = new TransformStream({
      transform(chunk, controller) {
        pending += decoder.decode(chunk, { stream: true });
        if (pending.length > 1_048_576) throw new Error("Snowflake stream frame exceeds limit");
        const end = pending.lastIndexOf("\n");
        if (end < 0) return;
        controller.enqueue(encoder.encode(pending.slice(0, end + 1).replace(/"role"\s*:\s*""/g, '"role":"assistant"')));
        pending = pending.slice(end + 1);
      },
      flush(controller) { controller.enqueue(encoder.encode((pending + decoder.decode()).replace(/"role"\s*:\s*""/g, '"role":"assistant"'))); },
    });
    return new Response(response.body.pipeThrough(transform), { status: response.status, statusText: response.statusText, headers: response.headers });
  };
}

function nativeModel(input) {
  if (quarantinedSDKs[input.npm]) throw new Error(quarantinedSDKs[input.npm]);
  const opts = { ...input.options, ...(input.api_key ? { apiKey: input.api_key } : {}), ...(input.base_url ? { baseURL: input.base_url } : {}), headers: input.headers };
  const npm = input.npm;
  const id = input.model_id;
  if (input.provider_id === "amazon-bedrock" && npm !== "@ai-sdk/amazon-bedrock/mantle") return Providers.AmazonBedrock.configure(opts).model(bedrockModelID(id, opts.region));
  if (input.provider_id === "cloudflare-workers-ai") return Providers.CloudflareWorkersAI.configure(opts).model(id);
  if (input.provider_id === "cloudflare-ai-gateway") return Providers.CloudflareAIGateway.configure({ ...opts, gatewayApiKey: input.api_key, apiKey: undefined }).model(id);
  if (input.provider_id.startsWith("github-copilot") && npm !== "@ai-sdk/anthropic") return Providers.GitHubCopilot.configure(opts).model(id);
  if (npm === "@ai-sdk/azure") return Providers.Azure.configure(opts).model(id);
  if (npm === "@ai-sdk/google" || input.protocol === "gemini") return Providers.Google.configure(opts).model(id);
  if (npm === "@ai-sdk/anthropic" || input.protocol === "anthropic-messages") return Providers.Anthropic.configure(opts).model(id);
  if (npm === "@openrouter/ai-sdk-provider") return Providers.OpenRouter.configure(opts).model(id);
  if (npm === "@ai-sdk/xai") return Providers.XAI.configure(opts).model(id);
  if (npm === "@ai-sdk/openai" || input.protocol === "openai-responses") return Providers.OpenAI.configure(opts).responses(id);
  if (sdkFactories[npm]) return undefined;
  return Providers.OpenAICompatible.configure({ ...opts, provider: input.provider_id, baseURL: input.base_url }).model(id);
}

class MessageHistoryError extends Error {
  constructor(message) {
    super(message);
    this.name = "MessageHistoryError";
    this.code = "MODEL_MESSAGE_HISTORY_INVALID";
  }
}

function historyToolCalls(message) {
  const calls = message.tool_calls ?? [];
  if (!Array.isArray(calls)) throw new MessageHistoryError("Assistant tool calls must be an array.");
  return calls.map(call => {
    if (!call || typeof call !== "object" || Array.isArray(call)) {
      throw new MessageHistoryError("Assistant tool call must be an object.");
    }
    // The Python planner persists ForgeX's canonical {id, name, arguments}
    // shape. Older callers provide OpenAI's nested function envelope. Both
    // provider execution paths convert these at this single boundary.
    const fn = call.function && typeof call.function === "object" ? call.function : {};
    const name = call.name ?? fn.name;
    if (typeof call.id !== "string" || !call.id.trim()) {
      throw new MessageHistoryError("Assistant tool call requires an ID.");
    }
    if (typeof name !== "string" || !name.trim()) {
      throw new MessageHistoryError("Assistant tool call requires a name.");
    }
    const raw = Object.hasOwn(call, "arguments") ? call.arguments
      : Object.hasOwn(fn, "arguments") ? fn.arguments : {};
    let args = raw;
    if (typeof raw === "string") {
      try { args = JSON.parse(raw || "{}"); }
      catch { throw new MessageHistoryError("Assistant tool-call arguments must be valid JSON."); }
    }
    if (!args || typeof args !== "object" || Array.isArray(args)) {
      throw new MessageHistoryError("Assistant tool-call arguments must be an object.");
    }
    return { id: call.id, name, input: args };
  });
}

function nativeMessages(input) {
  const system = input.system_prompt ? [input.system_prompt] : [];
  const messages = [];
  const toolNames = new Map(Object.entries(input.tool_names ?? {}));
  for (const msg of input.messages ?? [{ role: "user", content: input.prompt }]) {
    if (msg.role === "system" || msg.role === "developer") { system.push(String(msg.content)); continue; }
    if (msg.role === "tool") {
      messages.push({ role: "tool", content: [ToolResultPart.make({ id: msg.tool_call_id, name: msg.name ?? toolNames.get(msg.tool_call_id) ?? "tool", result: msg.content, resultType: "text" })] });
      continue;
    }
    const content = typeof msg.content === "string" ? [{ type: "text", text: msg.content }] : (msg.content ?? []).map(part => {
      if (part.type === "text") return part;
      if (part.type === "image_url") {
        const url = typeof part.image_url === "string" ? part.image_url : part.image_url.url;
        const match = /^data:([^;]+);base64,(.*)$/.exec(url);
        return { type: "media", mediaType: match?.[1] ?? "image/png", data: match?.[2] ?? url };
      }
      throw new Error(`Unsupported message content ${part.type}`);
    });
    for (const call of historyToolCalls(msg)) {
      toolNames.set(call.id, call.name);
      content.push(ToolCallPart.make({ id: call.id, name: call.name, input: call.input }));
    }
    messages.push({ role: msg.role, content, ...(msg.reasoning_content ? { native: { openaiCompatible: { reasoning_content: msg.reasoning_content } } } : {}) });
  }
  return { system: system.length ? system.join("\n\n") : undefined, messages };
}

function usage(value = {}) {
  const input = typeof value.inputTokens === "number" ? value.inputTokens : value.inputTokens?.total;
  const output = typeof value.outputTokens === "number" ? value.outputTokens : value.outputTokens?.total;
  const reasoning = value.reasoningTokens ?? value.outputTokens?.reasoning ?? value.outputTokenDetails?.reasoningTokens;
  return Object.fromEntries(Object.entries({ input_tokens: input, output_tokens: output, reasoning_tokens: reasoning, total_tokens: value.totalTokens ?? (input !== undefined && output !== undefined ? input + output : undefined) }).filter(([, v]) => Number.isInteger(v) && v >= 0));
}

export async function execute(input, emit, signal) {
  if (input.provider_id === "amazon-bedrock" && !input.api_key && !input.options?.credentials) {
    const { fromNodeProviderChain } = await import("@aws-sdk/credential-providers");
    const credentials = await fromNodeProviderChain(input.options?.profile ? { profile: input.options.profile } : {})();
    input = { ...input, options: { ...input.options, credentials: { ...credentials, region: input.options?.region ?? process.env.AWS_REGION ?? "us-east-1" } } };
  }
  const transport = providerFetch(input, timeoutFetch(input.policy, signal, emit));
  const model = nativeModel(input);
  if (!model) return executeSDK(input, transport, emit, signal);
  const tools = (input.tools ?? []).map(item => ({ name: item.function?.name ?? item.name, description: item.function?.description ?? item.description ?? "", inputSchema: item.function?.parameters ?? item.inputSchema ?? { type: "object", properties: {} } }));
  const request = LLM.request({
    model, ...nativeMessages(input), tools,
    toolChoice: input.tool_choice ?? "auto",
    generation: { ...(input.temperature_supported === false ? {} : { temperature: input.temperature }), topP: input.top_p, maxTokens: Math.min(input.max_tokens ?? 4096, input.output_limit ?? Infinity) },
    providerOptions: input.provider_options,
    responseFormat: input.response_format,
    http: input.http_body ? { body: input.http_body } : undefined,
  });
  // Session retries belong to ForgeX so cancellation, budgets, and durable
  // recovery see every physical attempt. No hidden SDK retries multiply them.
  const layer = LLMClient.layer.pipe(Layer.provide(RequestExecutor.singleAttemptLayer.pipe(Layer.provide(FetchHttpClient.layer))));
  const callIds = new Map();
  const program = LLM.stream(request).pipe(Stream.runForEach(event => Effect.promise(async () => {
    if (event.type === "text-delta") await emit({ type: "text_delta", text: event.text });
    if (event.type === "reasoning-delta") await emit({ type: "reasoning_delta", text: event.text });
    if (event.type === "tool-call") {
      const index = callIds.size; callIds.set(event.id, index);
      await emit({ type: "tool_call_delta", tool_call_index: index, tool_call_id: event.id, tool_name: event.name, arguments: event.input });
    }
    if (event.type === "finish") await emit({ type: "done", finish_reason: event.reason, usage: usage(event.usage) });
    if (event.type === "provider-error") {
      const error = new Error(event.message);
      error.isRetryable = event.retryable;
      error.reason = { ...event, _tag: event.classification === "context-overflow" ? "InvalidRequest" : "ProviderInternal", retryable: event.retryable };
      throw error;
    }
  })), Effect.provide(layer), Effect.provideService(FetchHttpClient.Fetch, transport));
  const exit = await Effect.runPromiseExit(program, { signal });
  if (exit._tag === "Failure") {
    const error = Cause.findErrorOption(exit.cause);
    throw signal?.aborted ? signal.reason : error._tag === "Some" ? error.value : new Error(Cause.pretty(exit.cause));
  }
}

async function executeSDK(input, transport, emit, signal) {
  const factory = await sdkFactories[input.npm]();
  if (typeof factory !== "function") throw new Error(`Provider factory missing for ${input.npm}`);
  const opts = { ...input.options, apiKey: input.api_key, ...(input.base_url ? { baseURL: input.base_url } : {}), headers: input.headers, fetch: transport };
  if (input.npm === "@ai-sdk/amazon-bedrock/mantle") Object.assign(opts, input.options?.credentials ?? {});
  if (input.npm === "gitlab-ai-provider") opts.apiKey = input.api_key;
  const provider = await factory(opts);
  const model = input.npm === "@ai-sdk/amazon-bedrock/mantle" && !input.model_id.includes("safeguard") && provider.responses
    ? provider.responses(input.model_id)
    : provider.languageModel ? provider.languageModel(input.model_id) : provider(input.model_id);
  // OpenCode's AI SDK 6 remains the default. A catalog package targeting the
  // v4 model interface needs the matching runtime (currently Salad Cloud).
  const sdk = model.specificationVersion === "v4" ? await import("ai-sdk-v7") : { jsonSchema, Output, streamText, tool };
  const toolNames = new Map(Object.entries(input.tool_names ?? {}));
  const messages = (input.messages ?? [{ role: "user", content: input.prompt }]).map(msg => {
    if (msg.role === "tool") return { role: "tool", content: [{ type: "tool-result", toolCallId: msg.tool_call_id, toolName: msg.name ?? toolNames.get(msg.tool_call_id) ?? "tool", output: { type: "text", value: String(msg.content) } }] };
    const content = typeof msg.content === "string" ? msg.content : (msg.content ?? []).map(part => part.type === "image_url" ? { type: "image", image: new URL(typeof part.image_url === "string" ? part.image_url : part.image_url.url) } : part);
    const role = msg.role === "developer" ? "system" : msg.role;
    const calls = historyToolCalls(msg);
    if (!calls.length) return { ...msg, role, content };
    return { role, content: [...(typeof content === "string" ? (content ? [{ type: "text", text: content }] : []) : content), ...calls.map(call => {
      toolNames.set(call.id, call.name);
      return { type: "tool-call", toolCallId: call.id, toolName: call.name, input: call.input };
    })] };
  });
  const tools = Object.fromEntries((input.tools ?? []).map(item => [item.function.name, sdk.tool({ description: item.function.description, inputSchema: sdk.jsonSchema(item.function.parameters) })]));
  const result = sdk.streamText({ model, messages, system: input.system_prompt, tools, toolChoice: input.tool_choice,
    temperature: input.temperature_supported === false ? undefined : input.temperature, topP: input.top_p,
    maxOutputTokens: Math.min(input.max_tokens ?? 4096, input.output_limit ?? Infinity),
    output: input.response_format?.schema ? sdk.Output.object({ schema: sdk.jsonSchema(input.response_format.schema) }) : undefined,
    providerOptions: input.provider_options, maxRetries: 0, abortSignal: signal });
  let index = 0;
  for await (const event of result.fullStream) {
    if (event.type === "error") throw event.error;
    if (event.type === "text-delta") await emit({ type: "text_delta", text: event.text });
    if (event.type === "reasoning-delta") await emit({ type: "reasoning_delta", text: event.text });
    if (event.type === "tool-call") await emit({ type: "tool_call_delta", tool_call_index: index++, tool_call_id: event.toolCallId, tool_name: event.toolName, arguments: event.input });
    if (event.type === "finish") await emit({ type: "done", finish_reason: event.finishReason, usage: usage(event.totalUsage) });
  }
}

export function normalizeError(error, providerId) {
  if (error instanceof MessageHistoryError) {
    return { category: "invalid_request", message: error.message,
      code: error.code, retryable: false };
  }
  const reason = error.reason ?? error;
  const http = reason.http?.response;
  const status = reason.status ?? http?.status ?? error.statusCode;
  const text = reason.message ?? error.message ?? "Provider request failed";
  const body = reason.http?.body ?? error.responseBody ?? "";
  const overflow = reason.classification === "context-overflow" || isContextOverflow(text + " " + body);
  const category = overflow ? "context_overflow" : ({ Authentication: "authentication", RateLimit: "rate_limit", QuotaExceeded: "quota_exceeded", ContentPolicy: "content_policy", InvalidRequest: "invalid_request", InvalidProviderOutput: "invalid_provider_output", Transport: "transport", ProviderInternal: "provider" }[reason._tag] ?? (status === 401 || status === 403 ? "authentication" : status === 429 ? "rate_limit" : "provider"));
  const invalidTool = reason._tag === "InvalidProviderOutput" && /^Invalid JSON input for .+ tool call /.test(String(text));
  // Only bounded diagnostics leave the engine; do not echo request bodies or
  // credentials. OpenCode's executor already redacts native HTTP failures.
  return { category, message: String(text).slice(0, 1024), status_code: status, retryable: !overflow && !invalidTool && (reason.retryable === true || error.isRetryable === true || status === 429 || status >= 500 || providerId === "openai" && status === 404), retry_after_ms: error.retryAfterMs, response_headers: http?.headers ?? error.responseHeaders, request_id: reason.http?.requestId, timeout_stage: error.stage, code: overflow ? "CONTEXT_OVERFLOW" : invalidTool ? "TOOL_ARGUMENTS_INVALID" : "PROVIDER_REQUEST_FAILED" };
}
