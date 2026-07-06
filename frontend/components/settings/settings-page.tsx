"use client";

import { Check, Loader2, Search, X } from "lucide-react";
import { useMemo, useState } from "react";

import { AboutSettings } from "@/components/settings/about-settings";
import { AppearanceSettings } from "@/components/settings/appearance-settings";
import { EditorSettings } from "@/components/settings/editor-settings";
import { GeneralSettings } from "@/components/settings/general-settings";
import { HardwareSettings } from "@/components/settings/hardware-settings";
import { ModelSettings } from "@/components/settings/model-settings";
import { SecuritySettings } from "@/components/settings/security-settings";
import { SettingsSidebar } from "@/components/settings/settings-sidebar";
import { TerminalSettings } from "@/components/settings/terminal-settings";
import type { SettingsCategory } from "@/components/settings/types";
import { WorkspaceSettings } from "@/components/settings/workspace-settings";
import type { ForgeXSettingsState } from "@/hooks/use-forgex-settings";
import type { ConsoleEntry, HealthResponse, ModelProviderResponse, ModelRouteResponse, ProjectResponse } from "@/types";

const titles: Record<SettingsCategory, string> = {
  general: "General",
  appearance: "Appearance",
  models: "Models",
  editor: "Editor",
  terminal: "Terminal",
  workspace: "Workspace",
  hardware: "Hardware",
  security: "Security",
  about: "About",
};

interface SettingsPageProps {
  selectedCategory: SettingsCategory;
  providers: ModelProviderResponse[];
  routes: ModelRouteResponse[];
  activeProject?: ProjectResponse | null;
  health: HealthResponse | null;
  settingsState: ForgeXSettingsState;
  onSelectCategory: (category: SettingsCategory) => void;
  onRefreshModelRouter: () => Promise<void>;
  onLog?: (entry: Omit<ConsoleEntry, "id" | "timestamp">) => void;
  onClose: () => void;
}

export function SettingsPage({
  selectedCategory,
  providers,
  routes,
  activeProject,
  health,
  settingsState,
  onSelectCategory,
  onRefreshModelRouter,
  onLog,
  onClose,
}: SettingsPageProps) {
  const [query, setQuery] = useState("");
  const sectionProps = useMemo(
    () => ({
      settings: settingsState.settings,
      schema: settingsState.schema,
      saving: settingsState.saving,
      onUpdate: settingsState.updateSetting,
    }),
    [settingsState.saving, settingsState.schema, settingsState.settings, settingsState.updateSetting],
  );
  const searchResults = useMemo(() => {
    const value = query.trim().toLowerCase();
    if (!value) return [];
    return Object.entries(settingsState.schema)
      .filter(([key, item]) => `${key} ${item.label ?? ""}`.toLowerCase().includes(value))
      .slice(0, 8)
      .map(([key, item]) => ({ key, label: item.label ?? key, category: (item.category ?? key.split(".")[0]) as SettingsCategory }));
  }, [query, settingsState.schema]);

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-[var(--fx-bg)] text-[var(--fx-text)]">
      <div className="flex h-16 shrink-0 items-center gap-4 border-b border-[var(--fx-border)] bg-[var(--fx-panel)] px-4">
        <div className="min-w-36">
          <h2 className="truncate text-[15px] font-semibold tracking-tight">Settings</h2>
          <p className="truncate text-[10px] text-[var(--fx-text-muted)]">Personalize your ForgeX</p>
        </div>
        <div className="relative mx-auto w-full max-w-xl">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--fx-text-muted)]" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} className="fx-control h-9 w-full pl-9 pr-3 text-xs outline-none" placeholder="Search settings..." aria-label="Search settings" />
          {query ? <div className="absolute left-0 right-0 top-11 z-30 overflow-hidden rounded-lg border border-[var(--fx-border)] bg-[var(--fx-panel)] shadow-[var(--fx-shadow)]">
            {searchResults.length ? searchResults.map((result) => <button key={result.key} className="flex w-full items-center justify-between border-b border-[var(--fx-border-soft)] px-3 py-2.5 text-left last:border-0 hover:bg-[var(--fx-hover)]" onClick={() => { onSelectCategory(result.category); setQuery(""); }}><span><span className="block text-xs text-[var(--fx-text)]">{result.label}</span><span className="text-[10px] capitalize text-[var(--fx-text-muted)]">{result.category}</span></span><span className="font-mono text-[9px] text-[var(--fx-text-muted)]">{result.key}</span></button>) : <div className="px-3 py-5 text-center text-xs text-[var(--fx-text-muted)]">No settings found</div>}
          </div> : null}
        </div>
        <div className="flex min-w-36 items-center justify-end gap-2">
          <span className="hidden items-center gap-1 text-[10px] text-[var(--fx-text-muted)] xl:flex">{settingsState.saving ? <><Loader2 className="h-3 w-3 animate-spin" /> Saving</> : <><Check className="h-3 w-3 text-[var(--fx-success)]" /> Saved</>}</span>
          <button className="fx-icon-button h-8 w-8" onClick={onClose} title="Close settings" aria-label="Close settings">
          <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="flex min-h-0 min-w-0 flex-1">
        <SettingsSidebar selected={selectedCategory} onSelect={onSelectCategory} />
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden px-5 py-6 lg:px-8">
          <div className="mx-auto grid w-full max-w-5xl gap-5 pb-14">
            <header className="border-b border-[var(--fx-border-soft)] pb-4">
              <div className="fx-kicker">Preferences</div>
              <h1 className="mt-2 text-2xl font-semibold tracking-tight">{titles[selectedCategory]}</h1>
              {settingsState.error ? (
                <p className="mt-2 rounded border border-[var(--fx-error)] bg-[var(--fx-error-soft)] px-3 py-2 text-sm text-[var(--fx-error)]">
                  Settings unavailable. Backend disconnected.
                </p>
              ) : settingsState.loading ? (
                <p className="mt-1 text-sm text-[var(--fx-text-muted)]">Loading settings...</p>
              ) : null}
            </header>

            {selectedCategory === "general" ? <GeneralSettings {...sectionProps} /> : null}
            {selectedCategory === "appearance" ? <AppearanceSettings {...sectionProps} /> : null}
            {selectedCategory === "models" ? (
              <ModelSettings providers={providers} routes={routes} activeProject={activeProject} onRefresh={onRefreshModelRouter} onLog={onLog} />
            ) : null}
            {selectedCategory === "editor" ? <EditorSettings {...sectionProps} /> : null}
            {selectedCategory === "terminal" ? <TerminalSettings {...sectionProps} /> : null}
            {selectedCategory === "workspace" ? <WorkspaceSettings {...sectionProps} /> : null}
            {selectedCategory === "hardware" ? <HardwareSettings {...sectionProps} /> : null}
            {selectedCategory === "security" ? (
              <SecuritySettings
                {...sectionProps}
                onReset={settingsState.reset}
                onExport={settingsState.exportSettings}
              />
            ) : null}
            {selectedCategory === "about" ? <AboutSettings health={health} /> : null}
          </div>
        </main>
      </div>
    </section>
  );
}
