import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { spawn } from "node:child_process";
const { execute, normalizeError, timeoutFetch, providerFetch, supportedSDKs, bedrockModelID } = createRequire(import.meta.url)("../../dist/model-router/engine.cjs");

async function server(handler) {
  const app = createServer((req, res) => {
    Promise.resolve().then(() => handler(req, res)).catch(error => {
      res.writeHead(500, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: { message: error.message } }));
    });
  });
  await new Promise(resolve => app.listen(0, "127.0.0.1", resolve));
  return { url: `http://127.0.0.1:${app.address().port}/v1`, close: () => new Promise(resolve => { app.close(resolve); app.closeAllConnections(); }) };
}
const input = (url, extra = {}) => ({ provider_id: "deepseek", model_id: "test-model", protocol: "openai-completions", npm: "@ai-sdk/openai-compatible", api_key: "fake-secret", base_url: url, prompt: "hello", temperature: 0.2, max_tokens: 50, policy: { header_ms: 3000, idle_ms: 3000 }, ...extra });

test("actual OpenCode chat protocol normalizes text, usage, finish, and tool calls", async () => {
  let body;
  const app = await server(async (req, res) => {
    assert.equal(req.headers.authorization, "Bearer fake-secret");
    const parts = []; for await (const chunk of req) parts.push(chunk);
    body = JSON.parse(Buffer.concat(parts));
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.end('data: {"choices":[{"index":0,"delta":{"content":"Hello"}}]}\n\n' +
      'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call-1","type":"function","function":{"name":"read_file","arguments":"{\\"path\\":\\"main.cpp\\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":12,"completion_tokens":5,"total_tokens":17}}\n\ndata: [DONE]\n\n');
  });
  try {
    const events = [];
    await execute(input(app.url), e => events.push(e));
    assert.equal(body.model, "test-model");
    assert.equal(body.stream, true);
    assert.equal(events.find(e => e.type === "text_delta").text, "Hello");
    assert.deepEqual(events.find(e => e.type === "tool_call_delta").arguments, { path: "main.cpp" });
    assert.equal(events.find(e => e.type === "done").usage.total_tokens, 17);
  } finally { await app.close(); }
});

test("OpenRouter repair preserves reasoning usage and forwards supported effort", async () => {
  let body;
  const app = await server(async (req, res) => {
    const parts = []; for await (const part of req) parts.push(part);
    body = JSON.parse(Buffer.concat(parts));
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.end('data: {"choices":[{"index":0,"delta":{"reasoning_content":"private thought"},"finish_reason":"length"}],"usage":{"prompt_tokens":3,"completion_tokens":8192,"completion_tokens_details":{"reasoning_tokens":8192},"total_tokens":8195}}\n\ndata: [DONE]\n\n');
  });
  try {
    const events = [];
    await execute(input(app.url, { provider_id: "openrouter", npm: "@openrouter/ai-sdk-provider",
      provider_options: { openrouter: { reasoning: { effort: "low" } } }, max_tokens: 16384 }), e => events.push(e));
    assert.deepEqual(body.reasoning, { effort: "low" });
    assert.equal(body.max_tokens, 16384);
    const done = events.find(e => e.type === "done");
    assert.equal(done.finish_reason, "length");
    assert.equal(done.usage.output_tokens, 8192);
    assert.equal(done.usage.reasoning_tokens, 8192);
  } finally { await app.close(); }
});

test("malformed native tool arguments retain a repairable code without partial calls", async () => {
  const app = await server((req, res) => {
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.end('data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"partial","type":"function","function":{"name":"write","arguments":"{\\\"path\\\":\\\"unfinished"}}]},"finish_reason":"length"}]}\n\ndata: [DONE]\n\n');
  });
  try {
    const events = [];
    await assert.rejects(execute(input(app.url), e => events.push(e)), error => {
      const normalized = normalizeError(error, "deepseek");
      assert.equal(normalized.code, "TOOL_ARGUMENTS_INVALID");
      assert.equal(normalized.retryable, false);
      return true;
    });
    assert.ok(!events.some(e => e.type === "tool_call_delta"));
  } finally { await app.close(); }
});

