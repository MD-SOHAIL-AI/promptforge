export interface ApplyStatusInput {
  status: string;
  files_failed: number;
  results: Array<{ status: string; message: string }>;
}

export interface RestoreStatusInput {
  status: string;
  files_failed: number;
  results: Array<{ status: string; message: string }>;
}

export type ApplyDisplayTone = "success" | "warning" | "error" | "muted";

export interface ApplyDisplayState {
  key: "applied" | "restored" | "restore_failed" | "failed" | "failed_rolled_back" | "failed_rollback_failed" | "in_progress" | "blocked";
  label: string;
  tone: ApplyDisplayTone;
  reason: string | null;
}

export function patchApplyDisplayState(apply: ApplyStatusInput, restore?: RestoreStatusInput | null): ApplyDisplayState {
  if (restore?.status === "restored") {
    return { key: "restored", label: "Restored", tone: "success", reason: null };
  }
  if (restore?.status === "failed") {
    return { key: "restore_failed", label: "Restore failed", tone: "error", reason: failureReason(restore) };
  }
  if (apply.status === "applied") {
    return { key: "applied", label: "Applied", tone: "success", reason: null };
  }
  if (apply.status === "failed_rolled_back") {
    return { key: "failed_rolled_back", label: "Failed, rolled back", tone: "warning", reason: failureReason(apply) };
  }
  if (apply.status === "failed_rollback_failed") {
    return { key: "failed_rollback_failed", label: "Failed, rollback failed", tone: "error", reason: failureReason(apply) };
  }
  if (apply.status === "failed") {
    return { key: "failed", label: "Failed", tone: "error", reason: failureReason(apply) };
  }
  if (apply.status === "staged" || apply.status === "applying") {
    return { key: "in_progress", label: apply.status === "staged" ? "Staged" : "Applying", tone: "warning", reason: null };
  }
  return { key: "blocked", label: "Blocked", tone: "muted", reason: failureReason(apply) };
}

export function applyToneClass(tone: ApplyDisplayTone): string {
  if (tone === "success") return "border-[var(--fx-success)] bg-[var(--fx-success-soft)] text-[var(--fx-success)]";
  if (tone === "error") return "border-[var(--fx-error)] bg-[var(--fx-error-soft)] text-[var(--fx-error)]";
  if (tone === "warning") return "border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] text-[var(--fx-warning)]";
  return "border-[var(--fx-border)] bg-[var(--fx-input)] text-[var(--fx-text-muted)]";
}

function failureReason(value: ApplyStatusInput | RestoreStatusInput): string | null {
  const failed = value.results.find((item) => item.status === "failed" && item.message.trim());
  return failed?.message.trim() || (value.files_failed ? `${value.files_failed} file operation(s) failed.` : null);
}
