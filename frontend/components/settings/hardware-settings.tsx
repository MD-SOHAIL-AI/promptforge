"use client";

import { SettingNumber, SettingSelect, SettingsCard, SettingText, SettingToggle } from "@/components/settings/setting-controls";
import type { SettingsSectionProps } from "@/components/settings/types";

export function HardwareSettings({ settings, saving, onUpdate }: SettingsSectionProps) {
  return (
    <div className="grid gap-4">
      <SettingsCard title="Defaults">
        <SettingText label="Default board" value={settings["hardware.default_board"]} disabled={saving} onChange={(value) => void onUpdate("hardware.default_board", value)} />
        <SettingSelect label="Default framework" value={settings["hardware.default_framework"]} options={["PlatformIO", "Arduino", "ESP-IDF"]} disabled={saving} onChange={(value) => void onUpdate("hardware.default_framework", value)} />
        <SettingNumber label="Default upload speed" value={settings["hardware.default_upload_speed"]} min={9600} max={2000000} disabled={saving} onChange={(value) => void onUpdate("hardware.default_upload_speed", value)} />
        <SettingNumber label="Default monitor speed" value={settings["hardware.default_monitor_speed"]} min={300} max={2000000} disabled={saving} onChange={(value) => void onUpdate("hardware.default_monitor_speed", value)} />
      </SettingsCard>
      <SettingsCard title="Device Safety">
        <SettingToggle label="Auto-detect devices" value={settings["hardware.auto_detect_devices"]} disabled={saving} onChange={(value) => void onUpdate("hardware.auto_detect_devices", value)} />
        <SettingNumber label="Refresh devices interval" value={settings["hardware.refresh_devices_interval"]} min={2} max={120} disabled={saving} onChange={(value) => void onUpdate("hardware.refresh_devices_interval", value)} />
        <SettingToggle label="Confirm before flash" value={settings["hardware.confirm_before_flash"]} disabled={saving} onChange={(value) => void onUpdate("hardware.confirm_before_flash", value)} />
        <SettingToggle label="Confirm before erase" value={settings["hardware.confirm_before_erase"]} disabled={saving} onChange={(value) => void onUpdate("hardware.confirm_before_erase", value)} />
        <SettingToggle label="Confirm before reset" value={settings["hardware.confirm_before_reset"]} disabled={saving} onChange={(value) => void onUpdate("hardware.confirm_before_reset", value)} />
        <SettingToggle label="Confirm before debug attach" value={settings["hardware.confirm_before_debug_attach"]} disabled={saving} onChange={(value) => void onUpdate("hardware.confirm_before_debug_attach", value)} />
      </SettingsCard>
    </div>
  );
}
