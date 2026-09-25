import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const workspaceHook = readFileSync(join(process.cwd(), "frontend/hooks/use-promptforge-workspace.ts"), "utf8");

test("external project import is reserved for explicit open-folder action", () => {
  const importCalls = workspaceHook.match(/promptForgeApi\.importProject\(\{ path/g) ?? [];
  assert.equal(importCalls.length, 1);
  assert.match(workspaceHook, /importProjectInFlightRef/);
});

test("external project refresh uses project id instead of re-importing by path", () => {
  assert.match(workspaceHook, /promptForgeApi\.refreshProject\(activeProject\.project_id\)/);
});
