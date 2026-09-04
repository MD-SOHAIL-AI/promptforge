"use client";

import { Download, RotateCcw, ShieldAlert } from "lucide-react";
import { useState } from "react";

import { useForgeXDialogs } from "@/components/ide/dialogs/forgex-dialog-provider";
import { SettingToggle, SettingsCard } from "@/components/settings/setting-controls";
import type { SettingsSectionProps } from "@/components/settings/types";

interface SecuritySettingsProps extends SettingsSectionProps {
  onReset: () => Promise<Record<string, unknown>>;
  onExport: () => Promise<unknown>;
}

export function SecuritySettings({ settings, saving, onUpdate, onReset, onExport }: SecuritySettingsProps) {
  const dialogs = useForgeXDialogs();
  const [message, setMessage] = useState<string | null>(null);

  const exportSettings = async () => {
    const result = await onExport();
    if (!result) {
      setMessage("Settings export failed.");
      return;
    }
    const blob = new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `forgex-settings-${new Date().toISOString().slice(0, 10)}.json`;
    link.click();
    URL.revokeObjectURL(url);
    setMessage("Settings exported without secrets.");
  };

  const resetSettings = async () => {
    const approved = await dialogs.confirmAction({
      title: "Reset settings?",
      description: "Reset ForgeX non-secret settings to defaults?",
      confirmText: "Reset",
      variant: "danger",
    });
    if (!approved) return;
    await onReset();
    setMessage("Settings reset to defaults.");
  };

  return (
    <div className="grid gap-4">
      <SettingsCard title="Protection" description="Security defaults apply across providers, agents, and connected hardware.">
        <SettingToggle label="Mask API keys" description="Hide sensitive provider credentials anywhere they could be displayed." value={settings["security.mask_api_keys"]} disabled={saving} onChange={(value) => void onUpdate("security.mask_api_keys", value)} />
        <SettingToggle label="Hardware approval strict mode" description="Require explicit approval before potentially destructive device actions." value={settings["security.hardware_approval_strict_mode"]} disabled={saving} onChange={(value) => void onUpdate("security.hardware_approval_strict_mode", value)} />
      </SettingsCard>
      <SettingsCard title="Data actions" description="Export safe preferences or restore the default configuration.">
        <div className="grid gap-2 py-4 sm:grid-cols-2">
        <button className="fx-control flex h-10 items-center justify-center gap-2 px-3 text-xs hover:bg-[var(--fx-hover)]" onClick={exportSettings}>
          <Download className="h-4 w-4" />
          Export settings without secrets
        </button>
        <button className="fx-control flex h-10 items-center justify-center gap-2 px-3 text-xs hover:bg-[var(--fx-hover)]" onClick={resetSettings}>
          <RotateCcw className="h-4 w-4" />
          Reset settings
        </button>
        </div>
        <div className="mb-4 rounded-lg border border-[var(--fx-border)] bg-[var(--fx-input)] p-3 text-xs text-[var(--fx-text-muted)]">
          <ShieldAlert className="mr-2 inline h-3.5 w-3.5" />
          Provider secrets remain in Model Router storage and are never included in settings export.
        </div>
        {message ? <div className="text-xs text-[var(--fx-text-muted)]">{message}</div> : null}
      </SettingsCard>
    </div>
  );
}
