import assert from "node:assert/strict";
import test from "node:test";

import {
  isStableRunStatus,
  shouldPollAfterSseEvent,
  shouldStartPollingAfterSseDisconnect,
} from "./agent-stream-policy.ts";

test("healthy SSE events do not trigger run polling", () => {
  assert.equal(shouldPollAfterSseEvent("running"), false);
  assert.equal(shouldPollAfterSseEvent("planning"), false);
});

test("terminal and approval SSE events trigger one settling refresh", () => {
  assert.equal(shouldPollAfterSseEvent("completed"), true);
  assert.equal(shouldPollAfterSseEvent("failed"), true);
  assert.equal(shouldPollAfterSseEvent("awaiting_flash_confirmation"), true);
});

test("SSE disconnect starts polling fallback only for active runs", () => {
  assert.equal(shouldStartPollingAfterSseDisconnect("running"), true);
  assert.equal(shouldStartPollingAfterSseDisconnect(undefined), true);
  assert.equal(shouldStartPollingAfterSseDisconnect("completed"), false);
  assert.equal(shouldStartPollingAfterSseDisconnect("cancelled"), false);
});

test("stable status helper covers terminal and flash approval states", () => {
  assert.equal(isStableRunStatus("awaiting_flash_confirmation"), true);
  assert.equal(isStableRunStatus("timed_out"), true);
  assert.equal(isStableRunStatus("running"), false);
});
