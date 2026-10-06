import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { spawn } from "node:child_process";
import test from "node:test";

import { createDesktopLogger } from "../dist/electron/desktop-logging.js";

function fixture(output = {}) {
  const stdout = new EventEmitter();
  const stderr = new EventEmitter();
  const calls = [];
  const logger = createDesktopLogger({ stdout, stderr, console: {
    log: (...data) => calls.push(["log", ...data]),
    warn: (...data) => calls.push(["warn", ...data]),
    error: (...data) => calls.push(["error", ...data]),
    ...output,
  } });
  return { stdout, stderr, calls, logger };
}

test("desktop logging preserves normal output and arguments", () => {
  const { logger, calls } = fixture();
  const detail = { state: "ready" };
  logger.log("backend", detail);
  logger.warn("warning");
  logger.error("failure");
  assert.deepEqual(calls, [["log", "backend", detail], ["warn", "warning"], ["error", "failure"]]);
});

test("synchronous broken pipes disable only the affected logging sink", () => {
  let writes = 0;
  const { logger, calls } = fixture({ log() {
    writes += 1;
    throw Object.assign(new Error("broken pipe"), { code: "EPIPE" });
  } });
  assert.doesNotThrow(() => logger.log("startup"));
  logger.log("backend ready");
  logger.error("stderr still works");
  assert.equal(writes, 1);
  assert.deepEqual(calls, [["error", "stderr still works"]]);
});

test("asynchronous broken pipes stop stdout and stderr independently", () => {
  const { logger, calls, stdout, stderr } = fixture();
  stdout.emit("error", Object.assign(new Error("pipe closed"), { code: "EPIPE" }));
  logger.log("ignored");
  logger.warn("stderr open");
  stderr.emit("error", Object.assign(new Error("pipe closed"), { code: "EPIPE" }));
  logger.warn("ignored");
  logger.error("ignored");
  assert.deepEqual(calls, [["warn", "stderr open"]]);
});

test("destroyed streams are not written and other logging errors remain visible", () => {
  const { logger, stdout, calls } = fixture();
  stdout.destroyed = true;
  logger.log("ignored");
  assert.deepEqual(calls, []);
  const otherError = Object.assign(new Error("I/O failure"), { code: "EIO" });
  const broken = fixture({ error() { throw otherError; } });
  assert.throws(() => broken.logger.error("failure"), (error) => error === otherError);
  assert.throws(() => broken.stderr.emit("error", otherError), (error) => error === otherError);
});

test("desktop logging survives real stdout and stderr pipes closed by its parent", { timeout: 10000 }, async () => {
  const moduleUrl = new URL("../dist/electron/desktop-logging.js", import.meta.url).href;
  const code = `
    import { desktopLog } from ${JSON.stringify(moduleUrl)};
    process.on('message', () => {
      desktopLog.log('backend ready');
      desktopLog.warn('backend warning');
      desktopLog.error('backend output');
      setTimeout(() => {
        desktopLog.log('still running');
        desktopLog.error('still running');
        process.send('alive', () => process.exit(0));
      }, 100);
    });
    process.send('ready');
  `;
  const child = spawn(process.execPath, ["--input-type=module", "-e", code], {
    stdio: ["ignore", "pipe", "pipe", "ipc"], windowsHide: true,
  });
  let alive = false;
  try {
    await new Promise((resolve, reject) => {
      child.on("error", reject);
      child.on("message", (message) => {
        if (message === "ready") {
          child.stdout.destroy();
          child.stderr.destroy();
          child.send("write");
        } else if (message === "alive") {
          alive = true;
        }
      });
      child.on("exit", (code, signal) => {
        try {
          assert.equal(signal, null);
          assert.equal(code, 0);
          assert.equal(alive, true);
          resolve();
        } catch (error) { reject(error); }
      });
    });
  } finally {
    if (child.exitCode === null) child.kill();
  }
});
