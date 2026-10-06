export const RUN_POLL_INTERVAL_MS = 750;
export const SSE_OPEN_FALLBACK_MS = 2500;
export const JOB_POLL_INTERVAL_MS = 3000;
export const OBSERVATION_FRESHNESS_MS = 10_000;

export function shouldObserveJob(runStatus?: string, jobStatus?: string): boolean {
  return isStableRunStatus(runStatus) && ["running", "paused", "interrupted", "blocked", "paused_model_unavailable"].includes(jobStatus ?? "");
}

export function isTerminalRunStatus(status?: string): boolean {
  return ["completed", "completed_with_warning", "failed", "cancelled", "blocked", "timed_out", "interrupted"].includes(status ?? "");
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
