"use client";

import { SettingNumber, SettingSelect, SettingsCard, SettingToggle } from "@/components/settings/setting-controls";
import type { SettingsSectionProps } from "@/components/settings/types";

export function TerminalSettings({ settings, saving, onUpdate }: SettingsSectionProps) {
  return (
    <div className="grid gap-4">
      <SettingsCard title="Terminal">
        <SettingNumber label="Terminal font size" value={settings["terminal.font_size"]} min={11} max={22} disabled={saving} onChange={(value) => void onUpdate("terminal.font_size", value)} />
        <SettingNumber label="Scrollback lines" value={settings["terminal.scrollback_lines"]} min={200} max={20000} disabled={saving} onChange={(value) => void onUpdate("terminal.scrollback_lines", value)} />
        <SettingToggle label="Clear output before build" value={settings["terminal.clear_output_before_build"]} disabled={saving} onChange={(value) => void onUpdate("terminal.clear_output_before_build", value)} />
        <SettingToggle label="Show timestamps" value={settings["terminal.show_timestamps"]} disabled={saving} onChange={(value) => void onUpdate("terminal.show_timestamps", value)} />
        <SettingSelect
          label="Default terminal tab"
          value={settings["terminal.default_tab"]}
          options={["Output", "Terminal", "Serial Monitor"]}
          disabled={saving}
          onChange={(value) => void onUpdate("terminal.default_tab", value)}
        />
        <SettingNumber label="Serial monitor baud default" value={settings["terminal.serial_baud_default"]} min={300} max={2000000} disabled={saving} onChange={(value) => void onUpdate("terminal.serial_baud_default", value)} />
      </SettingsCard>
    </div>
  );
}
