"use client";

import { ChevronRight, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function NexusSurface({ children, className = "", glow = false, interactive = false }: { children: ReactNode; className?: string; glow?: boolean; interactive?: boolean }) {
  return <div className={`fx-nexus-surface ${glow ? "is-glow" : ""} ${interactive ? "is-interactive" : ""} ${className}`}>{children}</div>;
}

export function NexusHud({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`fx-nexus-hud ${className}`}>{children}</div>;
}

export function SectionEyebrow({ children }: { children: ReactNode }) {
  return <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-[var(--fx-text-muted)]">{children}</div>;
}

export function Metric({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[9px] uppercase tracking-[.13em] text-[var(--fx-text-muted)]">{label}</div>
      <div className="mt-1 truncate font-mono text-[13px] font-semibold text-[var(--fx-text)]">{value}</div>
      {hint ? <div className="mt-0.5 truncate text-[10px] text-[var(--fx-text-muted)]">{hint}</div> : null}
    </div>
  );
}

export function ActionTile({ icon: Icon, title, detail, onClick, accent = false }: { icon: LucideIcon; title: string; detail: string; onClick?: () => void; accent?: boolean }) {
  return (
    <button onClick={onClick} className={`group flex min-w-0 items-center gap-3 rounded-[14px] border px-3.5 py-3 text-left ${accent ? "border-[color-mix(in_srgb,var(--fx-accent)_36%,var(--fx-border))] bg-[var(--fx-accent-faint)]" : "border-[var(--fx-border-soft)] bg-[color-mix(in_srgb,var(--fx-panel)_76%,transparent)] hover:border-[var(--fx-border)] hover:bg-[var(--fx-hover)]"}`}>
      <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-xl ${accent ? "bg-[var(--fx-accent-soft)] text-[var(--fx-accent)]" : "bg-[var(--fx-panel-elevated)] text-[var(--fx-text-muted)] group-hover:text-[var(--fx-text)]"}`}><Icon className="h-4 w-4" /></span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[12px] font-semibold text-[var(--fx-text)]">{title}</span>
        <span className="mt-0.5 block truncate text-[10px] text-[var(--fx-text-muted)]">{detail}</span>
      </span>
      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--fx-text-muted)] opacity-55 transition-transform group-hover:translate-x-0.5" />
    </button>
  );
}

export function ProgressRail({ value, tone = "accent" }: { value: number; tone?: "accent" | "success" | "warning" | "error" }) {
  const color = tone === "success" ? "var(--fx-success)" : tone === "warning" ? "var(--fx-warning)" : tone === "error" ? "var(--fx-error)" : "var(--fx-accent)";
  return <div className="h-1.5 overflow-hidden rounded-full bg-[color-mix(in_srgb,var(--fx-text)_8%,transparent)]"><div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: color, boxShadow: `0 0 14px color-mix(in srgb, ${color} 52%, transparent)` }} /></div>;
}
