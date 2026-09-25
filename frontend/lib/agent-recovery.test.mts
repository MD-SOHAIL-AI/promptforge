import test from "node:test";
import assert from "node:assert/strict";
import { formatRetryDelay, modelPauseMessage } from "./agent-recovery.ts";

test("retry delays are readable", () => {
  assert.equal(formatRetryDelay(5000), "about 5 seconds");
  assert.equal(formatRetryDelay(600000), "about 10 minutes");
  assert.equal(formatRetryDelay(86400000), "about 1 day");
});

test("paused model message guarantees no silent switch", () => {
  const message = modelPauseMessage({ status:"paused_model_unavailable", provider_diagnostics:{ outbound_request_count:1, request_reached_provider:true, retry_after_ms:5000 } });
  assert.match(message ?? "", /did not switch providers/);
});
