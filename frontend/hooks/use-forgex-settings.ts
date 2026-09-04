"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { promptForgeApi } from "@/lib/api";
import { toErrorMessage } from "@/lib/errors";
import { applyForgeXTheme, mergedForgeXSettings } from "@/lib/theme";
import type {
  ForgeXSettingSchemaItem,
  ForgeXSettingsExportResponse,
  ForgeXSettingValue,
} from "@/types";

export function useForgeXSettings() {
  const [settings, setSettings] = useState<Record<string, ForgeXSettingValue>>(() => mergedForgeXSettings());
  const [schema, setSchema] = useState<Record<string, ForgeXSettingSchemaItem>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const settingsRef = useRef(settings);

  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [settingsResponse, schemaResponse] = await Promise.all([
        promptForgeApi.settings(),
        promptForgeApi.settingsSchema(),
      ]);
      const next = mergedForgeXSettings(settingsResponse.settings);
      setSettings(next);
      setSchema(schemaResponse.schema ?? {});
      applyForgeXTheme(next);
    } catch (err) {
      setError(toErrorMessage(err, "Settings unavailable. Backend disconnected."));
      const fallback = mergedForgeXSettings();
      setSettings(fallback);
      applyForgeXTheme(fallback);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const media = window.matchMedia?.("(prefers-color-scheme: light)");
    if (!media) return;
    const handleChange = () => {
      if (settings["appearance.theme"] === "system") {
        applyForgeXTheme(settings);
      }
    };
    media.addEventListener("change", handleChange);
    return () => media.removeEventListener("change", handleChange);
  }, [settings]);

  const updateSetting = useCallback(
    async (key: string, value: ForgeXSettingValue) => {
      setSaving(true);
      setError(null);
      const previous = settingsRef.current;
      const optimistic = mergedForgeXSettings({ ...settingsRef.current, [key]: value });
      settingsRef.current = optimistic;
      setSettings(optimistic);
      applyForgeXTheme(optimistic);
      try {
        const response = await promptForgeApi.patchSettings({ [key]: value });
        const next = mergedForgeXSettings(response.settings);
        settingsRef.current = next;
        setSettings(next);
        applyForgeXTheme(next);
        return next;
      } catch (err) {
        setError(toErrorMessage(err, "Setting could not be saved"));
        settingsRef.current = previous;
        setSettings(previous);
        applyForgeXTheme(previous);
        return previous;
      } finally {
        setSaving(false);
      }
    },
    [],
  );

  const reset = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      const response = await promptForgeApi.resetSettings();
      const next = mergedForgeXSettings(response.settings);
      setSettings(next);
      applyForgeXTheme(next);
      return next;
    } catch (err) {
      setError(toErrorMessage(err, "Settings could not be reset"));
      return settingsRef.current;
    } finally {
      setSaving(false);
    }
  }, []);

  const exportSettings = useCallback(async (): Promise<ForgeXSettingsExportResponse | null> => {
    setError(null);
    try {
      return await promptForgeApi.exportSettings();
    } catch (err) {
      setError(toErrorMessage(err, "Settings could not be exported"));
      return null;
    }
  }, []);

  return useMemo(
    () => ({
      settings,
      schema,
      loading,
      saving,
      error,
      refresh,
      updateSetting,
      reset,
      exportSettings,
    }),
    [error, exportSettings, loading, refresh, reset, saving, schema, settings, updateSetting],
  );
}

export type ForgeXSettingsState = ReturnType<typeof useForgeXSettings>;
