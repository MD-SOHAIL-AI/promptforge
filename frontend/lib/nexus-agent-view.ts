import type { ProductAgentEvent, ProductAgentRun } from "@/types";
import type { ForgeAgentActivitySnapshot } from "@/lib/forge-agent-activity";

function clean(value: string | null | undefined) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

export function reasoningSummary(events: ProductAgentEvent[], currentActivity: ForgeAgentActivitySnapshot | null, run: ProductAgentRun | null): string {
  const publicActivity = [...events].reverse().find((event) => event.activity_record)?.activity_record;
  if (publicActivity) return publicActivity.title;

  if (currentActivity?.label) {
    return `Current activity: ${clean(currentActivity.label)}.`;
  }

  if (run?.status === "awaiting_flash_confirmation") {
    return "Verified firmware is waiting for explicit hardware approval.";
  }
  if (run?.status === "completed") {
    return "The current Forge turn is complete.";
  }
  return "Waiting for the next model or tool event.";
}

export function planProgress(run: ProductAgentRun | null) {
  const plan = run?.agent_plan ?? [];
  const completed = plan.filter((item) => item.status === "completed").length;
  const active = plan.findIndex((item) => item.status === "in_progress");
  return { plan, completed, total: plan.length, activeIndex: active };
}

export function buildMetrics(run: ProductAgentRun | null) {
  const raw = run?.build_result ?? null;
  if (!raw || typeof raw !== "object") return null;
  const get = (key: string) => (raw as Record<string, unknown>)[key];
  const success = Boolean(get("success"));
  const size = Number(get("build_size_bytes") ?? 0);
  const warnings = Number(get("warnings_count") ?? 0);
  const message = clean(String(get("message") ?? ""));
  const board = clean(String(get("board") ?? ""));
  const platform = clean(String(get("platform") ?? ""));
  const firmwarePath = clean(String(get("firmware_path") ?? ""));
  return { success, size, warnings, message, board, platform, firmwarePath };
}

const REVIEW_CLASSIFICATIONS = new Set([
  "CHANGESET_REVIEW_REQUIRED",
  "CHANGESET_APPROVAL_REQUIRED",
]);

export function pendingWorkspaceChangeCount(run: ProductAgentRun | null): number {
  if (!run?.change_set_id || !run.active_workspace_unchanged) return 0;
  if (run.status !== "completed") return 0;
  if (run.classification && !REVIEW_CLASSIFICATIONS.has(run.classification)) return 0;
  return Math.max(0, Number(run.created_file_count ?? 0))
    + Math.max(0, Number(run.modified_file_count ?? 0))
    + Math.max(0, Number(run.deleted_file_count ?? 0));
}

export function hasPendingWorkspaceChanges(run: ProductAgentRun | null): boolean {
  return pendingWorkspaceChangeCount(run) > 0;
}
