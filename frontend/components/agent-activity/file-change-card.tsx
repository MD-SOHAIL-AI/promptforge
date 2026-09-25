import { ExternalLink } from "lucide-react";
import type { ActivityRecord } from "@/lib/activity-stream";

export function FileChangeCard({ activity, onViewFile }: { activity: ActivityRecord; onViewFile?: (path: string) => void }) {
  const d = activity.details;
  const path = typeof d.path === "string" ? d.path : "";
  const diff = typeof d.diff === "string" ? d.diff : "";
  return (
    <div>
      <div className="fx-file-change-head">
        <span className="min-w-0 break-all font-mono text-[var(--fx-code-text)]">{path || "Workspace file"}</span>
        <span className="fx-diff-stat"><b>+{Number(d.added ?? 0)}</b><i>−{Number(d.removed ?? 0)}</i></span>
      </div>
      <div className="fx-card-actions">
        {diff ? <details className="fx-inline-details"><summary>View diff</summary><pre className="fx-stream-log is-diff">{diff}</pre></details> : null}
        {path && onViewFile ? <button className="fx-inline-action" type="button" onClick={() => onViewFile(path)}><ExternalLink size={12} /> View file</button> : null}
      </div>
    </div>
  );
}
