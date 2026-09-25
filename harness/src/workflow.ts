import { randomUUID } from "node:crypto";

import type { JsonValue } from "./contracts";
import { SubagentRuntime, type SubagentRequest, type SubagentResult } from "./subagents";

export interface WorkflowRequest {
  parentSessionId: string;
  parentRunId: string;
  provider: string;
  strategy: "parallel" | "pipeline";
  tasks: string[];
  depth: number;
}

export interface WorkflowResult {
  workflowId: string;
  status: "completed" | "partial" | "failed";
  children: ReadonlyArray<Readonly<SubagentResult>>;
  output: JsonValue;
}

export class WorkflowEngine {
  constructor(
    private readonly subagents: SubagentRuntime,
    private readonly maxTasks = 16,
    private readonly maxParallel = 4,
  ) {}

  async run(request: WorkflowRequest): Promise<Readonly<WorkflowResult>> {
    if (!request.tasks.length || request.tasks.length > this.maxTasks || request.tasks.some((task) => !task.trim())) {
      throw new Error("WORKFLOW_TASKS_INVALID");
    }
    const workflowId = `workflow-${randomUUID()}`;
    const base = (task: string): SubagentRequest => ({
      parentSessionId: request.parentSessionId,
      parentRunId: request.parentRunId,
      provider: request.provider,
      task,
      mode: "spawn",
      depth: request.depth,
      context: { workflowId },
    });
    const children: SubagentResult[] = [];
    if (request.strategy === "parallel") {
      for (let offset = 0; offset < request.tasks.length; offset += this.maxParallel) {
        const batch = request.tasks.slice(offset, offset + this.maxParallel);
        children.push(...await Promise.all(batch.map((task) => this.subagents.start(base(task)))));
      }
    } else {
      let prior: JsonValue = null;
      for (const task of request.tasks) {
        const child = await this.subagents.start({ ...base(task), context: { workflowId, prior } });
        children.push(child as SubagentResult);
        prior = child.output;
        if (child.status !== "completed") break;
      }
    }
    const completed = children.filter((child) => child.status === "completed").length;
    const status = completed === children.length && children.length === request.tasks.length ? "completed" : completed ? "partial" : "failed";
    return Object.freeze({ workflowId, status, children: Object.freeze(children), output: children.map((child) => child.output) });
  }
}
