import test from "node:test";
import assert from "node:assert/strict";
import { parseAgentSlashCommand, slashCommandSuggestions } from "./agent-slash-commands.ts";

test("slash command suggestions narrow by command prefix", () => {
  assert.deepEqual(slashCommandSuggestions("/rep").map((item) => item.name), ["repair"]);
  assert.equal(slashCommandSuggestions("normal prompt").length, 0);
});

test("subagent command requires a safe role and task", () => {
  const parsed = parseAgentSlashCommand("/subagent verify build the current firmware");
  assert.equal(parsed?.definition.name, "subagent");
  assert.throws(() => parseAgentSlashCommand("/subagent writer change files"), /explore\|review\|verify/);
});

test("unknown commands fail locally instead of spending a model request", () => {
  assert.throws(() => parseAgentSlashCommand("/does-not-exist"), /Unknown command/);
});
