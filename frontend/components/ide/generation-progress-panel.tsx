"use client";

import { CheckCircle2, ChevronDown, ChevronUp, Circle, CircleAlert, Clock3, Loader2 } from "lucide-react";
import { useState } from "react";

import type { GenerationFileProgress, GenerationProgress } from "@/types";

interface GenerationProgressPanelProps {
  progress: GenerationProgress | null;
}

function fileIcon(status: string) {
  if (status === "written") return <CheckCircle2 className="h-3.5 w-3.5 text-[var(--fx-success)]" />;
  if (status === "failed") return <CircleAlert className="h-3.5 w-3.5 text-[var(--fx-error)]" />;
  if (status === "validated") return <CheckCircle2 className="h-3.5 w-3.5 text-[var(--fx-info)]" />;
  if (status === "generating" || status === "validating" || status === "repairing" || status === "fallback") return <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--fx-info)]" />;
  if (status === "pending") return <Clock3 className="h-3.5 w-3.5 text-[var(--fx-text-muted)]" />;
  return <Circle className="h-3.5 w-3.5 text-[var(--fx-text-muted)]" />;
}

function statusLabel(file: GenerationFileProgress) {
  const parts = [file.status];
  if (file.repair_used) parts.push("repair");
  if (file.fallback_used) parts.push("fallback");
  return parts.join(" / ");
}

export function GenerationProgressPanel({ progress }: GenerationProgressPanelProps) {
  const [expanded, setExpanded] = useState(false);
  if (!progress) return null;

  const failedFile = progress.failed_files[0] ?? null;
  const sourceLabel = progress.provider_id === "forgex_builtin"
    ? "ForgeX verified template"
    : progress.provider_id === "codex"
      ? "Codex Agent"
      : progress.provider_id ?? "Automatic";
  const modeLabel = progress.mode === "chunked" ? "Chunked" : progress.provider_id === "forgex_builtin" ? "Verified template" : "One shot";
  const title = progress.status === "success"
    ? "Generation completed"
    : progress.status === "incomplete"
      ? "Generation incomplete"
      : progress.status === "failed"
        ? "Generation failed"
      : "Generation Progress";
  const visibleFiles = expanded ? progress.file_statuses : progress.file_statuses.slice(0, 4);

  return (
    <div className={`min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3 ${progress.status === "running" ? "shadow-[0_0_0_1px_var(--fx-info)]/20" : ""}`}>
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase text-[var(--fx-text-muted)]">
          {progress.status === "running" ? <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--fx-info)]" /> : null}
          {title}
        </div>
        <button
          className="flex shrink-0 items-center gap-1 rounded px-2 py-1 text-xs text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          {expanded ? "Less" : "Details"}
        </button>
      </div>

      <div className="space-y-1 text-xs text-[var(--fx-text-muted)]">
        <div>Mode: <span className="text-[var(--fx-code-text)]">{modeLabel}</span></div>
        <div>Source: <span className="text-[var(--fx-code-text)]">{sourceLabel}{progress.model_id ? ` / ${progress.model_id}` : ""}</span></div>
        <div>Files: <span className="text-[var(--fx-code-text)]">{progress.files_written} / {progress.files_total}</span></div>
        {progress.no_op ? <div className="text-[var(--fx-success)]">Workspace already matched the generated output; no project files changed.</div> : null}
        {progress.content_verified && !progress.no_op ? <div className="text-[var(--fx-success)]">Generated content verified in the workspace.</div> : null}
        {progress.current_file ? <div>Current: <span className="break-all text-[var(--fx-code-text)]">{progress.current_file}</span></div> : null}
        <div>Repairs: <span className="text-[var(--fx-code-text)]">{progress.repairs}</span> Fallbacks: <span className="text-[var(--fx-code-text)]">{progress.fallbacks}</span> Warnings: <span className="text-[var(--fx-code-text)]">{progress.warnings.length}</span></div>
        {failedFile ? <div className="break-words text-[var(--fx-error)]">Failed file: {failedFile}</div> : null}
      </div>

      {visibleFiles.length > 0 ? (
        <div className="mt-2 space-y-1">
          {visibleFiles.map((file) => (
            <div key={file.path} className="flex min-w-0 items-center gap-2 rounded bg-[var(--fx-input)]/55 px-2 py-1 text-xs">
              <div className="shrink-0">{fileIcon(file.status)}</div>
              <div className="min-w-0 flex-1 truncate text-[var(--fx-code-text)]" title={file.path}>{file.path}</div>
              <div className="shrink-0 text-[10px] uppercase text-[var(--fx-text-muted)]">{statusLabel(file)}</div>
            </div>
          ))}
        </div>
      ) : null}

      {expanded && progress.warnings.length > 0 ? (
        <div className="mt-2 space-y-1 text-xs text-[var(--fx-warning)]">
          {progress.warnings.map((warning) => <div key={warning} className="break-words">{warning}</div>)}
        </div>
      ) : null}
    </div>
  );
}
