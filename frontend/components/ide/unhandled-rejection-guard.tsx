"use client";

import { useEffect } from "react";

import { toErrorMessage } from "@/lib/errors";

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype;
}

function shouldSuppressOverlay(reason: unknown) {
  if (reason instanceof Error) return reason.name === "PromptForgeApiError";
  return Boolean(reason) && typeof reason === "object";
}

export function UnhandledRejectionGuard() {
  useEffect(() => {
    if ((window as Window & { __forgexUnhandledRejectionGuardInstalled?: boolean }).__forgexUnhandledRejectionGuardInstalled) return;
    const onUnhandledRejection = (event: PromiseRejectionEvent) => {
      if (!shouldSuppressOverlay(event.reason)) return;
      console.debug("ForgeX ignored handled promise rejection", {
        message: isPlainObject(event.reason)
          ? toErrorMessage(event.reason, "Unknown promise rejection")
          : event.reason instanceof Error
            ? event.reason.message
            : String(event.reason),
        reason: event.reason,
      });
      event.stopImmediatePropagation();
      event.preventDefault();
    };

    window.addEventListener("unhandledrejection", onUnhandledRejection, { capture: true });
    return () => window.removeEventListener("unhandledrejection", onUnhandledRejection, { capture: true });
  }, []);

  return null;
}
