import test from "node:test";
import assert from "node:assert/strict";
import {RequestDeadlineError, withRequestDeadline} from "./request-deadline.ts";

test("bounds a held-open body even when fetch ignores abort", async () => {
  let signal: AbortSignal | undefined;
  await assert.rejects(withRequestDeadline(async value => { signal = value; await Promise.resolve(); return await new Promise<never>(() => {}); }, {timeoutMs: 15}), RequestDeadlineError);
  assert.equal(signal?.aborted, true);
});
test("aborting observation does not grant mutation replay", async () => {
  const controller = new AbortController();
  const observation = withRequestDeadline(() => new Promise<never>(() => {}), {timeoutMs: 1000, signal: controller.signal});
  controller.abort();
  await assert.rejects(observation, {name: "AbortError"});
  await assert.rejects(withRequestDeadline(() => new Promise<never>(() => {}), {timeoutMs: 10, mutation: true}), (error: unknown) => error instanceof RequestDeadlineError && error.mutation && /result is unknown/.test(error.message));
});
