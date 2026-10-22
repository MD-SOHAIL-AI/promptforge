/** Exercise the built middleware with isolated, owned HTTP servers. */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";

const root = process.cwd();
const evidence = path.resolve(root, process.env.FORGEX_QUALIFICATION_OUTPUT ?? "audit/2026-10-10/live-session-fix");
const serverFile = path.join(root, "frontend/.next/standalone/frontend/server.js");
assert.ok(fs.existsSync(serverFile), "Build the frontend before running this qualification.");
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const listen = async server => {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return server.address().port;
};
const close = async server => {
  if (!server.listening) return;
  server.closeAllConnections();
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
};
let identity = "forgex-backend";
let healthDelayMs = 0;
let healthCount = 0;
const mutations = [];
const backend = http.createServer(async (request, response) => {
  if (request.url === "/health") {
    healthCount++;
    if (healthDelayMs) await delay(healthDelayMs);
    response.writeHead(200, {"Content-Type": "application/json"});
    response.end(JSON.stringify({service: identity, status: "healthy"}));
  } else if (request.url.startsWith("/projects/demo/refresh")) {
    let body = "";
    for await (const chunk of request) body += chunk;
    mutations.push({method: request.method, url: request.url, body, headers: request.headers});
    response.writeHead(202, {"Content-Type": "application/json"});
    response.end(JSON.stringify({accepted: true}));
  } else if (["/qualification/run", "/qualification/graph", "/qualification/queue"].includes(request.url)) {
    response.writeHead(200, {"Content-Type": "application/json"});
    response.end(JSON.stringify({saved: true}));
  } else if (request.url === "/qualification/events") {
    response.writeHead(200, {"Content-Type": "text/event-stream", "Cache-Control": "no-store"});
    response.write("data: first\n\n");
    const timer = setTimeout(() => response.end("data: last\n\n"), 1500);
    response.once("close", () => clearTimeout(timer));
  } else {
    response.writeHead(404);
    response.end();
  }
});
let frontend;
let output = "";
const checks = [];
const record = name => checks.push({name, passed: true});
try {
  const backendPort = await listen(backend);
  const reservation = http.createServer();
  const frontendPort = await listen(reservation);
  await close(reservation);
  frontend = spawn(process.execPath, [serverFile], {cwd: path.dirname(serverFile), windowsHide: true,
    env: {...process.env, PORT: String(frontendPort), HOSTNAME: "127.0.0.1", NODE_ENV: "production",
      FORGEX_BACKEND_URL: `http://127.0.0.1:${backendPort}`}, stdio: ["ignore", "pipe", "pipe"]});
  const exited = once(frontend, "exit");
  frontend.stdout.on("data", chunk => {output = (output + chunk).slice(-12000);});
  frontend.stderr.on("data", chunk => {output = (output + chunk).slice(-12000);});
  const base = `http://127.0.0.1:${frontendPort}/api/forgex`;
  let ready = false;
  const startupDeadline = Date.now() + 120000;
  for (let attempt = 0; Date.now() < startupDeadline; attempt++) {
    if (frontend.exitCode !== null) throw new Error(`Standalone server exited: ${output}`);
    try {
      const response = await fetch(`${base}/health`, {signal: AbortSignal.timeout(2000)});
      ready = response.ok && (await response.json()).service === "forgex-backend";
    } catch {}
    if (ready) break;
    await delay(200);
  }
  assert.ok(ready, `Standalone proxy readiness failed: ${output}`);
  record("standalone middleware identifies the backend");
  healthDelayMs = 250;
  const beforeReads = healthCount;
  const readResponses = await Promise.all(["run", "graph", "queue"].map(name => fetch(`${base}/qualification/${name}`)));
  assert.deepEqual(readResponses.map(result => result.status), [200, 200, 200]);
  assert.equal(healthCount - beforeReads, 1);
  healthDelayMs = 0;
  record("concurrent run, graph and queue observations share a slow in-flight health probe");
  const response = await fetch(`${base}/projects/demo/refresh?force=1`, {method: "POST",
    headers: {"Content-Type": "application/json", "X-Request-ID": "qualification-refresh",
      "Cookie": "qualification=session", "Authorization": "Bearer qualification-placeholder"},
    body: JSON.stringify({force: true})});
  assert.equal(response.status, 202);
  assert.equal(response.headers.get("x-request-id"), "qualification-refresh");
  assert.equal(mutations.length, 1);
  assert.equal(mutations[0].method, "POST");
  assert.equal(mutations[0].url, "/projects/demo/refresh?force=1");
  assert.deepEqual(JSON.parse(mutations[0].body), {force: true});
  assert.equal(mutations[0].headers.cookie, "qualification=session");
  assert.equal(mutations[0].headers.authorization, "Bearer qualification-placeholder");
  record("mutation method, query, body, auth and request ID reach the backend once");
  const events = await fetch(`${base}/qualification/events`);
  assert.equal(events.status, 200);
  const reader = events.body.getReader();
  const first = await reader.read();
  assert.match(new TextDecoder().decode(first.value), /data: first/);
  assert.doesNotMatch(new TextDecoder().decode(first.value), /data: last/);
  let tail = "";
  for (;;) {
    const chunk = await reader.read();
    if (chunk.done) break;
    tail += new TextDecoder().decode(chunk.value);
  }
  assert.match(tail, /data: last/);
  record("SSE reaches the renderer incrementally");
  identity = "unrelated-service";
  const mismatch = await fetch(`${base}/projects/demo/refresh`, {method: "POST", body: "blocked"});
  assert.equal(mismatch.status, 502);
  assert.equal((await mismatch.json()).code, "BACKEND_IDENTITY_MISMATCH");
  assert.equal(mutations.length, 1);
  record("unrelated listener receives no mutation");
  await close(backend);
  const unavailable = await fetch(`${base}/devices/boards`);
  assert.equal(unavailable.status, 503);
  assert.equal((await unavailable.json()).code, "BACKEND_UNAVAILABLE");
  record("absent backend produces a structured unavailable response");
  frontend.kill("SIGTERM");
  await Promise.race([exited, delay(3000)]);
} catch (error) {
  checks.push({name: "qualification failure", passed: false, message: String(error)});
  process.exitCode = 1;
} finally {
  if (frontend && frontend.exitCode === null && frontend.signalCode === null) frontend.kill("SIGKILL");
  await close(backend);
  fs.mkdirSync(evidence, {recursive: true});
  fs.writeFileSync(path.join(evidence, "production-proxy-results.json"),
    JSON.stringify({timestamp: new Date().toISOString(), passed: checks.every(check => check.passed), checks}, null, 2));
  fs.writeFileSync(path.join(evidence, "production-proxy-server.log"), output);
  for (const check of checks) process.stdout.write(`${check.passed ? "PASS" : "FAIL"} ${check.name}${check.message ? `: ${check.message}` : ""}\n`);
}