test("HTTP failures retain status and Retry-After with exactly one request", async () => {
  let requests = 0;
  const app = await server((req, res) => { requests++; res.writeHead(503, { "Retry-After": "90", "Content-Type": "application/json" }); res.end('{"error":{"message":"Overloaded"}}'); });
  try {
    await assert.rejects(execute(input(app.url), () => {}), error => {
      const result = normalizeError(error);
      assert.equal(result.status_code, 503);
      assert.equal(result.retry_after_ms, 90_000);
      assert.equal(result.retryable, true);
      return true;
    });
    assert.equal(requests, 1);
  } finally { await app.close(); }
});

test("quarantined SAP SDK cannot fall through to an HTTP-compatible provider", async () => {
  let sent = 0;
  const app = await server((req, res) => { sent++; res.end(); });
  try {
    await assert.rejects(execute(input(app.url, { provider_id: "sap-ai-core",
      npm: "@jerome-benoit/sap-ai-provider-v2" }), () => {}), /SAP AI Core is disabled/);
    assert.equal(sent, 0);
  } finally { await app.close(); }
});

test("context overflow is terminal", () => {
  const result = normalizeError({ statusCode: 400, message: "maximum context length exceeded", isRetryable: true });
  assert.equal(result.category, "context_overflow");
  assert.equal(result.retryable, false);
});

test("OpenAI uses Responses and normalizes its terminal event", async () => {
  const app = await server(async (req, res) => {
    assert.equal(req.url, "/v1/responses");
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks));
    assert.equal(body.input[0].role, "user");
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.end('data: {"type":"response.created","response":{"id":"resp-test","model":"gpt-4.1-mini","status":"in_progress","output":[]}}\n\n' +
      'data: {"type":"response.output_text.delta","item_id":"msg-1","output_index":0,"content_index":0,"delta":"OK"}\n\n' +
      'data: {"type":"response.completed","response":{"id":"resp-test","model":"gpt-4.1-mini","status":"completed","output":[{"id":"msg-1","type":"message","role":"assistant","content":[{"type":"output_text","text":"OK","annotations":[]}]}],"usage":{"input_tokens":4,"output_tokens":1,"total_tokens":5}}}\n\n');
  });
  try {
    const events = [];
    await execute(input(app.url, { provider_id: "openai", npm: "@ai-sdk/openai", model_id: "gpt-4.1-mini" }), e => events.push(e));
    assert.equal(events.find(e => e.type === "text_delta").text, "OK");
    assert.equal(events.find(e => e.type === "done").usage.total_tokens, 5);
  } finally { await app.close(); }
});

test("Anthropic uses Messages, its API key header, and inclusive usage", async () => {
  const app = await server(async (req, res) => {
    assert.equal(req.url, "/v1/messages");
    assert.equal(req.headers["x-api-key"], "fake-secret");
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.end('event: message_start\ndata: {"type":"message_start","message":{"id":"msg-test","type":"message","role":"assistant","model":"claude-test","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":3,"output_tokens":0,"cache_read_input_tokens":2,"cache_creation_input_tokens":1}}}\n\n' +
      'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n' +
      'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"OK"}}\n\n' +
      'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n' +
      'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":1}}\n\n' +
      'event: message_stop\ndata: {"type":"message_stop"}\n\n');
  });
  try {
    const events = [];
    await execute(input(app.url, { provider_id: "anthropic", npm: "@ai-sdk/anthropic" }), e => events.push(e));
    assert.equal(events.find(e => e.type === "text_delta").text, "OK");
    assert.equal(events.find(e => e.type === "done").usage.input_tokens, 6);
  } finally { await app.close(); }
});

