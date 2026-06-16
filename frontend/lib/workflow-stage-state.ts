import type { ExecutionEvent, ExecutionEventName, StageKey, StageStatus, WorkflowStage } from "@/types";

const stageOrder: StageKey[] = ["planning", "generation", "build", "flash", "monitor"];

const eventStages: Partial<Record<ExecutionEventName, StageKey>> = {
  TASK_CREATED: "planning",
  PLAN_GENERATED: "planning",
  PLAN_FAILED: "planning",
  CODE_GENERATION_STARTED: "generation",
  CODE_GENERATION_COMPLETED: "generation",
  CODE_GENERATION_FAILED: "generation",
  BUILD_STARTED: "build",
  BUILD_COMPLETED: "build",
  BUILD_FAILED: "build",
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
  return status === "FAILED" || success === false;
}

export function workflowFailed(event: ExecutionEvent) {
  return event.event === "WORKFLOW_FAILED" || payloadFailed(event);
}

export function terminalStatus(event: ExecutionEvent) {
  if (workflowFailed(event)) return "FAILED";
  const status = typeof event.payload.status === "string" ? event.payload.status.toUpperCase() : "";
  return status || "COMPLETED";
}

export function isTerminalEvent(event: ExecutionEvent) {
  return event.event === "WORKFLOW_COMPLETED" || event.event === "WORKFLOW_FAILED";
}

export function stageForEvent(eventName: ExecutionEventName): StageKey | null {
  return eventStages[eventName] ?? null;
}

function statusForStageEvent(event: ExecutionEvent): StageStatus {
  if (event.event.endsWith("_FAILED") || payloadFailed(event)) return "failed";
  if (event.event === "PLAN_GENERATED" || event.event.endsWith("_COMPLETED")) return "success";
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
