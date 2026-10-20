import assert from "node:assert/strict";
import test from "node:test";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";
import { proxyBackendRequest } from "./backend-proxy.ts";

const bundle = await build({ entryPoints: [fileURLToPath(new URL("./api.ts", import.meta.url))],
  bundle: true, platform: "node", format: "esm", write: false });
const { forgeXApi, ForgeXApiError } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString("base64")}`);

test("API observation recovers after a transient proxy failure while mutations dispatch once", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async input => {
    if (!String(input).includes("/build-evidence")) return Response.json({});
    if (++calls === 1) return Response.json({code: "BACKEND_UNAVAILABLE", message: "Reconnecting"}, {status: 503});
    return Response.json({state: "passed", scope: "saved_draft"});
  });
  assert.equal((await forgeXApi.agentBuildEvidence("saved-run", null)).state, "passed");
  assert.equal(calls, 2);
  calls = 0;
  t.mock.method(globalThis, "fetch", async input => {
    if (!String(input).includes("/refresh")) return Response.json({});
    calls++;
    return Response.json({code: "REQUEST_RESULT_UNKNOWN", message: "Inspect the saved result"}, {status: 503});
  });
  await assert.rejects(forgeXApi.refreshProject("isolated-project"), error => error.code === "REQUEST_RESULT_UNKNOWN");
  assert.equal(calls, 1);
});

test("backend proxy errors expose their concrete recovery message to the API client", async (t) => {
  const response = await proxyBackendRequest(new Request("http://localhost/api/forgex/health"),
    "http://127.0.0.1:12345", {fetcher: async () => {throw new TypeError("connection refused");}});
  t.mock.method(globalThis, "fetch", async () => response.clone());
  await assert.rejects(forgeXApi.agentBuildEvidence("saved-run", null), error =>
    error instanceof ForgeXApiError && error.code === "BACKEND_UNAVAILABLE"
    && error.message.includes("Saved work is preserved") && error.status === 503);
});

test("recovered health failures log reconnection while exhausted reads log one final error", async t => {
  const events = [];
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  globalThis.window = {forgexDesktop: {emitDiagnostic: async event => {events.push(event);}}};
  t.after(() => {if (previousWindow) Object.defineProperty(globalThis, "window", previousWindow); else delete globalThis.window;});
  let attempts = 0;
  t.mock.method(globalThis, "fetch", async () => ++attempts === 1
    ? Response.json({code: "BACKEND_UNAVAILABLE", message: "Temporary health timeout"}, {status: 503})
    : Response.json({state: "passed"}));
  await forgeXApi.agentBuildEvidence("fixture-run", null);
  assert.equal(events.filter(event => event.event === "renderer.http.failed").length, 0);
  assert.equal(events.filter(event => event.event === "renderer.http.reconnecting").length, 1);
  assert.equal(events.find(event => event.event === "renderer.http.reconnecting").error.error_code, "BACKEND_UNAVAILABLE");
  events.length = 0;
  t.mock.method(globalThis, "fetch", async () => Response.json({code: "BACKEND_UNAVAILABLE", message: "Backend stopped"}, {status: 503}));
  await assert.rejects(forgeXApi.agentBuildEvidence("fixture-run", null), error => error.status === 503);
  assert.equal(events.filter(event => event.event === "renderer.http.reconnecting").length, 2);
  assert.equal(events.filter(event => event.event === "renderer.http.failed").length, 1);
  assert.equal(events.at(-1).status, 503);
});

test("cancelled evidence reads are recorded as cancellations while request failures remain errors", async (t) => {
  const events = [];
  t.mock.method(globalThis, "fetch", async () => new Promise(() => {}));
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  globalThis.window = { forgexDesktop: { emitDiagnostic: async event => { events.push(event); } } };
  t.after(() => { if (previousWindow) Object.defineProperty(globalThis, "window", previousWindow); else delete globalThis.window; });
  const controller = new AbortController();
  const pending = forgeXApi.agentBuildEvidence("saved-run", null, controller.signal);
  controller.abort();
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(events.at(-1).event, "renderer.http.cancelled");
  assert.equal(events.at(-1).level, "DEBUG");
  assert.equal(events.filter(event => event.event === "renderer.http.failed").length, 0);

  t.mock.method(globalThis, "fetch", async () => { throw new TypeError("Connection unavailable"); });
  await assert.rejects(forgeXApi.agentBuildEvidence("saved-run", null), /Connection unavailable/);
  assert.equal(events.at(-1).event, "renderer.http.failed");
  assert.equal(events.at(-1).level, "ERROR");

  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({ code: "LOCAL_AUTH_REQUIRED", message: "Local authentication required" }), { status: 401 }));
  await assert.rejects(forgeXApi.agentBuildEvidence("saved-run", null), error => error instanceof ForgeXApiError && error.status === 401);
  assert.equal(events.at(-1).event, "renderer.http.failed");
  assert.equal(events.at(-1).level, "ERROR");
  assert.equal(events.at(-1).status, 401);
  let dispatched = 0;
  t.mock.method(globalThis, "fetch", async () => { dispatched++; return Response.json({authenticated: true}); });
  await assert.rejects(forgeXApi.agentBuildEvidence("saved-run", null), error => error.code === "LOCAL_AUTH_REQUIRED");
  await assert.rejects(forgeXApi.agentBuildEvidence("saved-run", null), error => error.code === "LOCAL_AUTH_REQUIRED");
  assert.equal(dispatched, 0);
  await forgeXApi.reconnect();
  await forgeXApi.agentBuildEvidence("saved-run", null);
  assert.equal(dispatched, 2);
});
