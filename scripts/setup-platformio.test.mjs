import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const packageJson = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
const scriptSource = await readFile(new URL("./setup-platformio.mjs", import.meta.url), "utf8");

test("setup:platformio is wired to the repo-local installer", () => {
  assert.equal(packageJson.scripts["setup:platformio"], "node scripts/setup-platformio.mjs");
});

test("PlatformIO setup installs into the project virtual environment", () => {
  assert.match(scriptSource, /"\.venv"/);
  assert.match(scriptSource, /"platformio==6\.2\.0"/);
  assert.equal(scriptSource.includes("npm install"), false);
});
