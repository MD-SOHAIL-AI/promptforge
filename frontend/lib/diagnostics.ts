import { localSession } from "./local-session.ts";
const API_BASE = "/api/forgex";

const SECRET_KEY_RE = /(authorization|cookie|api[_-]?key|token|password|secret|credential|private[_-]?key)/i;
const BEARER_RE = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi;
const COMMON_SECRET_RE = /\b(?:sk|sk-or|ghp|AIza)[A-Za-z0-9_-]{12,}\b/g;

export interface RuntimeDiagnosticEvent {
  component: string;
  event: string;
  level?: "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL";
  message?: string;
  fields?: Record<string, unknown>;
  [key: string]: unknown;
}

export function emitRuntimeDiagnostic(event: RuntimeDiagnosticEvent): void {
  const payload = redact(event) as RuntimeDiagnosticEvent;
  if (typeof window !== "undefined" && window.forgexDesktop?.emitDiagnostic) {
    void window.forgexDesktop.emitDiagnostic(payload).catch(() => null);
    return;
  }
  if (typeof fetch !== "function" || localSession.getSnapshot()) return;
  const { component, event: eventName, level, message, fields, ...rest } = payload;
  void fetch(`${API_BASE}/diagnostics/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ component, event: eventName, level, message, fields: { ...(fields ?? {}), ...rest } }),
    cache: "no-store",
    keepalive: true,
  }).then(response => { if (response.status === 401) localSession.reject(); }).catch(() => null);
}

export function serializeError(value: unknown): Record<string, unknown> {
  if (value instanceof Error) {
    const maybe = value as Error & { status?: unknown; code?: unknown; response?: unknown; body?: unknown };
    return cleanObject({
      name: value.name,
      message: value.message,
      stack: value.stack,
      status: maybe.status,
      error_code: maybe.code,
      response_body: maybe.body ?? maybe.response,
    });
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return cleanObject({
      name: typeof record.name === "string" ? record.name : "NonErrorRejection",
      message: messageFrom(value),
      stack: typeof record.stack === "string" ? record.stack : undefined,
      status: record.status,
      error_code: record.code ?? record.error_code,
      response_body: record.body ?? record.response ?? record.detail,
      value: record,
    });
  }
  return { name: "NonErrorRejection", message: String(value) };
}

export function coerceToError(value: unknown, fallback = "Unhandled promise rejection"): Error {
  if (value instanceof Error) return value;
  const serialized = serializeError(value);
  const message = typeof serialized.message === "string" && serialized.message ? serialized.message : fallback;
  const error = new Error(message);
  error.name = typeof serialized.name === "string" && serialized.name ? serialized.name : "NonErrorRejection";
  return error;
}

export function isBenignRuntimeRejection(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return record.type === "cancelation" && record.msg === "operation is manually canceled";
}

export function newRequestId(prefix = "renderer"): string {
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${random}`;
}

export function redact(value: unknown, depth = 0): unknown {
  if (depth > 8) return "[truncated]";
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : undefined;
  if (typeof value === "string") return sanitizeString(value, 16000);
  if (Array.isArray(value)) return value.slice(0, 100).map((item) => redact(item, depth + 1));
  if (typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (!key) continue;
      out[key] = SECRET_KEY_RE.test(key) ? "[redacted]" : redact(item, depth + 1);
    }
    return out;
  }
  return sanitizeString(String(value), 1000);
}

function cleanObject(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined && item !== null));
}

function messageFrom(value: unknown): string {
  if (value instanceof Error && value.message) return value.message;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    for (const key of ["message", "detail", "error", "body", "response"]) {
      const candidate = record[key];
      if (typeof candidate === "string" && candidate.trim()) return candidate;
      if (candidate && typeof candidate === "object") {
        const nested = messageFrom(candidate);
        if (nested && nested !== "[object Object]") return nested;
      }
    }
    try {
      return JSON.stringify(record);
    } catch {
      return Object.prototype.toString.call(value);
    }
  }
  return String(value);
}

function sanitizeString(value: string, limit: number): string {
  const cleaned = value.replace(/\x00/g, "").replace(BEARER_RE, "Bearer [redacted]").replace(COMMON_SECRET_RE, "[redacted]");
  return cleaned.length > limit ? `${cleaned.slice(0, limit)}\n[truncated]` : cleaned;
}
