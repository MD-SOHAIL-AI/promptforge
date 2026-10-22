"use client";

import { useMemo, useState } from "react";
import { ChevronDown, Square, SquareTerminal } from "lucide-react";
import { groupActivities, validActivity, settleActivity, type ActivityRecord } from "@/lib/activity-stream";
import type { ProductAgentEvent, ProductAgentRun } from "@/types";
import { ActivityDuration } from "./activity-card";
import { AgentStatusIndicator } from "./agent-status-indicator";
import { AgentActivityItem } from "./agent-activity-item";

const TERMINAL = ["completed", "completed_with_warning", "success", "failed", "cancelled", "blocked", "timed_out", "interrupted", "paused_model_unavailable"];
const SUMMARY_KINDS = new Set(["thinking", "summary", "plan", "approval", "build", "test", "deliverable", "flash"]);
export type ActivityDetailMode = "summary" | "normal" | "verbose";

export function AgentActivityFeed({ events, run, onViewFile, onApprove, onCancel, onReview, cancelling = false,
  collapseLiveThinking = false, detailMode = "normal", onDetailModeChange }: {
  events: ProductAgentEvent[]; run: ProductAgentRun | null;
  onViewFile?: (path: string) => void; onApprove?: () => void; onCancel?: () => void; onReview?: () => void;
  cancelling?: boolean; collapseLiveThinking?: boolean;
  detailMode?: ActivityDetailMode; onDetailModeChange?: (mode: ActivityDetailMode) => void;
}) {
  const [earlierPage, setEarlierPage] = useState(0);
  const [collapsed, setCollapsed] = useState(false);
  const stopping = cancelling || run?.status === "cancelling";
  const canStop = Boolean(onCancel && run && ["queued", "running", "cancelling", "paused_model_unavailable"].includes(run.status));
  const items = useMemo(() => {
    const latest = new Map<string, ActivityRecord>();
    for (const event of events) if (validActivity(event.activity_record) && (!run || event.run_id === run.run_id)) latest.set(event.activity_record.id, event.activity_record);
    const sorted = [...latest.values()].filter(activity => activity.kind !== "node").sort((a, b) => a.startedAt - b.startedAt || a.id.localeCompare(b.id));
    const end = sorted.findLast(activity => activity.kind === "run" && TERMINAL.includes(activity.status));
    const terminal = run && TERMINAL.includes(run.status) ? run.status : end?.status;
    const finishedAt = end ? end.startedAt + (end.durationMs ?? 0) : Date.parse(run?.updated_at ?? "");
    return terminal ? sorted.map(activity => settleActivity(activity, terminal, finishedAt)) : sorted;
  }, [events, run]);
  const nested = useMemo(() => {
    const children = new Map<string, ActivityRecord[]>();
    for (const activity of items) if (activity.kind !== "subagent" && activity.agentId && activity.parentId) {
      const current = children.get(activity.agentId) ?? [];
      current.push(activity); children.set(activity.agentId, current);
    }
    return children;
  }, [items]);
  const stopButton = canStop ? <button type="button" className="fx-task-stop" aria-label="Stop task" disabled={stopping} onClick={onCancel}>
    <Square size={12} aria-hidden="true" />{stopping ? "Stopping…" : "Stop"}
  </button> : null;

  if (!items.length && (!run || (TERMINAL.includes(run.status) && !canStop))) return null;
  const root = items.findLast(activity => activity.kind === "run");
  const main = items.filter(activity => activity.kind !== "run" && (!activity.parentId || activity.kind === "subagent") &&
    (detailMode !== "summary" || SUMMARY_KINDS.has(activity.kind) || ["failed", "warning", "awaiting_approval"].includes(activity.status)));
  const groups = groupActivities(main);
  const endIndex = Math.max(0, groups.length - earlierPage * 80);
  const visible = groups.slice(Math.max(0, endIndex - 80), endIndex);
  const label = stopping ? "Stopping task…" : run?.status === "paused_model_unavailable" ? "Task paused"
    : run?.status === "cancelled" ? "Task stopped" : run?.status === "blocked" ? "Task blocked"
    : run?.status === "timed_out" ? "Task timed out" : run?.status === "failed" ? "Task failed"
    : !root || root.status === "running" ? run?.status === "queued" ? "Starting ForgeX agent…" : "ForgeX is working"
    : "Agent activity";

  return <section aria-label="Agent activity" className="fx-activity-stream" data-detail-mode={detailMode}>
    <div className="fx-journal-toolbar">
      <button type="button" className="fx-run-heading" aria-expanded={!collapsed} onClick={() => setCollapsed(value => !value)}>
        <span className="fx-run-status">{root ? <AgentStatusIndicator activity={root} /> : <SquareTerminal aria-hidden="true" size={13} />}</span>
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {root ? <ActivityDuration activity={root} /> : null}
        <ChevronDown className={collapsed ? "fx-activity-chevron" : "fx-activity-chevron is-open"} />
      </button>
      {onDetailModeChange ? <select aria-label="Activity detail" value={detailMode} onChange={event => onDetailModeChange(event.target.value as ActivityDetailMode)}>
        <option value="summary">Summary</option><option value="normal">Normal</option><option value="verbose">Verbose</option>
      </select> : null}
      {stopButton}
    </div>
    <div className="fx-feed-collapse" data-open={!collapsed}>
      <div><div className="fx-activity-list">
        {!items.length ? <div role="status" className="fx-waiting">Connecting to live activity…</div> : null}
        {endIndex > 80 ? <button type="button" className="fx-text-button" onClick={() => setEarlierPage(value => value + 1)}>Show earlier activities</button> : null}
        {earlierPage > 0 ? <button type="button" className="fx-text-button" onClick={() => setEarlierPage(0)}>Return to latest activities</button> : null}
        {visible.map(group => <AgentActivityItem key={group[0].id} activities={group} onViewFile={onViewFile}
          onApprove={onApprove} onCancel={onCancel} onReview={onReview}
          collapseLiveThinking={collapseLiveThinking} verbose={detailMode === "verbose"}>
          {group[0].kind === "subagent" ? <div className="fx-specialist-children">
            {groupActivities(nested.get(group[0].agentId ?? "") ?? []).slice(-80).map(child =>
              <AgentActivityItem key={child[0].id} activities={child} onViewFile={onViewFile} verbose={detailMode === "verbose"} />)}
          </div> : null}
        </AgentActivityItem>)}
      </div></div>
    </div>
  </section>;
}
