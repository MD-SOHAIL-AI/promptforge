"use client";

import {
  Ban,
  Check,
  CircleAlert,
  Clock3,
  LoaderCircle,
  Minus,
} from "lucide-react";
import { activityState, isActivityActive, type ActivityRecord } from "@/lib/activity-stream";

export function AgentStatusIndicator({ activity }: { activity: ActivityRecord }) {
  const state = activityState(activity);
  const active = isActivityActive(activity);
  const className = `fx-agent-status is-${state}`;
  if (activity.status === "failed")
    return <CircleAlert aria-label="failed" className={`${className} text-[var(--fx-error)]`} />;
  if (activity.status === "warning")
    return <CircleAlert aria-label="warning" className={`${className} text-[var(--fx-warning)]`} />;
  if (activity.status === "awaiting_approval")
    return <Clock3 aria-label="waiting for approval" className={`${className} text-[var(--fx-warning)]`} />;
  if (activity.status === "cancelled" || activity.status === "interrupted")
    return <Ban aria-label={activity.status} className={className} />;
  if (activity.status === "queued")
    return <Minus aria-label="queued" className={className} />;
  if (active)
    return <LoaderCircle aria-label="running" className={`${className} animate-spin motion-reduce:animate-none`} />;
  return <Check aria-label="success" className={`${className} text-[var(--fx-success)]`} />;
}
