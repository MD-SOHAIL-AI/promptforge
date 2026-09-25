import type { ActivityRecord } from "@/lib/activity-stream";

export function CommandExecutionCard({ activity }: { activity: ActivityRecord }) {
  const d = activity.details;
  return (
    <div>
      {typeof d.command === "string" ? <pre className="fx-command-line"><span>$</span> {d.command}</pre> : null}
      {typeof d.output === "string" && d.output ? <details className="fx-inline-details" open={activity.status === "failed" || d.output.length < 500}><summary>Output · latest 16 KB</summary><pre className="fx-stream-log">{d.output}</pre></details> : null}
      <div className="fx-activity-metrics">
        {typeof d.exit_code === "number" ? <span>Exit code {d.exit_code}</span> : null}
        {typeof d.warnings === "number" ? <span>{d.warnings} warnings</span> : null}
      </div>
    </div>
  );
}
