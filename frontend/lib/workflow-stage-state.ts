import type { ExecutionEvent, ExecutionEventName, StageKey, StageStatus, WorkflowStage } from "@/types";

const stageOrder: StageKey[] = ["planning", "generation", "build", "flash", "monitor"];

const eventStages: Partial<Record<ExecutionEventName, StageKey>> = {
  TASK_CREATED: "planning",
  PLAN_GENERATED: "planning",
  PLAN_FAILED: "planning",
  CODE_GENERATION_STARTED: "generation",
  CODE_GENERATION_COMPLETED: "generation",
  CODE_GENERATION_FAILED: "generation",
  GENERATION_STRATEGY_SELECTED: "generation",
  GENERATION_STARTED: "generation",
  GENERATION_FAILED: "generation",
  REQUIREMENTS_EXTRACTION_STARTED: "generation",
  REQUIREMENTS_EXTRACTED: "generation",
  MANIFEST_GENERATION_STARTED: "generation",
  MANIFEST_CREATED: "generation",
  FILE_GENERATION_STARTED: "generation",
  FILE_GENERATION_REPAIR_STARTED: "generation",
  FILE_GENERATION_FALLBACK_STARTED: "generation",
  FILE_GENERATION_VALIDATED: "generation",
  FILE_WRITTEN: "generation",
  FILE_FAILED: "generation",
  GENERATION_INCOMPLETE: "generation",
  GENERATION_COMPLETED: "generation",
  BUILD_BLOCKED: "build",
  BUILD_STARTED: "build",
  BUILD_COMPLETED: "build",
  BUILD_FAILED: "build",
  BUILD_REPAIR_STARTED: "build",
  BUILD_REPAIR_COMPLETED: "build",
  BUILD_REPAIR_FAILED: "build",
  FLASH_STARTED: "flash",
  FLASH_COMPLETED: "flash",
  FLASH_FAILED: "flash",
  MONITOR_STARTED: "monitor",
  MONITOR_COMPLETED: "monitor",
  MONITOR_FAILED: "monitor",
};

function payloadFailed(event: ExecutionEvent) {
  const status = typeof event.payload.status === "string" ? event.payload.status.toUpperCase() : "";
  const success = typeof event.payload.success === "boolean" ? event.payload.success : undefined;
  if (["WAITING_FOR_DEVICE", "BLOCKED", "SKIPPED", "COMPLETED_WITH_PENDING_HARDWARE"].includes(status)) {
    return false;
  }
  return status === "FAILED" || success === false;
}

export function workflowFailed(event: ExecutionEvent) {
  return event.event === "WORKFLOW_FAILED" || payloadFailed(event);
}

export function workflowCancelled(event: ExecutionEvent) {
  return event.event === "WORKFLOW_CANCELLED";
}

export function terminalStatus(event: ExecutionEvent) {
  if (workflowCancelled(event)) return "CANCELLED";
  if (workflowFailed(event)) return "FAILED";
  const status = typeof event.payload.status === "string" ? event.payload.status.toUpperCase() : "";
  return status || "COMPLETED";
}

export function isTerminalEvent(event: ExecutionEvent) {
  return event.event === "WORKFLOW_COMPLETED" || event.event === "WORKFLOW_FAILED" || event.event === "WORKFLOW_CANCELLED";
}

export function stageForEvent(eventName: ExecutionEventName): StageKey | null {
  return eventStages[eventName] ?? null;
}

function statusForStageEvent(event: ExecutionEvent): StageStatus {
  const payloadStatus = typeof event.payload.status === "string" ? event.payload.status.toUpperCase() : "";
  if (payloadStatus === "WAITING_FOR_DEVICE") return "waiting_for_device";
  if (payloadStatus === "BLOCKED") return "blocked";
  if (payloadStatus === "SKIPPED") return "skipped";
  if (event.event === "BUILD_BLOCKED") return "blocked";
  if (event.event === "GENERATION_INCOMPLETE" || event.event === "GENERATION_FAILED") return "failed";
  if (
    event.event === "CODE_GENERATION_COMPLETED" &&
    (event.payload.artifact_validated === false ||
      event.payload.generation_satisfied_prompt === false)
  ) {
    return "failed";
  }
  if (event.event.endsWith("_FAILED") || payloadFailed(event)) return "failed";
  if (event.event === "PLAN_GENERATED" || event.event === "GENERATION_COMPLETED" || event.event.endsWith("_COMPLETED")) return "success";
  if (event.event.startsWith("GENERATION_") || event.event.startsWith("REQUIREMENTS_") || event.event.startsWith("MANIFEST_") || event.event.startsWith("FILE_")) {
    return "active";
  }
  return "active";
}

function stageIndex(key: StageKey) {
  return stageOrder.indexOf(key);
}

function shouldPromotePrerequisite(stage: WorkflowStage, changedStage: StageKey, nextStatus: StageStatus) {
  return nextStatus === "success" && stageIndex(stage.key) < stageIndex(changedStage);
}

export function applyStageEvent(stages: WorkflowStage[], event: ExecutionEvent): WorkflowStage[] {
  const key = stageForEvent(event.event);
  if (!key) return stages;

  const nextStatus = statusForStageEvent(event);

  return stages.map((stage) => {
    if (stage.status === "success") return stage;
    if (shouldPromotePrerequisite(stage, key, nextStatus)) return { ...stage, status: "success" };
    if (stage.key !== key) return stage;
    return stage.status === nextStatus ? stage : { ...stage, status: nextStatus };
  });
}

export function settleActiveStages(stages: WorkflowStage[], failed: boolean): WorkflowStage[] {
  return stages.map((stage) =>
    stage.status === "active" ? { ...stage, status: failed ? "failed" : "success" } : stage,
  );
}

export function cancelActiveStages(stages: WorkflowStage[]): WorkflowStage[] {
  return stages.map((stage) => (stage.status === "active" ? { ...stage, status: "cancelled" } : stage));
}
