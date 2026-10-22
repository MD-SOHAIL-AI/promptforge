import type { ActivityRecord } from "@/lib/activity-stream";

export function ContextRecallBlock({ activity }: { activity: ActivityRecord }) {
  const details = activity.details;
  const recalled = Array.isArray(details.recalled_context) ? details.recalled_context : [];
  return (
    <div>
      {typeof details.summary === "string" ? <p className="fx-progress-summary">{details.summary}</p> : null}
      <div className="fx-activity-metrics">
        {typeof details.context_files === "number" ? <span>{details.context_files} project files</span> : null}
        {typeof details.memory_count === "number" ? <span>{details.memory_count} relevant memories</span> : null}
        {typeof details.skill_count === "number" ? <span>{details.skill_count} available skills</span> : null}
      </div>
      {recalled.length ? (
        <details className="fx-inline-details">
          <summary>Recalled context</summary>
          <ul className="fx-recalled-context">
            {recalled.map((value, index) => {
              const item = value && typeof value === "object" ? value as { kind?: string; summary?: string } : {};
              return <li key={index}><strong>{String(item.kind ?? "Context")}</strong><span>{String(item.summary ?? "")}</span></li>;
            })}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
