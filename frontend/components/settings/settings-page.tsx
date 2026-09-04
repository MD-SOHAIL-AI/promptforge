"use client";

import { Check, Loader2, Search, Settings2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

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
import { ErrorNote } from "@/components/ui/error-note";
import { IconButton } from "@/components/ui/icon-button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import type { ForgeXSettingsState } from "@/hooks/use-forgex-settings";
import { notify } from "@/lib/notify";
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

const categoryOptions = Object.entries(titles) as Array<[SettingsCategory, string]>;

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
  const lastSavingRef = useRef(false);
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

  useEffect(() => {
    const finished = lastSavingRef.current && !settingsState.saving;
    lastSavingRef.current = settingsState.saving;
    if (!finished || settingsState.error || typeof window === "undefined") return;
    const timer = window.setTimeout(() => notify.success("Settings saved"), 700);
    return () => window.clearTimeout(timer);
  }, [settingsState.saving, settingsState.error]);

  const loading = settingsState.loading && !settingsState.error;

  return (
    <section className="fx-workbench flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden text-[var(--fx-text)]">
      <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-[var(--fx-border-soft)] px-5">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-[var(--fx-accent-faint)] text-[var(--fx-accent)]"><Settings2 className="h-4 w-4" /></span>
          <span className="min-w-0">
            <span className="block truncate text-[12px] font-semibold text-[var(--fx-text)]">Settings</span>
            <span className="hidden truncate text-[10px] text-[var(--fx-text-muted)] sm:block">Personalize your ForgeX</span>
          </span>
        </div>
        <div className="shrink-0 md:hidden">
          <Select value={selectedCategory} onValueChange={(value) => onSelectCategory(value as SettingsCategory)}>
            <SelectTrigger className="h-9 w-[150px] text-xs" aria-label="Settings category">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {categoryOptions.map(([category, title]) => <SelectItem key={category} value={category}>{title}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <div className="relative mx-auto hidden min-w-0 flex-1 sm:block sm:max-w-xl">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--fx-text-muted)]" />
          <input value={query} onChange={(event) => setQuery(event.target.value)} className="fx-control h-9 w-full pl-9 pr-3 text-xs outline-none" placeholder="Search settings..." aria-label="Search settings" />
          {query ? <div className="absolute left-0 right-0 top-11 z-30 overflow-hidden rounded-lg border border-[var(--fx-border)] bg-[var(--fx-panel)] shadow-[var(--fx-shadow)]">
            {searchResults.length ? searchResults.map((result) => <button key={result.key} className="flex w-full items-center justify-between border-b border-[var(--fx-border-soft)] px-3 py-2.5 text-left last:border-0 hover:bg-[var(--fx-hover)]" onClick={() => { onSelectCategory(result.category); setQuery(""); }}><span><span className="block text-xs text-[var(--fx-text)]">{result.label}</span><span className="text-[10px] capitalize text-[var(--fx-text-muted)]">{result.category}</span></span><span className="font-mono text-[9px] text-[var(--fx-text-muted)]">{result.key}</span></button>) : <div className="px-3 py-5 text-center text-xs text-[var(--fx-text-muted)]">No settings found</div>}
          </div> : null}
        </div>
        <div className="flex shrink-0 items-center justify-end gap-2 sm:min-w-36">
          <span className="hidden items-center gap-1 text-[10px] text-[var(--fx-text-muted)] xl:flex">{settingsState.saving ? <><Loader2 className="h-3 w-3 animate-spin" /> Saving</> : <><Check className="h-3 w-3 text-[var(--fx-success)]" /> Saved</>}</span>
          <IconButton label="Close settings" onClick={onClose} className="h-8 w-8"><X className="h-4 w-4" /></IconButton>
        </div>
      </header>

      <div className="flex min-h-0 min-w-0 flex-1 overflow-hidden">
        <SettingsSidebar selected={selectedCategory} onSelect={onSelectCategory} />
        <main className="min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden px-4 py-5 [scrollbar-gutter:stable] md:px-6 lg:px-8">
          <div className="mx-auto grid w-full max-w-[1120px] gap-5 pb-24">
            <header className="border-b border-[var(--fx-border-soft)] pb-4">
              <div className="fx-kicker">Preferences</div>
              <h1 className="mt-2 text-2xl font-semibold tracking-tight">{titles[selectedCategory]}</h1>
              {settingsState.error ? (
                <div className="mt-3 max-w-xl">
                  <ErrorNote message={settingsState.error} onRetry={() => void settingsState.refresh()} />
                </div>
              ) : null}
            </header>

            {loading ? (
              <div aria-busy="true" aria-label="Loading settings" className="grid gap-4">
                {[0, 1].map((card) => (
                  <section key={card} className="fx-card min-w-0 overflow-hidden">
                    <div className="border-b border-[var(--fx-border-soft)] px-4 py-3.5"><Skeleton className="h-3.5 w-44" /></div>
                    <div className="grid gap-6 px-4 py-4">
                      {[0, 1, 2].map((row) => (
                        <div key={row} className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
                          <span className="grid gap-2"><Skeleton className="h-3.5 w-40" /><Skeleton className="h-3 w-56 max-w-full" /></span>
                          <Skeleton className="h-9 w-full sm:w-40" />
                        </div>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            ) : (
              <>
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
              </>
            )}
          </div>
        </main>
      </div>
    </section>
  );
}
