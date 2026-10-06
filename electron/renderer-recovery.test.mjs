import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";
import { attachRendererRecovery, RendererRecoveryCircuitBreaker } from "../dist/electron/renderer-recovery.js";

test("renderer reload attempts age out without resetting on successful loads", () => {
  const breaker = new RendererRecoveryCircuitBreaker();
  for (const now of [0, 1000, 2000]) assert.equal(breaker.register(now), true);
  assert.equal(breaker.register(3000), false);
  assert.equal(breaker.register(60001), true);
});

test("four repeated crashes show a stable recovery screen and manual navigation resets it", () => {
  const contents = new EventEmitter();
  const reports = [];
  let reloads = 0;
  let screen = "";
  Object.assign(contents, {getURL: () => "http://localhost:3012/", isDestroyed: () => false,
    reload: () => { reloads++; contents.emit("did-finish-load"); }, loadURL: async url => { screen = decodeURIComponent(url); }});
  attachRendererRecovery(contents, e => reports.push(e), () => 1000);
  contents.emit("did-finish-load");
  for (let i = 0; i < 8; i++) contents.emit("render-process-gone", {}, {reason: "crashed"});
  assert.equal(reloads, 3);
  assert.match(screen, /saved drafts remain available/);
  assert.equal(reports.filter(e => e === "desktop.renderer.recovery_blocked").length, 1);
  contents.emit("will-navigate", {}, "http://localhost:3012/");
  contents.emit("render-process-gone", {}, {reason: "crashed"});
  assert.equal(reloads, 4);
});

test("intentional termination does not reload the renderer", () => {
  const contents = new EventEmitter();
  Object.assign(contents, {isDestroyed: () => false, reload: () => assert.fail("unexpected reload")});
  attachRendererRecovery(contents, () => {});
  for (const reason of ["clean-exit", "killed"]) contents.emit("render-process-gone", {}, {reason});
});
