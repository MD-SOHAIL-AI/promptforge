import assert from "node:assert/strict";
import test from "node:test";

import {
  FORGEX_LAYOUT_STORAGE_KEY,
  boundsWithInsets,
  clampForgeWindow,
  defaultForgeXLayout,
  loadForgeXLayout,
  migrateForgeXLayout,
  resizeForgeWindow,
  saveForgeXLayout,
  snapForgeWindow,
  type LayoutBounds,
} from "./layout-state.ts";

const desktop: LayoutBounds = { x: 0, y: 48, width: 1440, height: 848 };

test("legacy independent panel values migrate into one versioned layout", () => {
  const migrated = migrateForgeXLayout(null, desktop, {
    explorerWidth: "334",
    forgeWidth: "488",
    bottomPanelHeight: "310",
    forgeMinimized: "true",
    bottomPanelCollapsed: "true",
    bottomPanelMaximized: "false",
  });

  assert.equal(migrated.version, 1);
  assert.deepEqual(migrated.explorer, { width: 334, collapsed: false });
  assert.equal(migrated.bottomPanel.height, 310);
  assert.equal(migrated.bottomPanel.collapsed, true);
  assert.equal(migrated.bottomPanel.maximized, false);
  assert.equal(migrated.forge.width, 488);
  assert.equal(migrated.forge.minimized, true);
  assert.equal(migrated.forge.open, true);
});

test("versioned state wins over legacy values and malformed fields use safe defaults", () => {
  const migrated = migrateForgeXLayout({
    version: 1,
    explorer: { width: 399, collapsed: true },
    bottomPanel: { height: "bad", activeTab: "problems" },
    forge: { x: Number.NaN, y: 80, width: 512, height: 480, dock: "right", minimized: false, open: false },
    lastSettingsCategory: "appearance",
  }, desktop, {
    explorerWidth: "250",
    forgeWidth: "360",
    bottomPanelHeight: "300",
  });

  assert.equal(migrated.explorer.width, 399);
  assert.equal(migrated.explorer.collapsed, true);
  assert.equal(migrated.bottomPanel.height, 300);
  assert.equal(migrated.bottomPanel.activeTab, "problems");
  assert.equal(migrated.forge.width, 512);
  assert.equal(migrated.forge.dock, "right");
  assert.equal(migrated.forge.open, false);
  assert.equal(migrated.lastSettingsCategory, "appearance");
});

test("Forge geometry is clamped inside offset workbench bounds", () => {
  const bounds = { x: 52, y: 52, width: 908, height: 564 };
  const safe = clampForgeWindow({
    x: -900,
    y: 4_000,
    width: 2_000,
    height: 2_000,
    dock: "free",
    minimized: false,
    open: true,
  }, bounds);

  assert.equal(safe.x, 64);
  assert.equal(safe.y + safe.height, bounds.y + bounds.height - 12);
  assert.ok(safe.width <= bounds.width - 24);
  assert.ok(safe.height <= bounds.height - 24);
  assert.ok(safe.x + safe.width <= bounds.x + bounds.width - 12);
});

test("tiny or DPI-changed viewports retain a reachable Forge window", () => {
  const tiny = { x: 125, y: 90, width: 300, height: 220 };
  const safe = clampForgeWindow({
    x: 8_000,
    y: -8_000,
    width: 620,
    height: 700,
    dock: "right",
    minimized: false,
    open: true,
  }, tiny);

  assert.equal(safe.x, tiny.x + 12);
  assert.equal(safe.y, tiny.y + 12);
  assert.equal(safe.width, tiny.width - 24);
  assert.equal(safe.height, tiny.height - 24);
});

test("free windows snap to the closest edge only inside the threshold", () => {
  const base = defaultForgeXLayout(desktop).forge;
  const nearLeft = snapForgeWindow({ ...base, x: 18, dock: "free" }, desktop, 20);
  const nearRight = snapForgeWindow({ ...base, x: desktop.width - base.width - 16, dock: "free" }, desktop, 20);
  const center = snapForgeWindow({ ...base, x: 500, dock: "free" }, desktop, 20);

  assert.equal(nearLeft.dock, "left");
  assert.equal(nearLeft.x, 12);
  assert.equal(nearRight.dock, "right");
  assert.equal(nearRight.x + nearRight.width, desktop.width - 12);
  assert.equal(center.dock, "free");
});

test("west and north resize handles keep the opposite edges anchored", () => {
  const start = {
    x: 500,
    y: 180,
    width: 420,
    height: 500,
    dock: "free" as const,
    minimized: false,
    open: true,
  };
  const resized = resizeForgeWindow(start, "nw", -40, -30, desktop);

  assert.equal(resized.x, 460);
  assert.equal(resized.y, 150);
  assert.equal(resized.x + resized.width, start.x + start.width);
  assert.equal(resized.y + resized.height, start.y + start.height);
});

test("insets reserve command and status bars before geometry is clamped", () => {
  const inset = boundsWithInsets({ x: 0, y: 0, width: 960, height: 640 }, {
    top: 48,
    bottom: 24,
    left: 52,
  });

  assert.deepEqual(inset, { x: 52, y: 48, width: 908, height: 568 });
  const safe = clampForgeWindow(defaultForgeXLayout().forge, inset);
  assert.ok(safe.x >= 64);
  assert.ok(safe.y >= 60);
  assert.ok(safe.y + safe.height <= 604);
});

test("loading malformed JSON falls back to legacy state and saving writes the clamped schema", () => {
  const values = new Map<string, string>([
    [FORGEX_LAYOUT_STORAGE_KEY, "{not-json"],
    ["forgex.layout.explorerWidth", "315"],
  ]);
  const storage = {
    getItem(key: string) {
      return values.get(key) ?? null;
    },
    setItem(key: string, value: string) {
      values.set(key, value);
    },
  };

  const loaded = loadForgeXLayout(storage, desktop);
  assert.equal(loaded.explorer.width, 315);
  const saved = saveForgeXLayout(storage, { ...loaded, explorer: { width: 9_999, collapsed: false } }, desktop);
  assert.equal(saved.explorer.width, 440);
  assert.deepEqual(JSON.parse(values.get(FORGEX_LAYOUT_STORAGE_KEY) ?? "{}"), saved);
});
