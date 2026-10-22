"use client";

import { Check, ImageIcon, Monitor, Moon, Palette, Sparkles, Sun, Trash2, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { SettingNumber, SettingSelect, SettingSlider, SettingsCard, SettingToggle } from "@/components/settings/setting-controls";
import type { SettingsSectionProps } from "@/components/settings/types";
import { ACCENT_COLORS, FORGEX_THEMES } from "@/lib/theme";
import { invalidateBackgroundImage, validateBackgroundImage } from "@/lib/background-image";
import { WorkspaceBackground } from "@/components/nexus/workspace-background";

export function AppearanceSettings({ settings, onUpdate, onBatch, onPreview }: SettingsSectionProps) {
  const selectedTheme = String(settings["appearance.theme"]);
  const selectedAccent = String(settings["appearance.accent"]);
  const backgroundAssetId = String(settings["appearance.background_asset_id"] ?? "");
  const backgroundAssetName = String(settings["appearance.background_asset_name"] ?? "");
  const desktopBackgrounds = Boolean(typeof window !== "undefined" && window.forgexDesktop?.selectBackgroundImage);
  const [backgroundBusy, setBackgroundBusy] = useState(false);
  const busyRef = useRef(false);
  const [backgroundError, setBackgroundError] = useState<string | null>(null);
  useEffect(() => {
    const change = () => setBackgroundError(document.documentElement.dataset.backgroundError ?? null);
    change();
    window.addEventListener("forgex-theme-change", change);
    return () => window.removeEventListener("forgex-theme-change", change);
  }, []);

  const chooseBackground = async () => {
    if (!window.forgexDesktop?.selectBackgroundImage || busyRef.current) return;
    busyRef.current = true;
    setBackgroundBusy(true);
    setBackgroundError(null);
    let importedId: string | null = null;
    let committed = false;
    try {
      const result = await window.forgexDesktop.selectBackgroundImage();
      if (result.canceled || !result.asset) return;
      importedId = result.asset.assetId;
      await validateBackgroundImage(result.asset.url);
      await onBatch({ "appearance.background_asset_id": importedId, "appearance.background_asset_name": result.asset.filename });
      committed = true;
      if (backgroundAssetId && backgroundAssetId !== importedId) {
        await window.forgexDesktop.removeBackgroundImage(backgroundAssetId);
        invalidateBackgroundImage(backgroundAssetId);
      }
    } catch (error) {
      setBackgroundError(error instanceof Error ? error.message : "Background could not be changed.");
      if (importedId && !committed) await window.forgexDesktop?.removeBackgroundImage(importedId).catch(() => undefined);
    } finally { busyRef.current = false; setBackgroundBusy(false); }
  };

  const removeBackground = async () => {
    if (!backgroundAssetId || busyRef.current) return;
    busyRef.current = true;
    setBackgroundBusy(true);
    setBackgroundError(null);
    try {
      await onBatch({ "appearance.background_asset_id": "", "appearance.background_asset_name": "" });
      await window.forgexDesktop?.removeBackgroundImage?.(backgroundAssetId);
      invalidateBackgroundImage(backgroundAssetId);
    } catch (error) { setBackgroundError(error instanceof Error ? error.message : "Background could not be removed."); }
    finally { busyRef.current = false; setBackgroundBusy(false); }
  };


  return (
    <div className="grid gap-4">
      <SettingsCard title="Workspace background" description="Use a local image behind the workspace. ForgeX copies it into protected application storage." icon={<ImageIcon className="h-4 w-4" />}>
        <div className="grid gap-4 py-4 lg:grid-cols-[minmax(0,1fr)_240px]">
          <div className="fx-appearance-preview">
            <WorkspaceBackground />
            <div className="fx-appearance-preview-content">
              <span className="fx-kicker">Live workspace preview</span>
              <strong>{backgroundAssetName || "ForgeX graphite"}</strong>
              <div className="fx-preview-surface"><Sparkles size={16} /><span>Ready to build something extraordinary</span><span className="fx-preview-status">Agent ready</span></div>
              <span>Your image stays on this device. Code, menus, and dialogs retain reading contrast.</span>
            </div>
          </div>
          <div className="flex flex-col justify-center gap-2">
            <button type="button" className="fx-primary-action h-9 justify-center" disabled={!desktopBackgrounds || backgroundBusy} onClick={() => void chooseBackground()}>
              <Upload className="h-4 w-4" /> {backgroundAssetId ? "Replace image" : "Choose image"}
            </button>
            <button type="button" className="fx-toolbar-action h-9 justify-center" disabled={!backgroundAssetId || backgroundBusy} onClick={() => void removeBackground()}>
              <Trash2 className="h-4 w-4" /> Remove
            </button>
            {!desktopBackgrounds ? <p className="text-[10px] leading-4 text-[var(--fx-warning)]">Custom backgrounds require the ForgeX desktop app.</p> : <p className="text-[10px] leading-4 text-[var(--fx-text-muted)]">PNG, JPEG, or WebP. The original file remains untouched.</p>}
          </div>
        </div>
        {backgroundError ? <p role="alert" className="fx-inline-error mb-3">{backgroundError}</p> : null}
        <SettingSlider label="Image dimming" onPreview={(value) => onPreview("appearance.background_dim", value)} description="Darkens the image beneath work surfaces." value={settings["appearance.background_dim"] ?? 72} min={0} max={100} suffix="%" disabled={backgroundBusy} onChange={(value) => void onUpdate("appearance.background_dim", value)} />
        <SettingSlider label="Image blur" onPreview={(value) => onPreview("appearance.background_blur", value)} description="Softens detail so code remains the visual focus." value={settings["appearance.background_blur"] ?? 0} min={0} max={30} suffix="px" disabled={backgroundBusy} onChange={(value) => void onUpdate("appearance.background_blur", value)} />
        <SettingSelect label="Image fit" value={settings["appearance.background_fit"] ?? "cover"} options={["cover", "contain", "fill"]} disabled={backgroundBusy} onChange={(value) => void onUpdate("appearance.background_fit", value)} />
        <SettingSelect label="Image position" value={settings["appearance.background_position"] ?? "center"} options={["center", "top", "bottom", "left", "right"]} disabled={backgroundBusy} onChange={(value) => void onUpdate("appearance.background_position", value)} />
      </SettingsCard>

      <SettingsCard title="Theme gallery" description="Choose a complete color system. Changes preview and save immediately." icon={<Palette className="h-4 w-4" />}>
        <div className="grid grid-cols-2 gap-2 py-4 xl:grid-cols-3">
          <ThemeButton selected={selectedTheme === "system"} label="System" description="Follow your operating system" preview={["#090d12", "#f4f6f8", "#7c5cff"]} icon={<Monitor className="h-3 w-3" />} onClick={() => void onUpdate("appearance.theme", "system")} disabled={backgroundBusy} />
          {FORGEX_THEMES.map((theme) => (
            <ThemeButton key={theme.id} selected={selectedTheme === theme.id} label={theme.label} description={theme.description} preview={theme.preview} icon={theme.mode === "dark" ? <Moon className="h-3 w-3" /> : <Sun className="h-3 w-3" />} onClick={() => void onUpdate("appearance.theme", theme.id)} disabled={backgroundBusy} />
          ))}
        </div>
      </SettingsCard>

      <SettingsCard title="Accent color" description="Applied to actions, selections, focus rings, and live agent activity." icon={<Sparkles className="h-4 w-4" />}>
        <div className="flex flex-wrap gap-2 py-4">
          {Object.entries(ACCENT_COLORS).map(([name, color]) => (
            <button key={name} type="button" disabled={backgroundBusy} onClick={() => void onUpdate("appearance.accent", name)} className={`flex h-9 items-center gap-2 rounded-lg border px-3 text-xs capitalize ${selectedAccent === name ? "border-[var(--fx-accent)] bg-[var(--fx-accent-faint)]" : "border-[var(--fx-border)] hover:bg-[var(--fx-hover)]"}`}>
              <span className="h-3.5 w-3.5 rounded-full shadow-[0_0_12px_currentColor]" style={{ background: color, color }} />{name}{selectedAccent === name ? <Check className="h-3 w-3" /> : null}
            </button>
          ))}
        </div>
      </SettingsCard>

      <SettingsCard title="Surfaces and motion" description="Tune information density, depth, contrast, and animation.">
        <SettingSelect label="Activity detail" description="Choose how much live execution detail appears in conversations." value={settings["appearance.activity_detail"] ?? "normal"} options={["summary", "normal", "verbose"]} disabled={backgroundBusy} onChange={value => void onUpdate("appearance.activity_detail", value)} />
        <SettingSlider label="Surface opacity" onPreview={(value) => onPreview("appearance.surface_opacity", value)} description="Controls how much of the workspace background shows through panels." value={settings["appearance.surface_opacity"] ?? 94} min={65} max={100} suffix="%" disabled={backgroundBusy} onChange={(value) => void onUpdate("appearance.surface_opacity", value)} />
        <SettingSlider label="Glow intensity" onPreview={(value) => onPreview("appearance.glow_intensity", value)} description="Controls restrained accent light around active surfaces." value={settings["appearance.glow_intensity"] ?? 35} min={0} max={100} suffix="%" disabled={backgroundBusy} onChange={(value) => void onUpdate("appearance.glow_intensity", value)} />
        <SettingSlider label="Corner radius" onPreview={(value) => onPreview("appearance.corner_radius", value)} value={settings["appearance.corner_radius"] ?? 10} min={0} max={20} suffix="px" disabled={backgroundBusy} onChange={(value) => void onUpdate("appearance.corner_radius", value)} />
        <SettingSelect label="Contrast" value={settings["appearance.contrast"] ?? "standard"} options={["soft", "standard", "high"]} disabled={backgroundBusy} onChange={(value) => void onUpdate("appearance.contrast", value)} />
        <SettingSelect label="Motion" description="Full adds ambient status effects; Off keeps the interface still." value={settings["appearance.motion"] ?? "subtle"} options={["full", "subtle", "off"]} disabled={backgroundBusy} onChange={(value) => void onUpdate("appearance.motion", value)} />
        <SettingSelect label="UI density" value={settings["appearance.ui_density"]} options={["comfortable", "compact"]} optionLabels={{ comfortable: "Balanced", compact: "Compact" }} disabled={backgroundBusy} onChange={(value) => void onUpdate("appearance.ui_density", value)} />
        <SettingToggle label="Panel borders" value={settings["appearance.panel_borders"]} disabled={backgroundBusy} onChange={(value) => void onUpdate("appearance.panel_borders", value)} />
        <SettingNumber label="Interface font size" value={settings["appearance.font_size"]} min={12} max={18} disabled={backgroundBusy} onChange={(value) => void onUpdate("appearance.font_size", value)} />
        <SettingNumber label="Terminal font size" value={settings["appearance.terminal_font_size"]} min={11} max={22} disabled={backgroundBusy} onChange={(value) => void onUpdate("appearance.terminal_font_size", value)} />
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
