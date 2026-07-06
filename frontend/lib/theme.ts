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
  const accentValue = ACCENT_COLORS[accent] ?? ACCENT_COLORS.purple;

  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.density = String(merged["appearance.ui_density"] ?? "comfortable");
  document.documentElement.dataset.motion = String(merged["appearance.motion"] ?? "subtle");
  document.documentElement.dataset.panelBorders = String(merged["appearance.panel_borders"] !== false);
  document.documentElement.style.setProperty("--fx-accent", accentValue);
  document.documentElement.style.setProperty("--fx-accent-soft", `${accentValue}24`);
  document.documentElement.style.setProperty("--fx-accent-faint", `${accentValue}12`);
  document.documentElement.style.setProperty("--fx-font-size", `${Number.isFinite(fontSize) ? fontSize : 14}px`);
  document.documentElement.style.setProperty("--fx-terminal-font-size", `${Number.isFinite(terminalFontSize) ? terminalFontSize : 13}px`);
}
