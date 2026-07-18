import type { PatchPreflightResult } from "@/types";
import { patchPreflightStatus, preflightToneClass } from "@/lib/patch-preflight-status";

export function CompactPreflightStatus({ result }: { result: PatchPreflightResult }) {
  const status = patchPreflightStatus(result);
  return (
    <div className="mt-1 flex min-w-0 flex-wrap items-center gap-1">
      <span className={`rounded border px-1.5 py-0.5 ${preflightToneClass(status.tone)}`}>
        Preflight: {status.label}
      </span>
      <span className="min-w-0 truncate text-[var(--fx-text-muted)]">
        {status.summary}
      </span>
    </div>
  );
}

export function QaStatus({ label, enabled, inverted = false }: { label: string; enabled: boolean; inverted?: boolean }) {
  const safe = inverted ? !enabled : enabled;
  return (
    <div className="flex min-w-0 items-center justify-between gap-2 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 py-1">
      <span className="truncate text-[var(--fx-text-muted)]">{label}</span>
      <span className={safe ? "text-[var(--fx-success)]" : "text-[var(--fx-warning)]"}>
        {enabled ? "Enabled" : "Disabled"}
      </span>
    </div>
  );
}
