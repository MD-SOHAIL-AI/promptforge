import { EventEmitter } from "node:events";
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { FrontendManager, probeForgeXFrontend } from "../dist/electron/frontend-manager.js";
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

test("FrontendManager refuses to reuse an existing frontend process", async () => {
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
  await assert.rejects(manager.start(), /already in use/);
  assert.equal(manager.getStatus().ownsProcess, false);
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
  await assert.rejects(manager.start(), /already in use/);
  assert.equal(manager.getStatus().ownsProcess, false);
});

test("frontend identity probe requires a fresh keyed proof", async () => {
  const key = "test-frontend-key";
  const server = http.createServer((request, response) => {
    if (request.url !== "/desktop-identity") {
      response.writeHead(404).end();
      return;
    }
    const challenge = request.headers["x-forgex-challenge"];
    const proof = createHmac("sha256", key).update(challenge).digest("hex");
    response.writeHead(204, { "X-ForgeX-Proof": proof }).end();
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address();
    const url = `http://127.0.0.1:${address.port}`;
    assert.equal((await probeForgeXFrontend(url, key)).forgeX, true);
    assert.equal((await probeForgeXFrontend(url, "wrong-key")).forgeX, false);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
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
