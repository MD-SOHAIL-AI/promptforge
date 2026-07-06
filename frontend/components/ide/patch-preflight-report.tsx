"use client";

import { ChevronDown, ChevronRight, CircleAlert, FilePlus2, FileMinus2, FilePenLine, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";

import {
  conflictReadableMessage,
  conflictTypeLabel,
  patchPreflightStatus,
  preflightToneClass,
} from "@/lib/patch-preflight-status";
import type { PatchPreflightConflict, PatchPreflightResult } from "@/types";

interface PatchPreflightReportProps {
  result: Partial<PatchPreflightResult> | null;
  compact?: boolean;
  defaultExpanded?: boolean;
}

export function PatchPreflightReport({ result, compact = false, defaultExpanded = false }: PatchPreflightReportProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const status = useMemo(() => patchPreflightStatus(result), [result]);
  const conflicts = safeList(result?.conflicts);
  const warnings = safeList(result?.warnings);
  const create = safeStringList(result?.files_to_create);
  const modify = safeStringList(result?.files_to_modify);
  const remove = safeStringList(result?.files_to_delete);

  if (!result) return null;

  return (
    <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] text-xs">
      <button
        className="flex w-full min-w-0 items-center justify-between gap-2 px-2 py-2 text-left"
        onClick={() => setExpanded((value) => !value)}
        type="button"
      >
        <div className="flex min-w-0 items-center gap-2">
          {expanded ? <ChevronDown className="h-3.5 w-3.5 shrink-0 text-[var(--fx-text-muted)]" /> : <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--fx-text-muted)]" />}
          <span className={`shrink-0 rounded border px-1.5 py-0.5 ${preflightToneClass(status.tone)}`}>{status.label}</span>
          <span className="min-w-0 truncate text-[var(--fx-code-text)]">{status.summary}</span>
        </div>
        <span className="shrink-0 text-[11px] text-[var(--fx-warning)]">Apply disabled</span>
      </button>
      {expanded ? (
        <div className="space-y-2 border-t border-[var(--fx-border)] p-2">
          <div className="grid gap-1 sm:grid-cols-4">
            <StatusTile label="Patch integrity" value={label(result.integrity_status)} />
            <StatusTile label="Review" value={label(result.review_status)} />
            <StatusTile label="Workspace" value={label(result.workspace_status)} />
            <StatusTile label="Checked" value={typeof result.checked_at === "string" ? result.checked_at : "Unknown"} />
          </div>
          <div className="grid gap-2 sm:grid-cols-3">
            <FileList icon="create" label="To create" files={create} compact={compact} />
            <FileList icon="modify" label="To modify" files={modify} compact={compact} />
            <FileList icon="delete" label="To delete" files={remove} compact={compact} />
          </div>
          <IssueList title="Conflicts" issues={conflicts} empty="No blocking conflicts." />
          <IssueList title="Warnings" issues={warnings} empty="No warnings." />
          <div className="flex items-start gap-2 rounded border border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] p-2 text-[11px] text-[var(--fx-warning)]">
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>Preflight is read-only. Apply is disabled in this build.</span>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function StatusTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
      <div className="truncate text-[10px] uppercase text-[var(--fx-text-muted)]">{label}</div>
      <div className="mt-1 truncate text-[var(--fx-code-text)]">{value}</div>
    </div>
  );
}

function FileList({ icon, label, files, compact }: { icon: "create" | "modify" | "delete"; label: string; files: string[]; compact: boolean }) {
  const Icon = icon === "create" ? FilePlus2 : icon === "modify" ? FilePenLine : FileMinus2;
  const visible = compact ? files.slice(0, 4) : files;
  return (
    <div className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
      <div className="mb-1 flex items-center gap-1 text-[11px] font-semibold uppercase text-[var(--fx-text-muted)]">
        <Icon className="h-3.5 w-3.5" />
        {label} ({files.length})
      </div>
      {files.length === 0 ? (
        <div className="text-[11px] text-[var(--fx-text-muted)]">None</div>
      ) : (
        <div className="space-y-1">
          {visible.map((file) => (
            <div key={file} className="truncate text-[11px] text-[var(--fx-code-text)]" title={file}>{file}</div>
          ))}
          {visible.length < files.length ? <div className="text-[11px] text-[var(--fx-text-muted)]">+{files.length - visible.length} more</div> : null}
        </div>
      )}
    </div>
  );
}

function IssueList({ title, issues, empty }: { title: string; issues: PatchPreflightConflict[]; empty: string }) {
  return (
    <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2">
      <div className="mb-1 text-[11px] font-semibold uppercase text-[var(--fx-text-muted)]">{title} ({issues.length})</div>
      {issues.length === 0 ? (
        <div className="text-[11px] text-[var(--fx-text-muted)]">{empty}</div>
      ) : (
        <div className="space-y-1">
          {issues.map((issue, index) => (
            <div key={`${issue.path}-${issue.type}-${index}`} className="grid gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] p-1.5 sm:grid-cols-[minmax(0,1fr)_auto]">
              <div className="min-w-0">
                <div className="truncate text-[var(--fx-code-text)]">{issue.path || "Patch"}</div>
                <div className="break-words text-[11px] text-[var(--fx-text-muted)]">{conflictReadableMessage(issue)}</div>
              </div>
              <div className="flex items-start gap-1 text-[11px]">
                <span className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 py-0.5 text-[var(--fx-text-muted)]">{conflictTypeLabel(issue.type)}</span>
                <span className={issue.severity === "warning" ? "text-[var(--fx-warning)]" : "text-[var(--fx-error)]"}>
                  <CircleAlert className="inline h-3.5 w-3.5" /> {issue.severity || "error"}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function safeList(value: PatchPreflightResult["conflicts"] | PatchPreflightResult["warnings"] | undefined): PatchPreflightConflict[] {
  return Array.isArray(value) ? value : [];
}

function safeStringList(value: string[] | undefined): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function label(value: unknown) {
  return typeof value === "string" && value.trim() ? value.replaceAll("_", " ") : "Unknown";
}
