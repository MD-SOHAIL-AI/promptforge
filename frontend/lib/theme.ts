import type { ForgeXSettingValue } from "@/types";

export interface ForgeXThemeDefinition {
  id: string;
  label: string;
  mode: "dark" | "light";
  description: string;
  preview: [string, string, string];
}

export const FORGEX_THEMES: ForgeXThemeDefinition[] = [
  { id: "forgex-dark", label: "Forge Dark", mode: "dark", description: "The focused ForgeX default", preview: ["#090d12", "#111821", "#7c5cff"] },
  { id: "forgex-midnight", label: "Midnight", mode: "dark", description: "Deep blue for late sessions", preview: ["#050814", "#0b1020", "#8b7cff"] },
  { id: "obsidian", label: "Obsidian", mode: "dark", description: "Neutral black with violet energy", preview: ["#09090b", "#151518", "#a78bfa"] },
  { id: "graphite", label: "Graphite", mode: "dark", description: "Soft charcoal and cool cyan", preview: ["#111315", "#1a1e21", "#67e8f9"] },
  { id: "nord", label: "Nord", mode: "dark", description: "Calm arctic developer palette", preview: ["#242933", "#2e3440", "#88c0d0"] },
  { id: "solarized-dark", label: "Solarized Dark", mode: "dark", description: "Classic low-contrast coding theme", preview: ["#002b36", "#073642", "#2aa198"] },
  { id: "porcelain", label: "Porcelain", mode: "light", description: "Clean modern light workspace", preview: ["#f4f6f8", "#ffffff", "#6957e8"] },
  { id: "paper", label: "Paper", mode: "light", description: "Warm, comfortable and editorial", preview: ["#f6f2ea", "#fffcf5", "#9a5b22"] },
  { id: "dawn", label: "Dawn", mode: "light", description: "Soft lavender morning palette", preview: ["#f7f5fb", "#ffffff", "#7c3aed"] },
  { id: "solarized-light", label: "Solarized Light", mode: "light", description: "Warm low-glare classic", preview: ["#fdf6e3", "#eee8d5", "#268bd2"] },
  { id: "high-contrast-dark", label: "High Contrast Dark", mode: "dark", description: "Maximum separation and clarity", preview: ["#000000", "#0a0a0a", "#00d4ff"] },
  { id: "high-contrast-light", label: "High Contrast Light", mode: "light", description: "Crisp accessible light mode", preview: ["#ffffff", "#f5f5f5", "#0047cc"] },
];

export const FORGEX_FONT_SANS = '"Segoe UI Variable Text", "Segoe UI Variable", Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';

export const FORGEX_FONT_MONO = '"Cascadia Code", "Cascadia Mono", "SFMono-Regular", Consolas, "Liberation Mono", monospace';

export const ACCENT_COLORS = {
  purple: "#7c5cff",
  blue: "#3b82f6",
  cyan: "#06b6d4",
  green: "#22c55e",
  amber: "#f59e0b",
  orange: "#f97316",
  rose: "#f43f5e",
  neutral: "#94a3b8",
} as const;

export const DEFAULT_FORGEX_SETTINGS: Record<string, ForgeXSettingValue> = {
  "appearance.theme": "forgex-dark",
  "appearance.accent": "purple",
  "appearance.font_size": 14,
  "appearance.ui_density": "comfortable",
  "appearance.panel_borders": true,
  "appearance.motion": "subtle",
  "appearance.terminal_font_size": 13,
  "appearance.background_asset_id": "",
  "appearance.background_asset_name": "",
  "appearance.background_dim": 72,
  "appearance.background_blur": 0,
  "appearance.background_fit": "cover",
  "appearance.background_position": "center",
  "appearance.surface_opacity": 94,
  "appearance.contrast": "standard",
  "appearance.corner_radius": 10,
  "appearance.glow_intensity": 35,
  "editor.font_size": 14,
  "editor.tab_size": 2,
  "editor.word_wrap": true,
  "editor.minimap_enabled": false,
  "editor.show_line_numbers": true,
};

export function mergedForgeXSettings(settings?: Record<string, ForgeXSettingValue> | null) {
  return { ...DEFAULT_FORGEX_SETTINGS, ...(settings ?? {}) };
}

