"use client";

import { ShieldCheck } from "lucide-react";
import { SettingNumber, SettingsCard, SettingToggle } from "@/components/settings/setting-controls";
import type { SettingsSectionProps } from "@/components/settings/types";

export function HardwareSettings({ settings, saving, onUpdate }: SettingsSectionProps) {
  return (
    <div className="grid gap-4">
      <SettingsCard title="Device discovery" description="Find serial devices while ForgeX is open. You can also detect devices from the Device pane.">
        <SettingToggle label="Auto-detect devices" value={settings["hardware.auto_detect_devices"]} disabled={saving} onChange={(value) => void onUpdate("hardware.auto_detect_devices", value)} />
        <SettingNumber label="Refresh devices interval" description="Seconds between device scans." value={settings["hardware.refresh_devices_interval"]} min={2} max={120} disabled={saving} onChange={(value) => void onUpdate("hardware.refresh_devices_interval", value)} />
        <SettingNumber label="Default monitor speed" description="Baud rate used when the project does not specify a monitor speed." value={settings["hardware.default_monitor_speed"]} min={300} max={2000000} disabled={saving} onChange={(value) => void onUpdate("hardware.default_monitor_speed", value)} />
      </SettingsCard>
      <SettingsCard title="Project configuration" description="Board, framework and upload speed come from the selected project environment.">
        <p className="py-4 text-sm leading-6 text-[var(--fx-text-muted)]">Choose a build environment in the workspace header. Edit platformio.ini or the project framework configuration to change board and upload settings.</p>
      </SettingsCard>
      <SettingsCard title="Hardware approvals">
        <p className="flex items-start gap-3 py-4 text-sm leading-6 text-[var(--fx-text-muted)]"><ShieldCheck className="mt-1 h-5 w-5 shrink-0 text-[var(--fx-success)]" />Firmware upload requires approval for the selected device. Agent hardware actions require a separate approval before execution.</p>
      </SettingsCard>
    </div>
  );
}
