import { randomUUID } from "node:crypto";

import type { JsonObject, JsonValue } from "./contracts";
import { SessionEventStore } from "./event-store";

export interface SubagentRequest {
  parentSessionId: string;
  parentRunId: string;
  provider: string;
  task: string;
  mode: "spawn" | "fork" | "continue";
  depth: number;
  maxDepth?: number;
  timeoutMs?: number;
  context?: JsonObject;
  childSessionId?: string;
}

export interface SubagentResult {
  childSessionId: string;
  activationId: string;
  status: "completed" | "failed" | "cancelled" | "timed_out";
  output: JsonValue;
  startedAt: string;
  completedAt: string;
}

export type SubagentProvider = (
  request: Readonly<SubagentRequest>,
  signal: AbortSignal,
) => Promise<JsonValue>;

export class SubagentRuntime {
  private readonly providers = new Map<string, SubagentProvider>();
  private active = 0;

  constructor(
    private readonly events: SessionEventStore,
    private readonly maxConcurrent = 4,
    private readonly absoluteMaxDepth = 2,
  ) {}

  register(name: string, provider: SubagentProvider): () => void {
    if (!/^[a-z][a-z0-9_-]{0,63}$/.test(name) || this.providers.has(name)) {
      throw new Error("SUBAGENT_PROVIDER_INVALID");
    }
    this.providers.set(name, provider);
    return () => this.providers.delete(name);
  }

  listProviders(): string[] {
    return [...this.providers.keys()].sort();
  }

  async start(raw: SubagentRequest): Promise<Readonly<SubagentResult>> {
    const request = Object.freeze(structuredClone(raw));
    const maxDepth = Math.min(request.maxDepth ?? this.absoluteMaxDepth, this.absoluteMaxDepth);
    if (!Number.isSafeInteger(request.depth) || request.depth < 0 || request.depth >= maxDepth) {
      throw new Error("SUBAGENT_DEPTH_EXCEEDED");
    }
    if (!request.task.trim() || request.task.length > 16_384) throw new Error("SUBAGENT_TASK_INVALID");
    const provider = this.providers.get(request.provider);
    if (!provider) throw new Error("SUBAGENT_PROVIDER_UNAVAILABLE");
    if (this.active >= this.maxConcurrent) throw new Error("SUBAGENT_CONCURRENCY_EXCEEDED");
    if (request.mode === "continue" && !request.childSessionId) throw new Error("SUBAGENT_CHILD_REQUIRED");

    const childSessionId = request.childSessionId ?? `subagent-${randomUUID()}`;
    const activationId = `activation-${randomUUID()}`;
    const startedAt = new Date().toISOString();
    const controller = new AbortController();
    const timeoutMs = Math.max(1_000, Math.min(request.timeoutMs ?? 120_000, 600_000));
    let timeout!: NodeJS.Timeout;
    const deadline = new Promise<never>((_, reject) => {
      timeout = setTimeout(() => {
        controller.abort("timeout");
        reject(new SubagentTimeoutError());
      }, timeoutMs);
    });
    this.active += 1;
    try {
      await this.events.append(request.parentSessionId, "subagent/start", {
        activationId,
        childSessionId,
        provider: request.provider,
        mode: request.mode,
        depth: request.depth + 1,
        task: request.task,
      }, request.parentRunId);
      if (request.mode !== "continue") {
        await this.events.append(childSessionId, "session/created", {
          parentSessionId: request.parentSessionId,
          activationId,
          delegationDepth: request.depth + 1,
        });
      }
      try {
        const output = await Promise.race([provider(request, controller.signal), deadline]);
        return await this.settle(request, childSessionId, activationId, "completed", output, startedAt);
      } catch (error) {
        const status = controller.signal.aborted ? "timed_out" : "failed";
        return await this.settle(
          request,
          childSessionId,
          activationId,
          status,
          { message: error instanceof Error ? error.message : "Subagent failed" },
          startedAt,
        );
      }
    } finally {
      clearTimeout(timeout);
      this.active -= 1;
    }
  }

  async listChildren(parentSessionId: string): Promise<Array<{ childSessionId: string; activationId: string; status: string }>> {
    const events = await this.events.read(parentSessionId);
    const children = new Map<string, { childSessionId: string; activationId: string; status: string }>();
    for (const event of events) {
      if (event.type === "subagent/start") {
        const childSessionId = String(event.payload.childSessionId ?? "");
        const activationId = String(event.payload.activationId ?? "");
        if (childSessionId && activationId) children.set(activationId, { childSessionId, activationId, status: "running" });
      } else if (event.type === "subagent/end") {
        const activationId = String(event.payload.activationId ?? "");
        const child = children.get(activationId);
        if (child) child.status = String(event.payload.status ?? "failed");
      }
    }
    return [...children.values()];
  }

  private async settle(
    request: SubagentRequest,
    childSessionId: string,
    activationId: string,
    status: SubagentResult["status"],
    output: JsonValue,
    startedAt: string,
  ): Promise<Readonly<SubagentResult>> {
    const result = Object.freeze({ childSessionId, activationId, status, output: structuredClone(output), startedAt, completedAt: new Date().toISOString() });
    await this.events.append(childSessionId, "subagent/result", { activationId, status, output }, request.parentRunId);
    await this.events.append(request.parentSessionId, "subagent/end", { activationId, childSessionId, status }, request.parentRunId);
    return result;
  }
}

class SubagentTimeoutError extends Error {
  constructor() {
    super("Subagent timed out");
    this.name = "SubagentTimeoutError";
  }
}
