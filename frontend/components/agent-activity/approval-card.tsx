import { ShieldCheck } from "lucide-react";
import type { ActivityRecord } from "@/lib/activity-stream";

export function ApprovalCard({ activity, onApprove, onCancel, onReview }: { activity: ActivityRecord; onApprove?: () => void; onCancel?: () => void; onReview?: () => void }) {
  const d = activity.details;
  return (
    <div className="fx-approval-inline">
      <ShieldCheck size={17} />
      <div className="min-w-0 flex-1">
        <p>{typeof d.summary === "string" ? d.summary : "A sensitive operation is paused until you approve it."}</p>
        <span>{[d.board, d.environment, d.port].filter(Boolean).map(String).join(" · ")}</span>
        {activity.status === "awaiting_approval" ? <div className="fx-card-actions">
          {onReview ? <button type="button" className="fx-inline-action" onClick={onReview}>Review changes</button> : null}
          {onApprove ? <button type="button" className="fx-approve-action" onClick={onApprove}>Approve</button> : null}
          {onCancel ? <button type="button" className="fx-inline-action" onClick={onCancel}>Cancel</button> : null}
        </div> : null}
      </div>
    </div>
  );
}
