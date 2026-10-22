import assert from "node:assert/strict";
import { mkdtemp, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";

import { SessionEventStore } from "../../dist/harness/event-store.js";
import { HarnessRuntime } from "../../dist/harness/runtime.js";
import { ToolPipeline } from "../../dist/harness/tool-pipeline.js";
import { SubagentRuntime } from "../../dist/harness/subagents.js";
import { WorkflowEngine } from "../../dist/harness/workflow.js";
import { PluginRegistry } from "../../dist/harness/plugins.js";
import { SandboxedTerminal, assertNoHardwareBypass } from "../../dist/harness/sandbox.js";
import { IdempotencyStore } from "../../dist/harness/idempotency.js";

test("session events are ordered and checksum chained", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "forgex-harness-"));
  const store = new SessionEventStore(root);
  await store.append("session-one", "session/created", { title: "One" });
  await store.append("session-one", "message/user", { text: "hello" }, "run-one");
  const events = await store.read("session-one");
  assert.equal(events.length, 2);
  assert.equal(events[1].previousChecksum, events[0].checksum);
});

test("subagents are durable, bounded children and workflows compose them", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "forgex-harness-"));
  const store = new SessionEventStore(root);
  await store.append("parent", "session/created", {});
  const subagents = new SubagentRuntime(store, 4, 2);
  subagents.register("in-process", async (request) => ({ reviewed: request.task }));
  const workflow = new WorkflowEngine(subagents);
  const result = await workflow.run({ parentSessionId: "parent", parentRunId: "run", provider: "in-process", strategy: "parallel", tasks: ["a", "b"], depth: 0 });
  assert.equal(result.status, "completed");
  assert.equal((await subagents.listChildren("parent")).length, 2);
});

test("third-party plugins remain disabled until every capability is trusted", () => {
  const plugins = new PluginRegistry();
  const pending = plugins.install({ id: "example.plugin", version: "1.0.0", apiVersion: 1, entrypoint: "index.js", capabilities: ["tools.read"] }, new Set());
  assert.equal(pending.enabled, false);
  const bundled = plugins.install({ id: "forgex.hardware", version: "1.0.0", apiVersion: 1, entrypoint: "hardware.js", capabilities: ["hardware.flash"], bundled: true }, new Set());
  assert.equal(bundled.enabled, true);
});

test("terminal fails closed without verified device isolation", async () => {
  const terminal = new SandboxedTerminal({ executable: "unused", enforcement: "partial", deviceIsolation: "unknown" });
  await assert.rejects(
    terminal.execute(["pwsh", "-Command", "Get-ChildItem"], { mode: "read-only", workspaceRoot: process.cwd(), timeoutMs: 1000, maxOutputBytes: 1024 }),
    /fully enforcing/,
  );
  assert.throws(() => assertNoHardwareBypass(["python", "-m", "esptool", "write_flash"]), /TYPED_TOOL/);
  assert.throws(() => assertNoHardwareBypass(["pwsh", "-Command", "open COM7"]), /TYPED_TOOL/);
});

test("runtime exposes the provider catalog and durable sessions", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "forgex-harness-"));
  const runtime = new HarnessRuntime(new SessionEventStore(root));
  await runtime.initialize();
  const status = await runtime.invoke("harness.status", {});
  assert.equal(status.authoritative, false);
  assert.equal(status.runAuthority, "python-agent-orchestrator");
  const created = await runtime.invoke("sessions.create", { sessionId: "session-two", title: "Two" });
  assert.equal(created.sessionId, "session-two");
  const providers = await runtime.invoke("providers.list", {});
  assert.ok(providers.providers.length >= 9);
});

