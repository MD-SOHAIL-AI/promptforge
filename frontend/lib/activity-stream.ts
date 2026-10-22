export type ActivityStatus =
  | "queued"
  | "running"
  | "completed"
  | "success"
  | "warning"
  | "failed"
  | "cancelled"
  | "awaiting_approval"
  | "interrupted";
export type AgentState =
  | "idle"
  | "thinking"
  | "recalling_context"
  | "planning"
  | "tool_call"
  | "reading"
  | "searching"
  | "editing"
  | "running_command"
  | "building"
  | "testing"
  | "repairing"
  | "waiting_for_approval"
  | "completed"
  | "error";
export interface ActivityRecord {
  agentId?: string;
  parentId?: string | null;
  id: string;
  runId: string;
  kind: string;
  state?: AgentState;
  eventType?: string;
  displayStatus?: "queued" | "running" | "success" | "warning" | "failed";
  status: ActivityStatus;
  title: string;
  startedAt: number;
  durationMs: number | null;
  details: Record<string, unknown>;
}
export interface ActivityEnvelope {
  run_id: string;
  sequence: number;
  activity_record?: ActivityRecord;
  event_type?: string;
  status?: string;
  timestamp?: string;
  created_at?: string;
}
export interface AgentSummaryEnvelope extends ActivityEnvelope {
  event_type?: string;
  message?: string;
  content?: string;
  turn?: number;
  step?: number;
  attempt_id?: string;
  frame_index?: number;
  transient?: boolean;
}
export function validActivity(value: unknown): value is ActivityRecord {
  if (!value || typeof value !== "object") return false;
  const a = value as ActivityRecord;
  return (
    typeof a.id === "string" &&
    typeof a.runId === "string" &&
    typeof a.kind === "string" &&
    typeof a.title === "string" &&
    Number.isFinite(a.startedAt) &&
    (a.durationMs === null ||
      (Number.isFinite(a.durationMs) && a.durationMs >= 0)) &&
    [
      "running",
      "queued",
      "completed",
      "success",
      "warning",
      "failed",
      "cancelled",
      "awaiting_approval",
      "interrupted",
    ].includes(a.status) &&
    (a.displayStatus === undefined ||
      ["queued", "running", "success", "warning", "failed"].includes(
        a.displayStatus,
      )) &&
    !!a.details &&
    typeof a.details === "object"
  );
}
export function activityState(a: ActivityRecord): AgentState {
  if (a.state) return a.state;
  if (a.status === "failed") return "error";
  if (a.status === "awaiting_approval") return "waiting_for_approval";
  if (["completed", "success"].includes(a.status) && a.kind === "run")
    return "completed";
  return {
    summary: "thinking",
    thinking: "thinking",
    context: "recalling_context",
    plan: "planning",
    "file.read": "reading",
    search: "searching",
    "file.edit": "editing",
    command: "running_command",
    build: "building",
    test: "testing",
    "build.repair": "repairing",
    "project.repair": "repairing",
  }[a.kind] as AgentState ?? "tool_call";
}
export function isActivityActive(a: ActivityRecord): boolean {
  return ["queued", "running", "awaiting_approval"].includes(a.status);
}
// Each envelope is a bounded cumulative snapshot. Replay does not append logs twice.
export function mergeActivityEvents<T extends ActivityEnvelope>(
  current: T[],
  event: T,
): T[] {
  if (!event.run_id || !Number.isFinite(event.sequence)) return current;
  if (
    current.some(
      (old) => old.run_id === event.run_id && old.sequence === event.sequence,
    )
  )
    return current;
  const a = event.activity_record;
  if (a && (!validActivity(a) || a.runId !== event.run_id)) return current;
  const previous = a
    ? current.find(
        (old) =>
          old.run_id === event.run_id && old.activity_record?.id === a.id,
      )
    : undefined;
  if (previous && previous.sequence > event.sequence) return current;
  // Older journal entries projected warning/paused runs as still running.
  // Normalize the root from its explicit runtime outcome without changing tool evidence.
  if (a?.kind === "run" && event.event_type?.startsWith("runtime.")) {
    const outcome = event.status ?? event.event_type.slice("runtime.".length);
    const status = {
      completed: "completed", completed_with_warning: "warning",
      failed: "failed", timed_out: "failed", blocked: "failed",
      cancelled: "cancelled", interrupted: "interrupted",
      paused_model_unavailable: "interrupted",
    }[outcome] as ActivityStatus | undefined;
    if (status) {
      const endedAt = Date.parse(event.timestamp ?? event.created_at ?? "");
      const durationMs = a.durationMs ?? previous?.activity_record?.durationMs ??
        Math.max(0, (Number.isFinite(endedAt) ? endedAt : a.startedAt) - a.startedAt);
      event = { ...event, activity_record: { ...a, status, durationMs,
        displayStatus: status === "completed" ? "success" : status === "failed" ? "failed" : "warning",
        state: status === "failed" ? "error" : status === "completed" || status === "warning" ? "completed" : "idle",
        eventType: status === "failed" ? "agent.error" : status === "interrupted" ? "agent.interrupted" : "agent.completed",
      } };
    }
  }
  let next = previous ? current.filter((old) => old !== previous) : current;
  if (["runtime.completed", "runtime.completed_with_warning", "runtime.failed", "runtime.cancelled", "runtime.blocked", "runtime.interrupted", "runtime.timed_out", "runtime.paused_model_unavailable"].includes(event.event_type ?? "")) {
    const finishedAt = a?.kind === "run" && a.durationMs !== null
      ? a.startedAt + a.durationMs
      : Date.parse(event.timestamp ?? event.created_at ?? "");
    next = next.map((old) => {
      if (old.run_id !== event.run_id || !old.activity_record || (old.activity_record.durationMs !== null && !isActivityActive(old.activity_record))) return old;
      return { ...old, activity_record: settleActivity(old.activity_record, event.event_type ?? "", finishedAt) } as T;
    });
  }
  return [...next, event].sort((x, y) => x.sequence - y.sequence).slice(-2000);
}
/** Settling the run cannot manufacture successful tool evidence. */
export function settleActivity(activity: ActivityRecord, outcome: string, finishedAt: number): ActivityRecord {
  if (!isActivityActive(activity)) return activity.durationMs !== null ? activity : { ...activity, durationMs: Math.max(0, (Number.isFinite(finishedAt) ? finishedAt : activity.startedAt) - activity.startedAt) };
  const failed = ["failed", "timed_out", "runtime.failed", "runtime.timed_out"].includes(outcome);
  const cancelled = ["cancelled", "runtime.cancelled"].includes(outcome) || activity.kind === "approval";
  return { ...activity,
    status: cancelled ? "cancelled" : failed ? "failed" : "interrupted",
    displayStatus: failed ? "failed" : "warning",
    state: failed ? "error" : activity.state,
    durationMs: activity.durationMs ?? Math.max(0, (Number.isFinite(finishedAt) ? finishedAt : activity.startedAt) - activity.startedAt),
  };
}
export function activityElapsed(a: ActivityRecord, now: number): number {
  return a.durationMs ?? Math.max(0, now - a.startedAt);
}
export function groupActivities(items: ActivityRecord[]): ActivityRecord[][] {
  const groups: ActivityRecord[][] = [];
  for (const item of items) {
    const last = groups.at(-1);
    if (
      last &&
      ["file.read", "search"].includes(item.kind) &&
      item.status === "completed" &&
      last[0].kind === item.kind &&
      last[0].runId === item.runId &&
      last[0].agentId === item.agentId &&
      last[0].status === "completed"
    )
      last.push(item);
    else groups.push([item]);
  }
  return groups;
}

