"use client";

import { Activity, Bot, CheckCircle2, ChevronDown, FolderInput, Loader2, Play, RefreshCw, Send, Square, Trash2, User, Wifi, WifiOff } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useProductAgentRun } from "@/hooks/use-product-agent-run";
import { promptForgeApi } from "@/lib/api";
import type { AGYAssistedRunResult, AGYScratchImportResult, ProductAgentEvent, ProductAgentRun } from "@/types";

const selectableProviderKinds = new Set(["fake", "api_planner", "sandbox_agent", "api_provider", "agent_provider", "template_provider"]);
const terminalStatuses = new Set(["completed", "failed", "cancelled", "blocked", "timed_out", "interrupted"]);
const RUN_TIMEOUT_SECONDS = 300;
const activePhases: ProductAgentRun["status"][] = ["queued", "validating", "preparing_sandbox", "running", "collecting_artifacts"];
const statusProgress: Record<ProductAgentRun["status"], number> = {
  queued: 5,
  validating: 15,
  preparing_sandbox: 30,
  running: 55,
  collecting_artifacts: 85,
  cancelling: 90,
  completed: 100,
  failed: 100,
  cancelled: 100,
  blocked: 100,
  timed_out: 100,
  interrupted: 100,
};
const statusLabels: Record<ProductAgentRun["status"], string> = {
  queued: "Queued",
  validating: "Checking request",
  preparing_sandbox: "Preparing workspace",
  running: "Editing in sandbox",
  collecting_artifacts: "Preparing review",
  cancelling: "Stopping",
  completed: "Ready to review",
  failed: "Run failed",
  cancelled: "Stopped",
  blocked: "Needs attention",
  timed_out: "Timed out",
  interrupted: "Interrupted",
};

interface ConversationMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  runId?: string;
  providerId?: string;
  status?: ProductAgentRun["status"];
  classification?: string | null;
  reviewId?: string | null;
  activities?: string[];
}

function conversationKey(projectId: string) {
  return `forgex-agent-conversation:${projectId}`;
}

