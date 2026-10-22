import { contextBridge, ipcRenderer } from "electron";

const bridge = {
  backendUrl: process.env.FORGEX_BACKEND_URL ?? null,
  getStatus: () => ipcRenderer.invoke("desktop:get-status"),
  openFolder: () => ipcRenderer.invoke("desktop:open-folder"),
  openProject: () => ipcRenderer.invoke("desktop:open-project"),
  selectBackgroundImage: () => ipcRenderer.invoke("desktop:select-background-image"),
  resolveBackgroundImage: (assetId: string) => ipcRenderer.invoke("desktop:resolve-background-image", assetId),
  removeBackgroundImage: (assetId: string) => ipcRenderer.invoke("desktop:remove-background-image", assetId),
  emitDiagnostic: (payload: Record<string, unknown>) => ipcRenderer.invoke("desktop:diagnostic", payload),
};

contextBridge.exposeInMainWorld("forgexDesktop", bridge);

window.addEventListener("error", (event) => {
  void bridge.emitDiagnostic({
    component: "renderer",
    event: "renderer.error",
    level: "ERROR",
    operation: "window.error",
    message: event.message,
    source: event.filename,
    line: event.lineno,
    column: event.colno,
    error: serializeRendererError(event.error),
  });
});

window.addEventListener("unhandledrejection", (event) => {
  event.preventDefault();
  event.stopImmediatePropagation();
  const serialized = serializeRendererError(event.reason);
  const message = typeof serialized.message === "string" ? serialized.message : "Unhandled promise rejection";
  const benign = isBenignRuntimeRejection(event.reason);
  void bridge.emitDiagnostic({
    component: "renderer",
    event: benign ? "renderer.promise.cancelled" : "renderer.unhandledrejection",
    level: benign ? "DEBUG" : "ERROR",
    operation: "unhandledrejection",
    message,
    error: serialized,
  });
}, true);

function serializeRendererError(value: unknown): Record<string, unknown> {
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
      message: errorMessage(value),
      stack: typeof record.stack === "string" ? record.stack : undefined,
      status: record.status,
      error_code: record.code ?? record.error_code,
      response_body: record.body ?? record.response ?? record.detail,
      value: record,
    });
  }
  return { name: "NonErrorRejection", message: String(value) };
}

function cleanObject(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined && item !== null));
}

function isBenignRuntimeRejection(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return record.type === "cancelation" && record.msg === "operation is manually canceled";
}

function errorMessage(value: unknown): string {
  if (value instanceof Error && value.message) return value.message;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    for (const key of ["message", "detail", "error", "body", "response"]) {
      const candidate = record[key];
      if (typeof candidate === "string" && candidate.trim()) return candidate;
      if (candidate && typeof candidate === "object") {
        const nested = errorMessage(candidate);
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
