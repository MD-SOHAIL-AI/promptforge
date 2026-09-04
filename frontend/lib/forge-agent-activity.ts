import type { ForgeAgentActivityEvent, ProductAgentEvent } from "@/types";

export const MIN_ACTIVITY_VISIBLE_MS = 400;
const RUN_REFRESH_EVENT_PATTERNS = [
  /^runtime\./,
  /^node\./,
  /^risk\./,
  /^changes\./,
  /^changeset\./,
  /^flash\./,
  /^monitor\./,
  /^build/i,
  /^project_validation/i,
  /^generation_completed$/i,
  /^code_generation_completed$/i,
  /^agent\.turn\.completed$/,
];

export interface ForgeAgentActivitySnapshot {
  activity: string;
  label: string;
  phase: string;
  sessionId?: string | null;
  runId?: string | null;
  messageId?: string | null;
  startedAt: number;
}

export function snapshotFromActivityEvent(event: ForgeAgentActivityEvent, now = Date.now()): ForgeAgentActivitySnapshot | null {
  if (!event || event.phase === "completed" || event.event_type === "activity.completed") return null;
  if (!event.activity || !event.label) return null;
  return {
    activity: event.activity,
    label: event.label,
    phase: event.phase,
    sessionId: event.session_id,
    runId: event.run_id ?? null,
    messageId: event.message_id ?? null,
    startedAt: now,
  };
}

export function snapshotFromRunEvent(event: ProductAgentEvent, sessionId?: string | null, now = Date.now()): ForgeAgentActivitySnapshot | null {
  if (!event.activity || !event.activity_label || event.activity_phase === "completed") return null;
  return {
    activity: event.activity,
    label: event.activity_label,
    phase: event.activity_phase ?? "active",
    sessionId: sessionId ?? null,
    runId: event.run_id,
    startedAt: now,
  };
}

export function shouldAcceptActivity(
  snapshot: ForgeAgentActivitySnapshot,
  options: {
    activeSessionId?: string | null;
    activeRunId?: string | null;
  } = {},
): boolean {
  const { activeSessionId, activeRunId } = options;
  if (snapshot.sessionId && activeSessionId && snapshot.sessionId !== activeSessionId) return false;
  if (snapshot.runId && activeRunId && snapshot.runId !== activeRunId) return false;
  return true;
}

export function remainingActivityMs(startedAt: number, now = Date.now(), minimumMs = MIN_ACTIVITY_VISIBLE_MS): number {
  return Math.max(0, minimumMs - Math.max(0, now - startedAt));
}

export function agentEventKey(event: Pick<ProductAgentEvent, "run_id" | "sequence" | "event_type" | "index">): string {
  const runId = event.run_id || "session";
  const index = event.event_type === "message.delta" && typeof event.index === "number" ? `:${event.index}` : "";
  return `${runId}:${event.sequence}:${event.event_type}${index}`;
}

export function shouldRefreshRunForEvent(event: ProductAgentEvent): boolean {
  if (event.event_type === "message.delta") return false;
  const status = String(event.status ?? "").toLowerCase();
  if (["completed", "failed", "cancelled", "blocked", "timed_out", "awaiting_flash_confirmation"].includes(status)) return true;
  const type = String(event.event_type ?? "").toLowerCase();
  if (event.node_id) return true;
  return RUN_REFRESH_EVENT_PATTERNS.some((pattern) => pattern.test(type));
}
