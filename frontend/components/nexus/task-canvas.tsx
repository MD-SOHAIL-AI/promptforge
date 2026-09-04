"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, Bot, BrainCircuit, Check, ChevronDown, Code2, Copy, FileDiff, Hammer, History, LoaderCircle, Maximize2, Minimize2, Pencil, Rocket, RotateCcw, Settings2, ShieldAlert, Sparkles, User, Wrench } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AgentActivityTimeline, AgentPlan } from "@/components/nexus/agent-activity";
import { ForgeCore, type ForgeCoreState } from "@/components/nexus/forge-core";
import { MessageMarkdown } from "@/components/nexus/message-markdown";
import { ActionTile, NexusHud, NexusSurface, ProgressRail, SectionEyebrow } from "@/components/nexus/nexus-primitives";
import { UniversalComposer, type ForgeComposerContext, type ForgeMode } from "@/components/nexus/universal-composer";
import { IconButton } from "@/components/ui/button";
import { ErrorNote } from "@/components/ui/error-note";
import { Skeleton } from "@/components/ui/skeleton";
import type { useForgeAgentSession } from "@/hooks/use-forge-agent-session";
import { planProgress, trustFacts, type TrustFactTone } from "@/lib/nexus-agent-view";
import type { NexusCanvas } from "@/components/nexus/work-canvases";
import type { DetectedBoard, ForgeAgentMessage, ModelProviderResponse, ModelRouteResponse, ProductAgentEvent, ProductAgentRun, ProviderModelsResponse } from "@/types";

type Agent = ReturnType<typeof useForgeAgentSession>;

type ReasoningDetail = "minimal" | "normal" | "detailed";

type TranscriptRow =
  | { kind: "separator"; id: string; label: string }
  | { kind: "message"; id: string; message: ForgeAgentMessage }
  | { kind: "activity"; id: string; anchor: string }
  | { kind: "pending"; id: string }
  | { kind: "tail"; id: string };

function statusToCore(agent: Agent): ForgeCoreState {
  if (!agent.enabled) return "offline";
  if (agent.error || agent.run?.status === "failed" || agent.run?.status === "blocked" || agent.run?.status === "timed_out") return "error";
  if (agent.run?.status === "awaiting_flash_confirmation") return "waiting";
  if (agent.run?.status === "completed") return "complete";
  if (agent.currentActivity?.activity.includes("build")) return "building";
  if (agent.run?.status === "running" || agent.submitting) return agent.currentActivity ? "working" : "reasoning";
  return "idle";
}

function modeAutonomy(mode: ForgeMode): "auto" | "staged_changes" | "plan_only" {
  return mode === "plan" ? "plan_only" : mode === "ask" ? "staged_changes" : "auto";
}

const timeFormatter = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const dayFormatter = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

function parseTs(value: string | undefined): number | null {
  const parsed = value ? Date.parse(value) : NaN;
  return Number.isNaN(parsed) ? null : parsed;
}

function timeLabel(value: string | undefined) {
  const ts = parseTs(value);
  return ts === null ? null : timeFormatter.format(ts);
}

function dayKey(value: string | undefined) {
  const ts = parseTs(value);
  return ts === null ? null : new Date(ts).toDateString();
}

function dayLabel(value: string | undefined) {
  const ts = parseTs(value);
  if (ts === null) return null;
  const date = new Date(ts);
  const base = dayFormatter.format(date);
  return date.getFullYear() === new Date().getFullYear() ? base : `${base}, ${date.getFullYear()}`;
}

