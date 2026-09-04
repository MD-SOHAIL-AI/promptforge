"use client";

import { ModelSettingsPanel } from "@/components/ide/model-settings-panel";
import type { ModelSettingsSectionProps } from "@/components/settings/types";

export function ModelSettings({ providers, routes, activeProject, onRefresh, onLog }: ModelSettingsSectionProps) {
  return (
    <div className="min-h-0">
      <ModelSettingsPanel providers={providers} routes={routes} activeProject={activeProject} onRefresh={onRefresh} onLog={onLog} />
    </div>
  );
}
