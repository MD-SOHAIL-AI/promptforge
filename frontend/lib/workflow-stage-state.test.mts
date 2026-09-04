import assert from "node:assert/strict";
import test from "node:test";

import { applyStageEvent, settleActiveStages } from "./workflow-stage-state.ts";

const stages = [
  { key: "planning", label: "Planning", description: "", status: "success" },
  { key: "generation", label: "Generation", description: "", status: "active" },
  { key: "build", label: "Build", description: "", status: "pending" },
  { key: "flash", label: "Flash", description: "", status: "pending" },
  { key: "monitor", label: "Monitor", description: "", status: "pending" },
] as const;

test("files generated stays active until whole-project validation completes", () => {
  const next = applyStageEvent([...stages], {
    event: "GENERATION_COMPLETED",
    payload: { success: true },
  });

  assert.equal(next.find((stage) => stage.key === "generation")?.status, "active");
  const validated = applyStageEvent(next, {
    event: "PROJECT_VALIDATION_COMPLETED",
    payload: { success: true },
  });
  assert.equal(validated.find((stage) => stage.key === "generation")?.status, "success");
});

test("later workflow failure does not rewrite completed generation as failed", () => {
  const generated = applyStageEvent([...stages], {
    event: "PROJECT_VALIDATION_COMPLETED",
    payload: { success: true },
  });
  const building = applyStageEvent(generated, {
    event: "BUILD_STARTED",
    payload: {},
  });
  const settled = settleActiveStages(building, true);

  assert.equal(settled.find((stage) => stage.key === "generation")?.status, "success");
  assert.equal(settled.find((stage) => stage.key === "build")?.status, "failed");
});

test("repairable project validation remains active while repair runs", () => {
  const invalid = applyStageEvent([...stages], {
    event: "PROJECT_VALIDATION_FAILED",
    payload: { success: false },
  });
  const repairing = applyStageEvent(invalid, {
    event: "PROJECT_REPAIR_STARTED",
    payload: { attempt_number: 1 },
  });

  assert.equal(invalid.find((stage) => stage.key === "generation")?.status, "active");
  assert.equal(repairing.find((stage) => stage.key === "generation")?.status, "active");
});

test("build repair reactivates a failed build and can complete it", () => {
  const failed = applyStageEvent([...stages], { event: "BUILD_FAILED", payload: { success: false } });
  const repairing = applyStageEvent(failed, { event: "BUILD_REPAIR_STARTED", payload: { attempt_number: 1 } });
  const completed = applyStageEvent(repairing, { event: "BUILD_REPAIR_COMPLETED", payload: { success: true } });

  assert.equal(failed.find((stage) => stage.key === "build")?.status, "failed");
  assert.equal(repairing.find((stage) => stage.key === "build")?.status, "active");
  assert.equal(completed.find((stage) => stage.key === "build")?.status, "success");
});