export function applyForgeXTheme(settings?: Record<string, ForgeXSettingValue> | null) {
  if (typeof document === "undefined") return;
  const merged = mergedForgeXSettings(settings);
  const requestedTheme = String(merged["appearance.theme"] ?? "forgex-dark");
  const theme = requestedTheme === "system"
    ? window.matchMedia?.("(prefers-color-scheme: light)").matches ? "porcelain" : "forgex-dark"
    : requestedTheme === "light" ? "porcelain"
    : FORGEX_THEMES.some((item) => item.id === requestedTheme) ? requestedTheme : "forgex-dark";
  const accent = String(merged["appearance.accent"] ?? "purple") as keyof typeof ACCENT_COLORS;
  const fontSize = Number(merged["appearance.font_size"] ?? 14);
  const terminalFontSize = Number(merged["appearance.terminal_font_size"] ?? 13);
  const surfaceOpacity = clamp(Number(merged["appearance.surface_opacity"] ?? 94), 65, 100);
  const backgroundDim = clamp(Number(merged["appearance.background_dim"] ?? 72), 0, 100);
  const backgroundBlur = clamp(Number(merged["appearance.background_blur"] ?? 0), 0, 30);
  const cornerRadius = clamp(Number(merged["appearance.corner_radius"] ?? 10), 0, 20);
  const glowIntensity = clamp(Number(merged["appearance.glow_intensity"] ?? 35), 0, 100);
  const backgroundAssetId = String(merged["appearance.background_asset_id"] ?? "");
  const accentValue = ACCENT_COLORS[accent] ?? ACCENT_COLORS.purple;

  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.density = String(merged["appearance.ui_density"] ?? "comfortable");
  document.documentElement.dataset.motion = String(merged["appearance.motion"] ?? "subtle");
  document.documentElement.dataset.panelBorders = String(merged["appearance.panel_borders"] !== false);
  const accentHsl = hexToHslTriplet(accentValue);
  document.documentElement.style.setProperty("--fx-accent", accentValue);
  document.documentElement.style.setProperty("--fx-accent-hover", "color-mix(in srgb, var(--fx-accent) 85%, black)");
  document.documentElement.style.setProperty("--fx-accent-soft", `${accentValue}24`);
  document.documentElement.style.setProperty("--fx-accent-faint", `${accentValue}12`);
  document.documentElement.style.setProperty("--primary", accentHsl);
  document.documentElement.style.setProperty("--ring", accentHsl);
  document.documentElement.style.setProperty("--accent", accentHsl);
  document.documentElement.style.setProperty("--fx-ui-font", FORGEX_FONT_SANS);
  document.documentElement.style.setProperty("--fx-mono-font", FORGEX_FONT_MONO);
  document.documentElement.style.setProperty("--fx-glow", `${accentValue}${Math.round((glowIntensity / 100) * 102).toString(16).padStart(2, "0")}`);
  document.documentElement.style.setProperty("--fx-font-size", `${Number.isFinite(fontSize) ? fontSize : 14}px`);
  document.documentElement.style.setProperty("--fx-terminal-font-size", `${Number.isFinite(terminalFontSize) ? terminalFontSize : 13}px`);
  document.documentElement.style.setProperty("--fx-surface-opacity", `${surfaceOpacity}%`);
  document.documentElement.style.setProperty("--fx-background-dim", String(backgroundDim / 100));
  document.documentElement.style.setProperty("--fx-background-blur", `${backgroundBlur}px`);
  document.documentElement.style.setProperty("--fx-background-fit", String(merged["appearance.background_fit"] ?? "cover"));
  document.documentElement.style.setProperty("--fx-background-position", String(merged["appearance.background_position"] ?? "center"));
  document.documentElement.style.setProperty("--fx-radius", `${cornerRadius}px`);
  document.documentElement.style.setProperty("--fx-radius-sm", `${Math.max(3, cornerRadius - 3)}px`);
  document.documentElement.dataset.contrast = String(merged["appearance.contrast"] ?? "standard");
  document.documentElement.dataset.backgroundAsset = backgroundAssetId;
  document.documentElement.style.setProperty("--fx-background-image", "none");

  if (backgroundAssetId && window.forgexDesktop?.resolveBackgroundImage) {
    void window.forgexDesktop.resolveBackgroundImage(backgroundAssetId).then((asset) => {
      if (!asset || document.documentElement.dataset.backgroundAsset !== backgroundAssetId) return;
      const safeUrl = asset.url.replaceAll('"', "%22");
      document.documentElement.style.setProperty("--fx-background-image", `url("${safeUrl}")`);
      window.dispatchEvent(new Event("forgex-theme-change"));
    }).catch(() => null);
  }

  window.dispatchEvent(new Event("forgex-theme-change"));
}

function clamp(value: number, minimum: number, maximum: number) {
  if (!Number.isFinite(value)) return minimum;
  return Math.min(maximum, Math.max(minimum, value));
}

function hexToHslTriplet(hex: string) {
  const clean = hex.replace("#", "");
  const r = Number.parseInt(clean.slice(0, 2), 16) / 255;
  const g = Number.parseInt(clean.slice(2, 4), 16) / 255;
  const b = Number.parseInt(clean.slice(4, 6), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const lightness = (max + min) / 2;
  let hue = 0;
  let saturation = 0;
  if (max !== min) {
    const delta = max - min;
    saturation = lightness > 0.5 ? delta / (2 - max - min) : delta / (max + min);
    if (max === r) hue = (g - b) / delta + (g < b ? 6 : 0);
    else if (max === g) hue = (b - r) / delta + 2;
    else hue = (r - g) / delta + 4;
    hue *= 60;
  }
  return `${Math.round(hue)} ${Math.round(saturation * 100)}% ${Math.round(lightness * 100)}%`;
}
