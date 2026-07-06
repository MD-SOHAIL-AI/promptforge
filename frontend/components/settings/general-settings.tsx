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
        <SettingSelect
          label="Default project mode"
          value={settings["general.default_project_mode"]}
          options={["generate_into_open_folder", "new_project", "modify_existing_project"]}
          disabled={saving}
          onChange={(value) => void onUpdate("general.default_project_mode", value)}
        />
      </SettingsCard>
      <SettingsCard title="Workflow">
        <SettingToggle
          label="Confirm before flash"
          value={settings["general.confirm_before_flash"]}
          disabled={saving}
          onChange={(value) => void onUpdate("general.confirm_before_flash", value)}
        />
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
