"use client";

import { SettingSelect, SettingsCard, SettingToggle } from "@/components/settings/setting-controls";
import type { SettingsSectionProps } from "@/components/settings/types";

export function GeneralSettings({ settings, saving, onUpdate }: SettingsSectionProps) {
  return (
    <div className="grid gap-4">
      <SettingsCard title="Startup">
        <SettingSelect
          label="Startup behavior"
          value={settings["general.startup_behavior"]}
          options={["open_last_workspace", "show_welcome_screen", "open_empty_workspace"]}
          disabled={saving}
          onChange={(value) => void onUpdate("general.startup_behavior", value)}
        />
      </SettingsCard>
      <SettingsCard title="Workflow">
        <div className="border-b border-[var(--fx-border)] py-4 text-sm">
          <strong>Firmware upload approval</strong><span className="ml-3 text-[var(--fx-text-muted)]">Always required</span>
          <p className="mt-1 text-xs leading-5 text-[var(--fx-text-muted)]">Every upload requires confirmation of the selected artifact and device.</p>
        </div>
        <SettingToggle
          label="Auto-open generated main.cpp"
          value={settings["general.auto_open_generated_main_cpp"]}
          disabled={saving}
          onChange={(value) => void onUpdate("general.auto_open_generated_main_cpp", value)}
        />
      </SettingsCard>
    </div>
  );
}