test("tool guards are monotonic and tool results settle once", async () => {
  const pipeline = new ToolPipeline();
  pipeline.register({ name: "read", timeoutMs: 1000, execute: async () => ({ ok: true }) });
  pipeline.guard((call) => call.arguments.denied === true ? "blocked" : null);
  const emitted = [];
  const result = await pipeline.execute({ callId: "call-1", name: "read", arguments: { denied: true }, sessionId: "s", runId: "r" }, async (type) => { emitted.push(type); });
  assert.equal(result.status, "denied");
  assert.deepEqual(emitted, []);
  assert.equal(Object.isFrozen(result), true);
});

test("idempotency records replay durably and reject key reuse", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "forgex-idempotency-"));
  const first = new IdempotencyStore(root);
  let calls = 0;
  const result = await first.execute("request-key-0001", "fingerprint-a", async () => ({ calls: ++calls }));
  assert.deepEqual(result, { calls: 1 });
  const restarted = new IdempotencyStore(root);
  assert.deepEqual(await restarted.execute("request-key-0001", "fingerprint-a", async () => ({ calls: ++calls })), { calls: 1 });
  assert.equal(calls, 1);
  await assert.rejects(
    restarted.execute("request-key-0001", "fingerprint-b", async () => ({ calls: ++calls })),
    /different request/,
  );
});

test("tool timeout returns without waiting for an uncooperative implementation", async () => {
  const pipeline = new ToolPipeline();
  pipeline.register({ name: "hung", timeoutMs: 20, execute: async () => new Promise(() => {}) });
  const started = Date.now();
  const result = await pipeline.execute({ callId: "hung-1", name: "hung", arguments: {}, sessionId: "s", runId: "r" }, async () => {});
  assert.equal(result.status, "timed_out");
  assert.ok(Date.now() - started < 500);
});

test("parallel workflows batch work within the subagent concurrency bound", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "forgex-workflow-"));
  const store = new SessionEventStore(root);
  await store.append("parent-batched", "session/created", {});
  const subagents = new SubagentRuntime(store, 2, 2);
  subagents.register("worker", async (request) => ({ task: request.task }));
  const workflow = new WorkflowEngine(subagents, 16, 2);
  const result = await workflow.run({ parentSessionId: "parent-batched", parentRunId: "run", provider: "worker", strategy: "parallel", tasks: ["a", "b", "c", "d", "e"], depth: 0 });
  assert.equal(result.status, "completed");
  assert.equal(result.children.length, 5);
});

test("crash after a committed effect is not blindly replayed", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "forgex-crash-"));
  const events = new SessionEventStore(path.join(root, "events"));
  const first = new IdempotencyStore(path.join(root, "requests"));
  let effects = 0;
  const operation = async () => {
    effects += 1;
    const event = await events.append("session", "message/user", { text: "once" }, "run", "request-0001");
    return { eventId: event.eventId };
  };
  first.write = async () => { throw new Error("simulated host death before receipt"); };
  await assert.rejects(first.execute("request-0001", "fingerprint", operation), /host death/);
  const restarted = new IdempotencyStore(path.join(root, "requests"));
  await assert.rejects(restarted.execute("request-0001", "fingerprint", operation), /requires reconciliation/);
  assert.equal(effects, 1);
  const reconcile = async () => {
    const event = await events.append("session", "message/user", { text: "once" }, "run", "request-0001");
    return { eventId: event.eventId };
  };
  await restarted.execute("request-0001", "fingerprint", operation, reconcile);
  assert.equal((await events.read("session")).length, 1);
  assert.equal(effects, 1);
});

test("a live publisher blocks competing writers, and releasing it permits recovery", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "forgex-writer-"));
  const lock = new DatabaseSync(path.join(root, "session.jsonl.writer.sqlite3"));
  lock.exec("BEGIN IMMEDIATE");
  const store = new SessionEventStore(root);
  await assert.rejects(store.append("session", "session/created", {}), /SESSION_WRITER_LOCKED/);
  assert.deepEqual(await store.read("session"), []);
  lock.close();
  await store.append("session", "session/created", {});
  assert.equal((await store.read("session")).length, 1);
});
