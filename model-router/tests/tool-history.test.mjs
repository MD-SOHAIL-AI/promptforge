import assert from "node:assert/strict";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { test } from "node:test";

const { execute, normalizeError } = createRequire(import.meta.url)("../../dist/model-router/engine.cjs");
const providers = [
  ["openrouter", "@openrouter/ai-sdk-provider"],
  ["mistral", "@ai-sdk/mistral"],
];
const argumentsObject = { path: "src/main.cpp", limit: 10 };
const calls = {
  canonical: { id: "call-1", name: "read", arguments: argumentsObject },
  canonicalJSON: { id: "call-1", name: "read", arguments: JSON.stringify(argumentsObject) },
  wire: { id: "call-1", type: "function", function: { name: "read", arguments: JSON.stringify(argumentsObject) } },
  wireObject: { id: "call-1", function: { name: "read", arguments: argumentsObject } },
};

async function server(reply) {
  const received = [];
  const app = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    received.push(JSON.parse(Buffer.concat(chunks)));
    response.writeHead(200, { "Content-Type": "text/event-stream" });
    response.end(reply ?? 'data: {"id":"fixture","object":"chat.completion.chunk","created":1,"model":"test-model","choices":[{"index":0,"delta":{"content":"Verified."},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}}\n\ndata: [DONE]\n\n');
  });
  await new Promise(resolve => app.listen(0, "127.0.0.1", resolve));
  return { received, url: `http://127.0.0.1:${app.address().port}/v1`,
    close: () => new Promise(resolve => { app.close(resolve); app.closeAllConnections(); }) };
}

function input(provider_id, npm, url, call) {
  return { provider_id, npm, base_url: url, api_key: "fixture", model_id: "test-model",
    protocol: "openai-completions", max_tokens: 50, policy: { header_ms: 3000, idle_ms: 3000 },
    tools: [{ type: "function", function: { name: "read", description: "Read a file",
      parameters: { type: "object", properties: { path: { type: "string" }, limit: { type: "integer" } } } } }],
    messages: [{ role: "user", content: "Read this file." },
      { role: "assistant", content: "Checking.", tool_calls: [call] },
      { role: "tool", tool_call_id: "call-1", content: "void setup() {}" }],
  };
}

for (const [provider_id, npm] of providers) {
  test(`${provider_id}: opaque IDs do not collide with object prototype properties`, async () => {
    const app = await server();
    try {
      const payload = input(provider_id, npm, app.url, { ...calls.canonical, id: "__proto__" });
      payload.messages[2].tool_call_id = "__proto__";
      await execute(payload, () => {});
      assert.equal(app.received[0].messages[1].tool_calls[0].id, "__proto__");
      assert.equal(app.received[0].messages[2].tool_call_id, "__proto__");
    } finally { await app.close(); }
  });

  test(`${provider_id}: a malformed tool-call collection is rejected before HTTP`, async () => {
    const app = await server();
    try {
      const payload = input(provider_id, npm, app.url, calls.canonical);
      payload.messages[1].tool_calls = {};
      await assert.rejects(execute(payload, () => {}), error => {
        assert.equal(normalizeError(error).code, "MODEL_MESSAGE_HISTORY_INVALID");
        return true;
      });
      assert.equal(app.received.length, 0);
    } finally { await app.close(); }
  });

  for (const [shape, call] of Object.entries(calls)) {
    test(`${provider_id}: ${shape} history preserves call IDs, names, arguments and results`, async () => {
      const app = await server();
      try {
        const payload = input(provider_id, npm, app.url, call);
        const before = JSON.stringify(payload);
        const events = [];
        await execute(payload, event => events.push(event));
        assert.equal(JSON.stringify(payload), before, "conversion changed its caller's history");
        assert.equal(app.received.length, 1);
        const assistant = app.received[0].messages.find(message => message.role === "assistant");
        assert.equal(assistant.tool_calls[0].id, "call-1");
        assert.equal(assistant.tool_calls[0].function.name, "read");
        assert.deepEqual(JSON.parse(assistant.tool_calls[0].function.arguments), argumentsObject);
        const result = app.received[0].messages.find(message => message.role === "tool");
        assert.equal(result.tool_call_id, "call-1");
        assert.equal(result.content, "void setup() {}");
        assert.equal(events.find(event => event.type === "text_delta").text, "Verified.");
        assert.equal(events.find(event => event.type === "done").finish_reason, "stop");
      } finally { await app.close(); }
    });
  }

  for (const [description, call] of [
    ["missing name", { id: "call-1", arguments: {} }],
    ["missing ID", { name: "read", arguments: {} }],
    ["malformed JSON", { id: "call-1", name: "read", arguments: "fixture-private-invalid-json" }],
    ["non-object arguments", { id: "call-1", name: "read", arguments: [] }],
  ]) {
    test(`${provider_id}: ${description} is a safe terminal history error before HTTP`, async () => {
      const app = await server();
      try {
        await assert.rejects(execute(input(provider_id, npm, app.url, call), () => {}), error => {
          const normalized = normalizeError(error);
          assert.equal(normalized.code, "MODEL_MESSAGE_HISTORY_INVALID");
          assert.equal(normalized.category, "invalid_request");
          assert.equal(normalized.retryable, false);
          assert.ok(!normalized.message.includes("fixture-private-invalid-json"));
          assert.ok(!normalized.message.includes("Cannot read properties"));
          return true;
        });
        assert.equal(app.received.length, 0);
      } finally { await app.close(); }
    });
  }
}

