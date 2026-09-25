import { randomUUID } from "node:crypto";

import { HARNESS_RUNTIME, type JsonObject, type JsonValue } from "./contracts";
import { SessionEventStore } from "./event-store";
import { PROVIDERS } from "./providers";
import { ToolPipeline } from "./tool-pipeline";
import { PluginRegistry } from "./plugins";
import { SubagentRuntime } from "./subagents";
import { WorkflowEngine } from "./workflow";

export class HarnessRuntime {
  readonly tools = new ToolPipeline();
  readonly plugins = new PluginRegistry();
  readonly subagents: SubagentRuntime;
  readonly workflows: WorkflowEngine;
  readonly startedAt = new Date().toISOString();

  constructor(readonly events: SessionEventStore) {
    this.subagents = new SubagentRuntime(events);
    this.workflows = new WorkflowEngine(this.subagents);
  }

  async initialize(): Promise<void> {
    await this.events.initialize();
  }

  async invoke(method: string, params: JsonObject): Promise<JsonValue> {
    switch (method) {
      case "harness.status":
        return { runtime: HARNESS_RUNTIME, startedAt: this.startedAt, protocolVersion: 1, authoritative: false, mode: "shadow" };
      case "providers.list":
        return { providers: PROVIDERS as unknown as JsonValue };
      case "tools.list":
        return { tools: this.tools.list() };
      case "plugins.list":
        return { plugins: this.plugins.list() as unknown as JsonValue };
      case "subagents.providers":
        return { providers: this.subagents.listProviders() };
      case "subagents.list": {
        const sessionId = this.requiredText(params.sessionId, "sessionId");
        return { children: await this.subagents.listChildren(sessionId) as unknown as JsonValue };
      }
      case "sessions.list":
        return { sessionIds: await this.events.listSessions() };
      case "sessions.create": {
        const sessionId = this.text(params.sessionId) ?? `session-${randomUUID()}`;
        const event = await this.events.append(sessionId, "session/created", {
          projectId: this.text(params.projectId),
          title: this.text(params.title),
        });
        return { sessionId, event: event as unknown as JsonValue };
      }
      case "sessions.events": {
        const sessionId = this.requiredText(params.sessionId, "sessionId");
        return { events: await this.events.read(sessionId) as unknown as JsonValue };
      }
      case "sessions.append": {
        const sessionId = this.requiredText(params.sessionId, "sessionId");
        const type = this.requiredText(params.type, "type");
        const payload = params.payload;
        if (!payload || typeof payload !== "object" || Array.isArray(payload)) throw new RpcMethodError("RPC_PARAMS_INVALID", "payload must be an object");
        const event = await this.events.append(sessionId, type, payload as JsonObject, this.text(params.runId));
        return { event: event as unknown as JsonValue };
      }
      default:
        throw new RpcMethodError("RPC_METHOD_NOT_FOUND", `Unknown method: ${method}`);
    }
  }

  private requiredText(value: JsonValue | undefined, name: string): string {
    const result = this.text(value);
    if (!result) throw new RpcMethodError("RPC_PARAMS_INVALID", `${name} is required`);
    return result;
  }

  private text(value: JsonValue | undefined): string | null {
    return typeof value === "string" && value.trim() ? value.trim() : null;
  }
}

export class RpcMethodError extends Error {
  constructor(readonly code: string, message: string, readonly retryable = false) {
    super(message);
    this.name = "RpcMethodError";
  }
}
