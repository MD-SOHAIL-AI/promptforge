import assert from "node:assert/strict";
import test from "node:test";

import { composerAutonomy } from "./forge-agent-autonomy.ts";

test("composer Auto sends backend-routed auto autonomy", () => {
  assert.equal(composerAutonomy(true), "auto");
});

test("Ask composer mode stays read-only", () => {
  assert.equal(composerAutonomy(false), "read_only");
});

assert.equal(composerAutonomy(true, true), "plan_only");
assert.equal(composerAutonomy(false, true), "plan_only");
