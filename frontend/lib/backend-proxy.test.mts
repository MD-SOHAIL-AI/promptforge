import assert from "node:assert/strict";
import test from "node:test";
import { backendProxyUrl } from "./backend-proxy.ts";

test("packaged API and event streams use the configured backend port", () => {
  assert.equal(backendProxyUrl("http://localhost:3000/api/forgex/sessions/s/events?after_sequence=12", "http://127.0.0.1:43210").href,
    "http://127.0.0.1:43210/sessions/s/events?after_sequence=12");
  assert.equal(backendProxyUrl("http://localhost:3000/api/promptforge/health", "http://127.0.0.1:43210").href,
    "http://127.0.0.1:43210/health");
});

test("proxy never accepts a remote target or a URL embedded in its path", () => {
  for (const target of ["https://example.com", "http://127.0.0.1.evil.test", "http://user:secret@localhost", "http://localhost/api", "http://localhost/?target=evil"]) {
    assert.throws(() => backendProxyUrl("http://localhost/api/forgex/health", target), /TARGET_INVALID/);
  }
  assert.equal(backendProxyUrl("http://localhost/api/forgex//example.com/health", "http://127.0.0.1:12345").hostname, "127.0.0.1");
  assert.throws(() => backendProxyUrl("http://localhost/unrelated", "http://localhost:12345"), /PATH_INVALID/);
});
