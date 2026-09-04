import { EventEmitter } from "node:events";
import assert from "node:assert/strict";
import test from "node:test";

import {
  BACKEND_RUNTIME_CONTRACT,
  BackendManager,
  computeBackendSourceFingerprint,
} from "../dist/electron/backend-manager.js";

const sourceFingerprint = computeBackendSourceFingerprint(process.cwd());

const healthy = {
  reachable: true,
  forgeX: true,
  healthy: true,
  body: {
    status: "healthy",
    service: "forgex-backend",
    runtime_contract: BACKEND_RUNTIME_CONTRACT,
    source_fingerprint: sourceFingerprint,
  },
};
const resolvedPython = { executable: "python", source: "fallback" };

function fakeProcess(pid = 12345) {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.pid = pid;
  child.killed = false;
  child.killCalls = [];
  child.kill = (signal) => {
    child.killed = true;
    child.killCalls.push(signal);
    queueMicrotask(() => child.emit("exit", 0, signal));
    return true;
  };
  return child;
}

test("BackendManager reuses healthy existing ForgeX backend", async () => {
  let spawned = false;
  const manager = new BackendManager({
    port: 8000,
    cwd: process.cwd(),
    healthProbe: async () => healthy,
    resolvePython: () => resolvedPython,
    spawnBackend: () => {
      spawned = true;
      return fakeProcess();
    },
  });

  const status = await manager.start();
  await manager.stop();

  assert.equal(status.state, "ready");
  assert.equal(status.ownsProcess, false);
  assert.equal(status.pid, undefined);
  assert.equal(spawned, false);
});

test("BackendManager rejects non-ForgeX service on the backend port", async () => {
  const manager = new BackendManager({
    port: 8000,
    cwd: process.cwd(),
    healthProbe: async () => ({
      reachable: true,
      forgeX: false,
      healthy: false,
      statusCode: 200,
      body: { service: "other-service" },
    }),
    resolvePython: () => resolvedPython,
    spawnBackend: () => fakeProcess(),
  });

  await assert.rejects(
    manager.start(),
    /Port 8000 is already in use by another process/,
  );
  assert.equal(manager.getStatus().ownsProcess, false);
});

test("BackendManager handles health timeout without spawning", async () => {
  let spawned = false;
  const manager = new BackendManager({
    port: 8000,
    cwd: process.cwd(),
    healthProbe: async () => ({
      reachable: false,
      forgeX: false,
      healthy: false,
      timedOut: true,
      error: "Health request timed out",
    }),
    resolvePython: () => resolvedPython,
    spawnBackend: () => {
      spawned = true;
      return fakeProcess();
    },
  });

  await assert.rejects(manager.start(), /health check timed out/);
  assert.equal(spawned, false);
});

test("BackendManager records spawned PID and stops only spawned backend", async () => {
  const child = fakeProcess(24680);
  const probes = [
    {
      reachable: false,
      forgeX: false,
      healthy: false,
      error: "ECONNREFUSED",
    },
    healthy,
  ];
  const manager = new BackendManager({
    port: 8000,
    cwd: process.cwd(),
    readinessIntervalMs: 1,
    healthProbe: async () => probes.shift() ?? healthy,
    resolvePython: () => resolvedPython,
    spawnBackend: () => child,
  });

  const status = await manager.start();
  await manager.stop();

  assert.equal(status.state, "ready");
  assert.equal(status.ownsProcess, true);
  assert.equal(status.pid, 24680);
  assert.deepEqual(child.killCalls, ["SIGTERM"]);
});

test("BackendManager cleanup does not kill reused backend", async () => {
  const child = fakeProcess(11111);
  const manager = new BackendManager({
    port: 8000,
    cwd: process.cwd(),
    healthProbe: async () => healthy,
    resolvePython: () => resolvedPython,
    spawnBackend: () => child,
  });

  await manager.start();
  await manager.stop();

  assert.deepEqual(child.killCalls, []);
});

test("BackendManager rejects a stale ForgeX backend source fingerprint", async () => {
  const manager = new BackendManager({
    port: 8000,
    cwd: process.cwd(),
    healthProbe: async () => ({
      ...healthy,
      body: {
        ...healthy.body,
        source_fingerprint: "stale-source",
      },
    }),
    resolvePython: () => resolvedPython,
    spawnBackend: () => fakeProcess(),
  });

  await assert.rejects(manager.start(), /started from older source code/);
  assert.equal(manager.getStatus().ownsProcess, false);
});

test("BackendManager reports Python resolver startup failures cleanly", async () => {
  const manager = new BackendManager({
    port: 8000,
    cwd: process.cwd(),
    healthProbe: async () => ({
      reachable: false,
      forgeX: false,
      healthy: false,
      error: "ECONNREFUSED",
    }),
    resolvePython: () => {
      throw new Error("ForgeX backend dependencies are not installed.");
    },
    spawnBackend: () => fakeProcess(),
  });

  await assert.rejects(manager.start(), /ForgeX backend dependencies are not installed/);
  assert.equal(manager.getStatus().ownsProcess, false);
});
