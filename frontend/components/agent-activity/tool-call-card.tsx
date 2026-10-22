import type { ActivityRecord } from "@/lib/activity-stream";

export function ToolCallCard({ activities }: { activities: ActivityRecord[] }) {
  return (
    <div className="space-y-2">
      {activities.map((activity) => {
        const d = activity.details;
        return (
          <div key={activity.id} className="fx-tool-summary">
            {typeof d.summary === "string" ? <p>{d.summary}</p> : null}
            {typeof d.path === "string" ? <code>{d.path}</code> : null}
            {Array.isArray(d.files) && d.files.length ? (
              <div className="fx-deliverable-list">
                {d.files.map((file, index) => {
                  if (!file || typeof file !== "object") return null;
                  const item = file as Record<string, unknown>;
                  const path = typeof item.path === "string" ? item.path : `File ${index + 1}`;
                  const description = typeof item.description === "string" ? item.description : "";
                  return <div className="fx-deliverable-row" key={`${path}:${index}`}><code>{path}</code>{description ? <span>{description}</span> : null}</div>;
                })}
              </div>
            ) : null}
            {typeof d.output === "string" && d.output ? <details className="fx-inline-details"><summary>Concise output</summary><pre className="fx-stream-log">{d.output}</pre></details> : null}
          </div>
        );
      })}
      {activities.length > 1 ? <span className="fx-muted-note">{activities.length} related actions grouped</span> : null}
    </div>
  );
}
