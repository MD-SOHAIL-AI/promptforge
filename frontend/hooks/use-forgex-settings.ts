"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { promptForgeApi } from "@/lib/api";
import { toErrorMessage } from "@/lib/errors";
import { applyForgeXTheme, mergedForgeXSettings } from "@/lib/theme";
import { SettingsWriteQueue } from "@/lib/settings-write-queue";
import type { ForgeXSettingSchemaItem, ForgeXSettingsExportResponse, ForgeXSettingValue } from "@/types";

type Settings = Record<string, ForgeXSettingValue>;

export function useForgeXSettings() {
  const [settings, setSettings] = useState<Settings>(() => mergedForgeXSettings());
  const [schema, setSchema] = useState<Record<string, ForgeXSettingSchemaItem>>({});
  const [loading, setLoading] = useState(true);
  const [pendingKeys, setPendingKeys] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const confirmedRef = useRef<Settings>(mergedForgeXSettings());
  const pendingRef = useRef(new Map<number, Settings>());
  const previewRef = useRef<Settings>({});
  const generation = useRef(0);
  const refreshGeneration = useRef(0);
  const mounted = useRef(true);
  const resetting = useRef(false);

  const publish = useCallback(() => {
    const next = { ...confirmedRef.current };
    for (const patch of pendingRef.current.values()) Object.assign(next, patch);
    Object.assign(next, previewRef.current);
    if (mounted.current) {
      setSettings(next);
      setPendingKeys(resetting.current ? ["reset"] : [...new Set([...pendingRef.current.values()].flatMap(Object.keys))]);
      applyForgeXTheme(next);
    }
    return next;
  }, []);

  const [queue] = useState(() => new SettingsWriteQueue<Settings>(async patch => {
    const response = await promptForgeApi.patchSettings(patch as Settings);
    confirmedRef.current = mergedForgeXSettings(response.settings);
    if (mounted.current) { setConfirmed(true); setError(null); }
    return confirmedRef.current;
  }));

  const refresh = useCallback(async () => {
    const request = ++refreshGeneration.current;
    const revision = generation.current;
    if (mounted.current) setLoading(true);
    try {
      const [response, definition] = await Promise.all([promptForgeApi.settings(), promptForgeApi.settingsSchema()]);
      if (!mounted.current || request !== refreshGeneration.current) return;
      if (revision === generation.current && pendingRef.current.size === 0) {
        confirmedRef.current = mergedForgeXSettings(response.settings);
        setConfirmed(true);
        publish();
      }
      setSchema(definition.schema ?? {});
      setError(null);
    } catch (err) {
      if (mounted.current && request === refreshGeneration.current) setError(toErrorMessage(err, "Settings unavailable. Retry when the backend reconnects."));
    } finally {
      if (mounted.current && request === refreshGeneration.current) setLoading(false);
    }
  }, [publish]);

  useEffect(() => {
    mounted.current = true;
    applyForgeXTheme(confirmedRef.current);
    void refresh();
    return () => { mounted.current = false; };
  }, [refresh]);

  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: light)");
    if (!media) return;
    const change = () => { if (settings["appearance.theme"] === "system") applyForgeXTheme(settings); };
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, [settings]);

  const updateSettings = useCallback(async (updates: Settings) => {
    const patch = { ...updates };
    for (const [primary, alias] of [["terminal.font_size", "appearance.terminal_font_size"], ["terminal.serial_baud_default", "hardware.default_monitor_speed"]]) {
      if (primary in patch || alias in patch) { const value = patch[primary] ?? patch[alias]; patch[primary] = value; patch[alias] = value; }
    }
    if (resetting.current) throw new Error("Wait for settings reset to finish.");
    const operation = ++generation.current;
    for (const key of Object.keys(patch)) delete previewRef.current[key];
    pendingRef.current.set(operation, patch);
    setError(null);
    publish();
    try {
      await queue.enqueue(patch);
      pendingRef.current.delete(operation);
      return publish();
    } catch (err) {
      pendingRef.current.delete(operation);
      publish();
      if (mounted.current) setError(toErrorMessage(err, "Preferences could not be saved. Your last saved values were restored."));
      throw err;
    }
  }, [publish, queue]);

  // Simple control failures are displayed in state; transactions reject for rollback.
  const updateSetting = useCallback(async (key: string, value: ForgeXSettingValue) => {
    try { return await updateSettings({ [key]: value }); }
    catch { return publish(); }
  }, [publish, updateSettings]);

  const previewSetting = useCallback((key: string, value: ForgeXSettingValue) => {
    previewRef.current[key] = value;
    publish();
  }, [publish]);

  const resetCategory = useCallback(async (category: string) => {
    const patch: Settings = {};
    for (const [key, item] of Object.entries(schema)) {
      if (item.category === category && item.default !== undefined) patch[key] = item.default;
    }
    return updateSettings(patch);
  }, [schema, updateSettings]);

  const reset = useCallback(async () => {
    if (pendingRef.current.size || resetting.current) throw new Error("Wait for pending preferences to save before resetting.");
    resetting.current = true;
    generation.current++;
    publish();
    try {
      const response = await promptForgeApi.resetSettings();
      confirmedRef.current = mergedForgeXSettings(response.settings);
      previewRef.current = {};
      setConfirmed(true);
      setError(null);
      return publish();
    } catch (err) {
      setError(toErrorMessage(err, "Settings could not be reset"));
      throw err;
    } finally { resetting.current = false; publish(); }
  }, [publish]);

  const exportSettings = useCallback(async (): Promise<ForgeXSettingsExportResponse | null> => {
    try { return await promptForgeApi.exportSettings(); }
    catch (err) { setError(toErrorMessage(err, "Settings could not be exported")); return null; }
  }, []);

  return useMemo(() => ({ settings, schema, loading, saving: pendingKeys.length > 0, pendingKeys, error, confirmed,
    refresh, updateSetting, updateSettings, previewSetting, resetCategory, reset, exportSettings }),
    [settings, schema, loading, pendingKeys, error, confirmed, refresh, updateSetting, updateSettings, previewSetting, resetCategory, reset, exportSettings]);
}

export type ForgeXSettingsState = ReturnType<typeof useForgeXSettings>;
