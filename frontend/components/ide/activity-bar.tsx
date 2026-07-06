"use client";

import {
  Blocks,
  Bug,
  CircleHelp,
  Cpu,
  Database,
  Files,
  GitBranch,
  HardDrive,
  Package,
  Radio,
  Search,
  Settings,
  UserCircle,
} from "lucide-react";

export type ActivityId =
  | "explorer"
  | "search"
  | "source-control"
  | "run-debug"
  | "devices"
  | "libraries"
  | "boards"
  | "memory"
  | "serial"
  | "extensions";

const activities = [
  { id: "explorer" as const, label: "Explorer", icon: Files },
  { id: "search" as const, label: "Search", icon: Search },
  { id: "source-control" as const, label: "Source Control", icon: GitBranch },
  { id: "run-debug" as const, label: "Run & Debug", icon: Bug },
  { id: "devices" as const, label: "Devices", icon: Cpu },
  { id: "libraries" as const, label: "Libraries", icon: Package },
  { id: "boards" as const, label: "Boards", icon: HardDrive },
  { id: "memory" as const, label: "Memory", icon: Database },
  { id: "serial" as const, label: "Serial Monitor", icon: Radio },
  { id: "extensions" as const, label: "Extensions", icon: Blocks },
];

interface ActivityBarProps {
  active: ActivityId;
  onChange: (activity: ActivityId) => void;
  onOpenSettings?: () => void;
}

export function ActivityBar({ active, onChange, onOpenSettings }: ActivityBarProps) {
  return (
    <aside className="flex h-full min-h-0 w-[52px] flex-col items-center border-r border-[var(--fx-border)] bg-[var(--fx-panel)] py-2">
      <nav className="flex flex-1 flex-col items-center gap-1.5">
        {activities.map((item) => {
          const Icon = item.icon;
          const selected = active === item.id;
          return (
            <button
              key={item.id}
              className={`relative flex h-10 w-10 items-center justify-center rounded-lg text-[var(--fx-text-muted)] transition hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] ${
                selected ? "bg-[var(--fx-accent-faint)] text-[var(--fx-accent)]" : ""
              }`}
              title={item.label}
              aria-label={item.label}
              onClick={() => onChange(item.id)}
            >
              {selected ? <span className="absolute -left-1.5 h-6 w-0.5 rounded-r bg-[var(--fx-accent)]" /> : null}
              <Icon className="h-[18px] w-[18px]" />
            </button>
          );
        })}
      </nav>
      <div className="flex flex-col items-center gap-1 border-t border-[var(--fx-border)] pt-2">
        <button
          className="flex h-10 w-10 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-panel-elevated)] hover:text-[var(--fx-text)]"
          title="Settings"
          aria-label="Settings"
          onClick={onOpenSettings}
        >
          <Settings className="h-5 w-5" />
        </button>
        <button className="flex h-10 w-10 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-panel-elevated)] hover:text-[var(--fx-text)]" title="Help" aria-label="Help">
          <CircleHelp className="h-5 w-5" />
        </button>
        <button className="flex h-10 w-10 items-center justify-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-panel-elevated)] hover:text-[var(--fx-text)]" title="Profile" aria-label="Profile">
          <UserCircle className="h-5 w-5" />
        </button>
      </div>
    </aside>
  );
}

export const activityLabels: Record<ActivityId, string> = {
  explorer: "Explorer",
  search: "Search",
  "source-control": "Source Control",
  "run-debug": "Run & Debug",
  devices: "Devices",
  libraries: "Libraries",
  boards: "Boards",
  memory: "Memory",
  serial: "Serial Monitor",
  extensions: "Extensions",
};

export const activityDescriptions: Record<ActivityId, string> = {
  explorer: "Project files are loaded from the active ForgeX workspace.",
  search: "Workspace search will be wired after the base IDE shell is stable.",
  "source-control": "Source control status is a UI placeholder in this phase.",
  "run-debug": "Debug workflows remain available through the current prompt execution flow.",
  devices: "Device discovery UI is staged for a later hardware integration phase.",
  libraries: "Library management UI is staged for a later embedded tooling phase.",
  boards: "Board manager UI is staged without adding the board registry backend.",
  memory: "Memory analysis is staged for a later diagnostics phase.",
  serial: "Serial logs are available in the bottom panel when the backend streams them.",
  extensions: "Extensions are a placeholder for a future marketplace phase.",
};

export const activityIcons = Object.fromEntries(activities.map((item) => [item.id, item.icon])) as Record<
  ActivityId,
  (typeof activities)[number]["icon"]
>;
