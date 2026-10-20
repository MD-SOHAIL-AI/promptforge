import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";
import { backendProxyUrl, proxyBackendRequest } from "./backend-proxy.ts";

test("packaged API and event streams use the configured backend port", () => {
  assert.equal(backendProxyUrl("http://localhost:3000/api/forgex/sessions/s/events?after_sequence=12", "http://127.0.0.1:43210").href,
    "http://127.0.0.1:43210/sessions/s/events?after_sequence=12");
  assert.equal(backendProxyUrl("http://localhost:3000/api/promptforge/health", "http://127.0.0.1:43210").href,
    "http://127.0.0.1:43210/health");
});

test("unavailable and unrelated backends return concrete errors before a mutation is forwarded", async () => {
  for (const unavailable of [true, false]) {
    const calls: string[] = [];
    const response = await proxyBackendRequest(new Request("http://localhost/api/forgex/projects/p/refresh", {
      method: "POST", headers: {"X-Request-ID": "original-id", Cookie: "local-session"}, body: "{}",
    }), "http://127.0.0.1:12345", {fetcher: async input => {
      calls.push(String(input));
      if (unavailable) throw new TypeError("connection refused");
      return Response.json({detail: "Not Found"}, {status: 404});
    }});
    assert.equal(response.status, unavailable ? 503 : 502);
    const body = await response.json();
    assert.equal(body.code, unavailable ? "BACKEND_UNAVAILABLE" : "BACKEND_IDENTITY_MISMATCH");
    assert.equal(body.request_id, "original-id");
    assert.equal(body.details.request_dispatched, false);
    assert.equal(calls.length, 1);
    assert.equal(new URL(calls[0]).pathname, "/health");
  }
});

test("an uncertain mutation is never replayed", async () => {
  let calls = 0;
  const response = await proxyBackendRequest(new Request("http://localhost/api/forgex/projects/p/refresh", {
    method: "POST", body: "{}",
  }), "http://127.0.0.1:12345", {fetcher: async () => {
    calls++;
    if (calls === 1) return Response.json({service: "forgex-backend"});
    throw new TypeError("backend exited after dispatch");
  }});
  assert.equal((await response.json()).code, "REQUEST_RESULT_UNKNOWN");
  assert.equal(calls, 2);
});

