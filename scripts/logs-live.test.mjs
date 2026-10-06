import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { LiveLogTail } from "./logs-live.mjs";

test("merged tail buffers partial UTF8 records and follows rotation without duplicate events", t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "forgex-tail-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const base = path.join(directory, "live-runtime.jsonl");
  const file = path.join(directory, "live-runtime.backend-one.jsonl");
  fs.writeFileSync(base, '{"event":"old"}\n');
  const tail = new LiveLogTail(base);
  const record = Buffer.from('{"event":"write","message":"saved 😀","timestamp":"2026-01-01"}\n');
  const split = record.indexOf(Buffer.from("😀")) + 2;
  fs.writeFileSync(file, record.subarray(0, split));
  assert.deepEqual(tail.poll().events, []);
  fs.appendFileSync(file, record.subarray(split));
  assert.equal(tail.poll().events[0].message, "saved 😀");
  fs.renameSync(file, file + ".1");
  fs.writeFileSync(file, '{"event":"new"}\nbroken legacy fragment\n');
  fs.appendFileSync(base, '{"event":"legacy-new"}\n');
  const next = tail.poll();
  assert.deepEqual(next.events.map(event => event.event).sort(), ["legacy-new", "new"]);
  assert.equal(next.malformed, 1);
  assert.deepEqual(tail.poll().events, []);
});
