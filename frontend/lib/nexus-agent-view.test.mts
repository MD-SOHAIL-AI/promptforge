import assert from "node:assert/strict";
import test from "node:test";

import { deriveNexusActivities, reasoningSummary, trustFacts } from "./nexus-agent-view.ts";

test("reasoning summary prefers safe model turn summary", () => {
  const value = reasoningSummary([
    { run_id: "r", sequence: 1, event_type: "agent.turn.completed", message: "The error is isolated to display initialization." },
  ], null, null);
  assert.equal(value, "The error is isolated to display initialization.");
});

test("inspection tools are grouped into a higher-level activity", () => {
  const events = [
    { run_id: "r", sequence: 1, event_type: "tool.completed", tool: "grep_search", message: "Search completed", status: "running" },
    { run_id: "r", sequence: 2, event_type: "tool.completed", tool: "read_file", message: "Read completed", status: "running" },
    { run_id: "r", sequence: 3, event_type: "tool.completed", tool: "read_file", message: "Read completed", status: "completed" },
  ];
  const items = deriveNexusActivities(events, null, null);
  assert.equal(items.some((item) => item.kind === "inspect"), true);
  assert.equal(items.some((item) => item.label.includes("Inspected project")), true);
});

test("awaiting flash adds a visible hardware approval activity", () => {
  const items = deriveNexusActivities([], null, { run_id: "r", status: "awaiting_flash_confirmation", flash_port: "COM5" } as never);
  assert.equal(items.at(-1)?.kind, "hardware");
  assert.equal(items.at(-1)?.label.includes("hardware approval"), true);
});

test("trust facts summarize workspace protection, risk, build, approval, and provider", () => {
  const facts = trustFacts({
    run_id: "r",
    provider_id: "openrouter",
    status: "awaiting_flash_confirmation",
    created_file_count: 1,
    modified_file_count: 0,
    deleted_file_count: 0,
    active_workspace_unchanged: true,
    tool_execution_count: 3,
    cancellable: false,
    created_at: "",
    updated_at: "",
    stage_statuses: { build: "completed" },
    approval_id: "approval-a",
  } as never, {
    run_id: "r",
    status: "awaiting_flash_confirmation",
    nodes: [{ risk_level: "medium" }],
  } as never);

  assert.equal(facts.some((fact) => fact.id === "workspace" && fact.value === "Protected"), true);
  assert.equal(facts.some((fact) => fact.id === "risk" && fact.value === "medium"), true);
  assert.equal(facts.some((fact) => fact.id === "approval"), true);
});