test("real HTTP proxy preserves authentication, mutation body, query and streaming beyond the header deadline", async () => {
  const received: Array<{method?: string; url?: string; cookie?: string; id?: string; body: string}> = [];
  const server = http.createServer(async (request, response) => {
    if (request.url === "/health") {
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify({service: "forgex-backend"}));
      return;
    }
    if (request.url === "/events") {
      response.writeHead(200, {"Content-Type": "text/event-stream"});
      response.write("data: first\n\n");
      setTimeout(() => response.end("data: last\n\n"), 80);
      return;
    }
    let body = "";
    for await (const chunk of request) body += chunk;
    received.push({method: request.method, url: request.url, cookie: request.headers.cookie,
      id: request.headers["x-request-id"] as string, body});
    response.writeHead(200, {"Content-Type": "application/json"});
    response.end('{"refreshed":true}');
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as {port: number};
  const backend = `http://127.0.0.1:${address.port}`;
  try {
    const result = await proxyBackendRequest(new Request("http://localhost/api/forgex/projects/p/refresh?revision=2", {
      method: "POST", headers: {Cookie: "forgex_local_session=test", "X-Request-ID": "mutation-id"}, body: '{"force":false}',
    }), backend);
    assert.deepEqual(await result.json(), {refreshed: true});
    assert.deepEqual(received, [{method: "POST", url: "/projects/p/refresh?revision=2",
      cookie: "forgex_local_session=test", id: "mutation-id", body: '{"force":false}'}]);
    const streamed = await proxyBackendRequest(new Request("http://localhost/api/forgex/events"), backend,
      {headersTimeoutMs: 50});
    assert.equal(streamed.headers.get("content-type"), "text/event-stream");
    const reader = streamed.body!.getReader();
    assert.equal(new TextDecoder().decode((await reader.read()).value), "data: first\n\n");
    assert.equal(new TextDecoder().decode((await reader.read()).value), "data: last\n\n");
    assert.equal((await reader.read()).done, true);
  } finally {
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});

test("proxy never accepts a remote target or a URL embedded in its path", () => {
  for (const target of ["https://example.com", "http://127.0.0.1.evil.test", "http://user:secret@localhost", "http://localhost/api", "http://localhost/?target=evil"]) {
    assert.throws(() => backendProxyUrl("http://localhost/api/forgex/health", target), /TARGET_INVALID/);
  }
  assert.equal(backendProxyUrl("http://localhost/api/forgex//example.com/health", "http://127.0.0.1:12345").hostname, "127.0.0.1");
  assert.throws(() => backendProxyUrl("http://localhost/unrelated", "http://localhost:12345"), /PATH_INVALID/);
});

test("concurrent reads share only the in-flight identity probe, independently of caller cancellation", async () => {
  let health = 0;
  let dispatched = 0;
  let release!: () => void;
  const ready = new Promise<void>(resolve => {release = resolve;});
  let unrelated = false;
  const fetcher: typeof fetch = async input => {
    if (new URL(String(input)).pathname === "/health") {
      health++;
      await ready;
      return Response.json({service: unrelated ? "other-service" : "forgex-backend"});
    }
    dispatched++;
    return Response.json({saved: true});
  };
  const controller = new AbortController();
  const cancelled = proxyBackendRequest(new Request("http://localhost/api/forgex/run", {signal: controller.signal}),
    "http://localhost:12345", {fetcher});
  const reads = ["run", "graph", "queue"].map(path => proxyBackendRequest(new Request(`http://localhost/api/forgex/${path}`),
    "http://localhost:12345", {fetcher}));
  controller.abort();
  assert.equal((await cancelled).status, 499);
  release();
  assert.deepEqual((await Promise.all(reads)).map(response => response.status), [200, 200, 200]);
  assert.equal(health, 1);
  assert.equal(dispatched, 3);
  unrelated = true;
  const mutation = await proxyBackendRequest(new Request("http://localhost/api/forgex/apply", {method: "POST", body: "{}"}),
    "http://localhost:12345", {fetcher});
  assert.equal((await mutation.json()).code, "BACKEND_IDENTITY_MISMATCH");
  assert.equal(health, 2, "completed health responses must not be cached");
  assert.equal(dispatched, 3);
});

test("health timeout bounds a fetcher that ignores abort and prevents dispatch", async () => {
  // AbortSignal.timeout does not keep Node's event loop alive on its own.
  const keepAlive = setInterval(() => {}, 50);
  try {
    let calls = 0;
    const response = await proxyBackendRequest(new Request("http://localhost/api/forgex/apply", {method: "POST"}),
      "http://localhost:12345", {healthTimeoutMs: 20, fetcher: async () => {
        calls++;
        return new Promise<Response>(() => {});
      }});
    const body = await response.json();
    assert.equal(body.code, "BACKEND_UNAVAILABLE");
    assert.equal(body.details.failure_reason, "health_timeout");
    assert.equal(body.details.phase, "health");
    assert.equal(body.details.request_dispatched, false);
    assert.equal(calls, 1);
  } finally {clearInterval(keepAlive);}
});

test("transport diagnostics retain the cause code without exposing secrets", async () => {
  const events: Record<string, unknown>[] = [];
  const response = await proxyBackendRequest(new Request("http://localhost/api/forgex/run", {headers: {"X-Request-ID": "read-id"}}),
    "http://localhost:12345", {onFailure: event => events.push(event), fetcher: async () => {
      throw new TypeError("token=secret-fixture-value", {cause: {code: "ECONNREFUSED", password: "secret-fixture-value"}});
    }});
  const body = await response.json();
  assert.equal(body.details.cause_code, "ECONNREFUSED");
  assert.equal(body.details.health_request_id, "read-id:proxy-health");
  assert.doesNotMatch(JSON.stringify({events, body}), /secret-fixture-value|password|token=/);
});

test("header timeout reports an uncertain mutation exactly once", async () => {
  let dispatched = 0;
  const response = await proxyBackendRequest(new Request("http://localhost/api/forgex/apply", {method: "POST", body: "{}"}),
    "http://localhost:12345", {headersTimeoutMs: 20, fetcher: async input => {
      if (new URL(String(input)).pathname === "/health") return Response.json({service: "forgex-backend"});
      dispatched++;
      return new Promise<Response>(() => {});
    }});
  const body = await response.json();
  assert.equal(body.code, "REQUEST_RESULT_UNKNOWN");
  assert.equal(body.details.failure_reason, "headers_timeout");
  assert.equal(dispatched, 1);
});
