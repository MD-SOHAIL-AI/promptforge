import type { ProductAgentRun } from "@/types";

export async function recoveryRequestKey(action: string, runId: string, checkpoint?: string,
  selection?: {provider_id: string; model_id: string}): Promise<string> {
  const base = `${action}:${runId}:${checkpoint?.slice(0, 24) ?? "draft"}`;
  if (!selection) return base;
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(
    [selection.provider_id, selection.model_id])));
  const suffix = Array.from(new Uint8Array(digest)).slice(0, 8).map(value => value.toString(16).padStart(2, "0")).join("");
  return `${base}:${suffix}`;
}

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
  if (run.classification === "PROVIDER_BUSY" || run.provider_diagnostics?.last_failure_code === "PROVIDER_BUSY") {
    return "Another ForgeX task is using this model endpoint. This task paused locally before sending a provider request. Stop the other task or retry when it finishes.";
  }
  const delay = formatRetryDelay(run.provider_diagnostics?.retry_after_ms ?? null);
  const suffix = delay ? ` Try the same model again in ${delay}.` : " Retry the same model later.";
  return `The selected model is temporarily unavailable. ForgeX saved this run and did not switch providers.${suffix}`;
}
