import test from "node:test";
import assert from "node:assert/strict";
import {BackendRecoveryPolicy} from "../dist/electron/backend-recovery.js";
test("backend restarts stop after three attempts and reset after the observation window", () => {
  const policy = new BackendRecoveryPolicy([2, 4, 8], 120);
  assert.equal(policy.nextDelay(0), 2);
  assert.equal(policy.nextDelay(1), 4);
  assert.equal(policy.nextDelay(2), 8);
  assert.equal(policy.nextDelay(3), null);
  assert.equal(policy.nextDelay(123), 2);
});
