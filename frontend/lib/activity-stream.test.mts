import assert from "node:assert/strict";
import test from "node:test";
import {
  activityElapsed,
  groupActivities,
  mergeActivityEvents,
  validActivity,
  streamedAgentText,
  settleActivity,
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
test("stopped, paused and finished runs never manufacture successful tool evidence", () => {
  assert.equal(settleActivity(a, "cancelled", 6000).status, "cancelled");
  assert.equal(settleActivity(a, "paused_model_unavailable", 6000).status, "interrupted");
  assert.equal(settleActivity(a, "completed", 6000).status, "interrupted");
  assert.equal(settleActivity({ ...a, status: "completed", durationMs: 1000 }, "cancelled", 6000).status, "completed");
});
test("terminal run settles active activities without changing another run", () => {
  const current = [event(1), event(2, { ...a, id: "other", runId: "two" })];
  const state = mergeActivityEvents(current, { run_id: "one", sequence: 3, event_type: "runtime.failed", status: "failed" });
  assert.equal(state.find((item) => item.run_id === "one")?.activity_record?.status, "failed");
  assert.equal(state.find((item) => item.run_id === "two")?.activity_record?.status, "running");
});
test("failed run freezes reasoning duration at its recorded end during replay", () => {
  const thinking = { ...a, kind: "thinking", startedAt: 2000 };
  const root = { ...a, id: "root", kind: "run", status: "failed" as const,
    startedAt: 1000, durationMs: 3709 };
  const final = { ...event(3, root), event_type: "runtime.failed" };
  const state = mergeActivityEvents([event(1, thinking)], final);
  const activity = state[0].activity_record!;
  assert.equal(activity.durationMs, 2709);
  assert.equal(activityElapsed(activity, 999999), 2709);
  assert.deepEqual(mergeActivityEvents(state, final), state);
});
test("terminal event without a root activity uses its timestamp to stop timers", () => {
  const state = mergeActivityEvents([event(1)], {
    run_id: "one", sequence: 2, event_type: "runtime.cancelled",
    timestamp: new Date(6000).toISOString(),
  });
  assert.equal(state[0].activity_record?.durationMs, 5000);
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

test("legacy warning completion freezes the root while preserving a verified build", () => {
  const root = { ...a, id: "root", kind: "run", startedAt: 1000 };
  let state = mergeActivityEvents([], event(151, root));
  state = mergeActivityEvents(state, event(213, {
    ...a, id: "build", kind: "build", status: "completed", durationMs: 71519,
  }));
  state = mergeActivityEvents(state, {
    ...event(218, root), event_type: "runtime.completed_with_warning",
    status: "completed_with_warning", timestamp: new Date(84000).toISOString(),
  });
  assert.equal(state.find(e => e.activity_record?.kind === "build")?.activity_record?.status, "completed");
  assert.equal(state.find(e => e.activity_record?.kind === "run")?.activity_record?.status, "warning");
  assert.equal(state.find(e => e.activity_record?.kind === "run")?.activity_record?.durationMs, 83000);
});

test("legacy retry notification cannot restart its paused source run timer", () => {
  const root = { ...a, id: "root", kind: "run" };
  let state = mergeActivityEvents([], event(3, root));
  state = mergeActivityEvents(state, {
    ...event(85, root), event_type: "runtime.paused_model_unavailable",
    status: "paused_model_unavailable", timestamp: new Date(6000).toISOString(),
  });
  state = mergeActivityEvents(state, {
    ...event(87, root), event_type: "runtime.retried",
    status: "paused_model_unavailable", timestamp: new Date(9000).toISOString(),
  });
  assert.equal(state[0].activity_record?.status, "interrupted");
  assert.equal(activityElapsed(state[0].activity_record!, 999999), 5000);
});
