"use client";

import { Bot, Cpu, Files, Settings } from "lucide-react";

export type ActivityId = "explorer" | "forge" | "devices";

const activities = [
  { id: "explorer" as const, label: "Workspace", icon: Files },
  { id: "forge" as const, label: "Forge agents", icon: Bot },
  { id: "devices" as const, label: "Devices", icon: Cpu },
];

interface ActivityBarProps {
  active: ActivityId;
  onChange: (activity: ActivityId) => void;
  onOpenSettings?: () => void;
}

export function ActivityBar({ active, onChange, onOpenSettings }: ActivityBarProps) {
  return (
    <aside className="flex h-full min-h-0 w-[60px] flex-col items-center border-r border-[var(--fx-border)] bg-[var(--fx-rail)] py-3">
      <div className="mb-4 flex h-8 w-8 items-center justify-center rounded-xl border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] text-xs font-black tracking-tight text-[var(--fx-accent)] shadow-[0_8px_24px_rgba(0,0,0,.2)]">
        FX
      </div>
      <nav className="flex flex-1 flex-col items-center gap-2" aria-label="Primary workspace navigation">
        {activities.map((item) => {
          const Icon = item.icon;
          const selected = active === item.id;
          return (
            <button
              key={item.id}
              className={`group relative flex h-10 w-10 items-center justify-center rounded-xl border text-[var(--fx-text-muted)] transition ${
                selected
                  ? "border-[color:color-mix(in_srgb,var(--fx-accent)_38%,var(--fx-border))] bg-[var(--fx-accent-faint)] text-[var(--fx-accent)] shadow-[0_8px_20px_rgba(0,0,0,.15)]"
                  : "border-transparent hover:border-[var(--fx-border-soft)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
              }`}
              title={item.label}
              aria-label={item.label}
              onClick={() => onChange(item.id)}
            >
              {selected ? <span className="absolute -left-[11px] h-5 w-[3px] rounded-r-full bg-[var(--fx-accent)]" /> : null}
              <Icon className="h-[18px] w-[18px]" />
            </button>
          );
        })}
      </nav>
      <div className="flex flex-col items-center gap-1 border-t border-[var(--fx-border)] pt-3">
        <button
          className="flex h-10 w-10 items-center justify-center rounded-xl text-[var(--fx-text-muted)] hover:bg-[var(--fx-panel-elevated)] hover:text-[var(--fx-text)]"
          title="Settings"
          aria-label="Settings"
          onClick={onOpenSettings}
        >
          <Settings className="h-5 w-5" />
        </button>
      </div>
    </aside>
  );
}