/** Builds the visible Agent draft exclusively from public SSE content/summary packets. */
export function streamedAgentText(
  events: AgentSummaryEnvelope[],
  runId: string | null | undefined,
): string {
  if (!runId) return "";

  // Harness-style public attempt frames are deltas. Assemble them by attempt and
  // frame index. Durable settlement supersedes the transient frame set.
  const attempts = new Map<
    string,
    { settledSequence: number; settled?: string; frames: Map<number, string> }
  >();
  const summaries = new Map<string, { sequence: number; text: string }>();

  for (const event of events) {
    if (event.run_id !== runId) continue;
    const normalized = event.event_type?.replaceAll("_", ".").toLowerCase();
    const attemptId = event.attempt_id;
    if (normalized === "assistant.content.delta" && attemptId) {
      const value = typeof event.message === "string" ? event.message : "";
      if (!value) continue;
      const entry = attempts.get(attemptId) ?? {
        settledSequence: -1,
        frames: new Map<number, string>(),
      };
      entry.frames.set(Number(event.frame_index ?? event.sequence), value);
      attempts.set(attemptId, entry);
      continue;
    }
    if (normalized === "model.attempt.settled" && attemptId) {
      const entry = attempts.get(attemptId) ?? {
        settledSequence: -1,
        frames: new Map<number, string>(),
      };
      const content = typeof event.content === "string" ? event.content.trim() : "";
      if (content && event.sequence >= entry.settledSequence) {
        entry.settled = content;
        entry.settledSequence = event.sequence;
      }
      attempts.set(attemptId, entry);
      continue;
    }

    const activity = event.activity_record;
    const isPublicSummary =
      normalized?.startsWith("assistant.summary") ||
      normalized?.startsWith("reasoning.summary") ||
      normalized?.startsWith("thinking.summary");
    if (!isPublicSummary) continue;
    const text = (
      typeof event.message === "string"
        ? event.message
        : typeof activity?.details.summary === "string"
          ? activity.details.summary
          : ""
    ).trim();
    if (!text) continue;
    const key = String(event.turn ?? activity?.id ?? event.sequence);
    const previous = summaries.get(key);
    if (!previous || event.sequence >= previous.sequence)
      summaries.set(key, { sequence: event.sequence, text });
  }

  const attemptText = [...attempts.values()]
    .map((entry) =>
      entry.settled ??
      [...entry.frames.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([, delta]) => delta)
        .join(""),
    )
    .filter(Boolean);
  if (attemptText.length) return attemptText.join("\n\n");

  const result: string[] = [];
  for (const item of [...summaries.values()].sort((a, b) => a.sequence - b.sequence)) {
    const previous = result.at(-1);
    if (previous === item.text) continue;
    if (previous && item.text.startsWith(previous)) result[result.length - 1] = item.text;
    else result.push(item.text);
  }
  return result.join("\n\n");
}
