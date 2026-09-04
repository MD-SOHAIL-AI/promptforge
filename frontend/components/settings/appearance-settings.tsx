"use client";

import { Check, ImageIcon, Monitor, Moon, Palette, Sparkles, Sun, Trash2, Upload } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { SettingNumber, SettingSelect, SettingSlider, SettingsCard, SettingToggle } from "@/components/settings/setting-controls";
import type { SettingsSectionProps } from "@/components/settings/types";
import { ACCENT_COLORS, FORGEX_THEMES } from "@/lib/theme";

export function AppearanceSettings({ settings, saving, onUpdate }: SettingsSectionProps) {
  const selectedTheme = String(settings["appearance.theme"]);
  const selectedAccent = String(settings["appearance.accent"]);
  const backgroundAssetId = String(settings["appearance.background_asset_id"] ?? "");
  const backgroundAssetName = String(settings["appearance.background_asset_name"] ?? "");
  const desktopBackgrounds = Boolean(typeof window !== "undefined" && window.forgexDesktop?.selectBackgroundImage);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [backgroundBusy, setBackgroundBusy] = useState(false);
  const systemPreview = useMemo<[string, string, string]>(() => {
    const dark = (FORGEX_THEMES.find((theme) => theme.id === "forgex-dark") ?? FORGEX_THEMES[0]).preview;
    const light = (FORGEX_THEMES.find((theme) => theme.id === "porcelain") ?? FORGEX_THEMES.find((theme) => theme.mode === "light") ?? FORGEX_THEMES[0]).preview;
    return [dark[0], light[0], dark[2]];
  }, []);

  useEffect(() => {
    let active = true;
    if (!backgroundAssetId || !window.forgexDesktop?.resolveBackgroundImage) {
      setPreviewUrl(null);
      return;
    }
    void window.forgexDesktop.resolveBackgroundImage(backgroundAssetId).then((asset) => {
      if (active) setPreviewUrl(asset?.url ?? null);
    }).catch(() => {
      if (active) setPreviewUrl(null);
    });
    return () => { active = false; };
  }, [backgroundAssetId]);

  const chooseBackground = async () => {
    if (!window.forgexDesktop?.selectBackgroundImage || backgroundBusy) return;
    setBackgroundBusy(true);
    try {
      const result = await window.forgexDesktop.selectBackgroundImage();
      if (!result.canceled && result.asset) {
        await onUpdate("appearance.background_asset_id", result.asset.assetId);
        await onUpdate("appearance.background_asset_name", result.asset.filename);
      }
    } finally {
      setBackgroundBusy(false);
    }
  };

  const removeBackground = async () => {
    if (!backgroundAssetId || backgroundBusy) return;
    setBackgroundBusy(true);
    try {
      await window.forgexDesktop?.removeBackgroundImage?.(backgroundAssetId);
      await onUpdate("appearance.background_asset_id", "");
      await onUpdate("appearance.background_asset_name", "");
    } finally {
      setBackgroundBusy(false);
    }
  };

  return (
    <div className="grid gap-4">
      <SettingsCard title="Workspace background" description="Use a local image behind the workspace. ForgeX copies it into protected application storage." icon={<ImageIcon className="h-4 w-4" />}>
        <div className="grid gap-4 py-4 lg:grid-cols-[minmax(0,1fr)_240px]">
          <div
            className="relative min-h-36 overflow-hidden rounded-xl border border-[var(--fx-border)] bg-[var(--fx-bg)]"
            style={previewUrl ? { backgroundImage: `linear-gradient(rgba(5,8,14,.45),rgba(5,8,14,.78)),url("${previewUrl.replaceAll('"', "%22")}")`, backgroundPosition: "center", backgroundSize: "cover" } : undefined}
          >
            <div className="absolute inset-0 bg-[radial-gradient(circle_at_25%_15%,var(--fx-accent-soft),transparent_45%)]" />
            <div className="relative flex h-full min-h-36 flex-col justify-end p-4">
              <span className="fx-kicker">Live preview</span>
              <strong className="mt-2 truncate text-sm">{backgroundAssetName || "Technical glass workspace"}</strong>
              <span className="mt-1 text-[11px] text-[var(--fx-text-muted)]">Your image never leaves this device.</span>
            </div>
          </div>
          <div className="flex flex-col justify-center gap-2">
            <button type="button" className="fx-primary-action h-9 justify-center" disabled={!desktopBackgrounds || saving || backgroundBusy} onClick={() => void chooseBackground()}>
              <Upload className="h-4 w-4" /> {backgroundAssetId ? "Replace image" : "Choose image"}
            </button>
            <button type="button" className="fx-toolbar-action h-9 justify-center" disabled={!backgroundAssetId || saving || backgroundBusy} onClick={() => void removeBackground()}>
              <Trash2 className="h-4 w-4" /> Remove
            </button>
            {!desktopBackgrounds ? <p className="text-[10px] leading-4 text-[var(--fx-warning)]">Custom backgrounds require the ForgeX desktop app.</p> : <p className="text-[10px] leading-4 text-[var(--fx-text-muted)]">PNG, JPEG, or WebP. The original file remains untouched.</p>}
          </div>
        </div>
        <SettingSlider label="Image dimming" description="Darkens the image beneath work surfaces." value={settings["appearance.background_dim"] ?? 72} min={0} max={100} suffix="%" disabled={saving} onChange={(value) => void onUpdate("appearance.background_dim", value)} />
        <SettingSlider label="Image blur" description="Softens detail so code remains the visual focus." value={settings["appearance.background_blur"] ?? 0} min={0} max={30} suffix="px" disabled={saving} onChange={(value) => void onUpdate("appearance.background_blur", value)} />
        <SettingSelect label="Image fit" value={settings["appearance.background_fit"] ?? "cover"} options={["cover", "contain", "fill"]} disabled={saving} onChange={(value) => void onUpdate("appearance.background_fit", value)} />
        <SettingSelect label="Image position" value={settings["appearance.background_position"] ?? "center"} options={["center", "top", "bottom", "left", "right"]} disabled={saving} onChange={(value) => void onUpdate("appearance.background_position", value)} />
      </SettingsCard>

      <SettingsCard title="Theme gallery" description="Choose a complete color system. Changes preview and save immediately." icon={<Palette className="h-4 w-4" />}>
        <div className="grid gap-2 py-4 sm:grid-cols-2 xl:grid-cols-3">
          <ThemeButton selected={selectedTheme === "system"} label="System" description="Follow your operating system" preview={systemPreview} icon={<Monitor className="h-3 w-3" />} onClick={() => void onUpdate("appearance.theme", "system")} disabled={saving} />
          {FORGEX_THEMES.map((theme) => (
            <ThemeButton key={theme.id} selected={selectedTheme === theme.id} label={theme.label} description={theme.description} preview={theme.preview} icon={theme.mode === "dark" ? <Moon className="h-3 w-3" /> : <Sun className="h-3 w-3" />} onClick={() => void onUpdate("appearance.theme", theme.id)} disabled={saving} />
          ))}
        </div>
      </SettingsCard>

      <SettingsCard title="Accent energy" description="Applied to actions, selections, focus rings, and live agent activity." icon={<Sparkles className="h-4 w-4" />}>
        <div className="flex flex-wrap gap-2 py-4">
          {Object.entries(ACCENT_COLORS).map(([name, color]) => (
            <button key={name} type="button" disabled={saving} onClick={() => void onUpdate("appearance.accent", name)} className={`flex h-9 items-center gap-2 rounded-lg border px-3 text-xs capitalize ${selectedAccent === name ? "border-[var(--fx-accent)] bg-[var(--fx-accent-faint)]" : "border-[var(--fx-border)] hover:bg-[var(--fx-hover)]"}`}>
              <span className="h-3.5 w-3.5 rounded-full shadow-[0_0_12px_currentColor]" style={{ background: color, color }} />{name}{selectedAccent === name ? <Check className="h-3 w-3" /> : null}
            </button>
          ))}
        </div>
      </SettingsCard>

      <SettingsCard title="Glass and motion" description="Tune information density, depth, contrast, and animation.">
        <SettingSlider label="Surface opacity" description="Controls how much of the workspace background shows through panels." value={settings["appearance.surface_opacity"] ?? 94} min={65} max={100} suffix="%" disabled={saving} onChange={(value) => void onUpdate("appearance.surface_opacity", value)} />
        <SettingSlider label="Glow intensity" description="Controls restrained accent light around active surfaces." value={settings["appearance.glow_intensity"] ?? 35} min={0} max={100} suffix="%" disabled={saving} onChange={(value) => void onUpdate("appearance.glow_intensity", value)} />
        <SettingSlider label="Corner radius" value={settings["appearance.corner_radius"] ?? 10} min={0} max={20} suffix="px" disabled={saving} onChange={(value) => void onUpdate("appearance.corner_radius", value)} />
        <SettingSelect label="Contrast" value={settings["appearance.contrast"] ?? "standard"} options={["soft", "standard", "high"]} disabled={saving} onChange={(value) => void onUpdate("appearance.contrast", value)} />
        <SettingSelect label="Motion" description="Full adds ambient status effects; Off keeps the interface still." value={settings["appearance.motion"] ?? "subtle"} options={["full", "subtle", "off"]} disabled={saving} onChange={(value) => void onUpdate("appearance.motion", value)} />
        <SettingSelect label="UI density" value={settings["appearance.ui_density"]} options={["comfortable", "compact"]} optionLabels={{ comfortable: "Balanced", compact: "Compact" }} disabled={saving} onChange={(value) => void onUpdate("appearance.ui_density", value)} />
        <SettingToggle label="Panel borders" value={settings["appearance.panel_borders"]} disabled={saving} onChange={(value) => void onUpdate("appearance.panel_borders", value)} />
        <SettingNumber label="Interface font size" value={settings["appearance.font_size"]} min={12} max={18} disabled={saving} onChange={(value) => void onUpdate("appearance.font_size", value)} />
        <SettingNumber label="Terminal font size" value={settings["appearance.terminal_font_size"]} min={11} max={18} disabled={saving} onChange={(value) => void onUpdate("appearance.terminal_font_size", value)} />
      </SettingsCard>
    </div>
  );
}

function ThemeButton({ selected, label, description, preview, icon, onClick, disabled }: { selected: boolean; label: string; description: string; preview: readonly [string, string, string]; icon: React.ReactNode; onClick: () => void; disabled: boolean }) {
  return (
    <button type="button" disabled={disabled} onClick={onClick} className={`relative rounded-lg border p-2.5 text-left ${selected ? "border-[var(--fx-accent)] bg-[var(--fx-accent-faint)] shadow-[0_0_20px_var(--fx-glow)]" : "border-[var(--fx-border)] hover:bg-[var(--fx-hover)]"}`}>
      <div className="mb-2 flex h-11 overflow-hidden rounded-md border border-black/10" style={{ background: preview[0] }}><span className="m-2 flex-1 rounded-sm" style={{ background: preview[1] }} /><span className="my-2 mr-2 w-2 rounded-full" style={{ background: preview[2] }} /></div>
      <div className="flex items-center gap-1.5 text-xs font-medium">{icon}{label}</div>
      <div className="mt-0.5 truncate text-[10px] text-[var(--fx-text-muted)]">{description}</div>
      {selected ? <Check className="absolute right-2 top-2 h-3.5 w-3.5 text-[var(--fx-accent)]" /> : null}
    </button>
  );
}
