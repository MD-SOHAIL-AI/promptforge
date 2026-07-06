"use client";

import { AlertTriangle, Bot, CheckCircle2, Clock3, Loader2, Play, RefreshCw, Square, WifiOff } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { useGenericAgentRun } from "@/hooks/use-generic-agent-run";
import { isGenericRunTerminal, normalizedRunMessage } from "@/lib/generic-agent-state";
import type { GenericRunStatus } from "@/types";

interface GenericAgentPanelProps {
  projectId: string | null;
  projectName?: string | null;
  onOpenReview: (reviewId: string) => void;
}

const STATUS_LABELS: Record<GenericRunStatus, string> = {
  queued: "Queued",
  validating: "Validating",
  preparing_sandbox: "Preparing sandbox",
  running: "Running",
  collecting_artifacts: "Collecting artifacts",
  cancelling: "Cancelling",
  cancelled: "Cancelled",
  completed: "Completed",
  blocked: "Blocked",
  failed: "Failed",
  timed_out: "Timed out",
  interrupted: "Interrupted",
};

function statusClass(status: GenericRunStatus) {
  if (status === "completed") return "border-[var(--fx-success)] bg-[var(--fx-success-soft)] text-[var(--fx-success)]";
  if (["failed", "blocked", "timed_out", "interrupted"].includes(status)) return "border-[var(--fx-error)] bg-[var(--fx-error-soft)] text-[var(--fx-error)]";
  if (status === "cancelled" || status === "cancelling") return "border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] text-[var(--fx-warning)]";
  return "border-[var(--fx-info)] bg-[var(--fx-accent-soft)] text-[var(--fx-info)]";
}

