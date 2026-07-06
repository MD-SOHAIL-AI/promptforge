"use client";

import { CheckCircle2, CircleAlert, Clipboard, Download, ExternalLink, FileDiff, Loader2, ShieldCheck, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useForgeXDialogs } from "@/components/ide/dialogs/forgex-dialog-provider";
import { PatchPreflightReport } from "@/components/ide/patch-preflight-report";
import { promptForgeApi } from "@/lib/api";
import { toErrorMessage } from "@/lib/errors";
import { conflictReadableMessage, patchPreflightStatus, preflightLogLines } from "@/lib/patch-preflight-status";
import type { BridgeChangedFileResponse, BridgePatchExportResponse, BridgeReviewResponse, BridgeSafetyStatusResponse, ConsoleEntry, PatchApplyResult, PatchPreflightResult, RollbackRestorePreflightResult, RollbackRestoreResult, RollbackSnapshotCreateResponse } from "@/types";

interface BridgeReviewPanelProps {
  review: BridgeReviewResponse | null;
  providerName?: string;
  onApprove?: (reviewId: string) => void;
  onReject?: (reviewId: string) => void;
  onPatchUpdated?: () => void;
  workspaceRoot?: string | null;
  onLog?: (entry: Omit<ConsoleEntry, "id" | "timestamp">) => void;
  busy?: boolean;
}