test("Gemini uses its native API and API key header", async () => {
  const app = await server((req, res) => {
    assert.equal(req.url, "/v1/models/gemini-test:streamGenerateContent?alt=sse");
    assert.equal(req.headers["x-goog-api-key"], "fake-secret");
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.end('data: {"candidates":[{"content":{"parts":[{"text":"OK"}],"role":"model"},"finishReason":"STOP","index":0}],"usageMetadata":{"promptTokenCount":3,"candidatesTokenCount":1,"totalTokenCount":4}}\n\n');
  });
  try {
    const events = [];
    await execute(input(app.url, { provider_id: "google", npm: "@ai-sdk/google", model_id: "gemini-test" }), e => events.push(e));
    assert.equal(events.find(e => e.type === "text_delta").text, "OK");
    assert.equal(events.find(e => e.type === "done").usage.total_tokens, 4);
  } finally { await app.close(); }
});

test("embedded OpenRouter errors fail a successful HTTP response", async () => {
  const app = await server((req, res) => {
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.end('data: {"error":{"code":503,"message":"Provider is overloaded"}}\n\n');
  });
  try {
    await assert.rejects(execute(input(app.url, { provider_id: "openrouter", npm: "@openrouter/ai-sdk-provider" }), () => {}));
  } finally { await app.close(); }
});

test("header and idle deadlines abort the actual network read", async () => {
  const app = await server((req, res) => { if (req.url.endsWith("/idle")) { res.writeHead(200); res.write("start"); } });
  try {
    const fn = timeoutFetch({ header_ms: 80, idle_ms: 80 });
    await assert.rejects(fn(app.url), e => e.stage === "response_headers");
    const response = await fn(app.url + "/idle");
    await assert.rejects(response.text(), e => e.stage === "stream_idle");
  } finally { await app.close(); }
});

test("every catalog provider and model override has a supported or explicitly quarantined engine", async () => {
  const catalog = JSON.parse(await readFile("backend/model_router/data/models-dev.json", "utf8"));
  const quarantine = JSON.parse(await readFile("backend/model_router/data/provider-quarantine.json", "utf8"));
  for (const provider of Object.values(catalog)) {
    assert.ok(supportedSDKs.includes(provider.npm) || quarantine[provider.npm], `${provider.id}: ${provider.npm}`);
    for (const model of Object.values(provider.models)) if (model.provider?.npm) assert.ok(supportedSDKs.includes(model.provider.npm) || quarantine[model.provider.npm], `${provider.id}/${model.id}: ${model.provider.npm}`);
  }
});

test("tool history keeps plain results and reasoning continuation", async () => {
  let received;
  const app = await server(async (req, res) => {
    const chunks = []; for await (const chunk of req) chunks.push(chunk);
    received = JSON.parse(Buffer.concat(chunks));
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.end('data: {"choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n');
  });
  try {
    await execute(input(app.url, { messages: [
      { role: "user", content: "read the file" },
      { role: "assistant", content: "", reasoning_content: "inspect first", tool_calls: [{ id: "call-1", function: { name: "read_file", arguments: '{"path":"main.cpp"}' } }] },
      { role: "tool", tool_call_id: "call-1", content: "file contents" },
    ] }), () => {});
    assert.equal(received.messages[2].content, "file contents");
    assert.equal(received.messages[2].tool_call_id, "call-1");
    assert.equal(received.messages[1].reasoning_content, "inspect first");
  } finally { await app.close(); }
});

for (const [provider_id, npm] of [["mistral", "@ai-sdk/mistral"], ["salad-cloud", "@saladtechnologies-oss/ai-sdk-provider"]]) test(`${provider_id} SDK fallback streams through the bounded single-attempt transport`, async () => {
  let count = 0;
  const app = await server(async (req, res) => {
    count++;
    assert.equal(req.headers.authorization, "Bearer fake-secret");
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.end('data: {"id":"test","object":"chat.completion.chunk","created":1,"model":"test-model","choices":[{"index":0,"delta":{"content":"OK"},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}\n\ndata: [DONE]\n\n');
  });
  try {
    const events = [];
    await execute(input(app.url, { provider_id, npm }), event => events.push(event));
    assert.equal(count, 1);
    assert.equal(events.find(event => event.type === "text_delta").text, "OK");
    assert.equal(events.find(event => event.type === "done").usage.total_tokens, 4);
  } finally { await app.close(); }
});

