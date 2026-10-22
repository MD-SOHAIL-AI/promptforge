import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import vm from "node:vm";
import test from "node:test";
import ts from "typescript";

const require = createRequire(import.meta.url);
const code = ts.transpileModule(readFileSync(new URL("./paths.ts", import.meta.url), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;

test("desktop port configuration rejects partial numbers for every service", () => {
  for (const [variable, method] of [
    ["FORGEX_BACKEND_PORT", "resolveBackendPort"],
    ["FORGEX_FRONTEND_PORT", "resolveFrontendPort"],
    ["FORGEX_HARNESS_PORT", "resolveHarnessPort"],
  ]) {
    const env = {};
    const context = { exports: {}, process: { env }, require: id => id === "electron" ? { app: {} } : require(id) };
    vm.runInNewContext(code, context);
    for (const value of ["8000junk", "8e3", "8000.5", "", "0", "-1", "65536"]) {
      env[variable] = value;
      assert.throws(() => context.exports[method](), /Invalid .* port/);
    }
    for (const value of ["1", "8000", "65535"]) {
      env[variable] = value;
      assert.equal(context.exports[method](), Number(value));
    }
  }
});
