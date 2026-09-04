"use client";

import { Check, CircleAlert, Cpu, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";

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

function useAmbientPaused() {
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setPaused(media.matches || document.documentElement.dataset.motion === "off");
    update();
    media.addEventListener("change", update);
    window.addEventListener("forgex-theme-change", update);
    return () => {
      media.removeEventListener("change", update);
      window.removeEventListener("forgex-theme-change", update);
    };
  }, []);
  return paused;
}

export function ForgeCore({ state = "idle", size = "md", label = true }: { state?: ForgeCoreState; size?: "sm" | "md" | "lg"; label?: boolean }) {
  const px = size === "sm" ? 24 : size === "lg" ? 82 : 40;
  const iconSize = size === "sm" ? 11 : size === "lg" ? 24 : 14;
  const Icon = state === "complete" ? Check : state === "error" ? CircleAlert : state === "offline" ? Cpu : Sparkles;
  const paused = useAmbientPaused();
  const stillStyle = paused ? { animationPlayState: "paused" as const } : undefined;
  return (
    <div className="fx-core-wrap inline-flex items-center gap-2.5" data-state={state}>
      <div className="fx-core" data-state={state} style={{ width: px, height: px }} role="img" aria-label={`Forge ${labels[state]}`}>
        <span className="fx-core-halo" aria-hidden="true" style={stillStyle} />
        <span className="fx-core-ring fx-core-ring-a" aria-hidden="true" style={stillStyle} />
        <span className="fx-core-ring fx-core-ring-b" aria-hidden="true" style={stillStyle} />
        <span className="fx-core-center" aria-hidden="true" style={stillStyle}>
          <Icon style={{ width: iconSize, height: iconSize }} />
        </span>
      </div>
      {label ? <span className="text-[11px] font-medium text-[var(--fx-text-muted)]">{labels[state]}</span> : null}
    </div>
  );
}
