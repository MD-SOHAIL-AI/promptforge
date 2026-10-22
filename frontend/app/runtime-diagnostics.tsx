"use client";

import { useEffect } from "react";

import { emitRuntimeDiagnostic, isBenignRuntimeRejection, serializeError } from "@/lib/diagnostics";

export function RuntimeDiagnostics() {
  useEffect(() => {
    const onError = (event: ErrorEvent) => {
      emitRuntimeDiagnostic({
        component: "renderer",
        event: "renderer.error",
        level: "ERROR",
        operation: "window.error",
        message: event.message,
        source: event.filename,
        line: event.lineno,
        column: event.colno,
        error: serializeError(event.error),
      });
    };
    const onUnhandledRejection = (event: PromiseRejectionEvent) => {
      event.preventDefault();
      event.stopImmediatePropagation();
      const serialized = serializeError(event.reason);
      const message = typeof serialized.message === "string" ? serialized.message : "Unhandled promise rejection";
      const benign = isBenignRuntimeRejection(event.reason);
      emitRuntimeDiagnostic({
        component: "renderer",
        event: benign ? "renderer.promise.cancelled" : "renderer.unhandledrejection",
        level: benign ? "DEBUG" : "ERROR",
        operation: "unhandledrejection",
        message,
        error: serialized,
      });
    };
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onUnhandledRejection, true);
    return () => {
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onUnhandledRejection, true);
    };
  }, []);

  return null;
}
