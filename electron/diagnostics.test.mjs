import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs/promises";
import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";

test("desktop and backend instances rotate independently and preserve safe usage counters", { timeout: 30000 }, async () => {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), "forgex-log-concurrency-"));
  const base = path.join(temporary, "live-runtime.jsonl");
  const env = { ...process.env, FORGEX_LIVE_LOG_PATH: base, FORGEX_LIVE_LOG_MAX_BYTES: "65536", FORGEX_VERSION: "0.1.1" };
  const moduleUrl = pathToFileURL(path.resolve("dist/electron/diagnostics.js")).href;
  const desktopCode = `
    import { emitDiagnosticEvent } from ${JSON.stringify(moduleUrl)};
    for (let index = 0; index < 80; index++) emitDiagnosticEvent({component:'test', event:'record', message:'wifi_password=private auth_token=private',
      fields:{index, padding:'x'.repeat(2000), api_token:'private', input_tokens:12,
        usage:{output_tokens:7, total_tokens:'private'}, app_version:'spoof'}});
  `;
  const backendCode = "from backend.observability.diagnostics import emit_diagnostic_event\nfor index in range(80): emit_diagnostic_event(component='test',event='record',message='wifi_password=private auth_token=private',index=index,padding='x'*2000,api_token='private',input_tokens=12,usage={'output_tokens':7,'total_tokens':'private'},app_version='spoof')";
  const virtualPython = path.resolve(process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
  const python = process.env.FORGEX_PYTHON || (existsSync(virtualPython) ? virtualPython : process.platform === "win32" ? "python" : "python3");
  const children = [];
  function run(command, args) {
    return new Promise((resolve, reject) => {
      const child = spawn(command, args, { env, windowsHide: true, stdio: ["ignore", "ignore", "pipe"] });
      children.push(child);
      let error = "";
      child.stderr.on("data", data => { error += data; });
      child.on("error", reject);
      child.on("close", code => code === 0 ? resolve() : reject(new Error(`Log process exited ${code}: ${error}`)));
    });
  }
  try {
    await Promise.all([run(process.execPath, ["--input-type=module", "-e", desktopCode]),
      run(process.execPath, ["--input-type=module", "-e", desktopCode]), run(python, ["-c", backendCode])]);
    const names = await fs.readdir(temporary);
    const current = names.filter(name => name.endsWith(".jsonl"));
    assert.equal(current.length, 3);
    assert.equal(current.filter(name => name.includes(".desktop-")).length, 2);
    assert.equal(current.filter(name => name.includes(".backend-")).length, 1);
    for (const name of current) {
      for (const suffix of ["", ".1"]) {
        const records = (await fs.readFile(path.join(temporary, name + suffix), "utf8")).trim().split("\n").map(JSON.parse);
        assert.ok(records.length > 0);
        for (const record of records) {
          assert.equal(record.api_token, "[redacted]");
          assert.equal(record.input_tokens, 12);
          assert.deepEqual(record.usage, { output_tokens: 7, total_tokens: "[redacted]" });
          assert.equal(record.app_version, "0.1.1");
          assert.equal(record.message, "wifi_password=[redacted] auth_token=[redacted]");
        }
        if (!suffix) assert.equal(records.at(-1).index, 79);
      }
    }
  } finally {
    for (const child of children) if (child.exitCode === null) child.kill();
    await fs.rm(temporary, { recursive: true, force: true });
  }
});
