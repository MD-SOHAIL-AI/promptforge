"use client";

import type { HealthResponse } from "@/types";
import {useEffect, useState} from "react";
import {promptForgeApi} from "@/lib/api";
import {downloadJson} from "@/lib/download-json";

interface AboutSettingsProps {
  health: HealthResponse | null;
}

function row(label: string, value: string) {
  return (
    <div className="grid grid-cols-[170px_minmax(0,1fr)] gap-3 border-b border-[var(--fx-border)] py-2 text-sm last:border-b-0">
      <span className="text-[var(--fx-text-muted)]">{label}</span>
      <span className="break-all text-[var(--fx-text)]">{value}</span>
    </div>
  );
}

export function AboutSettings({ health }: AboutSettingsProps) {
  const [runtime, setRuntime] = useState<Awaited<ReturnType<typeof promptForgeApi.runtimeDiagnostics>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void promptForgeApi.runtimeDiagnostics(controller.signal).then(value => {if (!controller.signal.aborted) setRuntime(value);})
      .catch(() => {if (!controller.signal.aborted) setError("Runtime details could not be loaded.");});
    return () => controller.abort();
  }, []);
  const electronVersion =
    typeof window !== "undefined" && window.forgexDesktop ? "Electron desktop runtime" : "Browser runtime";
  const platformio = health?.platformio;
  const platformioLabel = !health
    ? "Unavailable"
    : platformio?.available
      ? `${platformio.version ?? "available"} · ${platformio.executable ?? "pio"}`
      : platformio?.message ?? "PlatformIO is unavailable";

  return (
    <div className="grid gap-4">
      <section className="fx-card p-5">
        <h3 className="mb-2 text-sm font-semibold text-[var(--fx-text)]">ForgeX</h3>
        {row("ForgeX version", health?.version ?? "Unknown")}
        {row("Electron", electronVersion)}
        {row("Frontend", "Next.js renderer")}
        {row("Backend status", health ? `${health.status} (${health.service ?? "backend"})` : "Disconnected")}
        {row("PlatformIO", platformioLabel)}
        {row("App data profile", runtime?.data_profile ?? "Unavailable")}
      </section>
      <section className="fx-card p-5">
        <h3 className="mb-2 text-sm font-semibold text-[var(--fx-text)]">Runtime health</h3>
        <p className="text-xs leading-5 text-[var(--fx-text-muted)]">ForgeX keeps provider credentials and device access in backend-owned storage. Runtime diagnostics are available from Models & Agents and the workspace status bar.</p>
        <details className="mt-4 text-sm"><summary className="cursor-pointer">Runtime identity and last observation</summary>
          {row("Contract", runtime?.runtime_contract ?? "Unavailable")}
          {row("Source fingerprint", runtime?.source_fingerprint ?? "Unavailable")}
          {row("Backend instance", runtime?.backend_instance_id ?? "Unavailable")}
          {row("Last observation", runtime?.observed_at ? new Date(runtime.observed_at).toLocaleString() : "Unavailable")}
          {row("Release mode", runtime ? runtime.packaged ? "Packaged candidate" : "Development source" : "Unavailable")}
        </details>
        <button className="fx-toolbar-action mt-4" disabled={!runtime} onClick={() => downloadJson("forgex-runtime-diagnostics.json", runtime)}>Export runtime details</button>
        {error ? <p role="status" className="mt-3 text-xs">{error}</p> : null}
      </section>
    </div>
  );
}
