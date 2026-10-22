import { expect, test, type Page } from "@playwright/test";
import { deflateSync } from "node:zlib";
import { workspace, emit } from "./fixtures/workspace";

// A valid local image exercises decoding without a network or native dialog.
function testImage() {
  const width = 640, height = 360;
  const pixels = Buffer.alloc((width * 3 + 1) * height);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const offset = y * (width * 3 + 1) + 1 + x * 3;
    pixels[offset] = Math.round(18 + 65 * x / width);
    pixels[offset + 1] = Math.round(35 + 100 * y / height);
    pixels[offset + 2] = Math.round(70 + 90 * x / width);
  }
  const crc = (buffer: Buffer) => {
    let value = 0xffffffff;
    for (const byte of buffer) {
      value ^= byte;
      for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ ((value & 1) ? 0xedb88320 : 0);
    }
    return (value ^ 0xffffffff) >>> 0;
  };
  const chunk = (name: string, data: Buffer) => {
    const type = Buffer.from(name), length = Buffer.alloc(4), checksum = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    checksum.writeUInt32BE(crc(Buffer.concat([type, data])));
    return Buffer.concat([length, type, data, checksum]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 2;
  return "data:image/png;base64," + Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]), chunk("IHDR", header), chunk("IDAT", deflateSync(pixels)), chunk("IEND", Buffer.alloc(0))]).toString("base64");
}

const schema = {
  "appearance.surface_opacity": { type: "number", category: "appearance", label: "Surface opacity", default: 94 },
  "appearance.theme": { type: "select", category: "appearance", label: "Theme", default: "forgex-dark" },
  "editor.word_wrap": { type: "boolean", category: "editor", label: "Word wrap", default: true },
};

async function setup(page: Page, initial: Record<string, unknown> = {}) {
  const stored: Record<string, unknown> = { "appearance.theme": "forgex-dark", "appearance.accent": "cyan", ...initial };
  let failure = false;
  const writes: Record<string, unknown>[] = [];
  await page.addInitScript(({ image }) => {
    const audit: Array<{ action: string; id: string }> = [];
    let imported = 0;
    const assets: Record<string, {assetId: string; filename: string; url: string}> = {};
    Object.assign(window, {
      backgroundAudit: audit,
      invalidBackground: false,
      forgexDesktop: {
        selectBackgroundImage: async () => {
          const assetId = "background-00000000-0000-4000-8000-" + String(++imported).padStart(12, "0") + ".png";
          const asset = {assetId, filename: "Aurora " + imported + ".png", url: (window as unknown as {invalidBackground: boolean}).invalidBackground ? "data:image/png;base64,AAAA" : image};
          assets[assetId] = asset; audit.push({action: "import", id: assetId});
          return {canceled: false, asset};
        },
        resolveBackgroundImage: async (id: string) => {audit.push({action: "resolve", id}); return assets[id] ?? null;},
        removeBackgroundImage: async (id: string) => {audit.push({action: "remove", id}); delete assets[id];},
      },
    });
  }, { image: testImage() });
  await workspace(page, { status: "completed" }, undefined, false, { settings: stored, schema });
  await page.route("**/api/forgex/settings", async route => {
    if (route.request().method() === "PATCH") {
      const payload = route.request().postDataJSON();
      const patch = payload.settings ?? payload;
      writes.push(patch);
      if (failure) { failure = false; return route.fulfill({status: 503, json: {message: "Preference storage unavailable"}}); }
      Object.assign(stored, patch);
    }
    return route.fulfill({ json: { settings: stored } });
  });
  await page.getByRole("button", {name: "Settings", exact: true}).click();
  return { stored, writes, failNext: () => { failure = true; } };
}

async function appearance(page: Page) {
  await page.locator('[data-settings-category="appearance"]').click();
  await expect(page.getByRole("heading", {name: "Workspace background", exact: true})).toBeVisible();
}

