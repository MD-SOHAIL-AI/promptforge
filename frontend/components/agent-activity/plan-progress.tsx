import type { ActivityRecord } from "@/lib/activity-stream";

export function PlanProgress({ activity }: { activity: ActivityRecord }) {
  const steps = Array.isArray(activity.details.steps) ? activity.details.steps : [];
  return (
    <ol className="fx-live-plan" aria-label="Plan progress">
      {steps.map((value, index) => {
        if (!value || typeof value !== "object") return null;
        const step = value as { id?: string; text?: string; status?: string; evidence?: string[]; evidence_labels?: string[] };
        const status = activity.durationMs !== null && ["pending", "in_progress"].includes(step.status ?? "") ? "interrupted" : step.status;
        return (
          <li key={step.id ?? index} data-status={status}>
            <span className="fx-plan-glyph" aria-label={status}>
              {status === "completed" ? "✓" : status === "in_progress" ? "●" : status === "failed" ? "×" : status === "skipped" || status === "interrupted" ? "−" : "○"}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block">{String(step.text ?? "")}</span>
              {Array.isArray(step.evidence) && step.evidence.length ? (
                <details className="fx-inline-details">
                  <summary>{step.evidence.length} verified action{step.evidence.length === 1 ? "" : "s"}</summary>
                  <span>{(step.evidence_labels ?? ["Confirmed by completed tool actions."]).join(" · ")}</span>
                </details>
              ) : status === "interrupted" || status === "skipped" ? <small>{status}</small> : null}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
