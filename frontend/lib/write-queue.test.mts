import assert from "node:assert/strict";
import test from "node:test";
import {WriteQueue} from "./write-queue.ts";

test("queued saves use the revision acknowledged by the preceding write", async () => {
  const queue = new WriteQueue();
  let release!: () => void;
  const held = new Promise<void>(resolve => {release = resolve;});
  const sent: string[] = [];
  let diskRevision = "original";
  const first = queue.run("project/main.cpp", async () => {
    sent.push(diskRevision);
    await held;
    diskRevision = "saved-first";
  });
  const second = queue.run("project/main.cpp", async () => {
    sent.push(diskRevision);
    diskRevision = "saved-second";
  });
  await queue.run("other-project/main.cpp", async () => {
    assert.deepEqual(sent, ["original"], "an unrelated file can save while this one is held");
  });
  release();
  await Promise.all([first, second]);
  assert.deepEqual(sent, ["original", "saved-first"]);
  assert.equal(diskRevision, "saved-second");
});

test("a rejected write settles once and a later explicit save is still possible", async () => {
  const queue = new WriteQueue();
  let attempts = 0;
  await assert.rejects(queue.run("file", async () => {attempts++; throw new Error("conflict");}), /conflict/);
  assert.equal(attempts, 1, "writes must never be automatically replayed");
  assert.equal(await queue.run("file", async () => {attempts++; return "saved";}), "saved");
  assert.equal(attempts, 2);
});
