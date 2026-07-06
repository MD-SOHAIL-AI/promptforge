"use client";

import { FileDiff, RotateCcw, ShieldCheck } from "lucide-react";

import { applyToneClass, patchApplyDisplayState } from "@/lib/patch-apply-status";
import type { BridgeSafetyStatusResponse, PatchApplyResult, RollbackRestorePreflightResult, RollbackRestoreResult } from "@/types";

interface PatchApplyDetailProps {
  apply: PatchApplyResult;
  safetyStatus?: BridgeSafetyStatusResponse | null;
  restorePreflight?: RollbackRestorePreflightResult | null;
  restoreResult?: RollbackRestoreResult | null;
  busy?: boolean;
  onOpenRollback?: (rollbackId: string) => void;
  onRestorePreflight?: (rollbackId: string) => void;
  onRestoreSnapshot?: (rollbackId: string) => void;
}

export function PatchApplyDetail({
  apply,
  safetyStatus,
  restorePreflight,
  restoreResult,
  busy = false,
  onOpenRollback,
  onRestorePreflight,
  onRestoreSnapshot,
}: PatchApplyDetailProps) {
  const rollbackId = apply.rollback_id || "";
  const displayState = patchApplyDisplayState(apply, restoreResult);
  const alreadyRestored = restoreResult?.status === "restored";
  const canRestore = Boolean(rollbackId && !alreadyRestored && safetyStatus?.restore_enabled && restorePreflight?.can_restore);
  const restoreDisabledReason = !rollbackId
    ? "No rollback snapshot is linked to this apply."
    : alreadyRestored
      ? "This rollback snapshot has already been restored."
    : !safetyStatus?.restore_enabled
      ? "Rollback restore is disabled by feature flag."
      : !restorePreflight
        ? "Run Restore Preflight before restoring."
        : !restorePreflight.can_restore
          ? "Restore preflight is blocked."
          : "";
  return (
    <section className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] p-2 text-[11px] text-[var(--fx-text-muted)]">
      <div className="mb-2 space-y-1">
        <div className="flex items-center gap-1 font-semibold uppercase text-[var(--fx-text-muted)]">
          <FileDiff className="h-3.5 w-3.5 text-[var(--fx-info)]" />
          Apply Detail
        </div>
        {rollbackId ? (
          <div className="flex min-w-0 flex-wrap items-center gap-1">
            <span className={`rounded border px-1.5 py-0.5 ${applyToneClass(displayState.tone)}`}>{displayState.label}</span>
            <span className="rounded border border-[var(--fx-success)] bg-[var(--fx-success-soft)] px-1.5 py-0.5 text-[var(--fx-success)]">Rollback snapshot available</span>
          </div>
        ) : null}
      </div>
      <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
        <Detail label="Apply ID" value={apply.apply_id} />
        <Detail label="Patch ID" value={apply.patch_id} />
        <Detail label="Review ID" value={apply.review_id} />
        <Detail label="Rollback ID" value={rollbackId || "None"} />
        <Detail label="Provider" value={apply.provider_id} />
        <Detail label="Status" value={displayState.label} tone={displayState.tone === "success" ? "success" : "warning"} />
        <Detail label="Restore status" value={restoreResult?.status ?? "Not restored"} tone={restoreResult?.status === "restored" ? "success" : restoreResult?.status === "failed" ? "warning" : undefined} />
        <Detail label="Files created" value={String(apply.files_created)} />
        <Detail label="Files modified" value={String(apply.files_modified)} />
        <Detail label="Files deleted" value={String(apply.files_deleted)} />
        <Detail label="Files failed" value={String(apply.files_failed)} tone={apply.files_failed ? "warning" : undefined} />
        <Detail label="Started at" value={apply.started_at} />
        <Detail label="Completed at" value={apply.completed_at} />
        <Detail label="Rollback available" value={apply.rollback_available ? "Yes" : "No"} />
        <Detail label="Apply enabled" value={apply.apply_enabled ? "Yes" : "No"} />
        <Detail label="Restore enabled" value={apply.restore_enabled ? "Yes" : "No"} />
      </div>

      {rollbackId ? (
        <div className="mt-2 flex flex-wrap gap-1">
          <button
            className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
            onClick={() => onOpenRollback?.(rollbackId)}
            disabled={busy}
          >
            <FileDiff className="h-3.5 w-3.5" />
            Open rollback snapshot
          </button>
          <button
            className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
            onClick={() => onRestorePreflight?.(rollbackId)}
            disabled={busy}
          >
            <ShieldCheck className="h-3.5 w-3.5" />
            Restore Preflight
          </button>
          <button
            className="flex h-7 items-center gap-1 rounded border border-[var(--fx-error)] bg-[var(--fx-error-soft)] px-1.5 text-[var(--fx-error)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
            onClick={() => onRestoreSnapshot?.(rollbackId)}
            disabled={busy || !canRestore}
            title={canRestore ? "Restore files from rollback snapshot" : restoreDisabledReason}
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Restore Snapshot
          </button>
        </div>
      ) : null}

      {restorePreflight ? (
        <div className={restorePreflight.can_restore ? "mt-2 text-[var(--fx-success)]" : "mt-2 text-[var(--fx-warning)]"}>
          Restore preflight {restorePreflight.can_restore ? "passed" : "blocked"} - {restorePreflight.conflicts.length} conflict(s)
        </div>
      ) : null}
      {rollbackId && !canRestore ? (
        <div className="mt-2 text-[var(--fx-text-muted)]">
          {restoreDisabledReason}
        </div>
      ) : null}
      {restoreResult ? (
        <div className={restoreResult.files_failed ? "mt-1 text-[var(--fx-warning)]" : "mt-1 text-[var(--fx-success)]"}>
          Restore {restoreResult.status} - restored {restoreResult.files_restored}, removed {restoreResult.files_removed}, failed {restoreResult.files_failed}
        </div>
      ) : null}
      {displayState.reason ? <div className="mt-1 truncate text-[var(--fx-error)]" title={displayState.reason}>{displayState.reason}</div> : null}

      <div className="mt-3 overflow-auto rounded border border-[var(--fx-border)]">
        <table className="w-full min-w-[720px] border-collapse text-left">
          <thead className="bg-[var(--fx-input)] text-[var(--fx-text-muted)]">
            <tr>
              <Header label="path" />
              <Header label="operation" />
              <Header label="status" />
              <Header label="before hash" />
              <Header label="after hash" />
              <Header label="message" />
            </tr>
          </thead>
          <tbody>
            {apply.results.length === 0 ? (
              <tr>
                <td className="px-2 py-2 text-[var(--fx-text-muted)]" colSpan={6}>
                  No file results recorded.
                </td>
              </tr>
            ) : (
              apply.results.map((result) => (
                <tr key={`${result.path}-${result.operation}`} className="border-t border-[var(--fx-border)]">
                  <Cell value={result.path} />
                  <Cell value={result.operation} />
                  <Cell value={result.status} tone={result.status === "success" ? "success" : "warning"} />
                  <Cell value={shortHash(result.before_hash)} />
                  <Cell value={shortHash(result.after_hash)} />
                  <Cell value={result.message} />
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Detail({ label, value, tone }: { label: string; value: string; tone?: "success" | "warning" }) {
  const toneClass = tone === "success" ? "text-[var(--fx-success)]" : tone === "warning" ? "text-[var(--fx-warning)]" : "text-[var(--fx-code-text)]";
  return (
    <div className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 py-1">
      <div className="text-[var(--fx-text-muted)]">{label}</div>
      <div className={`truncate ${toneClass}`} title={value}>{value}</div>
    </div>
  );
}

function Header({ label }: { label: string }) {
  return <th className="px-2 py-1 font-semibold uppercase">{label}</th>;
}

function Cell({ value, tone }: { value: string; tone?: "success" | "warning" }) {
  const toneClass = tone === "success" ? "text-[var(--fx-success)]" : tone === "warning" ? "text-[var(--fx-warning)]" : "text-[var(--fx-code-text)]";
  return <td className={`max-w-[220px] truncate px-2 py-1 ${toneClass}`} title={value}>{value}</td>;
}

function shortHash(value?: string | null) {
  return value ? value.slice(0, 12) : "-";
}
