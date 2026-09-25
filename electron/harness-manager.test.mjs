import assert from "node:assert/strict";
import http from "node:http";
import test from "node:test";

import { probeHarness } from "../dist/electron/harness-manager.js";

test("harness probe requires the launch token and validates service identity", async () => {
  const token = "x".repeat(32);
  const server = http.createServer((request, response) => {
    if (request.headers.authorization !== `Bearer ${token}`) {
      response.writeHead(401).end();
      return;
    }
    response.setHeader("Content-Type", "application/json");
    response.end(JSON.stringify({ service: "forgex-harness", status: "healthy" }));
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  try {
    const healthy = await probeHarness(`http://127.0.0.1:${address.port}`, token);
    assert.equal(healthy.healthy, true);
    const rejected = await probeHarness(`http://127.0.0.1:${address.port}`, "wrong");
    assert.equal(rejected.healthy, false);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
