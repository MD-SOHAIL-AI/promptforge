import assert from "node:assert/strict";
import test from "node:test";
import { SettingsWriteQueue } from "./settings-write-queue.ts";

test("rapid preference edits coalesce and preserve the latest value", async () => {
  const calls: Record<string, unknown>[] = [];
  const queue = new SettingsWriteQueue<Record<string, unknown>>(async patch => { calls.push(patch); return patch; }, 0);
  await Promise.all([queue.enqueue({ opacity: 80 }), queue.enqueue({ opacity: 65 }), queue.enqueue({ blur: 4 })]);
  assert.deepEqual(calls, [{ opacity: 65, blur: 4 }]);
});

test("writes in flight serialize with subsequent edits", async () => {
  let unblock!: () => void;
  const gate = new Promise<void>(resolve => { unblock = resolve; });
  const calls: Record<string, unknown>[] = [];
  const queue = new SettingsWriteQueue<Record<string, unknown>>(async patch => { calls.push(patch); if (calls.length === 1) await gate; return patch; }, 0);
  const first = queue.enqueue({ image: "first" });
  await new Promise(resolve => setTimeout(resolve, 20));
  const second = queue.enqueue({ image: "second", filename: "night.png" });
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(calls.length, 1);
  unblock();
  await Promise.all([first, second]);
  assert.deepEqual(calls, [{ image: "first" }, { image: "second", filename: "night.png" }]);
});

test("a failed write rejects its transaction and allows the next save", async () => {
  let count = 0;
  const queue = new SettingsWriteQueue<Record<string, unknown>>(async patch => { if (++count === 1) throw new Error("offline"); return patch; }, 0);
  await assert.rejects(queue.enqueue({ image: "bad" }), /offline/);
  assert.deepEqual(await queue.enqueue({ image: "working" }), { image: "working" });
});
