"use client";

import {
  Bot,
  Cpu,
  HardDrive,
  Info,
  MonitorCog,
  Palette,
  Settings2,
  Shield,
  SquareTerminal,
} from "lucide-react";

import type { SettingsCategory } from "@/components/settings/types";

const categories = [
  { id: "general" as const, label: "General", hint: "Startup and workflow", icon: Settings2 },
  { id: "appearance" as const, label: "Appearance", hint: "Themes and interface", icon: Palette },
  { id: "models" as const, label: "Models & Agents", hint: "Providers and routing", icon: Bot },
  { id: "editor" as const, label: "Editor", hint: "Code editing behavior", icon: MonitorCog },
  { id: "terminal" as const, label: "Terminal", hint: "Output and serial", icon: SquareTerminal },
  { id: "workspace" as const, label: "Workspace", hint: "Projects and files", icon: HardDrive },
  { id: "hardware" as const, label: "Hardware", hint: "Boards and safety", icon: Cpu },
  { id: "security" as const, label: "Security", hint: "Privacy and approvals", icon: Shield },
  { id: "about" as const, label: "About", hint: "Version and runtime", icon: Info },
];

interface SettingsSidebarProps {
  selected: SettingsCategory;
  onSelect: (category: SettingsCategory) => void;
}

export function SettingsSidebar({ selected, onSelect }: SettingsSidebarProps) {
  return (
    <aside className="fx-side-panel hidden min-h-0 w-52 shrink-0 overflow-y-auto border-r border-[var(--fx-border)] p-3 md:block lg:w-60">
      <div className="fx-kicker mb-3 px-2 pt-1">Workspace</div>
      <nav className="flex flex-col gap-1.5">
        {categories.map((category) => {
          const Icon = category.icon;
          const active = selected === category.id;
          return (
            <button
              key={category.id}
              data-settings-category={category.id}
              aria-pressed={active}
              className={`group relative flex min-h-12 w-full min-w-0 items-center gap-3 rounded-lg border px-2.5 py-2 text-left ${
                active
                  ? "border-[var(--fx-accent)]/35 bg-[var(--fx-accent-faint)] text-[var(--fx-text)]"
                  : "border-transparent text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
              }`}
              onClick={() => onSelect(category.id)}
            >
              {active ? <span className="absolute -left-3 h-7 w-0.5 rounded-r bg-[var(--fx-accent)]" /> : null}
              <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${active ? "bg-[var(--fx-accent-soft)] text-[var(--fx-accent)]" : "bg-[var(--fx-panel-elevated)]"}`}><Icon className="h-3.5 w-3.5" /></span>
              <span className="min-w-0"><span className="block truncate text-xs font-medium">{category.label}</span><span className="mt-0.5 block truncate text-[9px] text-[var(--fx-text-muted)]">{category.hint}</span></span>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
