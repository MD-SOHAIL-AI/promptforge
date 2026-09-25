"use client";

import { useMemo, useState } from "react";
import { ChevronDown, SquareTerminal } from "lucide-react";
import { groupActivities, validActivity, type ActivityRecord } from "@/lib/activity-stream";
import type { ProductAgentEvent, ProductAgentRun } from "@/types";
import { ActivityDuration } from "./activity-card";
import { AgentStatusIndicator } from "./agent-status-indicator";
import { AgentActivityItem } from "./agent-activity-item";

const TERMINAL = ["completed", "success", "failed", "cancelled", "blocked", "timed_out", "interrupted"];

function phaseFor(activity: ActivityRecord): string {
  if (["thinking", "summary", "context", "plan", "file.read", "search"].includes(activity.kind)) return "Understanding and planning";
  if (["file.edit", "subagent", "tool", "deliverable"].includes(activity.kind)) return "Continued implementation";
  if (["command", "build", "test", "simulation", "project.validation"].includes(activity.kind)) return "Validation";
  if (["build.repair", "project.repair"].includes(activity.kind)) return "Repair and verification";
  if (["approval", "flash", "monitor"].includes(activity.kind)) return "Hardware handoff";
  return "Agent activity";
}

function phaseGroups(items: ActivityRecord[]) {
  const grouped = groupActivities(items);
  const phases: Array<{ label: string; groups: ActivityRecord[][] }> = [];
  for (const group of grouped) {
    const label = phaseFor(group[0]);
    const last = phases.at(-1);
    if (last?.label === label) last.groups.push(group);
    else phases.push({ label, groups: [group] });
  }
  return phases;
}

export function AgentActivityFeed({ events, run, onViewFile, onApprove, onCancel, onReview, collapseLiveThinking = false }: {
  events: ProductAgentEvent[];
  run: ProductAgentRun | null;
  onViewFile?: (path: string) => void;
  onApprove?: () => void;
  onCancel?: () => void;
  onReview?: () => void;
  collapseLiveThinking?: boolean;
}) {
  const [limit, setLimit] = useState(80);
  const [collapsed, setCollapsed] = useState(false);
  const items = useMemo(() => {
    const latest = new Map<string, ActivityRecord>();
    for (const event of events) if (validActivity(event.activity_record) && (!run || event.run_id === run.run_id)) latest.set(event.activity_record.id, event.activity_record);
    const end = [...latest.values()].findLast((item) => item.kind === "run" && TERMINAL.includes(item.status));
    const terminal = end || (run && TERMINAL.includes(run.status));
    return [...latest.values()]
      .filter((activity) => activity.kind !== "node")
      .sort((a, b) => a.startedAt - b.startedAt || a.id.localeCompare(b.id))
      .map((activity) => {
        if (!terminal || !["queued", "running", "awaiting_approval"].includes(activity.status)) return activity;
        const finishedAt = end ? end.startedAt + (end.durationMs ?? 0) : Date.parse(run?.updated_at ?? "");
        return { ...activity, status: activity.kind === "approval" ? "cancelled" as const : "interrupted" as const, durationMs: Math.max(0, (Number.isFinite(finishedAt) ? finishedAt : activity.startedAt) - activity.startedAt) };
      });
  }, [events, run]);

  if (!items.length) {
    if (!run || TERMINAL.includes(run.status)) return null;
    return (
      <section aria-label="Agent activity" className="fx-activity-stream is-empty">
        <div className="fx-agent-journal-row" role="status" aria-live="polite">
          <span className="fx-agent-mark" aria-hidden="true"><SquareTerminal className="fx-agent-mark-glyph" /></span>
          <span className="min-w-0 flex-1 truncate">
            {run.status === "queued" ? "Starting ForgeX agent…" : "Connecting to live activity…"}
          </span>
        </div>
      </section>
    );
  }

  const root = items.findLast((activity) => activity.kind === "run");
  const main = items.filter((activity) => activity.kind !== "run" && (!activity.parentId || activity.kind === "subagent"));
  const visibleMain = main.slice(-limit);
  const phases = phaseGroups(visibleMain);

  return (
    <section aria-label="Agent activity" className="fx-activity-stream">
      {root ? (
        <button type="button" className="fx-run-heading" aria-expanded={!collapsed} onClick={() => setCollapsed(!collapsed)}>
          <span className="fx-run-status"><AgentStatusIndicator activity={root} /></span>
          <span className="min-w-0 flex-1 truncate">
            {root.status === "running" ? "ForgeX is working" : root.status === "completed" ? "Agent activity" : root.status.replaceAll("_", " ")}
          </span>
          <ActivityDuration activity={root} />
          <ChevronDown className={`fx-activity-chevron ${!collapsed ? "is-open" : ""}`} />
        </button>
      ) : null}
      <div className="fx-feed-collapse" data-open={!collapsed}>
        <div>
          <div className="fx-activity-list">
            {main.length > limit ? <button type="button" className="fx-text-button fx-activity-earlier" onClick={() => setLimit((value) => value + 80)}>Show earlier activities</button> : null}
            {phases.map((phase, phaseIndex) => (
              <section className="fx-activity-phase" key={`${phase.label}:${phaseIndex}`} aria-label={phase.label}>
                <h3 className="fx-activity-phase-title">{phase.label}</h3>
                <div className="fx-activity-phase-items">
                  {phase.groups.map((group) => (
                    <AgentActivityItem key={group[0].id} activities={group} onViewFile={onViewFile} onApprove={onApprove} onCancel={onCancel} onReview={onReview} collapseLiveThinking={collapseLiveThinking}>
                      {group[0].kind === "subagent" ? <div className="fx-specialist-children">
                        {groupActivities(items.filter((activity) => activity.kind !== "subagent" && activity.agentId === group[0].agentId)).map((nested) => <AgentActivityItem key={nested[0].id} activities={nested} onViewFile={onViewFile} />)}
                      </div> : null}
                    </AgentActivityItem>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
