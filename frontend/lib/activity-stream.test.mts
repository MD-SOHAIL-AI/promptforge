import assert from "node:assert/strict";
import test from "node:test";
import {
  activityElapsed,
  groupActivities,
  mergeActivityEvents,
  validActivity,
  streamedAgentText,
  type ActivityEnvelope,
  type ActivityRecord,
} from "./activity-stream.ts";
const a: ActivityRecord = {
  id: "cmd",
  runId: "one",
  kind: "command",
  status: "running",
  title: "Command",
  startedAt: 1000,
  durationMs: null,
  details: {},
};
const event = (sequence: number, activity = a): ActivityEnvelope => ({
  sequence,
  run_id: activity.runId,
  activity_record: activity,
});
test("start, complete and duplicate correlate", () => {
  let state = mergeActivityEvents([], event(1));
  assert.equal(state.length, 1);
  state = mergeActivityEvents(
    state,
    event(2, { ...a, status: "completed", durationMs: 4000 }),
  );
  state = mergeActivityEvents(state, event(2));
  assert.equal(state.length, 1);
  assert.equal(state[0].activity_record?.status, "completed");
});
test("run isolation and defensive ordering", () => {
  let state = mergeActivityEvents([], event(3, { ...a, status: "failed" }));
  state = mergeActivityEvents(state, event(1));
  assert.equal(state[0].activity_record?.status, "failed");
  state = mergeActivityEvents(state, event(1, { ...a, runId: "two" }));
  assert.equal(state.length, 2);
});
test("timer freezes, approvals and cancellation validate", () => {
  assert.equal(activityElapsed(a, 4000), 3000);
  assert.equal(activityElapsed({ ...a, durationMs: 2000 }, 99999), 2000);
  for (const status of [
    "awaiting_approval",
    "completed",
    "cancelled",
    "failed",
  ] as const)
    assert.ok(validActivity({ ...a, status }));
});
test("unknown events and malformed records are safe", () => {
  assert.equal(
    mergeActivityEvents([], { sequence: 1, run_id: "one" }).length,
    1,
  );
  assert.equal(validActivity({ ...a, startedAt: "bad" }), false);
  assert.equal(
    mergeActivityEvents([], { ...event(1), run_id: "wrong" }).length,
    0,
  );
});
test("output snapshots replay without duplicate chunks", () => {
  let state = mergeActivityEvents(
    [],
    event(1, { ...a, details: { output: "first" } }),
  );
  state = mergeActivityEvents(
    state,
    event(2, { ...a, details: { output: "first second" } }),
  );
  state = mergeActivityEvents(state, event(1));
  assert.equal(state[0].activity_record?.details.output, "first second");
});
test("group only adjacent completed reads in the same run", () => {
  const read = { ...a, kind: "file.read", status: "completed" as const };
  const groups = groupActivities([
    read,
    { ...read, id: "two" },
    a,
    { ...read, runId: "other" },
  ]);
  assert.deepEqual(
    groups.map((g) => g.length),
    [2, 1, 1],
  );
});
test("agent response grows from real public summary snapshots", () => {
  const first = {
    ...event(1, { ...a, id: "thinking-1", kind: "thinking", details: { summary: "Inspecting" } }),
    event_type: "assistant_summary_delta",
    message: "Inspecting",
    turn: 1,
  };
  const expanded = { ...first, sequence: 2, message: "Inspecting the project files" };
  const next = {
    ...event(3, { ...a, id: "thinking-2", kind: "thinking", details: { summary: "Preparing the change" } }),
    event_type: "assistant.summary.delta",
    message: "Preparing the change",
    turn: 2,
  };
  assert.equal(
    streamedAgentText([first, expanded, next], "one"),
    "Inspecting the project files\n\nPreparing the change",
  );
  assert.equal(
    streamedAgentText(
      [{ ...first, event_type: "assistant_summary_delta" }],
      "one",
    ),
    "Inspecting",
  );
  assert.equal(streamedAgentText([first], "another-run"), "");
});
