export const HARNESS_PROTOCOL = "forgex-harness-rpc-v1" as const;
export const HARNESS_RUNTIME = "forgex-harness-host-v1" as const;

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

export interface RpcRequest {
  protocol: typeof HARNESS_PROTOCOL;
  id: string;
  method: string;
  params: JsonObject;
  idempotencyKey?: string;
}

export interface RpcSuccess {
  protocol: typeof HARNESS_PROTOCOL;
  id: string;
  ok: true;
  result: JsonValue;
}

export interface RpcFailure {
  protocol: typeof HARNESS_PROTOCOL;
  id: string;
  ok: false;
  error: { code: string; message: string; retryable: boolean };
}

export type RpcResponse = RpcSuccess | RpcFailure;

export interface SessionEvent {
  schemaVersion: 1;
  eventId: string;
  sessionId: string;
  runId: string | null;
  sequence: number;
  timestamp: string;
  type: string;
  payload: JsonObject;
  previousChecksum: string;
  checksum: string;
}

export interface ProviderDescriptor {
  id: string;
  displayName: string;
  protocol: "deepseek" | "openai-completions" | "openai-responses" | "anthropic-messages";
  baseUrl: string;
  local: boolean;
  credentialRequired: boolean;
  modelDiscovery: boolean;
}

export interface ToolCall {
  callId: string;
  name: string;
  arguments: JsonObject;
  sessionId: string;
  runId: string;
}

export interface ToolResult {
  callId: string;
  name: string;
  status: "completed" | "denied" | "failed" | "cancelled" | "timed_out";
  content: JsonValue;
  errorCode?: string;
  startedAt: string;
  completedAt: string;
}

export function isJsonObject(value: unknown): value is JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  return Object.values(value).every(isJsonValue);
}

export function isJsonValue(value: unknown): value is JsonValue {
  if (value === null || ["string", "number", "boolean"].includes(typeof value)) return true;
  if (Array.isArray(value)) return value.every(isJsonValue);
  return isJsonObject(value);
}
