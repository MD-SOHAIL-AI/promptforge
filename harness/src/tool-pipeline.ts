import type { JsonValue, ToolCall, ToolResult } from "./contracts";

export interface ToolContext {
  signal: AbortSignal;
  emit: (type: string, payload: Record<string, JsonValue>) => Promise<void>;
}

export interface ToolDefinition {
  name: string;
  timeoutMs: number;
  execute: (call: ToolCall, context: ToolContext) => Promise<JsonValue>;
  finalize?: (value: JsonValue) => JsonValue;
}

export type ToolGuard = (call: Readonly<ToolCall>) => Promise<string | null> | string | null;

export class ToolPipeline {
  private readonly tools = new Map<string, ToolDefinition>();
  private readonly guards: ToolGuard[] = [];

  register(definition: ToolDefinition): () => void {
    if (!definition.name || this.tools.has(definition.name)) throw new Error("TOOL_REGISTRATION_INVALID");
    this.tools.set(definition.name, definition);
    return () => this.tools.delete(definition.name);
  }

  guard(guard: ToolGuard): () => void {
    this.guards.push(guard);
    return () => {
      const index = this.guards.indexOf(guard);
      if (index >= 0) this.guards.splice(index, 1);
    };
  }

  list(): string[] {
    return [...this.tools.keys()].sort();
  }

  async execute(call: ToolCall, emit: ToolContext["emit"]): Promise<Readonly<ToolResult>> {
    const startedAt = new Date().toISOString();
    const definition = this.tools.get(call.name);
    if (!definition) return this.result(call, startedAt, "failed", null, "TOOL_NOT_FOUND");
    for (const guard of this.guards) {
      const denial = await guard(Object.freeze(structuredClone(call)));
      if (denial) return this.result(call, startedAt, "denied", { message: denial }, "TOOL_POLICY_DENIED");
    }
    const controller = new AbortController();
    let settled = false;
    let timeout!: NodeJS.Timeout;
    const deadline = new Promise<never>((_, reject) => {
      timeout = setTimeout(() => {
        controller.abort("timeout");
        reject(new ToolTimeoutError());
      }, definition.timeoutMs);
    });
    const scopedEmit: ToolContext["emit"] = async (type, payload) => {
      if (!settled) await emit(type, payload);
    };
    try {
      await emit("tool/started", { callId: call.callId, name: call.name });
      const value = await Promise.race([
        definition.execute(call, { signal: controller.signal, emit: scopedEmit }),
        deadline,
      ]);
      const finalized = definition.finalize ? definition.finalize(value) : value;
      const result = this.result(call, startedAt, "completed", finalized);
      settled = true;
      await emit("tool/result", result as unknown as Record<string, JsonValue>);
      return result;
    } catch (error) {
      const status = error instanceof ToolTimeoutError || controller.signal.aborted ? "timed_out" : "failed";
      const result = this.result(call, startedAt, status, { message: error instanceof Error ? error.message : "Tool failed" }, status === "timed_out" ? "TOOL_TIMEOUT" : "TOOL_EXECUTION_FAILED");
      settled = true;
      await emit("tool/result", result as unknown as Record<string, JsonValue>);
      return result;
    } finally {
      clearTimeout(timeout);
    }
  }

  private result(call: ToolCall, startedAt: string, status: ToolResult["status"], content: JsonValue, errorCode?: string): Readonly<ToolResult> {
    return Object.freeze({ callId: call.callId, name: call.name, status, content: structuredClone(content), errorCode, startedAt, completedAt: new Date().toISOString() });
  }
}

class ToolTimeoutError extends Error {
  constructor() {
    super("Tool timed out");
    this.name = "ToolTimeoutError";
  }
}
