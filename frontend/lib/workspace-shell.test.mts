import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const shellSource = readFileSync(new URL("../components/ide/forgex-shell.tsx", import.meta.url), "utf8");
const commandBarSource = readFileSync(new URL("../components/ide/top-command-bar.tsx", import.meta.url), "utf8");
const activitySource = readFileSync(new URL("../components/ide/activity-bar.tsx", import.meta.url), "utf8");
const agentSource = readFileSync(new URL("../components/ide/product-agent-panel.tsx", import.meta.url), "utf8");
const codingAgentSource = readFileSync(new URL("../components/ide/coding-workflow/UnifiedCodingWorkflowPanel.tsx", import.meta.url), "utf8");

test("workspace opening supports desktop and browser paths with visible progress", () => {
  assert.match(shellSource, /forgexDesktop\?\.openFolder/);
  assert.match(shellSource, /dialogs\.input\(\{/);
  assert.match(shellSource, /setIsOpeningWorkspace\(true\)/);
  assert.match(shellSource, /await workspace\.importProjectPath/);
  assert.match(commandBarSource, /Open workspace/);
  assert.doesNotMatch(commandBarSource, />Open Project</);
});

test("activity rail exposes only supported product surfaces", () => {
  assert.match(activitySource, /Forge agents/);
  assert.match(activitySource, /Devices/);
  assert.doesNotMatch(activitySource, /Source Control|Extensions|Memory|Libraries/);
});

test("agent surfaces expose ready providers and review-first coding controls", () => {
  assert.match(agentSource, /item\.routeable/);
  assert.match(agentSource, /No configured provider is ready/);
  assert.doesNotMatch(agentSource, /Legacy AGY compatibility tools/);
  assert.match(codingAgentSource, /Generate safe review/);
  assert.match(codingAgentSource, /Review first/);
  assert.match(codingAgentSource, /Configure a model provider/);
});
