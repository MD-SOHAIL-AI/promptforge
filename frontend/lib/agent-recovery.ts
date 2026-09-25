import type { ProductAgentRun } from "@/types";

export function formatRetryDelay(milliseconds?: number | null): string | null {
  if (!milliseconds || milliseconds <= 0) return null;
  const seconds = Math.ceil(milliseconds / 1000);
  if (seconds < 60) return `about ${seconds} second${seconds === 1 ? "" : "s"}`;
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `about ${minutes} minute${minutes === 1 ? "" : "s"}`;
  const hours = Math.ceil(minutes / 60);
  if (hours < 24) return `about ${hours} hour${hours === 1 ? "" : "s"}`;
  const days = Math.ceil(hours / 24);
  return `about ${days} day${days === 1 ? "" : "s"}`;
}

export function modelPauseMessage(run: ProductAgentRun): string | null {
  if (run.status !== "paused_model_unavailable") return null;
  const delay = formatRetryDelay(run.provider_diagnostics?.retry_after_ms ?? null);
  const suffix = delay ? ` Try the same model again in ${delay}.` : " Retry the same model later.";
  return `The selected model is temporarily unavailable. ForgeX saved this run and did not switch providers.${suffix}`;
}
