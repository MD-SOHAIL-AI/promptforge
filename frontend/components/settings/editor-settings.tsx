"use client";

import { SettingNumber, SettingsCard, SettingToggle } from "@/components/settings/setting-controls";
import type { SettingsSectionProps } from "@/components/settings/types";

export function EditorSettings({ settings, saving, onUpdate }: SettingsSectionProps) {
  return (
    <div className="grid gap-4">
      <SettingsCard title="Editor">
        <SettingNumber
          label="Font size"
          value={settings["editor.font_size"]}
          min={11}
          max={22}
          disabled={saving}
          onChange={(value) => void onUpdate("editor.font_size", value)}
        />
        <SettingNumber
          label="Tab size"
          value={settings["editor.tab_size"]}
          min={2}
          max={8}
          disabled={saving}
          onChange={(value) => void onUpdate("editor.tab_size", value)}
        />
        <SettingToggle label="Word wrap" value={settings["editor.word_wrap"]} disabled={saving} onChange={(value) => void onUpdate("editor.word_wrap", value)} />
        <SettingToggle label="Minimap" value={settings["editor.minimap_enabled"]} disabled={saving} onChange={(value) => void onUpdate("editor.minimap_enabled", value)} />
        <SettingToggle label="Auto save" value={settings["editor.auto_save"]} disabled={saving} onChange={(value) => void onUpdate("editor.auto_save", value)} />
        <SettingToggle label="Format on save" value={settings["editor.format_on_save"]} disabled={saving} onChange={(value) => void onUpdate("editor.format_on_save", value)} />
        <SettingToggle label="Show breadcrumbs" value={settings["editor.show_breadcrumbs"]} disabled={saving} onChange={(value) => void onUpdate("editor.show_breadcrumbs", value)} />
        <SettingToggle label="Show line numbers" value={settings["editor.show_line_numbers"]} disabled={saving} onChange={(value) => void onUpdate("editor.show_line_numbers", value)} />
      </SettingsCard>
    </div>
  );
}
