import type { GenericRunDetailResponse, GenericRunEvent, GenericRunStatus } from "@/types";

export const TERMINAL_GENERIC_STATUSES = new Set<GenericRunStatus>([
  "cancelled",
  "completed",
  "blocked",
  "failed",
  "timed_out",
  "interrupted",
]);

export function isGenericRunTerminal(status: GenericRunStatus | null | undefined) {
  return Boolean(status && TERMINAL_GENERIC_STATUSES.has(status));
}

export function mergeGenericRun(
  current: GenericRunDetailResponse | null,
  incoming: GenericRunDetailResponse,
) {
  if (!current || current.run_id !== incoming.run_id) return incoming;
  if (isGenericRunTerminal(current.status) && !isGenericRunTerminal(incoming.status)) return current;
  if (Date.parse(incoming.updated_at) < Date.parse(current.updated_at)) return current;
  return incoming;
}

export function appendGenericEvent(
  events: GenericRunEvent[],
  incoming: GenericRunEvent,
  activeRunId: string,
) {
  if (incoming.run_id !== activeRunId) return events;
  if (events.some((item) => item.event_id === incoming.event_id || item.sequence === incoming.sequence)) return events;
  const highest = events.at(-1)?.sequence ?? 0;
  if (incoming.sequence <= highest) return events;
  return [...events, incoming].slice(-100);
}

export function normalizedRunMessage(run: GenericRunDetailResponse | null) {
  if (!run?.failure_code) return null;
  return run.safe_failure_message || "The agent run ended safely without provider details.";
}
