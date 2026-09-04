"use client";

import { useMemo } from "react";

import { useForgeXSettings } from "@/hooks/use-forgex-settings";
import { mergedForgeXSettings } from "@/lib/theme";

export interface EditorPreferences {
  fontSize: number;
  tabSize: number;
  wordWrap: boolean;
  minimapEnabled: boolean;
  showLineNumbers: boolean;
  autoSave: boolean;
  formatOnSave: boolean;
  showBreadcrumbs: boolean;
}

export const EDITOR_PREFERENCE_DEFAULTS: EditorPreferences = {
  fontSize: 14,
  tabSize: 2,
  wordWrap: true,
  minimapEnabled: false,
  showLineNumbers: true,
  autoSave: false,
  formatOnSave: false,
  showBreadcrumbs: true,
};

function boolSetting(value: unknown, fallback: boolean) {
  return typeof value === "boolean" ? value : fallback;
}

function numberSetting(value: unknown, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function useEditorPreferences(overrides?: Partial<EditorPreferences>) {
  const { settings } = useForgeXSettings();

  return useMemo(() => {
    const merged = mergedForgeXSettings(settings);
    const resolved: EditorPreferences = {
      fontSize: numberSetting(merged["editor.font_size"], EDITOR_PREFERENCE_DEFAULTS.fontSize),
      tabSize: numberSetting(merged["editor.tab_size"], EDITOR_PREFERENCE_DEFAULTS.tabSize),
      wordWrap: boolSetting(merged["editor.word_wrap"], EDITOR_PREFERENCE_DEFAULTS.wordWrap),
      minimapEnabled: boolSetting(merged["editor.minimap_enabled"], EDITOR_PREFERENCE_DEFAULTS.minimapEnabled),
      showLineNumbers: boolSetting(merged["editor.show_line_numbers"], EDITOR_PREFERENCE_DEFAULTS.showLineNumbers),
      autoSave: boolSetting(merged["editor.auto_save"], EDITOR_PREFERENCE_DEFAULTS.autoSave),
      formatOnSave: boolSetting(merged["editor.format_on_save"], EDITOR_PREFERENCE_DEFAULTS.formatOnSave),
      showBreadcrumbs: boolSetting(merged["editor.show_breadcrumbs"], EDITOR_PREFERENCE_DEFAULTS.showBreadcrumbs),
    };
    const applied = { ...resolved };
    for (const [key, value] of Object.entries(overrides ?? {})) {
      if (value !== undefined && key in applied) {
        (applied as Record<string, unknown>)[key] = value;
      }
    }
    return applied;
  }, [overrides, settings]);
}
