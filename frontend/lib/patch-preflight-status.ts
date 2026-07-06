import type { PatchPreflightConflict, PatchPreflightConflictType, PatchPreflightResult } from "@/types";

export type PatchPreflightUiStatus =
  | "safe_candidate"
  | "blocked"
  | "warning"
  | "needs_active_workspace"
  | "patch_modified"
  | "approval_required"
  | "unknown";

export interface PatchPreflightStatusInfo {
  status: PatchPreflightUiStatus;
  label: string;
  tone: "success" | "warning" | "error" | "muted";
  summary: string;
}

const conflictLabels: Record<PatchPreflightConflictType, string> = {
  clean: "Clean",
  workspace_drift: "Workspace changed since review.",
  patch_modified: "Patch file was modified after export.",
  patch_missing: "Patch file is missing.",
  review_missing: "Review session was not found.",
  review_not_approved: "Review is not approved yet.",
  review_expired: "Review expired. Create a new sandbox review.",
  provider_mismatch: "Patch provider does not match review provider.",
  path_unsafe: "Patch contains unsafe path.",
  target_missing: "Target file is missing.",
  target_changed: "Target file changed after review.",
  delete_conflict: "Delete target changed or is unsafe.",
  binary_unsupported: "Binary change is not supported.",
  large_file_unsupported: "Large file change is not supported.",
  ignored_path: "Patch targets ignored/build folder.",
  parse_error: "Patch format could not be parsed.",
  unknown: "Unknown preflight issue.",
};

export function patchPreflightStatus(result: Partial<PatchPreflightResult> | null | undefined): PatchPreflightStatusInfo {
  if (!result) {
    return {
      status: "unknown",
      label: "Unknown",
      tone: "muted",
      summary: "No preflight result is available.",
    };
  }
  const conflicts = safeConflicts(result.conflicts);
  const warnings = safeConflicts(result.warnings);
  if (hasConflict(conflicts, "workspace_drift") && result.workspace_status !== "ok") {
    return {
      status: "needs_active_workspace",
      label: "Needs active workspace",
      tone: "warning",
      summary: "Open the target workspace before running preflight.",
    };
  }
  if (result.integrity_status === "modified" || hasConflict(conflicts, "patch_modified")) {
    return {
      status: "patch_modified",
      label: "Patch modified",
      tone: "error",
      summary: "Patch file was modified after export.",
    };
  }
  if (hasConflict(conflicts, "review_not_approved")) {
    return {
      status: "approval_required",
      label: "Approval required",
      tone: "warning",
      summary: "Review is not approved yet.",
    };
  }
  if (result.can_apply === false && conflicts.length > 0) {
    const first = conflicts[0];
    return {
      status: "blocked",
      label: "Blocked",
      tone: "error",
      summary: first ? conflictReadableMessage(first) : "Preflight found blocking conflicts.",
    };
  }
  if (warnings.length > 0) {
    return {
      status: "warning",
      label: "Warning",
      tone: "warning",
      summary: conflictReadableMessage(warnings[0]),
    };
  }
  if (result.can_apply === true && conflicts.length === 0) {
    return {
      status: "safe_candidate",
      label: "Safe candidate",
      tone: "success",
      summary: "Patch appears safe to apply later.",
    };
  }
  return {
    status: "unknown",
    label: "Unknown",
    tone: "muted",
    summary: "Preflight result is incomplete.",
  };
}

export function conflictTypeLabel(type: PatchPreflightConflictType | string | undefined) {
  if (!type) return conflictLabels.unknown;
  return conflictLabels[type as PatchPreflightConflictType] ?? type.replaceAll("_", " ");
}

export function conflictReadableMessage(conflict: Partial<PatchPreflightConflict> | undefined) {
  if (!conflict) return "";
  const fallback = conflictTypeLabel(conflict.type);
  const message = typeof conflict.message === "string" && conflict.message.trim() ? conflict.message : fallback;
  return conflict.path ? `${conflict.path}: ${message}` : message;
}

export function preflightToneClass(tone: PatchPreflightStatusInfo["tone"]) {
  if (tone === "success") return "border-[var(--fx-success)] bg-[var(--fx-success-soft)] text-[var(--fx-success)]";
  if (tone === "warning") return "border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] text-[var(--fx-warning)]";
  if (tone === "error") return "border-[var(--fx-error)] bg-[var(--fx-error-soft)] text-[var(--fx-error)]";
  return "border-[var(--fx-border)] bg-[var(--fx-input)] text-[var(--fx-text-muted)]";
}

export function preflightLogLines(result: PatchPreflightResult) {
  const status = patchPreflightStatus(result);
  return [
    `[preflight] Patch integrity: ${result.integrity_status ?? "unknown"}`,
    `[preflight] Result: ${status.label.toLowerCase()}, ${result.conflicts?.length ?? 0} conflict${(result.conflicts?.length ?? 0) === 1 ? "" : "s"}`,
  ];
}

function safeConflicts(value: PatchPreflightResult["conflicts"] | PatchPreflightResult["warnings"] | undefined) {
  return Array.isArray(value) ? value : [];
}

function hasConflict(conflicts: PatchPreflightConflict[], type: PatchPreflightConflictType) {
  return conflicts.some((item) => item.type === type);
}
