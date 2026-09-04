import type { AgentExecutionGraph, ProductAgentEvent, ProductAgentRun } from "@/types";
import type { ForgeAgentActivitySnapshot } from "@/lib/forge-agent-activity";

export type NexusActivityKind = "reason" | "inspect" | "edit" | "build" | "review" | "hardware" | "plan" | "subagent" | "system" | "error";
export type NexusActivityStatus = "pending" | "active" | "success" | "failed";

export interface NexusActivityItem {
  id: string;
  sequence: number;
  kind: NexusActivityKind;
  status: NexusActivityStatus;
  label: string;
  detail?: string | null;
  tool?: string | null;
  timestamp?: string | null;
}

const INSPECTION_TOOLS = new Set(["list_files", "glob_files", "grep_search", "read_file", "memory_search", "load_skill"]);
const EDIT_TOOLS = new Set(["write_file", "edit_file_simple", "apply_patch"]);

function clean(value: string | null | undefined) {
  return String(value ?? "").replace(/\s+/g, " ").trim();
}

function toolLabel(tool: string) {
  return ({
    list_files: "Scanning project structure",
    glob_files: "Finding relevant files",
    grep_search: "Searching project source",
    read_file: "Reading implementation",
    memory_search: "Checking verified project memory",
    load_skill: "Loading engineering skill",
    write_file: "Writing staged source",
    edit_file_simple: "Editing staged source",
    update_plan: "Updating implementation plan",
    build_firmware: "Building firmware",
    spawn_subagent: "Delegating specialist investigation",
    run_command: "Running verification command",
  } as Record<string, string>)[tool] ?? `Running ${tool.replace(/_/g, " ")}`;
}

function kindForTool(tool: string): NexusActivityKind {
  if (INSPECTION_TOOLS.has(tool)) return "inspect";
  if (EDIT_TOOLS.has(tool)) return "edit";
  if (tool === "build_firmware" || tool === "run_command") return "build";
  if (tool === "update_plan") return "plan";
  if (tool === "spawn_subagent") return "subagent";
  return "system";
}

function eventStatus(event: ProductAgentEvent): NexusActivityStatus {
  const type = clean(event.event_type).toLowerCase();
  const status = clean(event.status).toLowerCase();
  if (type.includes("failed") || status === "failed" || status === "blocked" || status === "timed_out") return "failed";
  if (type.includes("completed") || status === "completed") return "success";
  return "active";
}

export function reasoningSummary(events: ProductAgentEvent[], currentActivity: ForgeAgentActivitySnapshot | null, run: ProductAgentRun | null): string {
  const reasoning = [...events]
    .reverse()
    .find((event) => event.event_type === "agent.turn.completed" && clean(event.message));
  if (reasoning?.message) return clean(reasoning.message).slice(0, 420);

  if (currentActivity?.label) {
    const label = currentActivity.label.toLowerCase();
    if (label.includes("search") || label.includes("read") || label.includes("inspect")) {
      return "I'm tracing the existing implementation first so the next change fits the project instead of duplicating or bypassing working logic.";
    }
    if (label.includes("build") || label.includes("verify")) {
      return "I'm validating the staged firmware now. Any compiler diagnostics become evidence for the next repair turn rather than ending the task.";
    }
    if (label.includes("edit") || label.includes("write") || label.includes("implement")) {
      return "I've narrowed the change to the relevant project surface and I'm applying it inside Forge's staged overlay so the active workspace stays protected until review.";
    }
  }

  if (run?.status === "awaiting_flash_confirmation") {
    return "The firmware is verified. The remaining action affects physical hardware, so Forge is waiting for explicit approval bound to this exact artifact and device target.";
  }
  if (run?.status === "completed") {
    return "The requested engineering task has reached a verified stopping point. You can inspect the evidence, review staged changes, or continue with a follow-up instruction.";
  }
  return "Forge is maintaining the current objective, project constraints, staged workspace, and verification state while deciding the next safe engineering action.";
}

