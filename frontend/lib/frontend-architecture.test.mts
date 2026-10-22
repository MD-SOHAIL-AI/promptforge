import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";

const removed = [
  "frontend/components/ide/forgex-shell.tsx",
  "frontend/components/ide/product-agent-panel.tsx",
  "frontend/components/layout/workspace-shell.tsx",
  "frontend/hooks/use-forgex-layout-state.ts",
  "frontend/lib/layout-state.ts",
];

test("Nexus is the only product shell", () => {
  for (const path of removed) assert.equal(existsSync(path), false, `${path} should stay retired`);
  assert.match(readFileSync("frontend/app/page.tsx", "utf8"), /NexusShell/);
});

test("model settings do not expose automatic provider fallback", () => {
  const source = readFileSync("frontend/components/ide/model-settings-panel.tsx", "utf8");
  assert.doesNotMatch(source, /Allow fallback|Automatic fallback|setFallbackEnabled/);
  assert.match(source, /Deterministic model execution/);
});

test("Nexus does not fake changed file count from a ChangeSet id", () => {
  const source = readFileSync("frontend/components/nexus/nexus-shell.tsx", "utf8");
  assert.doesNotMatch(source, /change_set_id\s*\?\s*1\s*:\s*0/);
});