test("Copilot Anthropic shim uses bearer headers and omits API-key auth", async () => {
  let seen;
  const fetch = providerFetch(input("", { provider_id: "github-copilot", npm: "@ai-sdk/anthropic", messages: [{ role: "assistant", content: "prior" }] }), async (url, init) => { seen = init; return new Response(); });
  await fetch("http://example.test/messages", { headers: { "x-api-key": "fake-secret" }, body: '{}' });
  assert.equal(seen.headers.get("x-api-key"), null);
  assert.equal(seen.headers.get("authorization"), "Bearer fake-secret");
  assert.equal(seen.headers.get("x-initiator"), "agent");
  assert.equal(seen.headers.get("X-GitHub-Api-Version"), "2026-06-01");
});

test("Snowflake transforms token limits and roles across chunk boundaries", async () => {
  let seen;
  const fetch = providerFetch(input("", { provider_id: "snowflake-cortex" }), async (url, init) => {
    seen = JSON.parse(init.body);
    return new Response(new ReadableStream({ start(controller) {
      for (const part of ['data: {"role":', '"","content":"OK"}\n\n']) controller.enqueue(new TextEncoder().encode(part));
      controller.close();
    } }), { headers: { "Content-Type": "text/event-stream" } });
  });
  const response = await fetch("http://example.test", { body: '{"max_tokens":40}' });
  assert.deepEqual(seen, { max_completion_tokens: 40 });
  assert.equal(await response.text(), 'data: {"role":"assistant","content":"OK"}\n\n');
});

test("provider SDKs cannot multiply requests with internal retries", async () => {
  let calls = 0;
  const fetch = providerFetch(input(""), async () => { calls++; return new Response("overloaded", { status: 503, headers: { "Retry-After": "90" } }); });
  await fetch("http://example.test/chat/completions", { method: "POST" });
  await assert.rejects(fetch("http://example.test/chat/completions", { method: "POST" }), error => {
    assert.equal(error.statusCode, 503);
    assert.equal(error.responseHeaders["retry-after"], "90");
    assert.equal(error.isRetryable, true);
    return true;
  });
  assert.equal(calls, 1);
});

test("Bedrock profiles and SigV4 authentication use the selected AWS region", async () => {
  assert.equal(bedrockModelID("anthropic.claude-test", "us-west-2"), "us.anthropic.claude-test");
  assert.equal(bedrockModelID("global.anthropic.claude-test", "us-west-2"), "global.anthropic.claude-test");
  const app = await server((req, res) => {
    assert.match(req.headers.authorization, /^AWS4-HMAC-SHA256 Credential=test-access\//);
    assert.match(req.headers.authorization, /us-west-2\/bedrock\/aws4_request/);
    assert.match(req.url, /us.anthropic.claude-test/);
    res.writeHead(503, { "Content-Type": "application/json" }); res.end('{"message":"Overloaded"}');
  });
  try {
    await assert.rejects(execute(input(app.url, { provider_id: "amazon-bedrock", npm: "@ai-sdk/amazon-bedrock", model_id: "anthropic.claude-test", api_key: undefined, options: { region: "us-west-2", credentials: { accessKeyId: "test-access", secretAccessKey: "test-secret", region: "us-west-2" } } }), () => {}), error => normalizeError(error).status_code === 503);
  } finally { await app.close(); }
});

test("worker redacts reflected credentials in provider errors", async () => {
  const app = await server((req, res) => { res.writeHead(401, { "Content-Type": "application/json" }); res.end('{"error":{"message":"Invalid fake-secret"}}'); });
  try {
    const child = spawn(process.execPath, ["dist/model-router/worker.cjs"], { stdio: ["pipe", "pipe", "pipe"], windowsHide: true });
    let output = ""; child.stdout.on("data", chunk => { output += chunk; }); child.stderr.resume();
    child.stdin.end(JSON.stringify(input(app.url)));
    const status = await new Promise(resolve => child.on("exit", resolve));
    assert.equal(status, 1);
    assert.ok(!output.includes("fake-secret"));
    assert.equal(JSON.parse(output.trim().split("\n").at(-1)).error.status_code, 401);
  } finally { await app.close(); }
});
