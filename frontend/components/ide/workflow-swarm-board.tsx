"use client";

import { BrainCircuit, Check, Code2, Hammer, Loader2, Radio, Upload, X } from "lucide-react";

import { formatDuration } from "@/lib/utils";
import type { StageKey, WorkflowStage } from "@/types";

const stageIcons: Record<StageKey, typeof BrainCircuit> = {
  planning: BrainCircuit,
  generation: Code2,
  build: Hammer,
  flash: Upload,
  monitor: Radio,
};

function statusLabel(stage: WorkflowStage) {
  if (stage.status === "waiting_for_device") return "device";
  if (stage.status === "success") return stage.durationMs === undefined ? "done" : formatDuration(stage.durationMs);
  return stage.status.replaceAll("_", " ");
}

function StatusGlyph({ status }: { status: WorkflowStage["status"] }) {
  if (status === "active") return <Loader2 className="h-3 w-3 animate-spin" />;
  if (status === "success") return <Check className="h-3 w-3" />;
  if (status === "failed" || status === "cancelled") return <X className="h-3 w-3" />;
  return <span className="h-1.5 w-1.5 rounded-full bg-current" />;
}

export function WorkflowSwarmBoard({ stages, running }: { stages: WorkflowStage[]; running: boolean }) {
  const activeStage = stages.find((stage) => stage.status === "active");
  const completed = stages.filter((stage) => stage.status === "success").length;

  return (
    <div className="min-w-0 overflow-hidden rounded-lg border border-[var(--fx-border)] bg-[var(--fx-input)]" aria-label="Workflow execution timeline">
      <div className="flex items-center justify-between border-b border-[var(--fx-border)] px-2.5 py-2">
        <div className="flex items-center gap-1.5 text-[9px] font-semibold uppercase tracking-[0.14em] text-[var(--fx-text-muted)]">
          <span className={`h-1.5 w-1.5 rounded-full ${running ? "fx-swarm-live bg-[var(--fx-success)]" : "bg-[var(--fx-text-muted)]"}`} />
          {running ? activeStage ? `Running ${activeStage.label}` : "Live workflow" : "Workflow ready"}
        </div>
        <span className="font-mono text-[9px] text-[var(--fx-text-muted)]">{completed}/{stages.length}</span>
      </div>
      <div className="grid grid-cols-5">
        {stages.map((stage, index) => {
          const Icon = stageIcons[stage.key];
          return (
            <div
              key={stage.key}
              className={`relative min-w-0 px-1 py-2 text-center ${index ? "border-l border-[var(--fx-border)]" : ""} ${stage.status === "active" ? "bg-[var(--fx-accent-faint)]" : ""}`}
              title={`${stage.label}: ${statusLabel(stage)} — ${stage.description}`}
            >
              {stage.status === "active" ? <div className="fx-shimmer absolute inset-x-0 top-0 h-0.5 bg-[var(--fx-info)]" /> : null}
              <span className={`mx-auto flex h-6 w-6 items-center justify-center rounded-full border ${
                stage.status === "success" ? "border-[var(--fx-success)] text-[var(--fx-success)]" :
                stage.status === "failed" ? "border-[var(--fx-error)] text-[var(--fx-error)]" :
                stage.status === "active" ? "border-[var(--fx-info)] text-[var(--fx-info)]" :
                "border-[var(--fx-border)] text-[var(--fx-text-muted)]"
              }`}>
                {stage.status === "pending" ? <Icon className="h-3 w-3" /> : <StatusGlyph status={stage.status} />}
              </span>
              <div className="mt-1 truncate text-[9px] font-medium text-[var(--fx-code-text)]">{stage.label}</div>
              <div className="truncate font-mono text-[8px] text-[var(--fx-text-muted)]">{statusLabel(stage)}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