test("background transactions validate, replace, roll back and remove the live workspace image", async ({page}) => {
  const {stored, writes, failNext} = await setup(page);
  await appearance(page);
  await page.getByRole("button", {name: "Choose image", exact: true}).click();
  await expect.poll(() => stored["appearance.background_asset_name"]).toBe("Aurora 1.png");
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.querySelector(".fx-product-shell > .fx-workspace-background .fx-workspace-background-image")!).backgroundImage)).toContain("data:image/png");
  expect(writes[0]).toHaveProperty(["appearance.background_asset_id"]);
  expect(writes[0]).toHaveProperty(["appearance.background_asset_name"], "Aurora 1.png");
  const originalId = stored["appearance.background_asset_id"];
  failNext();
  await page.getByRole("button", {name: "Replace image", exact: true}).click();
  await expect(page.getByRole("alert").filter({hasText: "Preference storage unavailable"}).last()).toBeVisible();
  expect(stored["appearance.background_asset_id"]).toBe(originalId);
  const removed = () => page.evaluate(() => (window as unknown as {backgroundAudit: Array<{action: string; id: string}>}).backgroundAudit.filter(item => item.action === "remove").map(item => item.id));
  expect(await removed()).not.toContain(originalId);
  expect(await removed()).toHaveLength(1);
  await page.getByRole("button", {name: "Replace image", exact: true}).click();
  await expect.poll(() => stored["appearance.background_asset_name"]).toBe("Aurora 3.png");
  await expect.poll(removed).toContain(originalId);
  await page.screenshot({path: "test-results/redesign-appearance.png"});
  await page.getByRole("button", {name: "Remove", exact: true}).click();
  await expect.poll(() => stored["appearance.background_asset_id"]).toBe("");
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.querySelector(".fx-product-shell > .fx-workspace-background .fx-workspace-background-image")!).backgroundImage)).toBe("none");
});

test("invalid images are rejected before persistence and missing images have a recoverable error", async ({page}) => {
  const {writes} = await setup(page, {"appearance.background_asset_id": "missing.png"});
  await appearance(page);
  await expect(page.getByRole("alert").filter({hasText: /saved background is missing/})).toBeVisible();
  await page.evaluate(() => { (window as unknown as {invalidBackground: boolean}).invalidBackground = true; });
  await page.getByRole("button", {name: "Replace image", exact: true}).click();
  await expect(page.getByRole("alert").filter({hasText: /could not be decoded/})).toBeVisible();
  expect(writes).toHaveLength(0);
});

test("rapid preferences persist together, preview opacity immediately and survive reload", async ({page}) => {
  const {stored, writes} = await setup(page);
  await appearance(page);
  const slider = page.getByRole("slider", {name: "Surface opacity", exact: true});
  await slider.evaluate(element => {
    const input = element as HTMLInputElement;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    setter.call(input, "70"); input.dispatchEvent(new Event("input", {bubbles: true}));
  });
  await expect.poll(() => page.evaluate(() => document.documentElement.style.getPropertyValue("--fx-surface-opacity"))).toBe("70%");
  expect(writes).toHaveLength(0);
  await slider.dispatchEvent("pointerup");
  await page.getByRole("combobox", {name: "Image fit", exact: true}).selectOption("fill");
  await page.getByRole("combobox", {name: "Image position", exact: true}).selectOption("right");
  await expect.poll(() => stored["appearance.background_position"]).toBe("right");
  expect(stored["appearance.surface_opacity"]).toBe(70);
  expect(stored["appearance.background_fit"]).toBe("fill");
  await page.reload();
  await appearance(page);
  await expect(slider).toHaveValue("70");
  await expect(page.getByRole("combobox", {name: "Image fit", exact: true})).toHaveValue("fill");
  await expect.poll(() => page.evaluate(() => document.documentElement.style.getPropertyValue("--fx-background-fit"))).toBe("100% 100%");
});

test("all settings sections work with keyboard search and a narrow viewport", async ({page}) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await setup(page);
  for (const category of ["appearance", "editor", "terminal", "workspace", "hardware", "security", "about", "models", "general"]) {
    await page.locator('[data-settings-category="' + category + '"]').click();
    await expect(page.locator(".fx-settings-body h1")).toBeVisible();
  }
  await page.getByRole("textbox", {name: "Search settings"}).fill("word wrap");
  await page.getByRole("button", {name: /Word wrap.*editor/}).click();
  await expect(page.getByRole("switch", {name: "Word wrap", exact: true})).toBeFocused();
  await page.setViewportSize({width: 700, height: 900});
  await appearance(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.locator(".fx-appearance-preview")).toBeVisible();
  await page.screenshot({path: "test-results/redesign-narrow-settings.png"});
  expect(errors).toEqual([]);
});

