import { EventEmitter } from "node:events";
import assert from "node:assert/strict";
import test from "node:test";

import { BackendManager } from "../dist/electron/backend-manager.js";
import { resolveBackendDataRoot } from "../dist/electron/paths.js";

const healthy = {
  reachable: true,
  forgeX: true,
  healthy: true,
  body: {
    status: "healthy",
    service: "forgex-backend",
  },
};

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
    spawnBackend: () => child,
  });

  await manager.start();
  await manager.stop();

  assert.deepEqual(child.killCalls, []);
});

test("resolveBackendDataRoot keeps explicit runtime overrides isolated", () => {
  const previousDataRoot = process.env.PROMPTFORGE_DATA_ROOT;
  const previousRuntimeRoot = process.env.PROMPTFORGE_RUNTIME_ROOT;
  const previousRoot = process.env.PROMPTFORGE_ROOT;
  try {
    process.env.PROMPTFORGE_ROOT = "C:\\qa\\forgex-root";
    delete process.env.PROMPTFORGE_DATA_ROOT;
    delete process.env.PROMPTFORGE_RUNTIME_ROOT;
    assert.equal(resolveBackendDataRoot(), "C:\\qa\\forgex-root");

    process.env.PROMPTFORGE_DATA_ROOT = "C:\\qa\\forgex-data";
    assert.equal(resolveBackendDataRoot(), "C:\\qa\\forgex-data");
  } finally {
    restoreEnv("PROMPTFORGE_DATA_ROOT", previousDataRoot);
    restoreEnv("PROMPTFORGE_RUNTIME_ROOT", previousRuntimeRoot);
    restoreEnv("PROMPTFORGE_ROOT", previousRoot);
  }
});

function restoreEnv(name, value) {
  if (value === undefined) {
    delete process.env[name];
    return;
  }
  process.env[name] = value;
}
