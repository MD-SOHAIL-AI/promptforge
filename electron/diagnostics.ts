import fs from "node:fs";
import path from "node:path";

const DEFAULT_LOG_PATH = path.join(".forgex", "logs", "live-runtime.jsonl");
const DEFAULT_MAX_BYTES = 5 * 1024 * 1024;
const SECRET_KEY_RE = /(authorization|cookie|api[_-]?key|token|password|secret|credential|private[_-]?key)/i;
const BEARER_RE = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi;
const COMMON_SECRET_RE = /\b(?:sk|sk-or|ghp|AIza)[A-Za-z0-9_-]{12,}\b/g;
const ALLOWED_LEVELS = new Set(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]);

export interface DiagnosticEventInput {
  component?: string;
  event?: string;
  level?: string;
  message?: unknown;
  fields?: Record<string, unknown>;
  [key: string]: unknown;
}

export function emitDiagnosticEvent(input: DiagnosticEventInput): void {
  const payload = eventPayload(input);
  try {
    const filePath = liveLogPath();
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    rotateIfNeeded(filePath, maxBytes());
    fs.appendFileSync(filePath, `${JSON.stringify(payload)}\n`, "utf8");
  } catch {
    // Diagnostics must never interfere with the desktop runtime.
  }
  printCondensed(payload);
}

export function serializeError(value: unknown): Record<string, unknown> {
  if (value instanceof Error) {
    const out: Record<string, unknown> = {
      name: value.name,
      message: value.message,
      stack: value.stack,
    };
    const maybe = value as Error & { status?: unknown; code?: unknown; response?: unknown };
    if (maybe.status !== undefined) out.status = maybe.status;
    if (maybe.code !== undefined) out.error_code = maybe.code;
    if (maybe.response !== undefined) out.response = maybe.response;
    return redact(out) as Record<string, unknown>;
  }
  if (typeof value === "object" && value !== null) return redact(value) as Record<string, unknown>;
  return { message: sanitizeString(String(value), 2000) };
}

function eventPayload(input: DiagnosticEventInput): Record<string, unknown> {
  const level = normalizeLevel(input.level);
  const component = shortText(typeof input.component === "string" ? input.component : "desktop", 128);
  const event = shortText(typeof input.event === "string" ? input.event : "desktop.event", 160);
  const fields = typeof input.fields === "object" && input.fields !== null ? input.fields : {};
  const payload: Record<string, unknown> = {
    timestamp: new Date().toISOString(),
    level,
    component,
    event,
  };
  if (input.message !== undefined && input.message !== null) {
    payload.message = sanitizeString(String(input.message), 2000);
  }
  for (const [key, value] of Object.entries({ ...fields, ...input })) {
    if (["timestamp", "component", "event", "level", "message", "fields"].includes(key)) continue;
    if (!key) continue;
    const safe = redact(value);
    if (safe !== undefined) payload[key] = safe;
  }
  return payload;
}

function redact(value: unknown, depth = 0): unknown {
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

function sanitizeString(value: string, limit: number): string {
  const cleaned = value.replace(/\x00/g, "").replace(BEARER_RE, "Bearer [redacted]").replace(COMMON_SECRET_RE, "[redacted]");
  return cleaned.length > limit ? `${cleaned.slice(0, limit)}\n[truncated]` : cleaned;
}

function shortText(value: string, limit: number): string {
  return value.replace(/\x00/g, "").trim().replace(/\s+/g, " ").slice(0, limit) || "unknown";
}

function normalizeLevel(value: unknown): string {
  let level = typeof value === "string" ? value.trim().toUpperCase() : "INFO";
  if (level === "WARN") level = "WARNING";
  return ALLOWED_LEVELS.has(level) ? level : "INFO";
}

function liveLogPath(): string {
  const configured = process.env.FORGEX_LIVE_LOG_PATH?.trim();
  return configured ? path.resolve(configured) : path.resolve(DEFAULT_LOG_PATH);
}

function maxBytes(): number {
  const configured = Number.parseInt(process.env.FORGEX_LIVE_LOG_MAX_BYTES ?? "", 10);
  if (!Number.isFinite(configured)) return DEFAULT_MAX_BYTES;
  return Math.max(64 * 1024, Math.min(configured, 100 * 1024 * 1024));
}

function rotateIfNeeded(filePath: string, limit: number): void {
  try {
    if (!fs.existsSync(filePath) || fs.statSync(filePath).size < limit) return;
    const rotated = `${filePath}.1`;
    if (fs.existsSync(rotated)) fs.unlinkSync(rotated);
    fs.renameSync(filePath, rotated);
  } catch {
    // Best effort only.
  }
}

function printCondensed(payload: Record<string, unknown>): void {
  const event = String(payload.event ?? "");
  if (payload.level === "DEBUG" || event.startsWith("http.request")) return;
  const component = String(payload.component ?? "desktop");
  const message = String(payload.message ?? payload.status ?? "");
  if (component === "desktop" || component === "renderer") {
    console.log(`[${component}] ${event} ${message.slice(0, 160)}`.trim());
  }
}
