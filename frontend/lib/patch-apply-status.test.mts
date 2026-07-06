import assert from "node:assert/strict";
import test from "node:test";

import { patchApplyDisplayState } from "./patch-apply-status.ts";

const apply = (status: string, message = "") => ({
  status,
  files_failed: message ? 1 : 0,
  results: message ? [{ status: "failed", message }] : [],
});

test("maps canonical apply and rollback statuses", () => {
  assert.equal(patchApplyDisplayState(apply("applied")).label, "Applied");
  assert.equal(patchApplyDisplayState(apply("failed", "Safe failure.")).label, "Failed");
  assert.equal(patchApplyDisplayState(apply("failed_rolled_back")).label, "Failed, rolled back");
  assert.equal(patchApplyDisplayState(apply("failed_rollback_failed")).label, "Failed, rollback failed");
});

test("persisted restore status takes precedence consistently", () => {
  assert.equal(patchApplyDisplayState(apply("applied"), apply("restored")).label, "Restored");
  const failed = patchApplyDisplayState(apply("applied"), apply("failed", "Restore validation failed."));
  assert.equal(failed.label, "Restore failed");
  assert.equal(failed.reason, "Restore validation failed.");
});
