"use client";

import { Check, CircleAlert, Cpu, Sparkles } from "lucide-react";

export type ForgeCoreState = "idle" | "reasoning" | "working" | "building" | "waiting" | "error" | "complete" | "offline";

const labels: Record<ForgeCoreState, string> = {
  idle: "Ready",
  reasoning: "Reasoning",
  working: "Working",
  building: "Building",
  waiting: "Waiting",
  error: "Needs attention",
  complete: "Verified",
  offline: "Offline",
};

export function ForgeCore({ state = "idle", size = "md", label = true }: { state?: ForgeCoreState; size?: "sm" | "md" | "lg"; label?: boolean }) {
  const px = size === "sm" ? 24 : size === "lg" ? 82 : 40;
  const iconSize = size === "sm" ? 11 : size === "lg" ? 24 : 14;
  const Icon = state === "complete" ? Check : state === "error" ? CircleAlert : state === "offline" ? Cpu : Sparkles;
  return (
    <div className="fx-core-wrap inline-flex items-center gap-2.5" data-state={state}>
      <div className="fx-core" data-state={state} style={{ width: px, height: px }} aria-label={`Forge ${labels[state]}`}>
        <span className="fx-core-halo" />
        <span className="fx-core-ring fx-core-ring-a" />
        <span className="fx-core-ring fx-core-ring-b" />
        <span className="fx-core-center">
          <Icon style={{ width: iconSize, height: iconSize }} />
        </span>
      </div>
      {label ? <span className="text-[11px] font-medium text-[var(--fx-text-muted)]">{labels[state]}</span> : null}
    </div>
  );
}
