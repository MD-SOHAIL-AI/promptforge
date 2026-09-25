import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
const require = createRequire(new URL("../package.json", import.meta.url));
const ts = require("typescript");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
function load(path) {
  let filename = resolve(path);
  if (!/\.[cm]?[jt]sx?$/.test(filename)) {
    for (const extension of [".tsx", ".ts", ".jsx", ".js"]) {
      try {
        readFileSync(filename + extension, "utf8");
        filename += extension;
        break;
      } catch {}
    }
  }
  const source = ts.transpileModule(readFileSync(filename, "utf8"), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  const compiled = { exports: {} };
  new Function("require", "module", "exports", source)(
    (name) => {
      if (name.startsWith("@/")) return load(`frontend/${name.slice(2)}`);
      if (name.startsWith(".")) return load(resolve(dirname(filename), name));
      return require(name);
    },
    compiled,
    compiled.exports,
  );
  return compiled.exports;
}
const { ActivityStream } = load(
  "frontend/components/nexus/activity-stream.tsx",
);
function render(status, kind = "command") {
  const activity = {
    id: "a",
    runId: "r",
    kind,
    status,
    title: "Command",
    startedAt: 1000,
    durationMs: 2000,
    details: {},
  };
  return renderToStaticMarkup(
    React.createElement(ActivityStream, {
      events: [{ run_id: "r", sequence: 1, activity_record: activity }],
      run: null,
    }),
  );
}
test("failed row has semantic status, keyboard button and frozen duration", () => {
  const html = render("failed");
  assert.match(html, /Command failed/);
  assert.match(html, /aria-expanded="false"/);
  assert.match(html, /2s/);
  assert.match(html, /type="button"/);
});
test("approval, cancellation and unknown kind render safely", () => {
  assert.match(
    render("awaiting_approval", "approval"),
    /Waiting for approval/,
  );
  assert.match(render("completed", "approval"), /Operation approved/);
  assert.match(render("cancelled", "approval"), /Approval closed/);
  assert.match(render("completed", "future.type"), /Command/);
});