function readConversation(projectId: string): ConversationMessage[] {
  try {
    const value = JSON.parse(window.localStorage.getItem(conversationKey(projectId)) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((item): item is ConversationMessage =>
      item && typeof item.id === "string" && (item.role === "user" || item.role === "assistant") && typeof item.content === "string",
    ).slice(-100);
  } catch {
    return [];
  }
}

function activityLabels(events: ProductAgentEvent[]) {
  return events.slice(-12).map((event) => {
    const label = event.event_type.replaceAll(".", " ");
    return event.tool ? `${label} / ${event.tool}` : label;
  });
}

function fallbackAgentMessage(run: ProductAgentRun) {
  if (!terminalStatuses.has(run.status)) return `${statusLabels[run.status]}...`;
  if (run.status === "completed") return "The sandbox task is complete. Review the proposed changes before applying them.";
  return `The sandbox task ended with ${run.status}${run.classification ? `: ${run.classification}` : "."}`;
}

function formatDuration(totalSeconds: number) {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function providerStateLabel(state?: string) {
  const labels: Record<string, string> = {
    ready: "Ready",
    missing_api_key: "Missing API key",
    not_logged_in: "Not logged in",
    cli_not_found: "CLI not found",
    rate_limited: "Rate limited",
    usage_limit_reached: "Usage limit reached",
    unavailable: "Unavailable",
    disabled: "Disabled",
    not_configured: "Not configured",
  };
  return labels[state ?? ""] ?? state?.replaceAll("_", " ") ?? "Unavailable";
}

export function ProductAgentPanel({
  projectId,
  projectName,
  onOpenReview,
  onWorkspaceChanged,
  embedded = false,
}: {
  projectId: string | null;
  projectName?: string | null;
  onOpenReview: (reviewId: string) => void;
  onWorkspaceChanged?: () => void;
  embedded?: boolean;
}) {
  const agent = useProductAgentRun(projectId);
  const [instruction, setInstruction] = useState("");
  const [providerId, setProviderId] = useState("codex");
  const [conversation, setConversation] = useState<{ projectId: string | null; messages: ConversationMessage[] }>({ projectId: null, messages: [] });
  const [scratchPath, setScratchPath] = useState("");
  const [scratchImport, setScratchImport] = useState<AGYScratchImportResult | null>(null);
  const [assistedRun, setAssistedRun] = useState<AGYAssistedRunResult | null>(null);
  const [compatibilityBusy, setCompatibilityBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const transcript = useRef<HTMLDivElement | null>(null);
  const transcriptEnd = useRef<HTMLDivElement | null>(null);
  const followLatest = useRef(true);
  const notifiedRun = useRef<string | null>(null);
  const providers = useMemo(() => agent.providers.filter((item) => selectableProviderKinds.has(item.kind)), [agent.providers]);
  const provider = providers.find((item) => item.provider_id === providerId) ?? providers[0];
  const active = Boolean(agent.run?.cancellable);
  const canRun = Boolean(projectId && agent.enabled && provider?.routeable && instruction.trim() && !active && !agent.submitting);
  const messages = conversation.projectId === projectId ? conversation.messages : [];
  const elapsedSeconds = agent.run ? Math.max(0, (now - Date.parse(agent.run.created_at)) / 1000) : 0;
  const latestEvent = agent.events.at(-1);
  const runProgress = agent.run ? latestEvent?.progress ?? statusProgress[agent.run.status] : 0;
  const remainingSeconds = Math.max(0, RUN_TIMEOUT_SECONDS - elapsedSeconds);
  const phaseIndex = agent.run ? activePhases.indexOf(agent.run.status) : -1;

  const scrollToLatest = useCallback((behavior: ScrollBehavior = "smooth") => {
    followLatest.current = true;
    setShowJumpToLatest(false);
    transcriptEnd.current?.scrollIntoView({ behavior, block: "end" });
  }, []);

  const handleTranscriptScroll = useCallback(() => {
    const element = transcript.current;
    if (!element) return;
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 56;
    followLatest.current = atBottom;
    setShowJumpToLatest(!atBottom);
  }, []);

  useEffect(() => {
    if (active || provider?.routeable) return;
    const readyProvider = providers.find((item) => item.routeable);
    if (readyProvider) setProviderId(readyProvider.provider_id);
  }, [active, provider?.routeable, providers]);

  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [active]);

  useEffect(() => {
    followLatest.current = true;
    setShowJumpToLatest(false);
    setConversation({ projectId, messages: projectId ? readConversation(projectId) : [] });
  }, [projectId]);

  useEffect(() => {
    if (!projectId || conversation.projectId !== projectId) return;
    window.localStorage.setItem(conversationKey(projectId), JSON.stringify(conversation.messages.slice(-100)));
    if (followLatest.current) {
      window.requestAnimationFrame(() => scrollToLatest("smooth"));
    }
  }, [conversation.messages, conversation.projectId, projectId, scrollToLatest]);

  useEffect(() => {
    const run = agent.run;
    if (!run || conversation.projectId !== projectId) return;
    const activities = activityLabels(agent.events);
    setConversation((current) => {
      let changed = false;
      const nextMessages = current.messages.map((message) => {
        if (message.runId !== run.run_id) return message;
        const content = run.assistant_message || fallbackAgentMessage(run);
        const sameActivities = message.activities?.length === activities.length
          && message.activities.every((item, index) => item === activities[index]);
        if (
          message.content === content
          && message.status === run.status
          && message.classification === run.classification
          && message.reviewId === run.review_id
          && sameActivities
        ) return message;
        changed = true;
        return {
          ...message,
          content,
          status: run.status,
          classification: run.classification,
          reviewId: run.review_id,
          activities,
        };
      });
      return changed ? { ...current, messages: nextMessages } : current;
    });
  }, [agent.events, agent.run, conversation.projectId, projectId]);

  useEffect(() => {
    const run = agent.run;
    if (!run || run.status !== "completed" || notifiedRun.current === run.run_id) return;
    notifiedRun.current = run.run_id;
    onWorkspaceChanged?.();
  }, [agent.run, onWorkspaceChanged]);

  async function sendMessage() {
    const content = instruction.trim();
    if (!canRun || !provider || !projectId) return;
    followLatest.current = true;
    setShowJumpToLatest(false);
    const createdAt = new Date().toISOString();
    const userMessage: ConversationMessage = { id: crypto.randomUUID(), role: "user", content, createdAt, providerId: provider.provider_id };
    const assistantId = crypto.randomUUID();
    const optimisticAssistant: ConversationMessage = {
      id: assistantId,
      role: "assistant",
      content: "Starting the agent and preparing a safe workspace...",
      createdAt,
      providerId: provider.provider_id,
      status: "queued",
    };
    setConversation((current) => ({ projectId, messages: [...(current.projectId === projectId ? current.messages : []), userMessage, optimisticAssistant] }));
    setInstruction("");
    const run = await agent.submit(content, provider.provider_id);
    setConversation((current) => ({
      projectId,
      messages: (current.projectId === projectId ? current.messages : []).map((message) => message.id === assistantId ? {
        ...message,
        content: run ? fallbackAgentMessage(run) : "I could not start this sandbox run. Check the provider status and try again.",
        runId: run?.run_id,
        status: run?.status ?? "failed",
      } : message),
    }));
  }

  function clearConversation() {
    if (!projectId || active) return;
    window.localStorage.removeItem(conversationKey(projectId));
    setConversation({ projectId, messages: [] });
  }

  async function runLegacyAGY() {
    setCompatibilityBusy(true);
    try { setAssistedRun(await promptForgeApi.runAGYAssisted()); }
    finally { setCompatibilityBusy(false); }
  }

  async function importLegacyAGY() {
    if (!scratchPath.trim()) return;
    setCompatibilityBusy(true);
    try { setScratchImport(await promptForgeApi.importAGYScratchProject(scratchPath.trim())); }
    finally { setCompatibilityBusy(false); }
  }

  return (
    <section className="flex h-full min-h-0 flex-col overflow-hidden bg-[var(--fx-panel)]" aria-label="ForgeX Agent Runtime">
      <header className="shrink-0 border-b border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-3 py-2.5">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-sm font-semibold"><Bot className="h-4 w-4 text-[var(--fx-accent)]" /> {embedded ? "Agent" : "Forge Agent"}</div>
            <div className="truncate text-[10px] text-[var(--fx-text-muted)]">{projectName || "No workspace selected"}</div>
          </div>
          <div className="flex items-center gap-1">
            <button className="rounded p-1.5 text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)]" onClick={() => void agent.refreshProviders()} title="Refresh provider status"><RefreshCw className="h-3.5 w-3.5" /></button>
            <button className="rounded p-1.5 text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] disabled:opacity-30" onClick={clearConversation} disabled={!messages.length || active} title="Clear conversation"><Trash2 className="h-3.5 w-3.5" /></button>
          </div>
        </div>
        <div className="mt-2 flex items-center gap-2">
          <select className="h-8 min-w-0 flex-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-[11px]" value={provider?.provider_id ?? providerId} onChange={(event) => setProviderId(event.target.value)} disabled={active}>
            {providers.map((item) => <option key={item.provider_id} value={item.provider_id} disabled={!item.routeable}>{item.display_name ?? item.provider_id}{item.routeable ? "" : " (unavailable)"}</option>)}
          </select>
          <span className={`h-2 w-2 rounded-full ${provider?.routeable ? "bg-[var(--fx-success)]" : "bg-[var(--fx-warning)]"}`} />
          <span className="text-[10px] text-[var(--fx-text-muted)]">{provider?.routeable ? "Ready" : providerStateLabel(provider?.state)}</span>
        </div>
        {provider?.paused_reason && !provider.routeable ? <div className="mt-2 text-[10px] text-[var(--fx-warning)]">{provider.paused_reason}</div> : null}
      </header>

      <div className="relative min-h-0 flex-1">
      <div ref={transcript} className="h-full overflow-y-auto px-3 py-4" onScroll={handleTranscriptScroll}>
        {agent.restoring ? (
          <div className="mb-3 flex items-center gap-2 rounded-xl border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3 text-[11px] text-[var(--fx-text-muted)]">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--fx-accent)]" />
            Restoring the latest run...
          </div>
        ) : null}
        {agent.submitting ? (
          <div className="fx-agent-launch mb-4 overflow-hidden rounded-2xl border border-[var(--fx-accent)]/60 bg-[var(--fx-panel-elevated)] shadow-[0_14px_40px_var(--fx-accent-faint)]" role="status" aria-live="polite">
            <div className="relative h-0.5 overflow-hidden bg-[var(--fx-accent-faint)]"><div className="fx-shimmer absolute h-full w-1/3 bg-gradient-to-r from-transparent via-[var(--fx-accent)] to-transparent" /></div>
            <div className="flex items-center gap-3 p-3">
              <div className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[var(--fx-accent-faint)] text-[var(--fx-accent)]">
                <span className="fx-agent-orbit absolute inset-1 rounded-full border border-transparent border-r-[var(--fx-accent)] border-t-[var(--fx-accent)]" />
                <Bot className="relative h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-xs font-semibold text-[var(--fx-text)]">Starting ForgeX agent</div>
                <div className="mt-1 text-[10px] text-[var(--fx-text-muted)]">Creating the run and connecting live updates</div>
                <div className="mt-2 flex gap-1" aria-hidden="true">{[0, 1, 2, 3, 4].map((bar) => <span key={bar} className="fx-agent-signal h-1 flex-1 rounded-full bg-[var(--fx-accent)]" style={{ animationDelay: `${bar * 90}ms` }} />)}</div>
              </div>
            </div>
          </div>
        ) : null}
        {agent.run ? (
          <div className={`mb-4 overflow-hidden rounded-2xl border bg-[var(--fx-panel-elevated)] shadow-sm ${
            active ? "border-[var(--fx-accent)]/60 shadow-[0_14px_40px_var(--fx-accent-faint)]" : agent.run.status === "completed" ? "border-[var(--fx-success)]/50" : "border-[var(--fx-border)]"
          }`} aria-live="polite">
            {active ? <div className="relative h-0.5 w-full overflow-hidden bg-[var(--fx-accent-faint)]"><div className="fx-shimmer absolute h-full w-1/3 bg-gradient-to-r from-transparent via-[var(--fx-accent)] to-transparent" /></div> : null}
            <div className="p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-2.5">
                  <div className={`relative mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${active ? "bg-[var(--fx-accent-faint)] text-[var(--fx-accent)]" : agent.run.status === "completed" ? "bg-[var(--fx-success-soft)] text-[var(--fx-success)]" : "bg-[var(--fx-input)] text-[var(--fx-text-muted)]"}`}>
                    {active ? <><span className="fx-agent-orbit absolute inset-1 rounded-full border border-transparent border-t-[var(--fx-accent)] border-r-[var(--fx-accent)]" /><span className="fx-agent-breathe absolute inset-2 rounded-full bg-[var(--fx-accent-soft)]" /><Bot className="relative h-4 w-4" /></> : agent.run.status === "completed" ? <CheckCircle2 className="h-4 w-4" /> : <Activity className="h-4 w-4" />}
                  </div>
                  <div className="min-w-0">
                    <div className="text-xs font-semibold text-[var(--fx-text)]">{statusLabels[agent.run.status]}</div>
                    <div className="mt-0.5 break-words text-[10px] leading-4 text-[var(--fx-text-muted)]">
                      {latestEvent?.safe_message || (active ? "Forge is actively monitoring this sandbox run." : fallbackAgentMessage(agent.run))}
                      {active ? <span className="ml-1 inline-flex gap-0.5 align-middle" aria-hidden="true">{[0,1,2].map((dot) => <span key={dot} className="fx-dot inline-block h-1 w-1 rounded-full bg-[var(--fx-accent)]" style={{ animationDelay: `${dot * 140}ms` }} />)}</span> : null}
                    </div>
                  </div>
                </div>
                {active ? (
                  <button
                    type="button"
                    className="flex shrink-0 items-center gap-1 rounded-md border border-[var(--fx-warning)]/50 px-2 py-1 text-[10px] text-[var(--fx-warning)] hover:bg-[var(--fx-warning-soft)] disabled:opacity-50"
                    onClick={() => void agent.cancel()}
                    disabled={agent.cancelling}
                  >
                    {agent.cancelling ? <Loader2 className="h-3 w-3 animate-spin" /> : <Square className="h-3 w-3" />}
                    Stop
                  </button>
                ) : null}
              </div>

              {active && phaseIndex >= 0 ? (
                <div className="mt-3 grid grid-cols-5 gap-1" aria-label={`Agent phase: ${statusLabels[agent.run.status]}`}>
                  {activePhases.map((phase, index) => (
                    <div key={phase} className="min-w-0">
                      <div className={`h-1 rounded-full ${index < phaseIndex ? "bg-[var(--fx-success)]" : index === phaseIndex ? "bg-[var(--fx-accent)]" : "bg-[var(--fx-border)]"}`} />
                      <div className={`mt-1 truncate text-[8px] ${index === phaseIndex ? "text-[var(--fx-text)]" : "text-[var(--fx-text-muted)]"}`}>{statusLabels[phase]}</div>
                    </div>
                  ))}
                </div>
              ) : null}

              <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[var(--fx-input)]">
                <div className={`h-full rounded-full transition-all duration-500 ${agent.run.status === "completed" ? "bg-[var(--fx-success)]" : "bg-[var(--fx-accent)]"}`} style={{ width: `${runProgress}%` }} />
              </div>
              <div className="mt-2 grid grid-cols-3 gap-2 text-[9px] text-[var(--fx-text-muted)]">
                <div><div className="text-[var(--fx-code-text)]">{formatDuration(elapsedSeconds)}</div>elapsed</div>
                <div><div className="text-[var(--fx-code-text)]">{runProgress}%</div>phase</div>
                <div><div className="text-[var(--fx-code-text)]">{active ? formatDuration(remainingSeconds) : "—"}</div>{active ? "until timeout" : "finished"}</div>
              </div>
              {agent.run.summary ? (
                <div className="mt-2 min-w-0 rounded border border-[var(--fx-border-soft)] bg-[var(--fx-input)] px-2 py-1.5 text-[9px] text-[var(--fx-text-muted)]">
                  <div className="truncate">Generation source: <span className="text-[var(--fx-code-text)]">{agent.run.summary.generation_source ?? agent.run.summary.actual_provider ?? agent.run.provider_id}</span></div>
                  <div className="mt-0.5 flex min-w-0 flex-wrap gap-x-3 gap-y-0.5">
                    <span>Generation: {agent.run.summary.generation_status}</span>
                    <span>Build: {agent.run.summary.build_status}</span>
                    <span>Flash: {agent.run.summary.flash_status}</span>
                    <span>Monitor: {agent.run.summary.monitor_status}</span>
                  </div>
                  {agent.run.summary.fallback_used ? <div className="mt-0.5 break-words text-[var(--fx-warning)]">Fallback: {agent.run.summary.fallback_reason}</div> : null}
                  {agent.run.provider_diagnostics ? (
                    <div className="mt-0.5 break-words text-[var(--fx-text-muted)]">
                      Provider request: {agent.run.provider_diagnostics.request_reached_provider ? "received" : agent.run.provider_diagnostics.outbound_request_count ? "attempted, not confirmed" : "not sent"}
                      {agent.run.provider_diagnostics.http_status ? ` · HTTP ${agent.run.provider_diagnostics.http_status}` : ""}
                      {agent.run.provider_diagnostics.provider_request_id ? ` · ${agent.run.provider_diagnostics.provider_request_id}` : ""}
                    </div>
                  ) : null}
                </div>
              ) : null}
              <div className="mt-2 flex items-center justify-between gap-2 border-t border-[var(--fx-border-soft)] pt-2 text-[9px] text-[var(--fx-text-muted)]">
                <span className="flex items-center gap-1">
                  {agent.connectionState === "live" ? <Wifi className="h-3 w-3 text-[var(--fx-success)]" /> : agent.connectionState === "polling" ? <RefreshCw className="h-3 w-3 animate-spin text-[var(--fx-info)]" /> : <WifiOff className="h-3 w-3" />}
                  {agent.connectionState === "live" ? "Live updates" : agent.connectionState === "polling" ? "Syncing response" : active ? "Connecting" : "Run synced"}
                </span>
                <span className="font-mono">{agent.run.run_id.slice(-8)}</span>
              </div>
            </div>
          </div>
        ) : null}
        {messages.length === 0 ? (
          <div className="flex h-full min-h-52 flex-col items-center justify-center px-5 text-center">
            <div className="mb-3 rounded-2xl border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3"><Bot className="h-6 w-6 text-[var(--fx-accent)]" /></div>
            <div className="text-sm font-medium">What should we build?</div>
            <div className="mt-1 max-w-64 text-[11px] leading-5 text-[var(--fx-text-muted)]">Describe a change. The agent works in a sandbox and keeps the full conversation here.</div>
          </div>
        ) : (
          <div className="space-y-4">
            {messages.map((message) => (
              <article key={message.id} className={`flex gap-2 ${message.role === "user" ? "justify-end" : "justify-start"}`}>
                {message.role === "assistant" ? <div className="mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--fx-accent-soft)]"><Bot className="h-3.5 w-3.5 text-[var(--fx-accent)]" /></div> : null}
                <div className={`max-w-[86%] rounded-2xl px-3 py-2.5 text-xs leading-5 ${message.role === "user" ? "rounded-br-sm bg-[var(--fx-accent)] text-white" : "rounded-bl-sm border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] text-[var(--fx-code-text)]"}`}>
                  <div className="whitespace-pre-wrap break-words">{message.content}</div>
                  {message.role === "assistant" && message.status ? (
                    <div className="mt-2 border-t border-[var(--fx-border-soft)] pt-2">
                      <div className="flex items-center gap-1.5 text-[10px] text-[var(--fx-text-muted)]">
                        {!terminalStatuses.has(message.status) ? <Loader2 className="h-3 w-3 animate-spin" /> : <CheckCircle2 className="h-3 w-3" />}
                        {message.providerId} / {statusLabels[message.status]}
                      </div>
                      {message.activities?.length ? <details className="mt-1 text-[10px] text-[var(--fx-text-muted)]"><summary className="cursor-pointer">Activity ({message.activities.length})</summary><div className="mt-1 space-y-0.5">{message.activities.map((item, index) => <div key={`${message.id}-${index}`}>{item}</div>)}</div></details> : null}
                      {message.reviewId ? <button className="mt-2 flex items-center gap-1 text-[11px] font-medium text-[var(--fx-success)]" onClick={() => onOpenReview(message.reviewId!)}><CheckCircle2 className="h-3.5 w-3.5" /> Open review</button> : null}
                    </div>
                  ) : null}
                </div>
                {message.role === "user" ? <div className="mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--fx-border)]"><User className="h-3.5 w-3.5" /></div> : null}
              </article>
            ))}
            {agent.run?.pending_approvals?.map((approval) => (
              <div key={approval.approval_id} className="ml-8 rounded-xl border border-[var(--fx-warning)] bg-[var(--fx-panel-elevated)] p-3 text-xs">
                <div className="font-medium">{approval.kind} approval required</div>
                <div className="my-2 text-[11px] text-[var(--fx-text-muted)]">{approval.safe_message}</div>
                <div className="flex gap-2"><button className="rounded bg-[var(--fx-accent)] px-2 py-1 text-white" onClick={() => void agent.resolveApproval(approval.approval_id, "approve_once")}>Approve once</button><button className="rounded border border-[var(--fx-border)] px-2 py-1" onClick={() => void agent.resolveApproval(approval.approval_id, "decline")}>Decline</button></div>
              </div>
            ))}
          </div>
        )}
        <div ref={transcriptEnd} />
      </div>
      {showJumpToLatest ? (
        <button
          type="button"
          className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] px-3 py-1.5 text-[10px] font-medium text-[var(--fx-text)] shadow-lg hover:border-[var(--fx-accent)] hover:bg-[var(--fx-hover)]"
          onClick={() => scrollToLatest()}
        >
          <ChevronDown className="h-3.5 w-3.5 text-[var(--fx-accent)]" />
          Jump to latest
        </button>
      ) : null}
      </div>

      <div className="shrink-0 border-t border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
        {agent.error ? <div className="mb-2 text-[10px] text-[var(--fx-error)]">{agent.error}</div> : null}
        <div className="rounded-xl border border-[var(--fx-border)] bg-[var(--fx-input)] p-2 focus-within:border-[var(--fx-accent)]">
          <textarea className="max-h-36 min-h-16 w-full resize-none bg-transparent px-1 text-xs leading-5 outline-none placeholder:text-[var(--fx-text-muted)]" value={instruction} onChange={(event) => setInstruction(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void sendMessage(); } }} maxLength={16_384} placeholder="Ask the agent to change this project..." />
          <div className="flex items-center justify-between pt-1">
            <span className="text-[9px] text-[var(--fx-text-muted)]">Enter to send / Shift+Enter for newline</span>
            {active ? <button className="flex h-7 items-center gap-1 rounded px-2 text-[10px] text-[var(--fx-warning)] hover:bg-[var(--fx-hover)]" onClick={() => void agent.cancel()}><Square className="h-3 w-3" /> Stop</button> : <button className="flex h-7 w-7 items-center justify-center rounded-lg bg-[var(--fx-accent)] text-white disabled:opacity-30" disabled={!canRun} onClick={() => void sendMessage()} aria-label="Send message">{agent.submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}</button>}
          </div>
        </div>

        <details className="mt-2 rounded border border-[var(--fx-border-soft)] px-2 py-1.5 text-[10px] text-[var(--fx-text-muted)]">
          <summary className="cursor-pointer">Legacy AGY compatibility tools</summary>
          <div className="mt-2 space-y-2">
            <div className="font-medium text-[var(--fx-text)]">Generate with AGY</div>
            <button className="flex h-7 items-center gap-2 rounded border border-[var(--fx-border)] px-2" disabled={compatibilityBusy} onClick={() => void runLegacyAGY()}><Play className="h-3 w-3" /> Run AGY and Create Review</button>
            {assistedRun?.classification === "AGY_ASSISTED_EXPECTED_FOLDER_MISSING" ? <div className="text-[var(--fx-warning)]">AGY generated output, but not at the expected folder. Paste/select the generated AGY scratch folder below.</div> : null}
            <div className="font-medium text-[var(--fx-text)]">AGY Scratch Import</div>
            <div className="flex gap-2"><input className="h-7 min-w-0 flex-1 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2" value={scratchPath} onChange={(event) => setScratchPath(event.target.value)} placeholder="Exact AGY output path" /><button className="flex h-7 items-center gap-1 rounded border border-[var(--fx-border)] px-2" disabled={compatibilityBusy || !scratchPath.trim()} onClick={() => void importLegacyAGY()}><FolderInput className="h-3 w-3" /> Import</button></div>
            {(scratchImport?.review_id || assistedRun?.review_id) ? <button className="text-[var(--fx-success)]" onClick={() => onOpenReview((scratchImport?.review_id || assistedRun?.review_id)!)}>Open created review</button> : null}
          </div>
        </details>
      </div>
    </section>
  );
}