export function BridgeReviewPanel({ review, providerName, onApprove, onReject, onPatchUpdated, workspaceRoot, onLog, busy = false }: BridgeReviewPanelProps) {
  const dialogs = useForgeXDialogs();
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [patchExport, setPatchExport] = useState<BridgePatchExportResponse | null>(null);
  const [patchBusy, setPatchBusy] = useState<"export" | "copy" | "verify" | "open" | "preflight" | "rollback" | "restore-preflight" | "restore" | "apply" | null>(null);
  const [patchMessage, setPatchMessage] = useState<string | null>(null);
  const [preflightResult, setPreflightResult] = useState<PatchPreflightResult | null>(null);
  const [rollbackSnapshot, setRollbackSnapshot] = useState<RollbackSnapshotCreateResponse | null>(null);
  const [restorePreflightResult, setRestorePreflightResult] = useState<RollbackRestorePreflightResult | null>(null);
  const [restoreResult, setRestoreResult] = useState<RollbackRestoreResult | null>(null);
  const [applyResult, setApplyResult] = useState<PatchApplyResult | null>(null);
  const [safetyStatus, setSafetyStatus] = useState<BridgeSafetyStatusResponse | null>(null);
  const selected = useMemo(() => {
    if (!review) return null;
    return review.changed_files.find((file) => file.path === selectedPath) ?? review.changed_files[0] ?? null;
  }, [review, selectedPath]);
  const preflightWorkspaceRoot = workspaceRoot || review?.workspace_root || "";
  const applyReady = Boolean(
    safetyStatus?.patch_apply_enabled &&
    safetyStatus?.rollback_restore_enabled &&
    preflightResult?.can_apply &&
    preflightResult.apply_enabled &&
    review?.status === "approved" &&
    patchExport?.integrity_status === "valid" &&
    preflightWorkspaceRoot,
  );

  useEffect(() => {
    let cancelled = false;
    promptForgeApi.bridgeSafetyStatus()
      .then((status) => {
        if (!cancelled) setSafetyStatus(status);
      })
      .catch(() => {
        if (!cancelled) setSafetyStatus(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const exportPatch = async () => {
    if (!review) return;
    setPatchBusy("export");
    setPatchMessage(null);
    try {
      const response = await promptForgeApi.exportBridgeReviewPatch(review.review_id);
      setPatchExport(response.patch);
      onPatchUpdated?.();
      setPatchMessage(`Patch exported: ${formatBytes(response.patch.patch_size)} across ${response.patch.file_count} file(s).`);
    } catch (error) {
      setPatchMessage(`Could not export patch: ${toErrorMessage(error, "Patch export failed.")}`);
    } finally {
      setPatchBusy(null);
    }
  };

  const copyPatch = async () => {
    if (!review) return;
    setPatchBusy("copy");
    setPatchMessage(null);
    try {
      if (!patchExport) {
        const response = await promptForgeApi.exportBridgeReviewPatch(review.review_id);
        setPatchExport(response.patch);
      }
      const patch = await promptForgeApi.bridgeReviewPatch(review.review_id);
      await navigator.clipboard.writeText(patch);
      const copied = await promptForgeApi.markBridgeReviewPatchCopied(review.review_id);
      setPatchExport(copied.patch);
      onPatchUpdated?.();
      setPatchMessage("Patch copied to clipboard. Review it before applying manually.");
    } catch (error) {
      setPatchMessage(`Could not copy patch: ${toErrorMessage(error, "Patch copy failed.")}`);
    } finally {
      setPatchBusy(null);
    }
  };

  const verifyPatch = async () => {
    if (!review) return;
    setPatchBusy("verify");
    setPatchMessage(null);
    try {
      const response = await promptForgeApi.verifyBridgeReviewPatch(review.review_id);
      setPatchExport(response.patch);
      onPatchUpdated?.();
      setPatchMessage(
        response.patch.integrity_status === "valid"
          ? "Patch integrity is valid."
          : "Patch integrity warning: exported patch content no longer matches recorded hash.",
      );
    } catch (error) {
      setPatchMessage(`Could not verify patch: ${toErrorMessage(error, "Patch verification failed.")}`);
    } finally {
      setPatchBusy(null);
    }
  };

  const openPatchFolder = async () => {
    if (!review) return;
    setPatchBusy("open");
    setPatchMessage(null);
    try {
      if (!patchExport) {
        const response = await promptForgeApi.exportBridgeReviewPatch(review.review_id);
        setPatchExport(response.patch);
      }
      await promptForgeApi.openBridgeReviewPatchFolder(review.review_id);
      setPatchMessage("Patch folder opened.");
    } catch (error) {
      setPatchMessage(`Could not open patch folder: ${toErrorMessage(error, "Patch folder could not be opened.")}`);
    } finally {
      setPatchBusy(null);
    }
  };

  const preflightPatch = async () => {
    if (!review || !patchExport) return;
    if (!preflightWorkspaceRoot) {
      setPatchMessage("Open the target workspace before running preflight.");
      return;
    }
    setPatchBusy("preflight");
    setPatchMessage("Running preflight...");
    setPreflightResult(null);
    setRollbackSnapshot(null);
    setRestorePreflightResult(null);
    setRestoreResult(null);
    setApplyResult(null);
    onLog?.({ channel: "system", message: "[preflight] Started patch preflight" });
    try {
      const result = await promptForgeApi.preflightPatch(patchExport.patch_id, preflightWorkspaceRoot);
      setPreflightResult(result);
      for (const line of preflightLogLines(result)) {
        onLog?.({ channel: result.can_apply ? "system" : "error", message: line });
      }
      const status = patchPreflightStatus(result);
      const firstIssue = result.conflicts[0] ?? result.warnings[0];
      setPatchMessage(
        result.can_apply
          ? safetyStatus?.patch_apply_enabled
            ? "Preflight passed. Patch can be applied after exact confirmation."
            : "Preflight passed. Patch appears safe to apply, but Apply is disabled."
          : `Preflight failed. ${firstIssue ? conflictReadableMessage(firstIssue) : status.summary}`,
      );
    } catch (error) {
      const message = `Could not run preflight: ${toErrorMessage(error, "Patch preflight failed.")}`;
      onLog?.({ channel: "error", message: `[preflight] ${message}` });
      setPatchMessage(message);
    } finally {
      setPatchBusy(null);
    }
  };

  const createRollbackSnapshot = async () => {
    if (!patchExport || !preflightResult?.can_apply || !preflightWorkspaceRoot) return;
    setPatchBusy("rollback");
    setPatchMessage(null);
    try {
      const snapshot = await promptForgeApi.createRollbackSnapshot(patchExport.patch_id, preflightWorkspaceRoot);
      setRollbackSnapshot(snapshot);
      setRestorePreflightResult(null);
      setRestoreResult(null);
      setPatchMessage("Rollback snapshot created. Apply is still disabled in this build.");
      onLog?.({ channel: "system", message: `[preflight] Rollback snapshot created, ${snapshot.files_backed_up} file${snapshot.files_backed_up === 1 ? "" : "s"} backed up` });
    } catch (error) {
      const message = `Could not create rollback snapshot: ${toErrorMessage(error, "Rollback snapshot failed.")}`;
      onLog?.({ channel: "error", message: `[preflight] ${message}` });
      setPatchMessage(message);
    } finally {
      setPatchBusy(null);
    }
  };

  const preflightRestore = async () => {
    if (!rollbackSnapshot || !preflightWorkspaceRoot) return;
    setPatchBusy("restore-preflight");
    setPatchMessage("Running restore preflight...");
    try {
      const result = await promptForgeApi.restorePreflightRollbackSnapshot(rollbackSnapshot.rollback_id, preflightWorkspaceRoot);
      setRestorePreflightResult(result);
      setRestoreResult(null);
      const firstIssue = result.conflicts[0] ?? result.warnings[0];
      setPatchMessage(
        result.can_restore
          ? "Restore preflight passed. Snapshot appears restorable, but Restore is disabled in this build."
          : `Restore preflight blocked. ${firstIssue ? `${firstIssue.path ? `${firstIssue.path}: ` : ""}${firstIssue.message}` : "Review restore conflicts."}`,
      );
      onLog?.({ channel: result.can_restore ? "system" : "error", message: `[preflight] Restore preflight ${result.can_restore ? "passed" : "blocked"}, ${result.conflicts.length} conflict${result.conflicts.length === 1 ? "" : "s"}` });
    } catch (error) {
      const message = `Could not run restore preflight: ${toErrorMessage(error, "Restore preflight failed.")}`;
      onLog?.({ channel: "error", message: `[preflight] ${message}` });
      setPatchMessage(message);
    } finally {
      setPatchBusy(null);
    }
  };

  const restoreSnapshot = async () => {
    if (!rollbackSnapshot || !restorePreflightResult?.can_restore || !safetyStatus?.restore_enabled || !preflightWorkspaceRoot) return;
    const confirmation = await dialogs.input({
      title: "Restore rollback snapshot?",
      description: "This modifies files in the active workspace. Only files recorded in the rollback snapshot will be restored. Type RESTORE to continue.",
      label: "Confirmation",
      placeholder: "RESTORE",
      confirmText: "Restore files",
      validate: (value) => value === "RESTORE" ? null : "Enter RESTORE exactly to continue.",
    });
    if (confirmation !== "RESTORE") {
      setPatchMessage("Restore cancelled. Exact confirmation RESTORE is required.");
      return;
    }
    setPatchBusy("restore");
    setPatchMessage("Restoring rollback snapshot...");
    onLog?.({ channel: "system", message: "[restore] Rollback restore started" });
    try {
      const result = await promptForgeApi.restoreRollbackSnapshot(rollbackSnapshot.rollback_id, preflightWorkspaceRoot, confirmation);
      setRestoreResult(result);
      setPatchMessage(`Restore completed. Files restored: ${result.files_restored}. Files removed: ${result.files_removed}. Files failed: ${result.files_failed}.`);
      onLog?.({ channel: result.files_failed ? "error" : "system", message: `[restore] Files restored: ${result.files_restored}, removed: ${result.files_removed}, failed: ${result.files_failed}` });
      if (result.files_failed === 0) {
        window.dispatchEvent(new CustomEvent("forgex:workspace-files-changed", { detail: { workspaceRoot: preflightWorkspaceRoot } }));
      }
    } catch (error) {
      const message = `Could not restore rollback snapshot: ${toErrorMessage(error, "Rollback restore failed.")}`;
      onLog?.({ channel: "error", message: `[restore] ${message}` });
      setPatchMessage(message);
    } finally {
      setPatchBusy(null);
    }
  };

  const applyPatch = async () => {
    if (!review || !patchExport || !preflightWorkspaceRoot || !applyReady) return;
    const confirmation = await dialogs.input({
      title: "Apply patch to active workspace?",
      description: "ForgeX will create a rollback snapshot before applying. Only files listed in the preflight report will be touched. Type APPLY to continue.",
      label: "Confirmation",
      placeholder: "APPLY",
      confirmText: "Apply patch",
      validate: (value) => value === "APPLY" ? null : "Enter APPLY exactly to continue.",
    });
    if (confirmation !== "APPLY") {
      setPatchMessage("Apply cancelled. Exact confirmation APPLY is required.");
      return;
    }
    setPatchBusy("apply");
    setPatchMessage("Applying patch...");
    onLog?.({ channel: "system", message: "[apply] Patch apply started" });
    try {
      const result = await promptForgeApi.applyPatch(patchExport.patch_id, preflightWorkspaceRoot, confirmation);
      setApplyResult(result);
      setPatchMessage(
        result.status === "applied"
          ? `Patch applied. Files created: ${result.files_created}. Files modified: ${result.files_modified}. Files deleted: ${result.files_deleted}. Rollback snapshot: ${result.rollback_id ?? "unknown"}. Rollback restore is available if needed.`
          : `Patch apply ${result.status}. Files failed: ${result.files_failed}. Rollback snapshot: ${result.rollback_id ?? "unknown"}.`,
      );
      onLog?.({ channel: result.status === "applied" ? "system" : "error", message: "[apply] Fresh preflight passed" });
      onLog?.({ channel: result.status === "applied" ? "system" : "error", message: `[apply] Rollback snapshot created: ${result.rollback_id ?? "unknown"}` });
      onLog?.({ channel: result.status === "applied" ? "system" : "error", message: `[apply] Files created: ${result.files_created}, modified: ${result.files_modified}, deleted: ${result.files_deleted}` });
      onLog?.({ channel: result.status === "applied" ? "system" : "error", message: `[apply] Patch apply ${result.status === "applied" ? "completed" : result.status}` });
      onPatchUpdated?.();
      if (result.status === "applied") {
        window.dispatchEvent(new CustomEvent("forgex:workspace-files-changed", { detail: { workspaceRoot: preflightWorkspaceRoot } }));
      }
    } catch (error) {
      const message = `Could not apply patch: ${toErrorMessage(error, "Patch apply failed.")}`;
      onLog?.({ channel: "error", message: `[apply] ${message}` });
      setPatchMessage(message);
    } finally {
      setPatchBusy(null);
    }
  };

  if (!review) {
    return (
      <section className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-[var(--fx-text)]">
          <FileDiff className="h-4 w-4 text-[var(--fx-info)]" />
          Bridge Review
        </div>
        <div className="mt-2 text-xs text-[var(--fx-text-muted)]">No bridge review is active.</div>
      </section>
    );
  }

  return (
    <section className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-[var(--fx-text)]">
          <FileDiff className="h-4 w-4 text-[var(--fx-info)]" />
          Bridge Review
        </div>
        <span className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 py-0.5 text-[11px] text-[var(--fx-text-muted)]">
          {review.status}
        </span>
      </div>
      <div className="mt-2 grid gap-1 text-xs text-[var(--fx-text-muted)]">
        <div>Provider: <span className="text-[var(--fx-code-text)]">{providerName ?? review.provider_id}</span></div>
        <div>Changed files: <span className="text-[var(--fx-code-text)]">{review.changed_files.length}</span></div>
        <div>{review.summary}</div>
      </div>
      <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(220px,280px)_minmax(0,1fr)]">
        <div className="space-y-1">
          {review.changed_files.map((file) => (
            <button
              key={file.path}
              className={`flex w-full min-w-0 items-center gap-2 rounded border px-2 py-1.5 text-left text-xs ${
                selected?.path === file.path
                  ? "border-[var(--fx-accent)] bg-[var(--fx-hover)] text-[var(--fx-text)]"
                  : "border-[var(--fx-border)] bg-[var(--fx-input)] text-[var(--fx-text-muted)]"
              }`}
              onClick={() => setSelectedPath(file.path)}
            >
              {file.safe ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-[var(--fx-success)]" /> : <CircleAlert className="h-3.5 w-3.5 shrink-0 text-[var(--fx-warning)]" />}
              <span className="min-w-0 flex-1 truncate">{file.path}</span>
              <span className="shrink-0">{file.change_type}</span>
            </button>
          ))}
        </div>
        <DiffPreview file={selected} />
      </div>
      <div className="mt-3 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2 text-xs text-[var(--fx-text-muted)]">
        {safetyStatus?.patch_apply_enabled ? "Patch apply is enabled for approved, preflighted patches." : "Apply disabled. Enable patch apply and rollback restore flags for development testing."}
        {patchExport ? (
          <div className="mt-2 grid gap-1 text-[11px] text-[var(--fx-text-muted)]">
            <div className="font-semibold uppercase text-[var(--fx-text-muted)]">Patch Metadata</div>
            <div>Patch SHA-256: <span className="break-all text-[var(--fx-code-text)]">{patchExport.patch_sha256 || "Unknown"}</span></div>
            <div>Patch size: <span className="text-[var(--fx-code-text)]">{formatBytes(patchExport.patch_size)}</span></div>
            <div>Files: <span className="text-[var(--fx-code-text)]">{patchExport.changed_file_count}</span></div>
            <div>Integrity: <span className={integrityClass(patchExport.integrity_status)}>{integrityLabel(patchExport.integrity_status)}</span></div>
            <div>Apply enabled: <span className="text-[var(--fx-warning)]">{patchExport.apply_enabled ? "Yes" : "No"}</span></div>
          </div>
        ) : (
          <div className="mt-1 text-[11px] text-[var(--fx-text-muted)]">
            No patch exported yet. Export a patch to view integrity metadata.
          </div>
        )}
        {preflightResult ? <div className="mt-2"><PatchPreflightReport result={preflightResult} compact defaultExpanded /></div> : null}
        {rollbackSnapshot ? (
          <div className="mt-2 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] p-2 text-[11px] text-[var(--fx-text-muted)]">
            <div className="font-semibold uppercase">Rollback snapshot</div>
            <div>Files backed up: <span className="text-[var(--fx-code-text)]">{rollbackSnapshot.files_backed_up}</span></div>
            <div>Total bytes: <span className="text-[var(--fx-code-text)]">{rollbackSnapshot.total_bytes}</span></div>
            <div>Restore: <span className="text-[var(--fx-warning)]">{rollbackSnapshot.restore_enabled ? "enabled" : "disabled"}</span></div>
          </div>
        ) : null}
        {restorePreflightResult ? (
          <div className="mt-2 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] p-2 text-[11px] text-[var(--fx-text-muted)]">
            <div className={restorePreflightResult.can_restore ? "font-semibold uppercase text-[var(--fx-success)]" : "font-semibold uppercase text-[var(--fx-warning)]"}>
              Restore preflight {restorePreflightResult.can_restore ? "passed" : "blocked"}
            </div>
            <div>Files to restore: <span className="text-[var(--fx-code-text)]">{restorePreflightResult.files_to_restore.length}</span></div>
            <div>Files to remove: <span className="text-[var(--fx-code-text)]">{restorePreflightResult.files_to_remove.length}</span></div>
            <div>Restore: <span className="text-[var(--fx-warning)]">{restorePreflightResult.restore_enabled ? "enabled" : "disabled"}</span></div>
            {restorePreflightResult.conflicts.slice(0, 2).map((item, index) => (
              <div key={`${item.path}-${item.type}-${index}`} className="break-words text-[var(--fx-warning)]">{item.path ? `${item.path}: ` : ""}{item.message}</div>
            ))}
          </div>
        ) : null}
        {restoreResult ? (
          <div className="mt-2 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] p-2 text-[11px] text-[var(--fx-text-muted)]">
            <div className={restoreResult.files_failed ? "font-semibold uppercase text-[var(--fx-warning)]" : "font-semibold uppercase text-[var(--fx-success)]"}>
              Restore {restoreResult.status}
            </div>
            <div>Files restored: <span className="text-[var(--fx-code-text)]">{restoreResult.files_restored}</span></div>
            <div>Files removed: <span className="text-[var(--fx-code-text)]">{restoreResult.files_removed}</span></div>
            <div>Files failed: <span className="text-[var(--fx-code-text)]">{restoreResult.files_failed}</span></div>
            <div>Apply: <span className="text-[var(--fx-warning)]">{restoreResult.apply_enabled ? "enabled" : "disabled"}</span></div>
          </div>
        ) : null}
        {applyResult ? (
          <div className="mt-2 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] p-2 text-[11px] text-[var(--fx-text-muted)]">
            <div className={applyResult.status === "applied" ? "font-semibold uppercase text-[var(--fx-success)]" : "font-semibold uppercase text-[var(--fx-warning)]"}>
              Patch {applyResult.status}
            </div>
            <div>Files created: <span className="text-[var(--fx-code-text)]">{applyResult.files_created}</span></div>
            <div>Files modified: <span className="text-[var(--fx-code-text)]">{applyResult.files_modified}</span></div>
            <div>Files deleted: <span className="text-[var(--fx-code-text)]">{applyResult.files_deleted}</span></div>
            <div>Rollback snapshot: <span className="break-all text-[var(--fx-code-text)]">{applyResult.rollback_id ?? "unknown"}</span></div>
            <div className="text-[var(--fx-success)]">Rollback restore is available if needed.</div>
          </div>
        ) : null}
        {patchMessage ? <div className="mt-1 text-[var(--fx-code-text)]">{patchMessage}</div> : null}
        {!patchExport ? <div className="mt-1 text-[11px] text-[var(--fx-text-muted)]">Export a patch before running preflight.</div> : null}
        {patchExport && !preflightWorkspaceRoot ? <div className="mt-1 text-[11px] text-[var(--fx-warning)]">Open the target workspace before running preflight.</div> : null}
      </div>
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <button
          className="flex h-8 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
          onClick={() => void exportPatch()}
          disabled={busy || Boolean(patchBusy)}
        >
          {patchBusy === "export" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
          Export Patch
        </button>
        <button
          className="flex h-8 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
          onClick={() => void copyPatch()}
          disabled={busy || Boolean(patchBusy)}
        >
          {patchBusy === "copy" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Clipboard className="h-3.5 w-3.5" />}
          Copy Patch
        </button>
        <button
          className="flex h-8 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
          onClick={() => void verifyPatch()}
          disabled={busy || Boolean(patchBusy)}
        >
          {patchBusy === "verify" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
          Verify Patch
        </button>
        <button
          className="flex h-8 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
          onClick={() => void openPatchFolder()}
          disabled={busy || Boolean(patchBusy)}
        >
          {patchBusy === "open" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ExternalLink className="h-3.5 w-3.5" />}
          Open Patch Folder
        </button>
        <button
          className="flex h-8 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
          onClick={() => void preflightPatch()}
          disabled={busy || Boolean(patchBusy) || !patchExport || !preflightWorkspaceRoot}
          title={!patchExport ? "Export a patch before running preflight" : !preflightWorkspaceRoot ? "Open the target workspace before running preflight" : "Run read-only patch preflight"}
        >
          {patchBusy === "preflight" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
          Preflight
        </button>
        <button
          className="flex h-8 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
          onClick={() => void createRollbackSnapshot()}
          disabled={busy || Boolean(patchBusy) || !preflightResult?.can_apply || !preflightWorkspaceRoot}
          title="Create read-only rollback backup in ForgeX state"
        >
          {patchBusy === "rollback" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
          Create Rollback Snapshot
        </button>
        <button
          className="flex h-8 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
          onClick={() => void preflightRestore()}
          disabled={busy || Boolean(patchBusy) || !rollbackSnapshot || !preflightWorkspaceRoot}
          title="Run read-only restore preflight"
        >
          {patchBusy === "restore-preflight" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
          Restore Preflight
        </button>
        {safetyStatus?.restore_enabled && restorePreflightResult?.can_restore ? (
          <button
            className="flex h-8 items-center gap-1 rounded border border-[var(--fx-error)] bg-[var(--fx-error-soft)] px-2 text-xs font-medium text-[var(--fx-error)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
            onClick={() => void restoreSnapshot()}
            disabled={busy || Boolean(patchBusy) || !rollbackSnapshot || !preflightWorkspaceRoot}
          >
            {patchBusy === "restore" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
            Restore Snapshot
          </button>
        ) : (
          <button
            className="flex h-8 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-xs text-[var(--fx-warning)] opacity-70"
            disabled
          >
            Restore disabled
          </button>
        )}
        {applyReady ? (
          <button
            className="flex h-8 items-center gap-1 rounded border border-[var(--fx-error)] bg-[var(--fx-error-soft)] px-2 text-xs font-medium text-[var(--fx-error)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
            onClick={() => void applyPatch()}
            disabled={busy || Boolean(patchBusy)}
            title="Apply patch to active workspace"
          >
            {patchBusy === "apply" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ShieldCheck className="h-3.5 w-3.5" />}
            Apply Patch
          </button>
        ) : (
          <button
            className="flex h-8 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-xs text-[var(--fx-warning)] opacity-70"
            disabled
            title={safetyStatus?.patch_apply_enabled ? "Approve review, verify patch, and run passing preflight first" : "Patch apply is disabled"}
          >
            Apply disabled
          </button>
        )}
        <button
          className="flex h-8 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50"
          onClick={() => onReject?.(review.review_id)}
          disabled={busy || review.status !== "pending"}
        >
          <XCircle className="h-3.5 w-3.5" />
          Reject
        </button>
        <button
          className="flex h-8 items-center gap-1 rounded bg-[var(--fx-accent)] px-2 text-xs font-medium text-white disabled:opacity-50"
          onClick={() => onApprove?.(review.review_id)}
          disabled={busy || review.status !== "pending"}
        >
          <CheckCircle2 className="h-3.5 w-3.5" />
          Approve
        </button>
      </div>
    </section>
  );
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function integrityLabel(value: BridgePatchExportResponse["integrity_status"]) {
  if (value === "valid") return "Valid";
  if (value === "modified") return "Modified";
  if (value === "missing") return "Missing";
  return "Unknown";
}

function integrityClass(value: BridgePatchExportResponse["integrity_status"]) {
  if (value === "valid") return "text-[var(--fx-success)]";
  if (value === "modified" || value === "missing") return "text-[var(--fx-warning)]";
  return "text-[var(--fx-text-muted)]";
}

function DiffPreview({ file }: { file: BridgeChangedFileResponse | null }) {
  if (!file) {
    return <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-3 text-xs text-[var(--fx-text-muted)]">No file selected.</div>;
  }
  if (!file.preview_supported) {
    return <div className="rounded border border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] p-3 text-xs text-[var(--fx-warning)]">{file.warning ?? "Diff preview is unavailable."}</div>;
  }
  return (
    <pre className="max-h-80 min-h-40 overflow-auto rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-3 text-[11px] leading-5 text-[var(--fx-code-text)]">
      {file.diff_preview || "No textual diff available."}
    </pre>
  );
}
