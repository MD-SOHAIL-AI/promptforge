import { CheckCircle2, RefreshCw, XCircle } from "lucide-react";

import type { GenerationDiagnosticsRun, ModelUsageRecord } from "@/types";
import type { BusyKey, DiagnosticsFilter } from "./types";
import { shortModel } from "./utils";

interface ProviderDiagnosticsPanelProps {
  usage: ModelUsageRecord[];
  generationRuns: GenerationDiagnosticsRun[];
  diagnosticsFilter: DiagnosticsFilter;
  busy: BusyKey;
  refreshUsage: () => Promise<void>;
  refreshDiagnostics: () => Promise<void>;
  clearDiagnostics: () => Promise<void>;
  setDiagnosticsFilter: (filter: DiagnosticsFilter) => void;
}

export function ProviderDiagnosticsPanel({ usage, generationRuns, diagnosticsFilter, busy, refreshUsage, refreshDiagnostics, clearDiagnostics, setDiagnosticsFilter }: ProviderDiagnosticsPanelProps) {
  const filteredGenerationRuns = generationRuns.filter((run) => {
    if (diagnosticsFilter === "all") return true;
    if (diagnosticsFilter === "success") return run.status === "success";
    if (diagnosticsFilter === "failed") return run.status === "failed";
    if (diagnosticsFilter === "incomplete") return run.status === "incomplete";
    if (diagnosticsFilter === "repair") return (run.repair_count ?? 0) > 0;
    if (diagnosticsFilter === "fallback") return (run.fallback_count ?? 0) > 0;
    return true;
  });

  return (
    <>
      <section className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="text-xs font-semibold uppercase text-[var(--fx-text-muted)]">Usage</div>
          <button className="text-[var(--fx-text-muted)] hover:text-[var(--fx-text)]" onClick={() => void refreshUsage()} title="Refresh usage"><RefreshCw className="h-3.5 w-3.5" /></button>
        </div>
        {usage.length === 0 ? <div className="text-xs text-[var(--fx-text-muted)]">No model calls recorded yet.</div> : (
          <div className="space-y-1 text-[11px]">
            {usage.slice().reverse().map((record, index) => (
              <div key={`${record.created_at}-${index}`} className="grid min-w-0 grid-cols-1 gap-2 rounded bg-[var(--fx-input)] px-2 py-1 sm:grid-cols-[minmax(0,1fr)_auto]">
                <div className="min-w-0">
                  <div className="truncate text-[var(--fx-code-text)]">{record.task_type} - {record.provider_id} / {shortModel(record.model_id)}</div>
                  <div className="truncate text-[var(--fx-text-muted)]">{record.created_at}</div>
                </div>
                <div className="text-left sm:text-right">
                  <div className={record.success ? "text-[var(--fx-success)]" : "text-[var(--fx-error)]"}>{record.success ? <CheckCircle2 className="inline h-3.5 w-3.5" /> : <XCircle className="inline h-3.5 w-3.5" />} {record.latency_ms}ms</div>
                  <div className="text-[var(--fx-text-muted)]">{record.total_tokens ?? "-"} tokens</div>
                  {record.error_code ? <div className="break-words text-[var(--fx-error)]">{record.error_code}</div> : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="text-xs font-semibold uppercase text-[var(--fx-text-muted)]">Generation Diagnostics</div>
          <div className="flex items-center gap-2">
            <button className="text-[var(--fx-text-muted)] hover:text-[var(--fx-text)]" onClick={() => void refreshDiagnostics()} title="Refresh generation diagnostics"><RefreshCw className="h-3.5 w-3.5" /></button>
            <button className="text-[var(--fx-text-muted)] hover:text-[var(--fx-error)] disabled:opacity-50" onClick={() => void clearDiagnostics()} disabled={Boolean(busy)} title="Clear generation diagnostics"><XCircle className="h-3.5 w-3.5" /></button>
          </div>
        </div>
        <div className="mb-2 flex flex-wrap gap-1">
          {(["all", "success", "failed", "incomplete", "repair", "fallback"] as DiagnosticsFilter[]).map((filter) => (
            <button key={filter} className={`rounded border px-2 py-1 text-[11px] ${diagnosticsFilter === filter ? "border-[var(--fx-accent)] bg-[var(--fx-accent)] text-white" : "border-[var(--fx-border)] bg-[var(--fx-input)] text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)]"}`} onClick={() => setDiagnosticsFilter(filter)}>{filter}</button>
          ))}
        </div>
        {filteredGenerationRuns.length === 0 ? <div className="text-xs text-[var(--fx-text-muted)]">No chunked generation runs recorded yet.</div> : (
          <div className="space-y-1 text-[11px]">
            {filteredGenerationRuns.slice().reverse().map((run, index) => {
              const required = Array.isArray(run.required_files) ? run.required_files.length : 0;
              const written = Array.isArray(run.file_statuses) ? run.file_statuses.filter((file) => file.status === "written").length : Array.isArray(run.generated_files) ? run.generated_files.length : 0;
              const statusClass = run.status === "success" ? "text-[var(--fx-success)]" : run.status === "incomplete" ? "text-[var(--fx-warning)]" : "text-[var(--fx-error)]";
              return (
                <div key={`${run.run_id ?? run.execution_id}-${index}`} className="grid min-w-0 grid-cols-1 gap-2 rounded bg-[var(--fx-input)] px-2 py-1 lg:grid-cols-[minmax(0,1fr)_auto]">
                  <div className="min-w-0">
                    <div className="truncate text-[var(--fx-code-text)]">{run.strategy ?? run.generation_mode ?? "generation"} - {run.provider_id ?? "provider"} / {shortModel(run.model_id ?? "model")}</div>
                    <div className="truncate text-[var(--fx-text-muted)]">{run.completed_at ?? run.started_at ?? "time unknown"}</div>
                    {run.workspace_root ? <div className="truncate text-[var(--fx-text-muted)]">{run.workspace_root}</div> : null}
                  </div>
                  <div className="text-left lg:text-right">
                    <div className={statusClass}>{run.status}</div>
                    <div className="text-[var(--fx-text-muted)]">Files {written}/{required || written}</div>
                    <div className="text-[var(--fx-text-muted)]">Repairs {run.repair_count ?? 0} Fallbacks {run.fallback_count ?? 0} Warnings {run.warning_count ?? 0}</div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>
    </>
  );
}