function formatDuration(ms: number) {
  const total = Math.floor(ms / 1000);
  const seconds = String(total % 60).padStart(2, "0");
  const minutes = Math.floor((total % 3600) / 60);
  const hours = Math.floor(total / 3600);
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${minutes}:${seconds}`;
}

function activityTone(event: ProductAgentEvent) {
  const type = event.event_type.toLowerCase();
  const status = (event.status ?? "").toLowerCase();
  if (type.includes("failed") || status === "failed" || status === "blocked" || status === "timed_out") return "var(--fx-error)";
  if (type.includes("completed") || status === "completed") return "var(--fx-success)";
  return "var(--fx-accent)";
}

function isActivityEvent(event: ProductAgentEvent) {
  const type = event.event_type.toLowerCase();
  if (event.tool) return true;
  if (event.activity || type.includes("activity") || type.includes("turn.completed")) return false;
  return /tool|build|node\.|subagent|review|flash|stage/.test(type);
}

function chipLabel(event: ProductAgentEvent) {
  if (event.tool) return event.tool.replace(/_/g, " ");
  return event.event_type.split(/[.:_]/).filter(Boolean).slice(-2).join(" ") || event.event_type;
}

function factualRunFacts(events: ProductAgentEvent[], run: ProductAgentRun | null) {
  const chips: string[] = [];
  const toolNames = [...new Set(events.map((event) => event.tool?.replace(/_/g, " ")).filter((tool): tool is string => Boolean(tool)))];
  const executions = run?.tool_execution_count ?? events.filter((event) => event.tool).length;
  if (executions > 0) chips.push(`${executions} tool execution${executions === 1 ? "" : "s"}`);
  const plan = planProgress(run);
  if (plan.total > 0) chips.push(`${plan.completed}/${plan.total} plan steps`);
  const changed = (run?.created_file_count ?? 0) + (run?.modified_file_count ?? 0) + (run?.deleted_file_count ?? 0);
  if (changed > 0) chips.push(`${changed} file${changed === 1 ? "" : "s"} staged`);
  const repairs = run?.repair_attempt_count ?? 0;
  if (repairs > 0) chips.push(`${repairs} repair pass${repairs === 1 ? "" : "es"}`);
  const buildResult = run?.build_result as Record<string, unknown> | null | undefined;
  if (buildResult && typeof buildResult === "object") chips.push(Boolean(buildResult.success) ? "build verified" : "build attempted");
  if (run?.validation_report) chips.push("validation report ready");
  if (!chips.length) return null;
  return { chips, tools: toolNames };
}

function DaySeparator({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 py-1.5">
      <span className="h-px flex-1 bg-[var(--fx-border-soft)]" />
      <span className="rounded-full border border-[var(--fx-border-soft)] bg-[var(--fx-panel)] px-2.5 py-0.5 text-[9px] font-semibold uppercase tracking-[.14em] text-[var(--fx-text-muted)]">{label}</span>
      <span className="h-px flex-1 bg-[var(--fx-border-soft)]" />
    </div>
  );
}

function MessageRow({ message, streaming, onRetry, onEdit }: { message: ForgeAgentMessage; streaming: boolean; onRetry?: () => void; onEdit?: () => void }) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";
  const time = timeLabel(message.created_at);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch { /* clipboard unavailable */ }
  };
  return (
    <div className={`group relative flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser ? <span className="mt-3 grid h-7 w-7 shrink-0 place-items-center rounded-xl bg-[var(--fx-accent-soft)] text-[var(--fx-accent)]"><Bot className="h-3.5 w-3.5" /></span> : null}
      <div className={`relative ${isUser ? "max-w-[76%]" : "min-w-0 max-w-[88%] flex-1"}`}>
        <div className={`pointer-events-none absolute -top-4 z-10 flex items-center gap-0.5 rounded-xl border border-[var(--fx-border-soft)] bg-[var(--fx-panel-elevated)] p-0.5 opacity-0 shadow-lg transition-opacity group-hover:pointer-events-auto group-hover:opacity-100 ${isUser ? "right-0" : "left-0"}`}>
          <IconButton label="Copy message" onClick={() => void copy()}>{copied ? <Check className="h-3.5 w-3.5 text-[var(--fx-success)]" /> : <Copy className="h-3.5 w-3.5" />}</IconButton>
          {onRetry ? <IconButton label={isUser ? "Resend message" : "Regenerate reply"} onClick={onRetry}><RotateCcw className="h-3.5 w-3.5" /></IconButton> : null}
          {isUser && onEdit ? <IconButton label="Edit message" onClick={onEdit}><Pencil className="h-3.5 w-3.5" /></IconButton> : null}
        </div>
        <div className="mb-1 mt-3 flex items-center gap-1.5 text-[9px] font-semibold uppercase tracking-[.12em] text-[var(--fx-text-muted)]">{isUser ? <><User className="h-3 w-3" /> You</> : "Forge"}{time ? <span className="font-mono normal-case tracking-normal opacity-70">{time}</span> : null}</div>
        {isUser ? (
          <div className="rounded-[18px] rounded-br-md bg-[var(--fx-panel-elevated)] px-4 py-3">
            <MessageMarkdown content={message.content} />
          </div>
        ) : (
          <MessageMarkdown content={message.content} streaming={streaming} />
        )}
      </div>
    </div>
  );
}

