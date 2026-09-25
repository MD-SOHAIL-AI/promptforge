import type { ActivityRecord } from "@/lib/activity-stream";
import { CommandExecutionCard } from "./command-execution-card";

export function BuildResultCard({ activity }: { activity: ActivityRecord }) {
  const d = activity.details;
  const diagnostics = Array.isArray(d.diagnostics) ? d.diagnostics : [];
  return (
    <div>
      <CommandExecutionCard activity={activity} />
      {diagnostics.length ? (
        <details className="fx-inline-details" open={activity.status === "failed"}>
          <summary>{diagnostics.length} diagnostic{diagnostics.length === 1 ? "" : "s"}</summary>
          <div className="fx-diagnostics">
            {diagnostics.map((value, index) => {
              const item = value && typeof value === "object" ? value as Record<string, unknown> : {};
              return <div key={index}><code>{String(item.path ?? "build")}:{String(item.line ?? "—")}</code><span>{String(item.message ?? "Unknown build error")}</span></div>;
            })}
          </div>
        </details>
      ) : null}
      {activity.status === "failed" ? <p className="fx-repair-note">Forge can continue the same run with a repair attempt.</p> : null}
    </div>
  );
}
