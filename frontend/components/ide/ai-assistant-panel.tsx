"use client";

import { Bot, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, FolderCog, Loader2, Send, SlidersHorizontal, Sparkles, Square, TerminalSquare } from "lucide-react";
import { useState } from "react";

import { GenerationProgressPanel } from "@/components/ide/generation-progress-panel";
import { WorkflowSwarmBoard } from "@/components/ide/workflow-swarm-board";
import type { ConsoleEntry, GenerationProgress, WorkflowStage } from "@/types";

interface AiAssistantPanelProps {
  stages: WorkflowStage[];
  isExecuting: boolean;
  isCancelling: boolean;
  socketState: "idle" | "connecting" | "reconnecting" | "open" | "closed";
  generationProgress: GenerationProgress | null;
  lastPrompt: string;
  trace: ConsoleEntry[];
  workspaceNeedsInitialization?: boolean;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onExecute: (prompt: string) => void;
  onCancel: () => void;
  embedded?: boolean;
}

export function AiAssistantPanel({
  stages,
  isExecuting,
  isCancelling,
  socketState,
  generationProgress,
  lastPrompt,
  trace,
  workspaceNeedsInitialization = false,
  collapsed,
  onToggleCollapsed,
  onExecute,
  onCancel,
  embedded = false,
}: AiAssistantPanelProps) {
  const [prompt, setPrompt] = useState(
    "Create an ESP32 blink LED project using PlatformIO.",
  );
  const [goalExpanded, setGoalExpanded] = useState(false);
  const [traceExpanded, setTraceExpanded] = useState(false);
  const canSubmit = prompt.trim().length > 0 && !isExecuting;
  const goalText = lastPrompt || prompt;
  const goalIsLong = goalText.length > 420 || goalText.split(/\r?\n/).length > 8;

  const submit = () => {
    if (!canSubmit) return;
    onExecute(prompt.trim());
  };

  if (collapsed) {
    return (
      <aside className="flex min-h-0 flex-col items-center border-l border-[var(--fx-border)] bg-[var(--fx-panel)] py-2">
        <button
          className="flex h-9 w-9 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
          title="Expand Forge"
          onClick={onToggleCollapsed}
        >
          <ChevronLeft className="h-4 w-4" />
        </button>
        <Bot className="mt-2 h-5 w-5 text-[var(--fx-accent)]" />
      </aside>
    );
  }

  const content = (
    <>
      {!embedded ? (
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-[var(--fx-border)] px-3">
          <div className="flex items-center gap-2 text-sm font-medium text-[var(--fx-text)]">
            <Bot className="h-4 w-4 text-[var(--fx-accent)]" />
            Forge
          </div>
          <div className="flex items-center gap-1">
            <span className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 py-0.5 text-[11px] text-[var(--fx-text-muted)]">
              {socketState === "open" ? "Live" : socketState === "reconnecting" ? "Reconnecting" : isExecuting ? "Starting" : "Ready"}
            </span>
            <button
              className="rounded p-1 text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
              onClick={onToggleCollapsed}
              title="Collapse Forge"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      ) : null}

      <div className="min-h-0 min-w-0 flex-1 space-y-3 overflow-y-auto overflow-x-hidden p-3">
        {workspaceNeedsInitialization ? (
          <div className="flex items-start gap-2 rounded border border-[var(--fx-warning)]/50 bg-[var(--fx-warning)]/10 p-2.5 text-xs text-[var(--fx-code-text)]">
            <FolderCog className="mt-0.5 h-4 w-4 shrink-0 text-[var(--fx-warning)]" />
            <div><div className="font-medium">Empty workspace</div><div className="mt-0.5 text-[var(--fx-text-muted)]">ForgeX will create and validate a PlatformIO project here before building.</div></div>
          </div>
        ) : null}
        <div className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase text-[var(--fx-text-muted)]">
              <Sparkles className="h-3.5 w-3.5 text-[var(--fx-info)]" />
              User Goal
            </div>
            {goalIsLong ? (
              <button
                className="flex shrink-0 items-center gap-1 rounded px-2 py-1 text-xs text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
                onClick={() => setGoalExpanded((value) => !value)}
              >
                {goalExpanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                {goalExpanded ? "Show less" : "Show more"}
              </button>
            ) : null}
          </div>
          <div
            className={`break-words rounded bg-[var(--fx-input)]/45 p-2 text-sm leading-5 text-[var(--fx-code-text)] ${
              goalExpanded ? "max-h-64 overflow-auto" : "max-h-36 overflow-auto"
            }`}
          >
            <p className="whitespace-pre-wrap">{goalText}</p>
          </div>
        </div>

        <div className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
          <div className="mb-3 flex items-center justify-between gap-2">
            <div className="text-xs font-semibold uppercase text-[var(--fx-text-muted)]">Assistant Plan</div>
            <button
              className="flex shrink-0 items-center gap-1 rounded border border-[var(--fx-border)] px-2 py-1 text-xs text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
              title="Plan customization will be expanded in a later UI pass"
            >
              <SlidersHorizontal className="h-3.5 w-3.5" />
              Customize
            </button>
          </div>
          <WorkflowSwarmBoard stages={stages} running={isExecuting} />
          <div className="mt-3 grid grid-cols-2 gap-2">
            <button
              className="flex items-center justify-center gap-2 rounded bg-[var(--fx-accent)] px-3 py-2 text-sm font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
              onClick={submit}
              disabled={!canSubmit}
            >
              {isExecuting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              Start
            </button>
            <button
              className="flex items-center justify-center gap-2 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-3 py-2 text-sm font-medium text-[var(--fx-text)] hover:bg-[var(--fx-hover)] disabled:cursor-not-allowed disabled:opacity-50"
              onClick={onCancel}
              disabled={!isExecuting || isCancelling}
            >
              {isCancelling ? <Loader2 className="h-4 w-4 animate-spin" /> : <Square className="h-4 w-4" />}
              {isCancelling ? "Cancelling..." : "Cancel"}
            </button>
          </div>
        </div>

        <GenerationProgressPanel progress={generationProgress} />

        <div className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase text-[var(--fx-text-muted)]">
              <TerminalSquare className="h-3.5 w-3.5 text-[var(--fx-info)]" /> Live activity
            </div>
            <button className="flex items-center gap-1 rounded px-2 py-1 text-xs text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)]" onClick={() => setTraceExpanded((value) => !value)}>
              {traceExpanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
              {traceExpanded ? "Less" : "Details"}
            </button>
          </div>
          <div className={`mt-2 space-y-1 overflow-y-auto rounded bg-[var(--fx-input)]/55 p-2 font-mono text-[10px] leading-4 ${traceExpanded ? "max-h-64" : "max-h-24"}`} aria-live="polite">
            {trace.length === 0 ? <div className="text-[var(--fx-text-muted)]">Waiting for workflow events…</div> : trace.slice(traceExpanded ? -60 : -5).map((item) => (
              <div key={item.id} className={item.channel === "error" ? "text-[var(--fx-error)]" : item.channel === "build" ? "text-[var(--fx-warning)]" : "text-[var(--fx-code-text)]"}>
                <span className="mr-1 text-[var(--fx-text-muted)]">{new Date(item.timestamp).toLocaleTimeString()}</span>{item.message}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="relative z-20 shrink-0 border-t border-[var(--fx-border)] bg-[var(--fx-panel)] p-3 shadow-[0_-10px_24px_rgba(0,0,0,.12)]">
        <textarea
          className="h-20 max-h-32 w-full resize-y rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2 text-sm text-[var(--fx-text)] outline-none placeholder:text-[var(--fx-text-muted)] focus:border-[var(--fx-info)]"
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="Describe the firmware task"
        />
        <button
          className="mt-2 flex w-full items-center justify-center gap-2 rounded bg-[var(--fx-panel-elevated)] px-3 py-2 text-sm text-[var(--fx-text)] hover:bg-[var(--fx-hover)] disabled:cursor-not-allowed disabled:opacity-50"
          onClick={submit}
          disabled={!canSubmit}
        >
          <Send className="h-4 w-4" />
          Send to Forge
        </button>
      </div>
    </>
  );

  if (embedded) {
    return <div className="flex min-h-0 min-w-0 flex-1 flex-col bg-[var(--fx-panel)]">{content}</div>;
  }

  return (
    <aside className="flex min-h-0 flex-col border-l border-[var(--fx-border)] bg-[var(--fx-panel)]">
      {content}
    </aside>
  );
}