test("theme and accessibility preferences change surfaces, preserve readable output and disable motion", async ({page}) => {
  await setup(page);
  await appearance(page);
  for (const [name, mode] of [["Porcelain", "light"], ["Obsidian", "dark"], ["High Contrast Light", "light"], ["Forge Dark", "dark"]]) {
    await page.getByRole("button", {name: new RegExp("^" + name)}).click();
    await expect.poll(() => page.evaluate(() => document.documentElement.dataset.mode)).toBe(mode);
    const colors = await page.evaluate(() => {
      const card = document.querySelector(".fx-card")!;
      return {background: getComputedStyle(card).backgroundColor, text: getComputedStyle(card).color};
    });
    expect(colors.text).not.toBe(colors.background);
  }
  await page.getByRole("combobox", {name: "Motion", exact: true}).selectOption("off");
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.motion)).toBe("off");
  await page.screenshot({path: "test-results/redesign-dark-settings.png"});
});

test("workspace tools fill their pane and redesigned activity supports summary, verbose and bounded history", async ({page}) => {
  const events = Array.from({length: 10000}, (_, index) => ({event_id: "event-" + index, run_id: "r", session_id: "s", sequence: index + 1, event_type: "tool.output", created_at: new Date().toISOString(),
    activity_record: {id: "tool-" + index, runId: "r", agentId: "main", parentId: null, kind: index % 2 ? "search" : "build", status: "completed", title: "Activity " + index, startedAt: Date.now() - (10000 - index) * 1000, durationMs: 50, details: {output: "Verified output " + index, summary: "Activity " + index}}}));
  await workspace(page, {events});
  await page.getByRole("button", {name: "Agent", exact: true}).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as {testStreams: Array<{url: string; closed: boolean}>}).testStreams.some(item => !item.closed && item.url.includes("/runs/r/events")))).toBe(true);
  await page.evaluate(records => {
    const sources = (window as unknown as {testStreams: Array<{url: string; closed: boolean; onmessage?: (event: {data: string}) => void}>}).testStreams;
    const source = sources.find(item => !item.closed && item.url.includes("/runs/r/events"));
    for (const record of records) source?.onmessage?.({data: JSON.stringify(record)});
  }, events);
  await expect(page.locator(".fx-activity-item").last()).toBeVisible({timeout: 30000});
  expect(await page.locator(".fx-activity-item").count()).toBeLessThanOrEqual(80);
  await page.getByRole("combobox", {name: "Activity detail", exact: true}).selectOption("summary");
  await expect(page.locator(".fx-activity-stream")).toHaveAttribute("data-detail-mode", "summary");
  await expect(page.getByText("Activity 9999", {exact: true})).toHaveCount(0);
  await page.getByRole("combobox", {name: "Activity detail", exact: true}).selectOption("verbose");
  await expect(page.locator(".fx-activity-stream")).toHaveAttribute("data-detail-mode", "verbose");
  for (let index = 0; index < 20; index++) {
    await emit(page, 10001 + index, {id: "live-" + index, kind: "build", status: "running", title: "Live build " + index, details: {summary: "Live build " + index}});
    await page.waitForTimeout(50);
  }
  await expect(page.locator('[data-activity-id="live-19"] .fx-activity-title')).toBeVisible();
  expect(await page.locator(".fx-activity-item").count()).toBeLessThanOrEqual(80);
  const live = page.getByRole("button", {name: "Live", exact: true});
  if (await live.count()) await live.click();
  await page.screenshot({path: "test-results/redesign-live-journal.png"});
  await page.getByRole("button", {name: "Open workspace", exact: true}).click();
  for (const tab of ["Files", "Changes", "Build", "Evidence", "Device", "Terminal"]) {
    await page.getByRole("tab", {name: tab, exact: true}).click();
    await expect(page.locator(".fx-workspace-dock-body")).toBeVisible();
    await page.screenshot({path: "test-results/redesign-tool-" + tab.toLowerCase() + ".png"});
  }
  const panel = await page.getByLabel("Panel", {exact: true}).boundingBox();
  const pane = await page.locator(".fx-workspace-dock-body").boundingBox();
  expect(panel!.height).toBeLessThan(pane!.height);
  expect(panel!.height).toBeGreaterThan(pane!.height - 80);
  await expect(page.getByRole("button", {name: "Restore bottom panel"})).toHaveCount(0);
});
test("typed settings commit complete values and project search finds retained projects", async ({page}) => {
  const {stored} = await setup(page);
  await page.locator('[data-settings-category="terminal"]').click();
  const baud = page.getByRole("spinbutton", {name: "Serial monitor baud default", exact: true});
  await baud.fill("57600");
  expect(stored["terminal.serial_baud_default"]).toBeUndefined();
  await baud.press("Enter");
  await expect.poll(() => stored["terminal.serial_baud_default"]).toBe(57600);
  await page.locator('[data-settings-category="workspace"]').click();
  const excluded = page.getByRole("textbox", {name: "Excluded folders", exact: true});
  await excluded.fill(".git, cache, generated");
  await excluded.press("Enter");
  await expect.poll(() => stored["workspace.excluded_folders"]).toEqual([".git", "cache", "generated"]);
  await page.getByRole("button", {name: "Projects", exact: true}).click();
  await page.getByRole("textbox", {name: "Search projects", exact: true}).fill("temperature");
  await expect(page.locator(".fx-project-list > button")).toHaveCount(1);
  await page.getByRole("textbox", {name: "Search projects", exact: true}).fill("unmatched");
  await expect(page.getByText("No projects match your search.", {exact: true})).toBeVisible();
  await page.getByRole("textbox", {name: "Search projects", exact: true}).fill("");
  await page.screenshot({path: "test-results/redesign-projects.png"});
});
test("conversation and compact live journal present real execution updates", async ({page}) => {
  await workspace(page);
  await page.screenshot({path: "test-results/redesign-chat.png"});
  await page.getByRole("button", {name: "Agent", exact: true}).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as {testStreams: Array<{url: string; closed: boolean}>}).testStreams.some(item => !item.closed && item.url.includes("/runs/r/events")))).toBe(true);
  await emit(page, 1, {id: "root", kind: "run", status: "running", title: "Task", startedAt: Date.now() - 12000});
  await emit(page, 2, {id: "reasoning", kind: "thinking", status: "completed", title: "Thinking", durationMs: 1900, details: {summary: "Checking the sensor configuration and keeping the existing pin assignments."}});
  await emit(page, 3, {id: "context", kind: "context", status: "completed", title: "Context", durationMs: 320, details: {summary: "Loaded the active project's firmware and build configuration."}});
  await emit(page, 4, {id: "read", kind: "file.read", status: "completed", title: "Read", durationMs: 48, details: {path: "src/main.cpp"}});
  await emit(page, 5, {id: "edit", kind: "file.edit", status: "completed", title: "Edit", durationMs: 180, details: {path: "src/main.cpp", summary: "Added bounded sensor sampling and serial diagnostics."}});
  await emit(page, 6, {id: "build", kind: "build", status: "running", title: "Build", details: {environment: "debug", command: "platformio run -e debug", output: "Processing debug (platform: espressif32; board: esp32dev)\nCompiling .pio/build/debug/src/main.cpp.o"}});
  await expect(page.getByRole("button", {name: /Building debug/})).toBeVisible();
  await page.screenshot({path: "test-results/redesign-agent.png"});
  await page.setViewportSize({width: 390, height: 844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.getByRole("combobox", {name: "Active project", exact: true})).toBeVisible();
  await expect(page.getByRole("button", {name: "Open workspace", exact: true})).toBeVisible();
  const model = await page.getByRole("button", {name: "Choose model", exact: true}).boundingBox();
  const stop = await page.locator(".fx-composer-controls").getByRole("button", {name: "Stop task", exact: true}).boundingBox();
  expect(stop!.y).toBeGreaterThanOrEqual(model!.y + model!.height);
  const project = await page.getByRole("combobox", {name: "Active project", exact: true}).boundingBox();
  expect(project!.width).toBeGreaterThan(100);
  await page.screenshot({path: "test-results/redesign-agent-narrow.png"});
  await emit(page, 7, {id: "root", kind: "run", status: "warning", title: "Task paused", durationMs: 14000, eventType: "runtime.paused_model_unavailable"});
  await page.route("**/api/forgex/agent-runtime/runs/r", route => route.fulfill({json: {run: {run_id: "r", session_id: "s", project_id: "p", status: "paused_model_unavailable", updated_at: new Date().toISOString(), events: [], plan: [], changed_files: []}}}));
  await emit(page, 8, {id: "root", kind: "run", status: "warning", title: "Task paused", durationMs: 14000, eventType: "runtime.paused_model_unavailable"});
  await expect(page.getByText("Build interrupted", {exact: true})).toBeVisible();
  await expect(page.getByText("Build passed", {exact: true})).toHaveCount(0);
});
