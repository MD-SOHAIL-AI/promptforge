"use client";

import { Check, Monitor, Moon, Palette, Sparkles, Sun } from "lucide-react";

import { SettingNumber, SettingSelect, SettingsCard, SettingToggle } from "@/components/settings/setting-controls";
import type { SettingsSectionProps } from "@/components/settings/types";
import { ACCENT_COLORS, FORGEX_THEMES } from "@/lib/theme";

export function AppearanceSettings({ settings, saving, onUpdate }: SettingsSectionProps) {
  const selectedTheme = String(settings["appearance.theme"]);
  const selectedAccent = String(settings["appearance.accent"]);
  return (
    <div className="grid gap-4">
      <SettingsCard title="Theme gallery" description="Choose a complete color system. Changes are previewed and saved instantly." icon={<Palette className="h-4 w-4" />}>
        <div className="grid grid-cols-2 gap-2 py-4 xl:grid-cols-3">
          <button type="button" disabled={saving} onClick={() => void onUpdate("appearance.theme", "system")} className={`relative rounded-lg border p-2.5 text-left ${selectedTheme === "system" ? "border-[var(--fx-accent)] bg-[var(--fx-accent-faint)]" : "border-[var(--fx-border)] hover:bg-[var(--fx-hover)]"}`}>
            <div className="mb-2 flex h-12 items-center justify-center rounded-md border border-[var(--fx-border-soft)] bg-gradient-to-br from-slate-900 to-slate-100"><Monitor className="h-4 w-4 text-white drop-shadow" /></div>
            <div className="text-xs font-medium">System</div><div className="mt-0.5 text-[10px] text-[var(--fx-text-muted)]">Follow your operating system</div>
            {selectedTheme === "system" ? <Check className="absolute right-2 top-2 h-3.5 w-3.5 text-[var(--fx-accent)]" /> : null}
          </button>
          {FORGEX_THEMES.map((theme) => (
            <button key={theme.id} type="button" disabled={saving} onClick={() => void onUpdate("appearance.theme", theme.id)} className={`relative rounded-lg border p-2.5 text-left ${selectedTheme === theme.id ? "border-[var(--fx-accent)] bg-[var(--fx-accent-faint)]" : "border-[var(--fx-border)] hover:bg-[var(--fx-hover)]"}`}>
              <div className="mb-2 flex h-12 overflow-hidden rounded-md border border-black/10" style={{ background: theme.preview[0] }}><span className="m-2 flex-1 rounded-sm" style={{ background: theme.preview[1] }} /><span className="my-2 mr-2 w-2 rounded-full" style={{ background: theme.preview[2] }} /></div>
              <div className="flex items-center gap-1.5 text-xs font-medium">{theme.mode === "dark" ? <Moon className="h-3 w-3" /> : <Sun className="h-3 w-3" />}{theme.label}</div>
              <div className="mt-0.5 truncate text-[10px] text-[var(--fx-text-muted)]">{theme.description}</div>
              {selectedTheme === theme.id ? <Check className="absolute right-2 top-2 h-3.5 w-3.5 text-[var(--fx-accent)]" /> : null}
            </button>
          ))}
        </div>
      </SettingsCard>

      <SettingsCard title="Accent color" description="Applied to actions, selections, focus rings, and live agent activity." icon={<Sparkles className="h-4 w-4" />}>
        <div className="flex flex-wrap gap-2 py-4">
          {Object.entries(ACCENT_COLORS).map(([name, color]) => <button key={name} type="button" disabled={saving} onClick={() => void onUpdate("appearance.accent", name)} className={`flex h-10 items-center gap-2 rounded-lg border px-3 text-xs capitalize ${selectedAccent === name ? "border-[var(--fx-accent)] bg-[var(--fx-accent-faint)]" : "border-[var(--fx-border)] hover:bg-[var(--fx-hover)]"}`}><span className="h-3.5 w-3.5 rounded-full shadow-sm" style={{ background: color }} />{name}{selectedAccent === name ? <Check className="h-3 w-3" /> : null}</button>)}
        </div>
      </SettingsCard>

      <SettingsCard title="Interface" description="Tune information density, typography, and motion.">
        <SettingNumber label="Interface font size" description="Changes labels, controls, and workspace chrome." value={settings["appearance.font_size"]} min={12} max={18} disabled={saving} onChange={(value) => void onUpdate("appearance.font_size", value)} />
        <SettingSelect label="UI density" description="Balanced is optimized for daily work; compact fits more information." value={settings["appearance.ui_density"]} options={["comfortable", "compact"]} optionLabels={{ comfortable: "Balanced", compact: "Compact" }} disabled={saving} onChange={(value) => void onUpdate("appearance.ui_density", value)} />
        <SettingSelect label="Motion" description="Subtle motion communicates state without distracting from code." value={settings["appearance.motion"] ?? "subtle"} options={["subtle", "minimal"]} disabled={saving} onChange={(value) => void onUpdate("appearance.motion", value)} />
        <SettingToggle label="Panel borders" description="Keep clear visual separation between major workspace regions." value={settings["appearance.panel_borders"]} disabled={saving} onChange={(value) => void onUpdate("appearance.panel_borders", value)} />
        <SettingNumber label="Terminal font size" value={settings["appearance.terminal_font_size"]} min={11} max={18} disabled={saving} onChange={(value) => void onUpdate("appearance.terminal_font_size", value)} />
      </SettingsCard>
    </div>
  );
}
