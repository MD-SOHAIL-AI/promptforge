"use client";

import { useEffect, useState, type ReactNode } from "react";
import { ChevronRight, SquareTerminal } from "lucide-react";
import { activityElapsed, isActivityActive, type ActivityRecord } from "@/lib/activity-stream";
import { AgentStatusIndicator } from "./agent-status-indicator";

function formatDuration(ms: number) {
  const seconds = ms < 60_000 ? Number((ms / 1000).toFixed(1)) : Math.floor(ms / 1000);
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function ActivityDuration({ activity }: { activity: ActivityRecord }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (activity.durationMs !== null) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [activity.durationMs]);
  return <span className="fx-activity-time">{formatDuration(activityElapsed(activity, now))}</span>;
}

export function ActivityCard({
  activity,
  title,
  subtitle,
  defaultOpen = false,
  autoOpenActive = true,
  children,
}: {
  activity: ActivityRecord;
  title: string;
  subtitle?: string | null;
  defaultOpen?: boolean;
  autoOpenActive?: boolean;
  children?: ReactNode;
}) {
  const [expanded, setExpanded] = useState<boolean | null>(null);
  const open = expanded ?? (defaultOpen || (autoOpenActive && isActivityActive(activity)));
  const hasDetails = Boolean(children);
  const metaParts = [
    typeof activity.details.turn === "number" ? `Turn ${activity.details.turn}` : null,
    typeof activity.details.step === "number" ? `Step ${activity.details.step}` : null,
    typeof activity.details.tool === "string" ? activity.details.tool.replaceAll("_", " ") : null,
  ].filter((value): value is string => Boolean(value));

  return (
    <article className="fx-activity-item" data-state={activity.state} data-status={activity.status}>
      <button
        type="button"
        className="fx-activity-toggle"
        aria-expanded={open}
        onClick={() => hasDetails && setExpanded(!open)}
        data-expandable={hasDetails || undefined}
      >
        <span className="fx-agent-mark" aria-hidden="true">
          <SquareTerminal className="fx-agent-mark-glyph" />
          <span className="fx-agent-mark-status"><AgentStatusIndicator activity={activity} /></span>
        </span>
        <span className="fx-activity-copy">
          <span className="fx-activity-title">{title}</span>
          {subtitle ? <span className="fx-activity-subtitle">{subtitle}</span> : null}
        </span>
        <ActivityDuration activity={activity} />
        {hasDetails ? <ChevronRight aria-hidden="true" className={`fx-activity-chevron ${open ? "is-open" : ""}`} /> : null}
      </button>
      {hasDetails ? (
        <div className="fx-activity-collapse" data-open={open}>
          <div>
            <div className="fx-activity-details">
              <div className="fx-activity-evidence-head">
                <span>{activity.eventType || activity.kind}</span>
                <span>{activity.status.replaceAll("_", " ")}</span>
              </div>
              {metaParts.length ? <div className="fx-activity-meta">{metaParts.map((part) => <span key={part}>{part}</span>)}</div> : null}
              {children}
            </div>
          </div>
        </div>
      ) : null}
    </article>
  );
}
