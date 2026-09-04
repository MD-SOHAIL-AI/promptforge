export const FORGEX_LAYOUT_VERSION = 1 as const;
export const FORGEX_LAYOUT_STORAGE_KEY = "forgex.layout.v1";

export const LEGACY_LAYOUT_KEYS = {
  explorerWidth: "forgex.layout.explorerWidth",
  forgeWidth: "forgex.layout.aiWidth",
  bottomPanelHeight: "forgex.layout.terminalHeight",
  forgeMinimized: "forgex.layout.aiCollapsed",
  bottomPanelCollapsed: "forgex.layout.terminalCollapsed",
  bottomPanelMaximized: "forgex.layout.terminalMaximized",
} as const;

export type ForgeDock = "free" | "left" | "right";
export type ForgeResizeDirection = "n" | "ne" | "e" | "se" | "s" | "sw" | "w" | "nw";

export interface LayoutBounds {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface LayoutInsets {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

export interface ForgeWindowLayout {
  x: number;
  y: number;
  width: number;
  height: number;
  dock: ForgeDock;
  minimized: boolean;
  open: boolean;
}

export interface ForgeXLayoutState {
  version: typeof FORGEX_LAYOUT_VERSION;
  explorer: {
    width: number;
    collapsed: boolean;
  };
  bottomPanel: {
    height: number;
    collapsed: boolean;
    maximized: boolean;
    activeTab: string;
  };
  forge: ForgeWindowLayout;
  lastSettingsCategory: string;
}

export interface StorageReader {
  getItem(key: string): string | null;
}

export interface StorageWriter extends StorageReader {
  setItem(key: string, value: string): void;
}

export interface ForgeGeometryOptions {
  margin?: number;
  minWidth?: number;
  minHeight?: number;
  maxWidth?: number;
  maxHeight?: number;
}

const DEFAULT_VIEWPORT: LayoutBounds = { x: 0, y: 0, width: 1440, height: 920 };
const DEFAULT_MARGIN = 12;
const DEFAULT_FORGE_WIDTH = 420;
const DEFAULT_FORGE_HEIGHT = 620;
const MIN_FORGE_WIDTH = 340;
const MIN_FORGE_HEIGHT = 300;
const MAX_FORGE_WIDTH = 760;
const MAX_FORGE_HEIGHT = 860;

export function defaultForgeWindow(bounds: LayoutBounds = DEFAULT_VIEWPORT): ForgeWindowLayout {
  const safeBounds = normalizeBounds(bounds);
  return clampForgeWindow(
    {
      x: safeBounds.x + safeBounds.width - DEFAULT_FORGE_WIDTH - 24,
      y: safeBounds.y + 24,
      width: DEFAULT_FORGE_WIDTH,
      height: DEFAULT_FORGE_HEIGHT,
      dock: "free",
      minimized: false,
      open: true,
    },
    safeBounds,
  );
}

export function defaultForgeXLayout(bounds: LayoutBounds = DEFAULT_VIEWPORT): ForgeXLayoutState {
  return {
    version: FORGEX_LAYOUT_VERSION,
    explorer: { width: 286, collapsed: false },
    bottomPanel: { height: 240, collapsed: false, maximized: false, activeTab: "terminal" },
    forge: defaultForgeWindow(bounds),
    lastSettingsCategory: "general",
  };
}

export function boundsWithInsets(
  bounds: LayoutBounds,
  insets: Partial<LayoutInsets> = {},
): LayoutBounds {
  const safe = normalizeBounds(bounds);
  const top = nonNegative(insets.top, 0);
  const right = nonNegative(insets.right, 0);
  const bottom = nonNegative(insets.bottom, 0);
  const left = nonNegative(insets.left, 0);
  return {
    x: safe.x + Math.min(left, safe.width),
    y: safe.y + Math.min(top, safe.height),
    width: Math.max(1, safe.width - left - right),
    height: Math.max(1, safe.height - top - bottom),
  };
}

export function clampForgeWindow(
  value: ForgeWindowLayout,
  bounds: LayoutBounds,
  options: ForgeGeometryOptions = {},
): ForgeWindowLayout {
  const safeBounds = normalizeBounds(bounds);
  const margin = Math.min(nonNegative(options.margin, DEFAULT_MARGIN), Math.min(safeBounds.width, safeBounds.height) / 2);
  const availableWidth = Math.max(1, safeBounds.width - margin * 2);
  const availableHeight = Math.max(1, safeBounds.height - margin * 2);
  const minimumWidth = Math.min(availableWidth, positive(options.minWidth, MIN_FORGE_WIDTH));
  const minimumHeight = Math.min(availableHeight, positive(options.minHeight, MIN_FORGE_HEIGHT));
  const maximumWidth = Math.max(minimumWidth, Math.min(availableWidth, positive(options.maxWidth, MAX_FORGE_WIDTH)));
  const maximumHeight = Math.max(minimumHeight, Math.min(availableHeight, positive(options.maxHeight, MAX_FORGE_HEIGHT)));
  const width = clamp(positive(value.width, DEFAULT_FORGE_WIDTH), minimumWidth, maximumWidth);
  const height = clamp(positive(value.height, DEFAULT_FORGE_HEIGHT), minimumHeight, maximumHeight);
  const left = safeBounds.x + margin;
  const top = safeBounds.y + margin;
  const right = safeBounds.x + safeBounds.width - margin - width;
  const bottom = safeBounds.y + safeBounds.height - margin - height;
  const dock = validDock(value.dock) ? value.dock : "free";
  const requestedX = finite(value.x, right);
  const requestedY = finite(value.y, top);

  return {
    x: dock === "left" ? left : dock === "right" ? right : clamp(requestedX, left, right),
    y: clamp(requestedY, top, bottom),
    width,
    height,
    dock,
    minimized: Boolean(value.minimized),
    open: value.open !== false,
  };
}

export function snapForgeWindow(
  value: ForgeWindowLayout,
  bounds: LayoutBounds,
  threshold = 32,
  options: ForgeGeometryOptions = {},
): ForgeWindowLayout {
  const free = clampForgeWindow({ ...value, dock: "free" }, bounds, options);
  const safeBounds = normalizeBounds(bounds);
  const margin = Math.min(nonNegative(options.margin, DEFAULT_MARGIN), Math.min(safeBounds.width, safeBounds.height) / 2);
  const leftDistance = Math.abs(free.x - (safeBounds.x + margin));
  const rightDistance = Math.abs((free.x + free.width) - (safeBounds.x + safeBounds.width - margin));
  const snapDistance = nonNegative(threshold, 32);
  const dock: ForgeDock = Math.min(leftDistance, rightDistance) > snapDistance
    ? "free"
    : leftDistance <= rightDistance ? "left" : "right";
  return clampForgeWindow({ ...free, dock }, safeBounds, options);
}

export function resizeForgeWindow(
  value: ForgeWindowLayout,
  direction: ForgeResizeDirection,
  deltaX: number,
  deltaY: number,
  bounds: LayoutBounds,
  options: ForgeGeometryOptions = {},
): ForgeWindowLayout {
  const base = clampForgeWindow(value, bounds, options);
  const east = direction.includes("e");
  const west = direction.includes("w");
  const north = direction.includes("n");
  const south = direction.includes("s");
  const right = base.x + base.width;
  const bottom = base.y + base.height;
  const draft = {
    ...base,
    x: west ? base.x + finite(deltaX, 0) : base.x,
    y: north ? base.y + finite(deltaY, 0) : base.y,
    width: base.width + (east ? finite(deltaX, 0) : west ? -finite(deltaX, 0) : 0),
    height: base.height + (south ? finite(deltaY, 0) : north ? -finite(deltaY, 0) : 0),
  };
  const resized = clampForgeWindow(draft, bounds, options);

  if (west) resized.x = right - resized.width;
  if (north) resized.y = bottom - resized.height;
  return clampForgeWindow(resized, bounds, options);
}

export function clampForgeXLayout(
  value: ForgeXLayoutState,
  bounds: LayoutBounds = DEFAULT_VIEWPORT,
): ForgeXLayoutState {
  const safeBounds = normalizeBounds(bounds);
  const defaultState = defaultForgeXLayout(safeBounds);
  return {
    version: FORGEX_LAYOUT_VERSION,
    explorer: {
      width: Math.round(clamp(positive(value.explorer?.width, defaultState.explorer.width), 220, 440)),
      collapsed: Boolean(value.explorer?.collapsed),
    },
    bottomPanel: {
      height: Math.round(clamp(
        positive(value.bottomPanel?.height, defaultState.bottomPanel.height),
        Math.min(120, safeBounds.height),
        Math.max(Math.min(120, safeBounds.height), Math.floor(safeBounds.height * 0.7)),
      )),
      collapsed: Boolean(value.bottomPanel?.collapsed),
      maximized: Boolean(value.bottomPanel?.maximized),
      activeTab: nonEmptyString(value.bottomPanel?.activeTab, defaultState.bottomPanel.activeTab),
    },
    forge: clampForgeWindow(value.forge ?? defaultState.forge, safeBounds),
    lastSettingsCategory: nonEmptyString(value.lastSettingsCategory, defaultState.lastSettingsCategory),
  };
}

export function migrateForgeXLayout(
  raw: unknown,
  bounds: LayoutBounds = DEFAULT_VIEWPORT,
  legacy: Partial<Record<keyof typeof LEGACY_LAYOUT_KEYS, string | null>> = {},
): ForgeXLayoutState {
  const defaults = defaultForgeXLayout(bounds);
  const candidate = record(raw);
  const explorer = record(candidate?.explorer);
  const bottomPanel = record(candidate?.bottomPanel);
  const forge = record(candidate?.forge);

  const migrated: ForgeXLayoutState = {
    version: FORGEX_LAYOUT_VERSION,
    explorer: {
      width: numberValue(explorer?.width, numberFromString(legacy.explorerWidth, defaults.explorer.width)),
      collapsed: booleanValue(explorer?.collapsed, defaults.explorer.collapsed),
    },
    bottomPanel: {
      height: numberValue(bottomPanel?.height, numberFromString(legacy.bottomPanelHeight, defaults.bottomPanel.height)),
      collapsed: booleanValue(
        bottomPanel?.collapsed,
        booleanFromString(legacy.bottomPanelCollapsed, defaults.bottomPanel.collapsed),
      ),
      maximized: booleanValue(
        bottomPanel?.maximized,
        booleanFromString(legacy.bottomPanelMaximized, defaults.bottomPanel.maximized),
      ),
      activeTab: nonEmptyString(bottomPanel?.activeTab, defaults.bottomPanel.activeTab),
    },
    forge: {
      x: numberValue(forge?.x, defaults.forge.x),
      y: numberValue(forge?.y, defaults.forge.y),
      width: numberValue(forge?.width, numberFromString(legacy.forgeWidth, defaults.forge.width)),
      height: numberValue(forge?.height, defaults.forge.height),
      dock: validDock(forge?.dock) ? forge.dock : defaults.forge.dock,
      minimized: booleanValue(
        forge?.minimized,
        booleanFromString(legacy.forgeMinimized, defaults.forge.minimized),
      ),
      open: booleanValue(forge?.open, defaults.forge.open),
    },
    lastSettingsCategory: nonEmptyString(candidate?.lastSettingsCategory, defaults.lastSettingsCategory),
  };
  return clampForgeXLayout(migrated, bounds);
}

export function loadForgeXLayout(
  storage: StorageReader,
  bounds: LayoutBounds = DEFAULT_VIEWPORT,
  storageKey = FORGEX_LAYOUT_STORAGE_KEY,
): ForgeXLayoutState {
  const serialized = storage.getItem(storageKey);
  const legacy = readLegacyLayout(storage);
  if (!serialized) return migrateForgeXLayout(null, bounds, legacy);
  try {
    return migrateForgeXLayout(JSON.parse(serialized) as unknown, bounds, legacy);
  } catch {
    return migrateForgeXLayout(null, bounds, legacy);
  }
}

export function saveForgeXLayout(
  storage: StorageWriter,
  state: ForgeXLayoutState,
  bounds: LayoutBounds = DEFAULT_VIEWPORT,
  storageKey = FORGEX_LAYOUT_STORAGE_KEY,
): ForgeXLayoutState {
  const safe = clampForgeXLayout(state, bounds);
  storage.setItem(storageKey, JSON.stringify(safe));
  return safe;
}

export function sameForgeWindow(left: ForgeWindowLayout, right: ForgeWindowLayout): boolean {
  return left.x === right.x
    && left.y === right.y
    && left.width === right.width
    && left.height === right.height
    && left.dock === right.dock
    && left.minimized === right.minimized
    && left.open === right.open;
}

function readLegacyLayout(storage: StorageReader) {
  return {
    explorerWidth: storage.getItem(LEGACY_LAYOUT_KEYS.explorerWidth),
    forgeWidth: storage.getItem(LEGACY_LAYOUT_KEYS.forgeWidth),
    bottomPanelHeight: storage.getItem(LEGACY_LAYOUT_KEYS.bottomPanelHeight),
    forgeMinimized: storage.getItem(LEGACY_LAYOUT_KEYS.forgeMinimized),
    bottomPanelCollapsed: storage.getItem(LEGACY_LAYOUT_KEYS.bottomPanelCollapsed),
    bottomPanelMaximized: storage.getItem(LEGACY_LAYOUT_KEYS.bottomPanelMaximized),
  };
}

function normalizeBounds(bounds: LayoutBounds): LayoutBounds {
  return {
    x: finite(bounds?.x, 0),
    y: finite(bounds?.y, 0),
    width: positive(bounds?.width, DEFAULT_VIEWPORT.width),
    height: positive(bounds?.height, DEFAULT_VIEWPORT.height),
  };
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function validDock(value: unknown): value is ForgeDock {
  return value === "free" || value === "left" || value === "right";
}

function numberValue(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function booleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function numberFromString(value: string | null | undefined, fallback: number): number {
  if (value === null || value === undefined || value.trim() === "") return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function booleanFromString(value: string | null | undefined, fallback: boolean): boolean {
  if (value === "true") return true;
  if (value === "false") return false;
  return fallback;
}

function nonEmptyString(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function finite(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function positive(value: unknown, fallback: number): number {
  const number = finite(value, fallback);
  return number > 0 ? number : fallback;
}

function nonNegative(value: unknown, fallback: number): number {
  const number = finite(value, fallback);
  return number >= 0 ? number : fallback;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
