import assert from "node:assert/strict";
import test from "node:test";
import { LocalSessionState } from "./local-session.ts";

test("authentication loss settles once and requires explicit restoration", () => {
  const state = new LocalSessionState();
  const transitions: boolean[] = [];
  const unsubscribe = state.subscribe(() => transitions.push(state.getSnapshot()));
  state.reject();
  state.reject();
  assert.equal(state.getSnapshot(), true);
  assert.deepEqual(transitions, [true]);
  state.restore();
  assert.deepEqual(transitions, [true, false]);
  unsubscribe();
  state.reject();
  assert.deepEqual(transitions, [true, false]);
  assert.equal(state.getServerSnapshot(), false);
});
