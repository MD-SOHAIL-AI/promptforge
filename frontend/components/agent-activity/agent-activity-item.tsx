"use client";

import type { ReactNode } from "react";
import type { ActivityRecord } from "@/lib/activity-stream";
import { ActivityCard } from "./activity-card";
import { ApprovalCard } from "./approval-card";
import { BuildResultCard } from "./build-result-card";
import { CommandExecutionCard } from "./command-execution-card";
import { ContextRecallBlock } from "./context-recall-block";
import { FileChangeCard } from "./file-change-card";
import { PlanProgress } from "./plan-progress";
import { ThinkingBlock } from "./thinking-block";
import { ToolCallCard } from "./tool-call-card";

function compactSummary(activity: ActivityRecord): string | null {
  const summary = typeof activity.details.summary === "string" ? activity.details.summary.trim() : "";
  if (!summary || activity.kind === "thinking" || activity.kind === "summary") return null;
  const firstLine = summary.split(/\r?\n/, 1)[0].replace(/\s+/g, " ").trim();
  if (!firstLine || firstLine.length > 132) return null;
  return firstLine.replace(/[.:;,-]+$/, "");
}

function titleFor(activity: ActivityRecord, count: number) {
  const live = activity.status === "running" || activity.status === "queued";
  if (["cancelled", "interrupted", "warning"].includes(activity.status) && !["thinking", "summary", "approval"].includes(activity.kind)) {
    const action = {build: "Build", command: "Command", "file.edit": "File edit", "file.read": "File read", search: "Search", test: "Test", "build.repair": "Repair", "project.repair": "Repair", plan: "Plan", subagent: "Specialist"}[activity.kind] ?? "Task step";
    return `${action} ${activity.status === "cancelled" ? "stopped" : activity.status === "warning" ? "needs attention" : "interrupted"}`;
  }
  if (count > 1) return activity.kind === "file.read" ? `Read ${count} files` : `Inspected ${count} project items`;
  const path = typeof activity.details.path === "string" ? activity.details.path : "";
  if (activity.kind === "file.read" && path) return `Reading ${path}`;
  if (activity.kind === "file.edit" && path) return `${activity.details.created ? "Creating" : "Editing"} ${path}`;
  if (activity.kind === "command" && typeof activity.details.command === "string") {
    const command = activity.details.command.trim().replace(/\s+/g, " ");
    if (command) return `Running ${command.length > 92 ? `${command.slice(0, 89)}…` : command}`;
  }
  if (activity.kind === "build" && typeof activity.details.environment === "string") return `Building ${activity.details.environment}`;
  const summaryTitle = compactSummary(activity);
  if (summaryTitle) return summaryTitle;
  if (activity.kind === "thinking" || activity.kind === "summary") return live ? "Thinking…" : "Reasoning summary";
  if (activity.kind === "context") return live ? "Reading project context…" : "Project context ready";
  if (activity.kind === "plan") {
    const steps = Array.isArray(activity.details.steps) ? activity.details.steps : [];
    return `Plan · ${steps.filter((step) => step?.status === "completed").length} of ${steps.length}`;
  }
  if (activity.kind === "approval") return activity.status === "awaiting_approval" ? "Waiting for approval" : activity.status === "completed" ? "Operation approved" : "Approval closed";
  if (activity.kind === "command") return live ? "Running command…" : activity.status === "failed" ? "Command failed" : "Command completed";
  if (activity.kind === "build") return live ? "Building project…" : activity.status === "failed" ? "Build failed" : "Build passed";
  if (["build.repair", "project.repair"].includes(activity.kind)) return live ? "Repairing…" : activity.status === "failed" ? "Repair failed" : "Repair completed";
  if (activity.kind === "file.edit") return activity.details.created ? "Created file" : "Edited file";
  if (activity.kind === "file.read") return "Read file";
  if (activity.kind === "search") return live ? "Searching codebase…" : "Inspected project files";
  if (activity.kind === "deliverable") return "Prepared deliverables";
  if (activity.kind === "subagent") return `${String(activity.details.role ?? "Review").replace(/^./, (value) => value.toUpperCase())} specialist`;
  return activity.title;
}

export function AgentActivityItem({ activities, children, onViewFile, onApprove, onCancel, onReview, collapseLiveThinking = false, verbose = false }: {
  activities: ActivityRecord[];
  children?: ReactNode;
  onViewFile?: (path: string) => void;
  onApprove?: () => void;
  onCancel?: () => void;
  onReview?: () => void;
  collapseLiveThinking?: boolean;
  verbose?: boolean;
}) {
  const activity = activities[0];
  const summary = typeof activity.details.summary === "string" ? activity.details.summary : null;
  const title = titleFor(activity, activities.length);
  let content: ReactNode;
  if (activity.kind === "thinking" || activity.kind === "summary") content = <ThinkingBlock activity={activity} />;
  else if (activity.kind === "context") content = <ContextRecallBlock activity={activity} />;
  else if (activity.kind === "plan") content = <PlanProgress activity={activity} />;
  else if (activity.kind === "file.edit") content = <FileChangeCard activity={activity} onViewFile={onViewFile} />;
  else if (activity.kind === "command") content = <CommandExecutionCard activity={activity} />;
  else if (["build", "test", "build.repair", "project.repair"].includes(activity.kind)) content = <BuildResultCard activity={activity} />;
  else if (activity.kind === "approval") content = <ApprovalCard activity={activity} onApprove={onApprove} onCancel={onCancel} onReview={onReview} />;
  else content = <ToolCallCard activities={activities} />;
  return (
    <ActivityCard activity={activity} title={title} subtitle={activity.kind === "subagent" ? String(activity.details.assignment ?? "") : null} defaultOpen={verbose || ["plan", "approval"].includes(activity.kind)} autoOpenActive={!(collapseLiveThinking && ["thinking", "summary"].includes(activity.kind))}>
      {summary && summary.trim() !== title && !["thinking", "summary", "context", "approval"].includes(activity.kind) ? <p className="fx-progress-summary">{summary}</p> : null}
      {content}
      {children}
    </ActivityCard>
  );
}
