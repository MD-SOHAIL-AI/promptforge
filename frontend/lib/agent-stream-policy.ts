export const RUN_POLL_INTERVAL_MS = 750;
export const SSE_OPEN_FALLBACK_MS = 2500;

export function isTerminalRunStatus(status?: string): boolean {
  return ["completed", "failed", "cancelled", "blocked", "timed_out"].includes(status ?? "");
}

export function isStableRunStatus(status?: string): boolean {
  return isTerminalRunStatus(status) || status === "awaiting_flash_confirmation" || status === "paused_model_unavailable";
}

export function shouldPollAfterSseEvent(status?: string): boolean {
  return isStableRunStatus(status);
}

export function shouldStartPollingAfterSseDisconnect(status?: string): boolean {
  return !isStableRunStatus(status);
}
