export function toErrorMessage(error: unknown, fallback = "Unknown error"): string {
  if (error == null) return fallback;
  if (typeof error === "string") return error || fallback;
  if (error instanceof Error) return error.message || error.name || fallback;
  if (typeof Response !== "undefined" && error instanceof Response) {
    return `Request failed (${error.status}${error.statusText ? ` ${error.statusText}` : ""})`;
  }
  if (typeof Event !== "undefined" && error instanceof Event) {
    return `Browser event: ${error.type || "unknown"}`;
  }
  if (typeof error === "object") {
    const record = error as Record<string, unknown>;
    const message = stringValue(record.message) ?? stringValue(record.detail) ?? stringValue(record.error);
    if (message) return message;
    const detail = record.detail;
    if (detail && typeof detail === "object") {
      const nested = detail as Record<string, unknown>;
      return stringValue(nested.message) ?? stringValue(nested.detail) ?? fallback;
    }
    return fallback;
  }
  return fallback;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}
export function apiErrorCode(value: unknown): string | undefined {
  if (!value || typeof value !== "object") return undefined;
  const record = value as Record<string, unknown>;
  if (typeof record.code === "string") return record.code;
  for (const key of ["body", "detail", "error"]) {
    const nested = record[key];
    if (nested && typeof nested === "object" && typeof (nested as Record<string, unknown>).code === "string") {
      return (nested as Record<string, unknown>).code as string;
    }
  }
  return undefined;
}