export function GenericAgentPanel({ projectId, projectName, onOpenReview }: GenericAgentPanelProps) {
  const [qaFixtureState, setQaFixtureState] = useState<string | null>(null);
  useEffect(() => {
    setQaFixtureState(new URLSearchParams(window.location.search).get("forgexQaAgentState"));
  }, []);
  const agent = useGenericAgentRun(projectId, qaFixtureState);
  const [instruction, setInstruction] = useState("");
  const hasWorkspace = Boolean(projectId || agent.qaFixtureActive);
  const active = Boolean(agent.run && !isGenericRunTerminal(agent.run.status));
  const validInstruction = instruction.trim().length > 0 && instruction.length <= 16_384;
  const canRun = Boolean(projectId && !agent.qaFixtureActive && agent.provider?.execution_enabled && validInstruction && !active && !agent.submitting);
  const providerReason = useMemo(() => {
    if (!agent.provider) return agent.providersLoading ? null : "AGY provider diagnostics are unavailable.";
    if (!agent.provider.execution_enabled) return agent.provider.disabled_reason || "Generic execution flags are disabled.";
    if (!agent.provider.installed) return "AGY is not installed.";
    if (agent.provider.authentication_status === "unauthenticated") return "AGY is not authenticated in the official local tool.";
    return null;
  }, [agent.provider, agent.providersLoading]);
  const failure = normalizedRunMessage(agent.run);

  const run = async () => {
    if (!canRun) return;
    const value = instruction;
    setInstruction("");
    const result = await agent.submit(value);
    if (!result) setInstruction(value);
  };

  return (
    <section className="flex h-full min-h-0 flex-col overflow-hidden" aria-label="ForgeX Agent">
      <div className="shrink-0 space-y-3 border-b border-[var(--fx-border)] p-3">
        <div className="flex min-w-0 items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-[var(--fx-text)]">Agent</div>
            <div className="truncate text-[11px] text-[var(--fx-text-muted)]">{projectName || (agent.qaFixtureActive ? "QA workspace" : "No workspace selected")}</div>
          </div>
          <button
            type="button"
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
            title="Refresh provider status"
            onClick={() => void agent.reloadProviders()}
            disabled={agent.providersLoading}
          >
            <RefreshCw className={`h-3.5 w-3.5 ${agent.providersLoading ? "animate-spin" : ""}`} />
          </button>
        </div>

        <label className="block text-[11px] font-medium text-[var(--fx-text-muted)]">
          Provider
          <select
            className="mt-1 h-9 w-full rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-xs text-[var(--fx-text)] outline-none focus:border-[var(--fx-accent)]"
            value="agy"
            disabled
            aria-label="Agent provider"
          >
            <option value="agy">{agent.providersLoading ? "Loading AGY…" : "Google Antigravity / AGY"}</option>
          </select>
        </label>

        {providerReason ? (
          <div className="flex items-start gap-2 rounded border border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] p-2 text-[11px] text-[var(--fx-warning)]">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span className="min-w-0 break-words">{providerReason}</span>
          </div>
        ) : null}

        <label className="block text-[11px] font-medium text-[var(--fx-text-muted)]">
          Instruction
          <textarea
            className="mt-1 min-h-24 w-full resize-y rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-2 text-xs leading-5 text-[var(--fx-text)] outline-none placeholder:text-[var(--fx-text-muted)] focus:border-[var(--fx-accent)] disabled:opacity-60"
            value={instruction}
            onChange={(event) => setInstruction(event.target.value)}
            placeholder="Describe the sandbox-only change…"
            maxLength={16_384}
            disabled={active || agent.submitting || !hasWorkspace}
            aria-label="Agent instruction"
          />
        </label>

        <div className="flex items-center gap-2">
          <button
            type="button"
            className="flex h-9 flex-1 items-center justify-center gap-1.5 rounded bg-[var(--fx-accent)] px-3 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40"
            disabled={!canRun}
            onClick={() => void run()}
          >
            {agent.submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            {agent.submitting ? "Submitting" : "Run"}
          </button>
          {agent.run?.cancellable ? (
            <button
              type="button"
              className="flex h-9 items-center justify-center gap-1.5 rounded border border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] px-3 text-xs text-[var(--fx-warning)] disabled:opacity-50"
              onClick={() => void agent.cancel()}
              disabled={agent.cancelling || agent.run.status === "cancelling"}
            >
              {agent.cancelling ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Square className="h-3.5 w-3.5" />}
              Cancel
            </button>
          ) : null}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {!agent.run ? (
          <div className="flex h-full min-h-40 flex-col items-center justify-center gap-2 text-center text-xs text-[var(--fx-text-muted)]">
            {agent.error ? <WifiOff className="h-5 w-5 text-[var(--fx-error)]" /> : <Bot className="h-5 w-5 text-[var(--fx-accent)]" />}
            <span>{agent.error || (providerReason ? "Agent execution is currently disabled." : hasWorkspace ? "Ready for a sandboxed AGY run." : "Open a workspace to use Agent.")}</span>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] p-3">
              <div className="flex min-w-0 items-center justify-between gap-2">
                <span className={`rounded border px-2 py-0.5 text-[11px] font-medium ${statusClass(agent.run.status)}`}>
                  {STATUS_LABELS[agent.run.status]}
                </span>
                <span className="max-w-36 truncate font-mono text-[10px] text-[var(--fx-text-muted)]" title={agent.run.run_id}>
                  {agent.run.run_id}
                </span>
              </div>
              <div className="mt-3 h-1.5 overflow-hidden rounded bg-[var(--fx-panel-elevated)]">
                <div className="h-full rounded bg-[var(--fx-accent)] transition-all" style={{ width: `${agent.run.progress}%` }} />
              </div>
              <div className="mt-1 flex justify-between text-[10px] text-[var(--fx-text-muted)]">
                <span>{agent.run.progress}%</span>
                <span>{agent.run.artifact_count} artifacts</span>
              </div>
            </div>

            {agent.connectionState !== "idle" ? (
              <div className="flex items-center gap-1.5 text-[11px] text-[var(--fx-text-muted)]">
                {agent.connectionState === "polling" ? <RefreshCw className="h-3 w-3" /> : <Clock3 className="h-3 w-3" />}
                {agent.connectionState === "reconnecting"
                  ? "Event stream reconnecting"
                  : agent.connectionState === "resync_required"
                    ? "Event resync required"
                    : agent.connectionState === "polling"
                      ? "Using bounded polling fallback"
                      : "Receiving sanitized events"}
              </div>
            ) : null}

            {failure || agent.error ? (
              <div className="rounded border border-[var(--fx-error)] bg-[var(--fx-error-soft)] p-2 text-[11px] text-[var(--fx-error)]">
                {failure || agent.error}
              </div>
            ) : null}

            <div>
              <div className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-[var(--fx-text-muted)]">Timeline</div>
              <div className="space-y-1.5">
                {agent.events.map((event) => (
                  <div key={event.event_id} className="flex min-w-0 gap-2 rounded border border-[var(--fx-border-soft)] bg-[var(--fx-panel-elevated)] p-2 text-[11px]">
                    {event.status === "completed" ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--fx-success)]" /> : <Clock3 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--fx-info)]" />}
                    <div className="min-w-0">
                      <div className="truncate text-[var(--fx-code-text)]">{event.status ? STATUS_LABELS[event.status] : event.event_type.replaceAll("_", " ")}</div>
                      {event.safe_message ? <div className="break-words text-[var(--fx-text-muted)]">{event.safe_message}</div> : null}
                    </div>
                  </div>
                ))}
                {agent.events.length === 0 ? <div className="text-[11px] text-[var(--fx-text-muted)]">Status is available; timeline events are reconnecting.</div> : null}
              </div>
            </div>

            {agent.run.status === "completed" && agent.run.review_id ? (
              <button
                type="button"
                className="flex h-9 w-full items-center justify-center gap-1.5 rounded border border-[var(--fx-success)] bg-[var(--fx-success-soft)] text-xs font-medium text-[var(--fx-success)]"
                onClick={() => onOpenReview(agent.run!.review_id!)}
              >
                <CheckCircle2 className="h-3.5 w-3.5" />
                Open review
              </button>
            ) : null}
          </div>
        )}
      </div>
    </section>
  );
}
