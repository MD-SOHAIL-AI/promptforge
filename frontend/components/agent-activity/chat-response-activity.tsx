"use client";

import type { ActivityRecord } from "@/lib/activity-stream";
import { ActivityCard } from "./activity-card";

export function ChatResponseActivity({ startedAt, responding, contextCount = 0 }: { startedAt: string; responding: boolean; contextCount?: number }) {
  const activity: ActivityRecord = {
    id: `chat-${startedAt}`,
    runId: "chat",
    kind: "thinking",
    state: "thinking",
    eventType: responding ? "message.delta" : "message.started",
    status: "running",
    title: responding ? "Responding" : "Thinking",
    startedAt: Number.isFinite(Date.parse(startedAt)) ? Date.parse(startedAt) : Date.now(),
    durationMs: null,
    details: {},
  };
  return (
    <div className="fx-chat-activity">
      <ActivityCard activity={activity} title={responding ? "Responding…" : "Thinking…"} defaultOpen={!responding}>
        <p className="fx-thinking-summary">{responding ? "Streaming the public response from the selected model." : "Understanding the request and waiting for the model response."}</p>
        {contextCount ? <p className="fx-safe-reasoning-note">Using {contextCount} attached project file{contextCount === 1 ? "" : "s"} as read-only context.</p> : null}
      </ActivityCard>
    </div>
  );
}
