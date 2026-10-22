"use client";

import { SettingList, SettingNumber, SettingsCard, SettingToggle } from "@/components/settings/setting-controls";
import type { SettingsSectionProps } from "@/components/settings/types";

export function WorkspaceSettings({ settings, saving, onUpdate }: SettingsSectionProps) {
  return (
    <div className="grid gap-4">
      <SettingsCard title="Workspace">
        <SettingToggle label="Remember last workspace" value={settings["workspace.remember_last_workspace"]} disabled={saving} onChange={(value) => void onUpdate("workspace.remember_last_workspace", value)} />
        <SettingNumber label="Recent project limit" value={settings["workspace.recent_project_limit"]} min={1} max={50} disabled={saving} onChange={(value) => void onUpdate("workspace.recent_project_limit", value)} />
        <SettingList label="Excluded folders" value={settings["workspace.excluded_folders"]} disabled={saving} onChange={(value) => void onUpdate("workspace.excluded_folders", value)} />
        <SettingToggle label="Confirm before delete" value={settings["workspace.confirm_before_delete"]} disabled={saving} onChange={(value) => void onUpdate("workspace.confirm_before_delete", value)} />
        <SettingToggle label="Auto-refresh explorer" value={settings["workspace.auto_refresh_explorer"]} disabled={saving} onChange={(value) => void onUpdate("workspace.auto_refresh_explorer", value)} />
        <SettingToggle label="Show hidden files" value={settings["workspace.show_hidden_files"]} disabled={saving} onChange={(value) => void onUpdate("workspace.show_hidden_files", value)} />
      </SettingsCard>
    </div>
  );
}
