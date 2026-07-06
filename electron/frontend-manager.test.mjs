import { EventEmitter } from "node:events";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { FrontendManager } from "../dist/electron/frontend-manager.js";
import { resolveApplicationRoot, resolveFrontendServerEntryFromRoot } from "../dist/electron/paths.js";

const ready = { reachable: true, forgeX: true, statusCode: 200 };

function fakeProcess(pid = 34567) {
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

test("FrontendManager reuses a healthy ForgeX frontend", async () => {
  let spawned = false;
  const manager = new FrontendManager({
    port: 3127,
    serverEntry: "frontend/server.js",
    probe: async () => ready,
    spawnFrontend: () => {
      spawned = true;
      return fakeProcess();
    },
  });
  const status = await manager.start();
  await manager.stop();
  assert.equal(status.state, "ready");
  assert.equal(status.ownsProcess, false);
  assert.equal(spawned, false);
});

test("FrontendManager owns and stops only its spawned frontend", async () => {
  const child = fakeProcess();
  const probes = [{ reachable: false, forgeX: false, error: "ECONNREFUSED" }, ready];
  const manager = new FrontendManager({
    port: 3127,
    serverEntry: "frontend/server.js",
    readinessIntervalMs: 1,
    probe: async () => probes.shift() ?? ready,
    spawnFrontend: () => child,
    executable: "electron.exe",
  });
  const status = await manager.start();
  await manager.stop();
  assert.equal(status.ownsProcess, true);
  assert.deepEqual(child.killCalls, ["SIGTERM"]);
});

test("FrontendManager rejects an unrelated service on the packaged frontend port", async () => {
  const manager = new FrontendManager({
    port: 3127,
    serverEntry: "frontend/server.js",
    probe: async () => ({ reachable: true, forgeX: false, statusCode: 200 }),
    spawnFrontend: () => fakeProcess(),
  });
  await assert.rejects(manager.start(), /already in use by another service/);
  assert.equal(manager.getStatus().ownsProcess, false);
});

test("packaged application root is independent of process cwd", () => {
  assert.equal(resolveApplicationRoot(true, "C:\\qa\\ForgeX\\resources", "C:\\repo"), "C:\\qa\\ForgeX");
  assert.equal(resolveApplicationRoot(false, "C:\\qa\\ForgeX\\resources", "C:\\repo"), "C:\\repo");
});

test("missing packaged frontend resource produces a normalized error", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-packaged-path-"));
  try {
    assert.throws(() => resolveFrontendServerEntryFromRoot(root), /Packaged frontend server entry was not found/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
