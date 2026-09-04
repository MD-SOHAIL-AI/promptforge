import assert from "node:assert/strict";
import test from "node:test";

import {
  agentEventKey,
  remainingActivityMs,
  shouldAcceptActivity,
  shouldRefreshRunForEvent,
  snapshotFromActivityEvent,
  snapshotFromRunEvent,
} from "./forge-agent-activity.ts";

test("session activity event creates an active snapshot with backend label", () => {
  const snapshot = snapshotFromActivityEvent({
    sequence: 1,
    event_type: "activity.updated",
    activity: "inspecting",
    label: "Inspecting",
    phase: "active",
    session_id: "session-a",
    run_id: null,
    message_id: "message-a",
    created_at: new Date().toISOString(),
  }, 1000);

  assert.equal(snapshot?.label, "Inspecting");
  assert.equal(snapshot?.sessionId, "session-a");
  assert.equal(snapshot?.startedAt, 1000);
});

test("completed activity event clears visible activity", () => {
  const snapshot = snapshotFromActivityEvent({
    sequence: 2,
    event_type: "activity.completed",
    activity: "finishing",
    label: "Wrapping up",
    phase: "completed",
    session_id: "session-a",
    created_at: new Date().toISOString(),
  });

  assert.equal(snapshot, null);
});

test("run events create activity snapshots when runtime provides activity fields", () => {
  const snapshot = snapshotFromRunEvent({
    run_id: "run-a",
    sequence: 1,
    event_type: "build_started",
    activity: "building",
    activity_label: "Building",
    activity_phase: "active",
  }, "session-a", 2000);

  assert.equal(snapshot?.label, "Building");
  assert.equal(snapshot?.runId, "run-a");
  assert.equal(snapshot?.sessionId, "session-a");
});

test("activity is isolated to the selected session and run", () => {
  const snapshot = {
    activity: "building",
    label: "Building",
    phase: "active",
    sessionId: "session-a",
    runId: "run-a",
    startedAt: 1000,
  };

  assert.equal(shouldAcceptActivity(snapshot, { activeSessionId: "session-a", activeRunId: "run-a" }), true);
  assert.equal(shouldAcceptActivity(snapshot, { activeSessionId: "session-b", activeRunId: "run-a" }), false);
  assert.equal(shouldAcceptActivity(snapshot, { activeSessionId: "session-a", activeRunId: "run-b" }), false);
});

test("minimum activity display duration is presentation-only", () => {
  assert.equal(remainingActivityMs(1000, 1100, 400), 300);
  assert.equal(remainingActivityMs(1000, 1500, 400), 0);
});

test("agent event keys include run id so resumed runs do not collide", () => {
  const first = agentEventKey({ run_id: "run-a", sequence: 1, event_type: "tool.completed" });
  const second = agentEventKey({ run_id: "run-b", sequence: 1, event_type: "tool.completed" });

  assert.notEqual(first, second);
});

test("message deltas do not force a full run refresh", () => {
  assert.equal(shouldRefreshRunForEvent({
    run_id: "run-a",
    sequence: 5,
    event_type: "message.delta",
    delta: "{",
    index: 1,
  }), false);
});

test("lifecycle events still request a full run refresh", () => {
  assert.equal(shouldRefreshRunForEvent({
    run_id: "run-a",
    sequence: 9,
    event_type: "runtime.completed",
    status: "completed",
  }), true);
  assert.equal(shouldRefreshRunForEvent({
    run_id: "run-a",
    sequence: 10,
    event_type: "node.completed",
    node_id: "validation",
  }), true);
});
