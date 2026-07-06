import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  appendGenericEvent,
  isGenericRunTerminal,
  mergeGenericRun,
  normalizedRunMessage,
} from "./generic-agent-state.ts";

const run = (status: string, updated_at = "2026-06-29T00:00:00Z") => ({
  run_id: "bridge-run-agent-001",
  provider_id: "agy" as const,
  status,
  created_at: "2026-06-29T00:00:00Z",
  updated_at,
  progress: 50,
  artifact_count: 0,
  changed_file_count: 0,
  cancellation_requested: false,
  cancellable: true,
});

const event = (sequence: number, event_id = `event-${sequence}`, run_id = "bridge-run-agent-001") => ({
  event_id,
  run_id,
  sequence,
  timestamp: "2026-06-29T00:00:00Z",
  event_type: "state_changed",
});

test("recognizes every terminal Agent state", () => {
  for (const status of ["completed", "cancelled", "failed", "blocked", "timed_out", "interrupted"]) {
    assert.equal(isGenericRunTerminal(status as never), true);
  }
  assert.equal(isGenericRunTerminal("running"), false);
});

test("terminal run state never regresses", () => {
  const current = { ...run("completed"), progress: 100, cancellable: false } as never;
  const stale = run("running", "2026-06-29T00:01:00Z") as never;
  assert.equal(mergeGenericRun(current, stale), current);
});

test("older detail polling results are ignored", () => {
  const current = run("running", "2026-06-29T00:02:00Z") as never;
  const stale = run("validating", "2026-06-29T00:01:00Z") as never;
  assert.equal(mergeGenericRun(current, stale), current);
});

test("events are ordered and duplicates are removed", () => {
  const first = appendGenericEvent([], event(1), "bridge-run-agent-001");
  const duplicate = appendGenericEvent(first as never, event(1, "another-id") as never, "bridge-run-agent-001");
  const second = appendGenericEvent(duplicate as never, event(2) as never, "bridge-run-agent-001");
  assert.deepEqual(second.map((item) => item.sequence), [1, 2]);
});

test("stale events from another run are ignored", () => {
  const events = appendGenericEvent([], event(1, "event-other", "bridge-run-other") as never, "bridge-run-agent-001");
  assert.deepEqual(events, []);
});

test("failure normalization never requires provider output", () => {
  assert.equal(normalizedRunMessage({ ...run("failed"), failure_code: "process_failed", safe_failure_message: null } as never), "The agent run ended safely without provider details.");
});

test("hook owns one EventSource and guards double submission", () => {
  const source = readFileSync(new URL("../hooks/use-generic-agent-run.ts", import.meta.url), "utf8");
  assert.equal((source.match(/new EventSource\(/g) ?? []).length, 1);
  assert.match(source, /submitLockRef\.current/);
  assert.match(source, /startPolling/);
  assert.doesNotMatch(source, /localStorage\.setItem\([^\n]*instruction/i);
});

test("generic run panel contains review navigation but no Apply action", () => {
  const source = readFileSync(new URL("../components/ide/generic-agent-panel.tsx", import.meta.url), "utf8");
  assert.match(source, /Open review/);
  assert.doesNotMatch(source, />\s*Apply\s*</);
});
