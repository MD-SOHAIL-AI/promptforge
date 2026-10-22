import assert from "node:assert/strict";
import test from "node:test";

import { pendingWorkspaceChangeCount, reasoningSummary } from "./nexus-agent-view.ts";

test("reasoning summary does not render arbitrary provider turn text", () => {
  const value = reasoningSummary([
    { run_id: "r", sequence: 1, event_type: "agent.turn.completed", message: "The error is isolated to display initialization." },
  ], null, null);
  assert.equal(value, "Waiting for the next model or tool event.");
});

test("pending workspace change count only includes unapplied completed review runs", () => {
  const base = {
    run_id: "r",
    change_set_id: "c",
    status: "completed",
    classification: "CHANGESET_REVIEW_REQUIRED",
    active_workspace_unchanged: true,
    created_file_count: 2,
    modified_file_count: 1,
    deleted_file_count: 0,
  } as never;
  assert.equal(pendingWorkspaceChangeCount(base), 3);
  assert.equal(pendingWorkspaceChangeCount({ ...base, active_workspace_unchanged: false } as never), 0);
  assert.equal(pendingWorkspaceChangeCount({ ...base, classification: "CHANGESET_APPLIED" } as never), 0);
  assert.equal(pendingWorkspaceChangeCount({ ...base, status: "running" } as never), 0);
});
