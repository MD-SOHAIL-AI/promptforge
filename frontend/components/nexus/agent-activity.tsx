"use client";

import { BrainCircuit, Check, CircleAlert, Code2, Cpu, FileSearch, GitCompareArrows, Hammer, ListChecks, Network, Sparkles, Wrench } from "lucide-react";
import { useMemo, useState } from "react";

import { ForgeCore, type ForgeCoreState } from "@/components/nexus/forge-core";
import { NexusSurface, SectionEyebrow } from "@/components/nexus/nexus-primitives";
import { deriveNexusActivities, planProgress, type NexusActivityItem } from "@/lib/nexus-agent-view";
import type { ForgeAgentActivitySnapshot } from "@/lib/forge-agent-activity";
import type { ProductAgentEvent, ProductAgentRun } from "@/types";

const kindIcons = {
  reason: BrainCircuit,
  inspect: FileSearch,
  edit: Code2,
  build: Hammer,
  review: GitCompareArrows,
  hardware: Cpu,
  plan: ListChecks,
  subagent: Network,
  system: Sparkles,
  error: CircleAlert,
};

function itemTone(item: NexusActivityItem) {
  if (item.status === "failed") return "var(--fx-error)";
  if (item.status === "success") return "var(--fx-success)";
  return "var(--fx-accent)";
}

function ActivityRow({ item, last, forceCollapsed = false }: { item: NexusActivityItem; last: boolean; forceCollapsed?: boolean }) {
  const Icon = kindIcons[item.kind] ?? Wrench;
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const open = forceCollapsed ? false : userOpen ?? item.status === "active";
  const tone = itemTone(item);
  return (
    <button onClick={() => item.detail && setUserOpen((value) => !(value ?? item.status === "active"))} className="group relative flex w-full gap-3 text-left" aria-expanded={open}>
      {!last ? <span className="absolute bottom-[-12px] left-[13px] top-7 w-px bg-[var(--fx-border-soft)]" /> : null}
      <span className={`relative z-10 mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full border bg-[var(--fx-bg)] ${item.status === "active" && !forceCollapsed ? "fx-activity-active" : ""}`} style={{ borderColor: `color-mix(in srgb, ${tone} 42%, var(--fx-border))`, color: tone }}>
        {item.status === "success" ? <Check className="h-3.5 w-3.5" /> : <Icon className="h-3.5 w-3.5" />}
      </span>
      <span className="min-w-0 flex-1 pb-4">
        <span className="flex items-center justify-between gap-3">
          <span className="truncate text-[11px] font-medium text-[var(--fx-text)]">{item.label}</span>
          <span className="shrink-0 text-[9px] uppercase tracking-[.11em] text-[var(--fx-text-muted)]">{item.status === "active" && !forceCollapsed ? "live" : item.status}</span>
        </span>
        {open && item.detail ? <span className="mt-1 block text-[10px] leading-4 text-[var(--fx-text-muted)]">{item.detail}</span> : null}
      </span>
    </button>
  );
}

export function AgentActivityTimeline({ events, currentActivity, run, allCollapsed = false }: { events: ProductAgentEvent[]; currentActivity: ForgeAgentActivitySnapshot | null; run: ProductAgentRun | null; allCollapsed?: boolean }) {
  const activities = useMemo(() => deriveNexusActivities(events, currentActivity, run), [events, currentActivity, run]);
  return (
    <div className="space-y-1">
      {activities.length ? activities.map((item, index) => <ActivityRow key={item.id} item={item} last={index === activities.length - 1} forceCollapsed={allCollapsed} />) : <div className="rounded-xl border border-dashed border-[var(--fx-border-soft)] px-4 py-6 text-center text-[10px] text-[var(--fx-text-muted)]">Forge activity will appear here as the task runs.</div>}
    </div>
  );
}

export function AgentPlan({ run }: { run: ProductAgentRun | null }) {
  const { plan, completed, total } = planProgress(run);
  if (!plan.length) return null;
  return (
    <NexusSurface className="p-4">
      <div className="flex items-center justify-between gap-3">
        <SectionEyebrow>Live plan</SectionEyebrow>
        <span className="font-mono text-[10px] text-[var(--fx-text-muted)]">{completed}/{total}</span>
      </div>
      <div className="mt-3 space-y-2">
        {plan.map((item, index) => {
          const status = item.status;
          return <div key={`${item.text}-${index}`} className="flex items-start gap-2.5 text-[11px]">
            <span className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border ${status === "completed" ? "border-[var(--fx-success)] bg-[var(--fx-success-soft)] text-[var(--fx-success)]" : status === "in_progress" ? "border-[var(--fx-accent)] bg-[var(--fx-accent-soft)] text-[var(--fx-accent)] fx-activity-active" : "border-[var(--fx-border)] text-[var(--fx-text-muted)]"}`}>{status === "completed" ? <Check className="h-3 w-3" /> : <span className="text-[9px]">{index + 1}</span>}</span>
            <span className={status === "completed" ? "text-[var(--fx-text-muted)] line-through decoration-[var(--fx-border)]" : "text-[var(--fx-text)]"}>{item.text}</span>
          </div>;
        })}
      </div>
    </NexusSurface>
  );
}

export function AgentStateHeader({ state, title, subtitle }: { state: ForgeCoreState; title: string; subtitle?: string }) {
  return <div className="flex items-center justify-between gap-4"><div className="min-w-0"><div className="truncate text-[14px] font-semibold text-[var(--fx-text)]">{title}</div>{subtitle ? <div className="mt-0.5 truncate text-[10px] text-[var(--fx-text-muted)]">{subtitle}</div> : null}</div><ForgeCore state={state} size="sm" /></div>;
}