export function deriveNexusActivities(events: ProductAgentEvent[], currentActivity: ForgeAgentActivitySnapshot | null, run: ProductAgentRun | null): NexusActivityItem[] {
  const items: NexusActivityItem[] = [];
  let inspectionBucket: ProductAgentEvent[] = [];

  const flushInspection = () => {
    if (!inspectionBucket.length) return;
    const last = inspectionBucket[inspectionBucket.length - 1];
    const failed = inspectionBucket.some((event) => eventStatus(event) === "failed");
    const active = inspectionBucket.some((event) => eventStatus(event) === "active");
    items.push({
      id: `inspect-${inspectionBucket[0].sequence}-${last.sequence}`,
      sequence: last.sequence,
      kind: "inspect",
      status: failed ? "failed" : active ? "active" : "success",
      label: inspectionBucket.length === 1 ? toolLabel(clean(last.tool)) : `Inspected project - ${inspectionBucket.length} actions`,
      detail: inspectionBucket.length > 1 ? inspectionBucket.map((event) => toolLabel(clean(event.tool))).join(" - ") : clean(last.message) || null,
      tool: clean(last.tool) || null,
      timestamp: last.timestamp ?? last.created_at ?? null,
    });
    inspectionBucket = [];
  };

  for (const event of [...events].sort((a, b) => a.sequence - b.sequence)) {
    const tool = clean(event.tool);
    const type = clean(event.event_type).toLowerCase();
    if (tool && INSPECTION_TOOLS.has(tool) && (type.includes("tool") || type.includes("agent"))) {
      inspectionBucket.push(event);
      if (inspectionBucket.length >= 5 || eventStatus(event) === "active") flushInspection();
      continue;
    }
    flushInspection();

    if (type === "agent.turn.completed" && clean(event.message)) {
      items.push({ id: `reason-${event.sequence}`, sequence: event.sequence, kind: "reason", status: "success", label: "Reasoning summary", detail: clean(event.message), timestamp: event.timestamp ?? event.created_at ?? null });
      continue;
    }
    if (type.includes("build")) {
      items.push({ id: `build-${event.sequence}`, sequence: event.sequence, kind: "build", status: eventStatus(event), label: type.includes("failed") ? "Build failed" : type.includes("completed") ? "Build verified" : "Building firmware", detail: clean(event.message) || null, timestamp: event.timestamp ?? event.created_at ?? null });
      continue;
    }
    if (tool) {
      items.push({ id: `tool-${event.sequence}`, sequence: event.sequence, kind: kindForTool(tool), status: eventStatus(event), label: toolLabel(tool), detail: clean(event.message) || null, tool, timestamp: event.timestamp ?? event.created_at ?? null });
      continue;
    }
    if (type.includes("subagent")) {
      items.push({ id: `subagent-${event.sequence}`, sequence: event.sequence, kind: "subagent", status: eventStatus(event), label: clean(event.message) || "Specialist agent", timestamp: event.timestamp ?? event.created_at ?? null });
      continue;
    }
    if (type.includes("review")) {
      items.push({ id: `review-${event.sequence}`, sequence: event.sequence, kind: "review", status: eventStatus(event), label: clean(event.message) || "Independent review", timestamp: event.timestamp ?? event.created_at ?? null });
      continue;
    }
    if (type.includes("flash") || type.includes("monitor") || type.includes("serial")) {
      items.push({ id: `hardware-${event.sequence}`, sequence: event.sequence, kind: "hardware", status: eventStatus(event), label: clean(event.message) || "Hardware action", timestamp: event.timestamp ?? event.created_at ?? null });
    }
  }
  flushInspection();

  if (currentActivity && !items.some((item) => item.status === "active")) {
    items.push({
      id: `live-${currentActivity.startedAt}`,
      sequence: Number.MAX_SAFE_INTEGER,
      kind: currentActivity.activity.includes("build") ? "build" : currentActivity.activity.includes("edit") || currentActivity.activity.includes("write") ? "edit" : "system",
      status: "active",
      label: currentActivity.label,
      detail: null,
    });
  }

  if (run?.status === "awaiting_flash_confirmation") {
    items.push({ id: `approval-${run.run_id}`, sequence: Number.MAX_SAFE_INTEGER - 1, kind: "hardware", status: "active", label: "Firmware verified - hardware approval required", detail: run.flash_port ? `Target ${run.flash_port}` : "Select the connected device to continue." });
  }

  const deduped = new Map<string, NexusActivityItem>();
  for (const item of items) deduped.set(`${item.kind}:${item.label}:${item.status}`, item);
  return [...deduped.values()].sort((a, b) => a.sequence - b.sequence).slice(-12);
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

export type TrustFactTone = "neutral" | "success" | "warning" | "error" | "accent";

export interface TrustFact {
  id: string;
  label: string;
  value: string;
  tone: TrustFactTone;
}

export function trustFacts(run: ProductAgentRun | null, graph: AgentExecutionGraph | null = null): TrustFact[] {
  if (!run) return [];
  const facts: TrustFact[] = [];
  const changed = (run.created_file_count ?? 0) + (run.modified_file_count ?? 0) + (run.deleted_file_count ?? 0);
  facts.push({
    id: "workspace",
    label: "Workspace",
    value: run.active_workspace_unchanged ? "Protected" : "Updated",
    tone: run.active_workspace_unchanged ? "success" : "warning",
  });
  if (changed > 0) {
    facts.push({
      id: "changes",
      label: "Changes",
      value: `${changed} staged file${changed === 1 ? "" : "s"}`,
      tone: run.change_set_id ? "accent" : "neutral",
    });
  }
  const riskNodes = graph?.nodes?.filter((node) => node.risk_level && node.risk_level !== "unassessed") ?? [];
  const highRisk = riskNodes.find((node) => node.risk_level === "high");
  const mediumRisk = riskNodes.find((node) => node.risk_level === "medium");
  const risk = highRisk ?? mediumRisk ?? riskNodes[0];
  if (risk) {
    facts.push({
      id: "risk",
      label: "Risk",
      value: risk.risk_level,
      tone: risk.risk_level === "high" ? "error" : risk.risk_level === "medium" ? "warning" : "success",
    });
  }
  const buildStatus = run.stage_statuses?.build;
  if (buildStatus && buildStatus !== "pending") {
    facts.push({
      id: "build",
      label: "Build",
      value: String(buildStatus),
      tone: buildStatus === "completed" ? "success" : buildStatus === "failed" ? "error" : "accent",
    });
  }
  if (run.status === "awaiting_flash_confirmation" || run.approval_id) {
    facts.push({
      id: "approval",
      label: "Hardware",
      value: run.status === "awaiting_flash_confirmation" ? "Approval required" : "Approval issued",
      tone: "warning",
    });
  }
  const provider = run.summary?.actual_provider || run.provider_id;
  if (provider) {
    facts.push({
      id: "provider",
      label: "Provider",
      value: String(provider),
      tone: run.summary?.fallback_used ? "warning" : "neutral",
    });
  }
  return facts;
}