function ActivityStrip({ events }: { events: ProductAgentEvent[] }) {
  const [open, setOpen] = useState(() => events.some((event) => activityTone(event) === "var(--fx-error)"));
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const latest = events[events.length - 1];
  const latestLabel = (latest.safe_message || latest.message || chipLabel(latest)).replace(/\s+/g, " ").trim();
  const expanded = events.filter((event) => String(event.sequence) === expandedId && (event.safe_message || event.message));
  return (
    <div className="ml-10 max-w-[720px] rounded-[12px] border border-dashed border-[var(--fx-border-soft)] bg-[color-mix(in_srgb,var(--fx-panel)_52%,transparent)] px-2.5 py-1.5">
      <button onClick={() => setOpen((value) => !value)} className="flex w-full items-center gap-2 text-left" aria-expanded={open}>
        <Wrench className="h-3 w-3 shrink-0 text-[var(--fx-text-muted)]" />
        <span className="shrink-0 text-[9px] font-semibold uppercase tracking-[.13em] text-[var(--fx-text-muted)]">Activity - {events.length}</span>
        <span className="min-w-0 flex-1 truncate text-[10px] text-[var(--fx-text-muted)]">{latestLabel}</span>
        <ChevronDown className={`h-3 w-3 shrink-0 text-[var(--fx-text-muted)] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open ? (
        <>
          <div className="mt-1.5 flex flex-wrap gap-1">
            {events.map((event) => {
              const id = String(event.sequence);
              const active = expandedId === id;
              return (
                <button key={id} onClick={() => setExpandedId((current) => current === id ? null : id)} className={`flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[9px] transition-colors ${active ? "border-[var(--fx-border)] bg-[var(--fx-hover)] text-[var(--fx-text)]" : "border-[var(--fx-border-soft)] text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"}`}>
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: activityTone(event) }} />
                  <span className="max-w-[180px] truncate">{chipLabel(event)}</span>
                </button>
              );
            })}
          </div>
          {expanded.map((event) => (
            <div key={event.sequence} className="mt-1.5 whitespace-pre-wrap rounded-lg bg-[var(--fx-bg)] px-2.5 py-2 text-[10px] leading-4 text-[var(--fx-text-muted)]">{event.safe_message || event.message}</div>
          ))}
        </>
      ) : null}
    </div>
  );
}

function PendingTurnRow() {
  return (
    <div className="flex gap-3">
      <span className="mt-1 grid h-7 w-7 shrink-0 place-items-center rounded-xl bg-[var(--fx-accent-soft)] text-[var(--fx-accent)]"><Bot className="h-3.5 w-3.5" /></span>
      <div className="space-y-2 rounded-[18px] rounded-tl-md bg-[var(--fx-panel-elevated)] px-4 py-3">
        <Skeleton className="h-3 w-44" />
        <Skeleton className="h-3 w-28" />
      </div>
    </div>
  );
}

function RestoringSkeleton() {
  return (
    <div className="mx-auto max-w-[880px] space-y-6 pt-2">
      {[0, 1, 2, 3].map((index) => (
        <div key={index} className={`flex items-start gap-3 ${index % 2 ? "justify-end" : "justify-start"}`}>
          {index % 2 === 0 ? <Skeleton className="h-7 w-7 shrink-0 rounded-xl" /> : null}
          <div className="space-y-2">
            <Skeleton className={`h-3 ${index % 2 ? "ml-auto w-40" : "w-48"}`} />
            <Skeleton className={`h-3 ${index % 2 ? "ml-auto w-56" : "w-64"}`} />
            {index === 1 ? <Skeleton className="ml-auto h-3 w-32" /> : null}
          </div>
          {index % 2 === 1 ? <Skeleton className="h-7 w-7 shrink-0 rounded-xl" /> : null}
        </div>
      ))}
    </div>
  );
}

function RunEvidenceCard({ facts, detail, live }: { facts: { chips: string[]; tools: string[] }; detail: ReasoningDetail; live: boolean }) {
  if (detail === "minimal") {
    return <div className="flex items-center gap-2 text-[11px] text-[var(--fx-text-muted)]"><BrainCircuit className="h-3.5 w-3.5 shrink-0 text-[var(--fx-accent)]" /><span className="truncate">{facts.chips.join(" - ")}</span></div>;
  }
  return (
    <NexusSurface className="p-4" glow={live}>
      <div className="flex items-center justify-between gap-3">
        <SectionEyebrow>Run evidence</SectionEyebrow>
        {live ? <span className="flex items-center gap-1.5 text-[9px] uppercase tracking-[.12em] text-[var(--fx-accent)]"><LoaderCircle className="h-3 w-3 animate-spin" /> live</span> : null}
      </div>
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {facts.chips.map((chip) => <span key={chip} className="rounded-full border border-[var(--fx-border-soft)] bg-[var(--fx-panel-elevated)] px-2.5 py-1 font-mono text-[10px] text-[var(--fx-text)]">{chip}</span>)}
      </div>
      {detail === "detailed" && facts.tools.length ? (
        <div className="mt-3 border-t border-[var(--fx-border-soft)] pt-2.5">
          <div className="text-[9px] font-semibold uppercase tracking-[.14em] text-[var(--fx-text-muted)]">Tools used</div>
          <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
            {facts.tools.map((tool) => <span key={tool} className="text-[10px] text-[var(--fx-text-muted)]">{tool}</span>)}
          </div>
        </div>
      ) : null}
    </NexusSurface>
  );
}

function trustToneClass(tone: TrustFactTone) {
  return {
    neutral: "border-[var(--fx-border-soft)] text-[var(--fx-text-muted)]",
    success: "border-[color-mix(in_srgb,var(--fx-success)_42%,var(--fx-border))] text-[var(--fx-success)]",
    warning: "border-[color-mix(in_srgb,var(--fx-warning)_44%,var(--fx-border))] text-[var(--fx-warning)]",
    error: "border-[color-mix(in_srgb,var(--fx-error)_44%,var(--fx-border))] text-[var(--fx-error)]",
    accent: "border-[color-mix(in_srgb,var(--fx-accent)_44%,var(--fx-border))] text-[var(--fx-accent)]",
  }[tone];
}

function TrustSummaryPanel({ agent }: { agent: Agent }) {
  const facts = trustFacts(agent.run, agent.graph);
  if (!facts.length) return null;
  const next = agent.run?.summary?.next_suggested_action || agent.run?.assistant_message;
  return (
    <NexusSurface className="p-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <SectionEyebrow>Trust summary</SectionEyebrow>
          {next ? <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-[var(--fx-text)]">{next}</p> : null}
        </div>
        <div className="flex flex-wrap gap-1.5 md:justify-end">
          {facts.map((fact) => (
            <span key={fact.id} className={`rounded-lg border bg-[var(--fx-panel-elevated)] px-2.5 py-1 text-[10px] ${trustToneClass(fact.tone)}`}>
              <span className="text-[var(--fx-text-muted)]">{fact.label}: </span>{fact.value}
            </span>
          ))}
        </div>
      </div>
    </NexusSurface>
  );
}

export function TaskCanvas({
  agent,
  projectName,
  selectedBoard,
  selectedBoardId,
  activeEnvironment,
  onOpenCanvas,
  onOpenSessions,
  onWorkspaceChanged,
  modelProviders,
  modelRoute,
  loadProviderModels,
  onModelChange,
  focused = false,
  onFocusToggle,
}: {
  agent: Agent;
  projectName: string | null;
  selectedBoard: DetectedBoard | null;
  selectedBoardId: string | null;
  activeEnvironment: string | null;
  onOpenCanvas: (canvas: NexusCanvas) => void;
  onOpenSessions: () => void;
  onWorkspaceChanged: () => void;
  modelProviders: ModelProviderResponse[];
  modelRoute: ModelRouteResponse | null;
  loadProviderModels: (providerId: string) => Promise<ProviderModelsResponse>;
  onModelChange: (providerId: string, modelId: string) => Promise<void> | void;
  focused?: boolean;
  onFocusToggle?: () => void;
}) {
  const [mode, setMode] = useState<ForgeMode>("auto");
  const [reasoningDetail, setReasoningDetail] = useState<ReasoningDetail>("normal");
  const [showReasoningMenu, setShowReasoningMenu] = useState(false);
  const [draft, setDraft] = useState("");
  const [atBottom, setAtBottom] = useState(true);
  const [activityCollapsed, setActivityCollapsed] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const [nowTick, setNowTick] = useState(() => Date.now());
  const scrollRef = useRef<HTMLDivElement>(null);
  const prevStatusRef = useRef<string | null>(null);

  const coreState = statusToCore(agent);
  const running = agent.run?.status === "queued" || agent.run?.status === "running" || agent.run?.status === "cancelling";
  const chatMessages = useMemo(() => agent.messages.filter((message) => message.role === "user" || message.role === "assistant"), [agent.messages]);
  const lastMessage = chatMessages[chatMessages.length - 1];
  const lastAssistant = [...chatMessages].reverse().find((message) => message.role === "assistant");
  const lastUserMessage = [...chatMessages].reverse().find((message) => message.role === "user");
  const taskTitle = agent.activeSession?.title || projectName || "Forge task";
  const changedCount = (agent.run?.created_file_count ?? 0) + (agent.run?.modified_file_count ?? 0) + (agent.run?.deleted_file_count ?? 0);
  const buildReady = Boolean(agent.run?.build_result);
  const needsRecovery = agent.run?.status === "failed" || agent.run?.status === "blocked" || agent.run?.status === "timed_out";
  const facts = useMemo(() => factualRunFacts(agent.events, agent.run), [agent.events, agent.run]);

  const activityIndex = useMemo(() => {
    const byAnchor = new Map<string, ProductAgentEvent[]>();
    const relevant = agent.events.filter(isActivityEvent);
    let anchoredCount = 0;
    if (chatMessages.length) {
      const ids = new Set(chatMessages.map((message) => message.message_id));
      for (const event of relevant) {
        let anchor: string | null = event.message_id && ids.has(event.message_id) ? event.message_id : null;
        if (!anchor) {
          for (let i = chatMessages.length - 1; i >= 0; i--) {
            const candidate = chatMessages[i];
            if (candidate.run_id && candidate.run_id === event.run_id) { anchor = candidate.message_id; break; }
          }
        }
        if (!anchor) {
          for (let i = chatMessages.length - 1; i >= 0; i--) {
            if (chatMessages[i].role === "user") { anchor = chatMessages[i].message_id; break; }
          }
        }
        if (!anchor) continue;
        anchoredCount++;
        const bucket = byAnchor.get(anchor);
        if (bucket) bucket.push(event); else byAnchor.set(anchor, [event]);
      }
    }
    return { byAnchor, relevantCount: relevant.length, anchoredCount };
  }, [agent.events, chatMessages]);
  const unanchoredActivity = activityIndex.relevantCount > activityIndex.anchoredCount;

  const rows = useMemo<TranscriptRow[]>(() => {
    const out: TranscriptRow[] = [];
    let lastDay: string | null = null;
    for (const message of chatMessages) {
      const key = dayKey(message.created_at);
      if (key && key !== lastDay) {
        const label = dayLabel(message.created_at);
        if (label) out.push({ kind: "separator", id: `sep-${message.message_id}`, label });
        lastDay = key;
      }
      out.push({ kind: "message", id: message.message_id, message });
      if (activityIndex.byAnchor.get(message.message_id)?.length) out.push({ kind: "activity", id: `act-${message.message_id}`, anchor: message.message_id });
    }
    if (running && lastMessage?.role === "user") out.push({ kind: "pending", id: "pending-turn" });
    out.push({ kind: "tail", id: "tail" });
    return out;
  }, [activityIndex.byAnchor, chatMessages, lastMessage?.role, running]);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 110,
    overscan: 8,
    useFlushSync: false,
    getItemKey: (index) => rows[index]?.id ?? String(index),
  });

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem("forgex-reasoning-detail") as ReasoningDetail | null;
      if (saved && ["minimal", "normal", "detailed"].includes(saved)) setReasoningDetail(saved);
    } catch { /* local preference only */ }
  }, []);

  const updateAtBottom = useCallback(() => {
    const element = scrollRef.current;
    if (!element) return;
    setAtBottom(element.scrollHeight - element.scrollTop - element.clientHeight <= 80);
  }, []);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "auto") => {
    const element = scrollRef.current;
    if (!element) return;
    element.scrollTo({ top: element.scrollHeight, behavior });
  }, []);

  const lastContentLength = lastMessage?.content.length ?? 0;
  useEffect(() => {
    if (atBottom) scrollToBottom();
  }, [atBottom, scrollToBottom, chatMessages.length, lastContentLength, agent.events.length, agent.currentActivity?.label]);

  const runStartedAt = useMemo(() => parseTs(agent.run?.created_at), [agent.run?.created_at]);
  useEffect(() => {
    if (!running) return;
    setNowTick(Date.now());
    const id = window.setInterval(() => setNowTick(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [running]);
  const elapsedLabel = running && runStartedAt !== null ? formatDuration(Math.max(0, nowTick - runStartedAt)) : null;

  const railValue = useMemo(() => {
    const plan = planProgress(agent.run);
    if (plan.total > 0) return Math.round((plan.completed / plan.total) * 100);
    if (agent.run?.status === "completed") return 100;
    return null;
  }, [agent.run]);

  useEffect(() => {
    const status = agent.run?.status ?? null;
    const previous = prevStatusRef.current;
    if (previous && previous !== status) {
      if (status === "completed") setAnnouncement(lastAssistant ? "Forge finished responding" : "Forge task completed");
      else if (status === "failed" || status === "blocked" || status === "timed_out") setAnnouncement("Forge run stopped with an error");
      else if (status === "awaiting_flash_confirmation") setAnnouncement("Forge is waiting for hardware flash approval");
    }
    prevStatusRef.current = status;
  }, [agent.run?.status, lastAssistant]);

  const setReasoning = (next: ReasoningDetail) => {
    setReasoningDetail(next);
    setShowReasoningMenu(false);
    try { window.localStorage.setItem("forgex-reasoning-detail", next); } catch { /* ignore */ }
  };

  const send = useCallback(async (content: string, context?: ForgeComposerContext) => {
    const result = await agent.sendMessage(content ?? "", undefined, {
      autonomy: modeAutonomy(mode),
      boardPort: selectedBoard?.port ?? null,
      boardType: selectedBoardId,
      environment: activeEnvironment,
      context,
    });
    if (result?.run?.change_set_id) onWorkspaceChanged();
  }, [activeEnvironment, agent, mode, onWorkspaceChanged, selectedBoard, selectedBoardId]);

  const precedingUserContent = (message: ForgeAgentMessage) => {
    const index = chatMessages.findIndex((item) => item.message_id === message.message_id);
    for (let i = index - 1; i >= 0; i--) {
      if (chatMessages[i].role === "user") return chatMessages[i].content;
    }
    return null;
  };

  const retryLastUser = useCallback(() => {
    if (lastUserMessage) void send(lastUserMessage.content);
  }, [lastUserMessage, send]);

  const sessionHint = useMemo(() => {
    if (agent.run?.status === "awaiting_flash_confirmation") return "Firmware verified - waiting for hardware approval";
    if (running) return agent.currentActivity?.label || "Forge is working through the task";
    if (agent.run?.status === "completed") return "Task reached a verified stopping point";
    return selectedBoard ? `${selectedBoard.board_type} - ${selectedBoard.port}` : "Ready for an engineering objective";
  }, [agent.currentActivity?.label, agent.run?.status, running, selectedBoard]);

  const renderRow = (row: TranscriptRow | undefined) => {
    if (!row) return null;
    switch (row.kind) {
      case "separator":
        return <DaySeparator label={row.label} />;
      case "message": {
        const retryContent = row.message.role === "user" ? row.message.content : precedingUserContent(row.message);
        return (
          <MessageRow
            message={row.message}
            streaming={running && row.message.role === "assistant" && row.message.message_id === lastMessage?.message_id}
            onRetry={retryContent ? () => void send(retryContent) : undefined}
            onEdit={row.message.role === "user" ? () => setDraft(row.message.content) : undefined}
          />
        );
      }
      case "activity": {
        const events = activityIndex.byAnchor.get(row.anchor);
        return events?.length ? <ActivityStrip events={events} /> : null;
      }
      case "pending":
        return <PendingTurnRow />;
      case "tail":
        return (
          <div className="space-y-4 pb-6 pt-2">
            <TrustSummaryPanel agent={agent} />

            {facts ? <RunEvidenceCard facts={facts} detail={reasoningDetail} live={running} /> : null}

            <AgentPlan run={agent.run} />

            {agent.run?.autonomy === "plan_only" && agent.run.status === "completed" && (agent.run.agent_plan?.length ?? 0) > 0 ? <NexusSurface className="border-[color-mix(in_srgb,var(--fx-accent)_30%,var(--fx-border))] p-4"><div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><SectionEyebrow>Plan ready</SectionEyebrow><p className="mt-1 text-[11px] text-[var(--fx-text)]">Forge finished read-only investigation. Approve the plan to continue in Auto mode, or use the composer to revise it.</p></div><button onClick={() => { setMode("auto"); void send("Execute the approved implementation plan now. Preserve all stated constraints, build and repair as needed, then verify the final result."); }} className="fx-primary-action"><Sparkles className="h-4 w-4" /> Execute plan</button></div></NexusSurface> : null}

            {agent.run?.status === "awaiting_flash_confirmation" ? <NexusSurface className="overflow-hidden border-[color-mix(in_srgb,var(--fx-warning)_36%,var(--fx-border))] p-5"><div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between"><div className="flex items-start gap-3"><span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-[var(--fx-warning-soft)] text-[var(--fx-warning)]"><ShieldAlert className="h-5 w-5" /></span><div><SectionEyebrow>Hardware approval</SectionEyebrow><div className="mt-1 text-[14px] font-semibold text-[var(--fx-text)]">Verified firmware is ready to flash</div><div className="mt-1 text-[10px] leading-4 text-[var(--fx-text-muted)]">{agent.run.flash_board_type ?? selectedBoardId ?? "Board"} - {agent.run.flash_port ?? selectedBoard?.port ?? "Select device"} - {(agent.run.build_result as Record<string, unknown> | null)?.firmware_hash ? `SHA ${String((agent.run.build_result as Record<string, unknown>).firmware_hash).slice(0, 12)}...` : "artifact-bound approval"}.</div></div></div><button onClick={() => void agent.confirmFlash({ port: selectedBoard?.port, boardType: selectedBoardId, environment: activeEnvironment })} className="fx-primary-action"><Rocket className="h-4 w-4" /> Flash device</button></div></NexusSurface> : null}

            {needsRecovery ? <div className="grid gap-2 sm:grid-cols-2">
              <ActionTile icon={BrainCircuit} title="Diagnose failure" detail="Inspect the latest error without editing" onClick={() => void send("Diagnose the latest Forge run failure. Explain the likely root cause, cite the relevant diagnostics or files, and do not modify, build, flash, upload, or monitor anything.")} />
              <ActionTile icon={Wrench} title="Repair and verify" detail="Stage a focused fix and build it" onClick={() => void send("Repair the latest Forge run failure. Diagnose first, stage the smallest safe fix, build and verify the result, and do not flash hardware.")} accent />
            </div> : null}

            {changedCount || buildReady ? <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {changedCount ? <ActionTile icon={FileDiff} title={`${changedCount} files changed`} detail="Review staged changes and evidence" onClick={() => onOpenCanvas("review")} accent /> : null}
              {buildReady ? <ActionTile icon={Hammer} title="Build artifact" detail={agent.run?.build_result && (agent.run.build_result as Record<string, unknown>).success ? "Firmware build verified" : "Inspect build diagnostics"} onClick={() => onOpenCanvas("build")} /> : null}
              <ActionTile icon={Code2} title="Open code canvas" detail="Inspect or take manual control" onClick={() => onOpenCanvas("code")} />
            </div> : null}

            {agent.error ? <ErrorNote tone="error" message={agent.error} onRetry={lastUserMessage ? retryLastUser : undefined} /> : null}

            {lastAssistant && agent.run?.status === "completed" ? <div className="flex items-center justify-center gap-2 py-2 text-[9px] uppercase tracking-[.13em] text-[var(--fx-text-muted)]"><span className="h-px flex-1 bg-[var(--fx-border-soft)]" /><Check className="h-3 w-3 text-[var(--fx-success)]" /> verified stopping point<span className="h-px flex-1 bg-[var(--fx-border-soft)]" /></div> : null}
          </div>
        );
    }
  };

  return (
    <div className="relative flex h-full min-h-0 flex-col overflow-hidden">
      <div className="fx-nexus-task-header relative flex h-16 shrink-0 items-center justify-between gap-4 px-5">
        <div className="flex min-w-0 items-center gap-3">
          <ForgeCore state={coreState} size="md" label={false} />
          <div className="min-w-0"><div className="flex min-w-0 items-center gap-2"><h1 className="truncate text-[14px] font-semibold tracking-[-.02em] text-[var(--fx-text)]">{taskTitle}</h1>{running ? <span className="rounded-full border border-[color-mix(in_srgb,var(--fx-accent)_32%,var(--fx-border))] bg-[var(--fx-accent-faint)] px-2 py-0.5 text-[8px] font-semibold uppercase tracking-[.14em] text-[var(--fx-accent)]">live</span> : null}{elapsedLabel ? <span className="font-mono text-[9px] text-[var(--fx-text-muted)]">{elapsedLabel}</span> : null}</div><p className="mt-0.5 truncate text-[10px] text-[var(--fx-text-muted)]">{sessionHint}</p></div>
        </div>
        <div className="flex items-center gap-1">
          {onFocusToggle ? <button onClick={onFocusToggle} className="fx-nexus-icon" title={focused ? "Exit focus mode" : "Focus mode"}>{focused ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}</button> : null}
          <button onClick={onOpenSessions} className="fx-nexus-icon" title="Task history"><History className="h-4 w-4" /></button>
          <div className="relative"><button onClick={() => setShowReasoningMenu((value) => !value)} className="fx-nexus-icon" title="Reasoning display"><Settings2 className="h-4 w-4" /></button>{showReasoningMenu ? <NexusHud className="absolute right-0 top-10 z-50 w-[240px] p-2"><div className="px-2 pb-1.5 pt-1 text-[9px] font-semibold uppercase tracking-[.14em] text-[var(--fx-text-muted)]">Reasoning display</div>{(["minimal", "normal", "detailed"] as ReasoningDetail[]).map((item) => <button key={item} onClick={() => setReasoning(item)} className={`flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-[10px] capitalize ${item === reasoningDetail ? "bg-[var(--fx-accent-faint)] text-[var(--fx-text)]" : "text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"}`}><span>{item}</span>{item === reasoningDetail ? <Check className="h-3 w-3 text-[var(--fx-success)]" /> : null}</button>)}</NexusHud> : null}</div>
        </div>
        {railValue !== null ? <div className="absolute inset-x-5 bottom-[5px]"><ProgressRail value={railValue} tone={railValue >= 100 ? "success" : "accent"} /></div> : null}
      </div>

      <div ref={scrollRef} onScroll={updateAtBottom} className="min-h-0 flex-1 overflow-y-auto px-4 pb-40 pt-4 md:px-6 lg:px-8">
        {agent.restoring ? (
          <RestoringSkeleton />
        ) : !chatMessages.length ? (
          <div className="mx-auto max-w-[880px]">
            <div className="py-12 text-center md:py-20">
              <ForgeCore state="idle" size="lg" label={false} />
              <h2 className="mt-6 text-[24px] font-semibold tracking-[-.04em] text-[var(--fx-text)]">What are we engineering?</h2>
              <p className="mx-auto mt-2 max-w-[520px] text-[11px] leading-5 text-[var(--fx-text-muted)]">Give Forge an objective. It can investigate the project, plan, edit inside a protected overlay, build, repair failures, verify artifacts, and ask before touching hardware.</p>
              <div className="mx-auto mt-6 grid max-w-[680px] gap-2 text-left sm:grid-cols-2">
                <ActionTile icon={Sparkles} title="Build a feature" detail="Add OLED support and verify it" accent onClick={() => void send("Add OLED support to this project, preserve the existing hardware pin assignments, build it, repair any failures, and verify the final firmware.")} />
                <ActionTile icon={Wrench} title="Debug the project" detail="Investigate the latest build failure" onClick={() => void send("Investigate the current project and latest build state. Find the root cause of any failure, repair it safely, and verify the result.")} />
              </div>
            </div>
          </div>
        ) : (
          <div className="relative mx-auto max-w-[880px]" style={{ height: virtualizer.getTotalSize() }}>
            {virtualizer.getVirtualItems().map((item) => (
              <div key={item.key} data-index={item.index} ref={virtualizer.measureElement} className="absolute left-0 top-0 w-full" style={{ transform: `translateY(${item.start}px)` }}>
                {renderRow(rows[item.index])}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-30 bg-gradient-to-t from-[var(--fx-bg)] via-[color-mix(in_srgb,var(--fx-bg)_94%,transparent)] to-transparent px-4 pb-4 pt-12 md:px-6">
        <div className="pointer-events-auto mx-auto flex max-w-[880px] flex-col gap-2">
          {!atBottom && !agent.restoring ? (
            <div className="flex justify-center">
              <button onClick={() => scrollToBottom("smooth")} className="flex items-center gap-1.5 rounded-full border border-[color-mix(in_srgb,var(--fx-accent)_38%,var(--fx-border))] bg-[var(--fx-accent-faint)] px-3.5 py-1.5 text-[10px] font-semibold text-[var(--fx-accent)] shadow-lg backdrop-blur transition hover:brightness-110">
                <ArrowDown className="h-3 w-3" /> Jump to latest
              </button>
            </div>
          ) : null}
          {unanchoredActivity && !agent.restoring ? (
            <NexusSurface className="max-h-[190px] overflow-y-auto p-3">
              <div className="mb-2 flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <SectionEyebrow>Engineering activity</SectionEyebrow>
                  {running ? <span className="flex items-center gap-1.5 text-[9px] uppercase tracking-[.12em] text-[var(--fx-accent)]"><LoaderCircle className="h-3 w-3 animate-spin" /> live</span> : <span className="text-[9px] uppercase tracking-[.12em] text-[var(--fx-text-muted)]">evidence</span>}
                </div>
                <button onClick={() => setActivityCollapsed((value) => !value)} className="text-[9px] font-semibold uppercase tracking-[.12em] text-[var(--fx-text-muted)] transition-colors hover:text-[var(--fx-text)]">{activityCollapsed ? "Expand all" : "Collapse all"}</button>
              </div>
              <AgentActivityTimeline events={agent.events} currentActivity={agent.currentActivity} run={agent.run} allCollapsed={activityCollapsed} />
            </NexusSurface>
          ) : null}
          <UniversalComposer mode={mode} onModeChange={setMode} initialValue={draft} onSubmit={(value, context) => { setDraft(""); void send(value, context); }} onStop={() => void agent.cancel()} running={running} disabled={agent.submitting} submitDisabled={!projectName} modelProviders={modelProviders} modelRoute={modelRoute} loadProviderModels={loadProviderModels} onModelChange={onModelChange} />
        </div>
      </div>

      <div aria-live="polite" role="status" className="sr-only">{announcement}</div>
    </div>
  );
}
