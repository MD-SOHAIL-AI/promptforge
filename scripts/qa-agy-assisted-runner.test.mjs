import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const command = path.join(import.meta.dirname, "qa-agy-assisted-runner.mjs");
const repo = path.resolve(import.meta.dirname, "..");

test("real AGY assisted runner refuses without explicit confirmation", () => {
  const result = spawnSync(process.execPath, [command, "--template", "esp32-platformio-blink"], {
    cwd: repo, encoding: "utf8", shell: false, timeout: 10_000,
    env: { ...process.env, FORGEX_ENABLE_AGY_ASSISTED_RUNNER: "1", FORGEX_ENABLE_AGY_SCRATCH_IMPORT: "1" },
  });
  assert.equal(result.status, 2);
  assert.match(result.stdout, /AGY_ASSISTED_UNSAFE_ABORTED/);
});

test("real AGY assisted runner refuses without feature flag", () => {
  const result = spawnSync(process.execPath, [command, "--confirm-real-agy", "--template", "esp32-platformio-blink"], {
    cwd: repo, encoding: "utf8", shell: false, timeout: 10_000,
    env: { ...process.env, FORGEX_ENABLE_AGY_ASSISTED_RUNNER: "0" },
  });
  assert.equal(result.status, 2);
  assert.match(result.stdout, /AGY_ASSISTED_UNSAFE_ABORTED/);
});

test("QA adapter launches only the internal runner with direct argv", () => {
  const source = fs.readFileSync(command, "utf8");
  assert.match(source, /spawnSync\("python"/);
  assert.match(source, /backend\.bridges\.agy_assisted_runner_cli/);
  assert.match(source, /shell: false/);
  assert.doesNotMatch(source, /shell: true|codex|opencode|claude|api-provider/i);
  assert.doesNotMatch(source, /console\.(?:log|error)\([^\n]*(?:stdout|stderr|instruction)/i);
});
