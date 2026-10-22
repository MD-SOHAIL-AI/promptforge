import assert from "node:assert/strict";
import test from "node:test";
import {recoverRead} from "./read-recovery.ts";

test("transient reads recover automatically within three attempts", async () => {
  let calls = 0;
  const attempts: number[] = [];
  const result = await recoverRead("GET", async () => {
    if (++calls < 3) throw {code: "BACKEND_UNAVAILABLE"};
    return {saved: true};
  }, {signal: new AbortController().signal, delayMs: 1, onRetry: attempt => attempts.push(attempt)});
  assert.deepEqual(result, {saved: true});
  assert.deepEqual(attempts, [1, 2]);
  assert.equal(calls, 3);
});

test("persistent failures stop after three reads", async () => {
  let calls = 0;
  await assert.rejects(recoverRead("GET", async () => {
    calls++;
    throw new TypeError("network unavailable");
  }, {signal: new AbortController().signal, delayMs: 1}), TypeError);
  assert.equal(calls, 3);
});

test("mutations, uncertain outcomes, identity failures and permission errors never retry", async () => {
  for (const [method, error] of [["POST", {code: "BACKEND_UNAVAILABLE"}], ["POST", new TypeError("network")],
    ["GET", {code: "REQUEST_RESULT_UNKNOWN"}], ["GET", {code: "BACKEND_IDENTITY_MISMATCH"}],
    ["GET", {code: "AUTH_REQUIRED"}]] as const) {
    let calls = 0;
    await assert.rejects(recoverRead(method, async () => {calls++; throw error;},
      {signal: new AbortController().signal, delayMs: 1}), value => value === error);
    assert.equal(calls, 1);
  }
});

test("caller cancellation interrupts backoff before another request", async () => {
  const controller = new AbortController();
  let calls = 0;
  await assert.rejects(recoverRead("GET", async () => {calls++; throw {code: "BACKEND_UNAVAILABLE"};},
    {signal: controller.signal, delayMs: 1000, onRetry: () => controller.abort()}), {name: "AbortError"});
  assert.equal(calls, 1);
});
