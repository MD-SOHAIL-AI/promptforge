import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const launcherSource = await readFile(new URL("./dev-desktop.mjs", import.meta.url), "utf8");

test("desktop launcher does not preemptively kill listeners on startup ports", () => {
  assert.equal(launcherSource.includes("clearStartupPorts"), false);
  assert.equal(launcherSource.includes("waitForPortFree"), false);
  assert.equal(launcherSource.includes("listenerPids"), false);
  assert.equal(launcherSource.includes("killPidTree"), false);
  assert.equal(launcherSource.includes("taskkill /PID ${child.pid}"), true);
  assert.equal(launcherSource.includes("taskkill\", [\"/PID\""), false);
  assert.equal(launcherSource.includes("netstat"), false);
  assert.equal(launcherSource.includes("lsof"), false);
});

test("desktop launcher remains import-safe for tests", async () => {
  const module = await import("./dev-desktop.mjs");
  assert.equal(typeof module.runDesktopLauncher, "function");
});

test("desktop launcher warns about missing PlatformIO without installing it", () => {
  assert.equal(launcherSource.includes("warnIfPlatformIOMissing"), true);
  assert.equal(launcherSource.includes("setup:platformio"), true);
  assert.equal(launcherSource.includes("pip install"), false);
});
