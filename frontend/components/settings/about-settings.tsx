"use client";

import type { HealthResponse } from "@/types";

interface AboutSettingsProps {
  health: HealthResponse | null;
}

function row(label: string, value: string) {
  return (
    <div className="grid grid-cols-[170px_minmax(0,1fr)] gap-3 border-b border-[var(--fx-border)] py-2 text-sm last:border-b-0">
      <span className="text-[var(--fx-text-muted)]">{label}</span>
      <span className="truncate text-[var(--fx-text)]">{value}</span>
    </div>
  );
}

export function AboutSettings({ health }: AboutSettingsProps) {
  const electronVersion =
    typeof window !== "undefined" && window.forgexDesktop ? "Electron desktop runtime" : "Browser runtime";

  return (
    <div className="grid gap-4">
      <section className="fx-card p-5">
        <h3 className="mb-2 text-sm font-semibold text-[var(--fx-text)]">ForgeX</h3>
        {row("ForgeX version", health?.version ?? "Unknown")}
        {row("Electron", electronVersion)}
        {row("Frontend", "Next.js renderer")}
        {row("Backend status", health ? `${health.status} (${health.service ?? "backend"})` : "Disconnected")}
        {row("PlatformIO", health ? "Checked by backend services" : "Unavailable")}
        {row("App data path", "Backend-owned app data")}
      </section>
      <section className="fx-card p-5">
        <h3 className="mb-2 text-sm font-semibold text-[var(--fx-text)]">Runtime health</h3>
        <p className="text-xs leading-5 text-[var(--fx-text-muted)]">ForgeX keeps provider credentials and device access in backend-owned storage. Runtime diagnostics are available from Models & Agents and the workspace status bar.</p>
      </section>
    </div>
  );
}
