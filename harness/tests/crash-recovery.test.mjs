import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { SessionEventStore } from "../../dist/harness/event-store.js";

test("publisher lock is released after the owning process is killed", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "forgex-crash-lock-"));
  const lock = path.join(root, "session.jsonl.writer.sqlite3");
  const source = `import { DatabaseSync } from 'node:sqlite';
    const db = new DatabaseSync(process.argv[1]); db.exec('BEGIN IMMEDIATE');
    process.stdout.write('locked'); setInterval(() => {}, 1000);`;
  const child = spawn(process.execPath, ["--input-type=module", "-e", source, lock], {
    windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
  });
  child.stderr.resume();
  try {
    const [data] = await once(child.stdout, "data", { signal: AbortSignal.timeout(5000) });
    assert.equal(data.toString(), "locked");
    const store = new SessionEventStore(root);
    await assert.rejects(store.append("session", "session/created", {}), /SESSION_WRITER_LOCKED/);
    const exited = once(child, "exit", { signal: AbortSignal.timeout(5000) });
    child.kill("SIGKILL");
    await exited;
    await store.append("session", "session/created", {});
    assert.equal((await store.read("session")).length, 1);
  } finally {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
  }
});
