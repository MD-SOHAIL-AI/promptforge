import assert from "node:assert/strict";
import test from "node:test";

import { composerAutonomy } from "./forge-agent-autonomy.ts";

test("composer Auto sends backend-routed auto autonomy", () => {
  assert.equal(composerAutonomy(true), "auto");
});

test("manual composer mode keeps staged changes only", () => {
  assert.equal(composerAutonomy(false), "staged_changes");
});

assert.equal(composerAutonomy(true, true), "plan_only");
assert.equal(composerAutonomy(false, true), "plan_only");
