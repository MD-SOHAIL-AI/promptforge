import assert from "node:assert/strict";
import test from "node:test";

import { assertContained, packagedFlagMatrix, qaFixturesAllowed } from "./qa-packaged-helpers.mjs";

test("packaged QA cleanup accepts only descendants of the QA root", () => {
  assert.doesNotThrow(() => assertContained("C:\\repo\\.promptforge\\qa\\phase", "C:\\repo\\.promptforge\\qa"));
  assert.throws(() => assertContained("C:\\repo\\workspace", "C:\\repo\\.promptforge\\qa"), /outside/);
  assert.throws(() => assertContained("C:\\repo\\.promptforge\\qa", "C:\\repo\\.promptforge\\qa"), /outside/);
});

test("packaged QA fixtures require QA mode", () => {
  assert.equal(qaFixturesAllowed({}), false);
  assert.equal(qaFixturesAllowed({ FORGEX_QA_MODE: "0" }), false);
  assert.equal(qaFixturesAllowed({ FORGEX_QA_MODE: "1" }), true);
});

test("packaged feature matrix preserves both disabled production defaults", () => {
  assert.deepEqual(packagedFlagMatrix(), [
    { name: "defaults", patchApply: false, rollbackRestore: false },
    { name: "apply_only", patchApply: true, rollbackRestore: false },
    { name: "restore_only", patchApply: false, rollbackRestore: true },
    { name: "apply_and_restore", patchApply: true, rollbackRestore: true },
  ]);
});