const sse = events => events.map(event => `event: ${event.type ?? "message"}\ndata: ${JSON.stringify(event)}\n\n`).join("");
const protocols = [
  { id: "openai", npm: "@ai-sdk/openai", reply: sse([
    { type: "response.created", response: { id: "resp", model: "test-model", status: "in_progress", output: [] } },
    { type: "response.output_text.delta", item_id: "msg", output_index: 0, content_index: 0, delta: "Verified." },
    { type: "response.completed", response: { id: "resp", model: "test-model", status: "completed",
      output: [{ id: "msg", type: "message", role: "assistant", content: [{ type: "output_text", text: "Verified.", annotations: [] }] }],
      usage: { input_tokens: 3, output_tokens: 1, total_tokens: 4 } } },
  ]), check(body) {
    const call = body.input.find(part => part.type === "function_call");
    assert.equal(call.call_id, "call-1");
    assert.equal(call.name, "read");
    assert.deepEqual(JSON.parse(call.arguments), argumentsObject);
    const result = body.input.find(part => part.type === "function_call_output");
    assert.equal(result.call_id, "call-1");
    assert.ok(JSON.stringify(result.output).includes("void setup() {}"));
  } },
  { id: "anthropic", npm: "@ai-sdk/anthropic", reply: sse([
    { type: "message_start", message: { id: "msg", type: "message", role: "assistant", model: "test-model", content: [], stop_reason: null, stop_sequence: null, usage: { input_tokens: 3, output_tokens: 0 } } },
    { type: "content_block_start", index: 0, content_block: { type: "text", text: "" } },
    { type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "Verified." } },
    { type: "content_block_stop", index: 0 },
    { type: "message_delta", delta: { stop_reason: "end_turn", stop_sequence: null }, usage: { output_tokens: 1 } },
    { type: "message_stop" },
  ]), check(body) {
    const call = body.messages[1].content.find(part => part.type === "tool_use");
    assert.equal(call.id, "call-1");
    assert.equal(call.name, "read");
    assert.deepEqual(call.input, argumentsObject);
    const result = body.messages[2].content.find(part => part.type === "tool_result");
    assert.equal(result.tool_use_id, "call-1");
    assert.ok(JSON.stringify(result.content).includes("void setup() {}"));
  } },
  { id: "google", npm: "@ai-sdk/google", reply: sse([
    { candidates: [{ index: 0, content: { role: "model", parts: [{ text: "Verified." }] }, finishReason: "STOP" }],
      usageMetadata: { promptTokenCount: 3, candidatesTokenCount: 1, totalTokenCount: 4 } },
  ]), check(body) {
    const call = body.contents[1].parts.find(part => part.functionCall).functionCall;
    assert.equal(call.name, "read");
    assert.deepEqual(call.args, argumentsObject);
    const result = body.contents[2].parts.find(part => part.functionResponse).functionResponse;
    assert.equal(result.name, "read");
    assert.ok(JSON.stringify(result.response).includes("void setup() {}"));
  } },
];
test("SDK v4 model uses its matching runtime with canonical tool history", async () => {
  const app = await server();
  try {
    const events = [];
    await execute(input("salad-cloud", "@saladtechnologies-oss/ai-sdk-provider", app.url,
      calls.canonical), event => events.push(event));
    assert.equal(app.received.length, 1);
    const call = app.received[0].messages[1].tool_calls[0];
    assert.equal(call.id, "call-1");
    assert.equal(call.function.name, "read");
    assert.deepEqual(JSON.parse(call.function.arguments), argumentsObject);
    assert.equal(app.received[0].messages[2].tool_call_id, "call-1");
    assert.equal(app.received[0].messages[2].content, "void setup() {}");
    assert.equal(events.find(event => event.type === "text_delta").text, "Verified.");
    assert.ok(events.some(event => event.type === "done"));
  } finally { await app.close(); }
});

for (const protocol of protocols) {
  test(`${protocol.id}: canonical tool history reaches its native wire protocol`, async () => {
    const app = await server(protocol.reply);
    try {
      const events = [];
      await execute(input(protocol.id, protocol.npm, app.url, calls.canonical), event => events.push(event));
      assert.equal(app.received.length, 1);
      protocol.check(app.received[0]);
      assert.equal(events.find(event => event.type === "text_delta").text, "Verified.");
      assert.ok(events.some(event => event.type === "done"));
    } finally { await app.close(); }
  });
}
