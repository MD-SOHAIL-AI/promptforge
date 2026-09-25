import type { ActivityRecord } from "@/lib/activity-stream";

export function ThinkingBlock({ activity }: { activity: ActivityRecord }) {
  const summary = typeof activity.details.summary === "string" ? activity.details.summary : "Understanding the request and choosing the next safe action.";
  return (
    <div className="fx-reasoning-summary">
      <p>{summary}</p>
    </div>
  );
}
