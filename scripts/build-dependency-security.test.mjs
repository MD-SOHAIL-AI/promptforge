import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";

const frontendRequire = createRequire(new URL("../frontend/package.json", import.meta.url));
const matchRequire = createRequire(frontendRequire.resolve("micromatch"));
const braces = matchRequire("braces");

test("installed glob consumers use the bounded, identified braces fork", () => {
  assert.equal(matchRequire("braces/package.json").name, "@forgex/braces");
  for (const consumer of ["fast-glob", "tailwindcss"]) {
    const consumerRequire = createRequire(frontendRequire.resolve(consumer));
    const transitiveRequire = createRequire(consumerRequire.resolve("micromatch"));
    assert.equal(transitiveRequire.resolve("braces"), matchRequire.resolve("braces"));
  }
  const watchRequire = createRequire(frontendRequire.resolve("chokidar"));
  assert.equal(watchRequire.resolve("braces"), matchRequire.resolve("braces"));
  const match = frontendRequire("micromatch");
  assert.deepEqual(match(["src/main.ts", "src/main.tsx", "src/main.py"], "src/*.{ts,tsx}"), ["src/main.ts", "src/main.tsx"]);
  assert.deepEqual(braces.expand("src/{agent,editor}/{1..3}.ts"), [
    "src/agent/1.ts", "src/agent/2.ts", "src/agent/3.ts",
    "src/editor/1.ts", "src/editor/2.ts", "src/editor/3.ts",
  ]);
  assert.equal(braces.compile("a/{b,c}/d"), "a/(b|c)/d");
  assert.equal(braces.stringify(braces.parse("a/{b,c}/d")), "a/{b,c}/d");
});

test("deep brace and parenthesis patterns fail with a controlled limit before AST walking", () => {
  for (const [open, close] of [["{", "}"], ["(", ")"]]) {
    const pattern = open.repeat(4000) + "a,b" + close.repeat(4000);
    for (const call of [braces, braces.create, braces.parse, braces.compile, braces.expand, braces.stringify]) {
      assert.throws(() => call(pattern, { maxDepth: Infinity }), error =>
        error instanceof SyntaxError && error.message.includes("maximum depth"));
    }
    assert.doesNotThrow(() => braces.compile(open.repeat(32) + "a,b" + close.repeat(32)));
  }
});

test("direct AST walkers bound externally constructed trees and child cycles", () => {
  const root = { type: "root", nodes: [] };
  let node = root;
  for (let index = 0; index < 10000; index++) {
    const child = { type: "root", nodes: [], parent: node };
    node.nodes.push(child);
    node = child;
  }
  const cyclic = { type: "root", nodes: [] };
  cyclic.nodes.push(cyclic);
  for (const ast of [root, cyclic]) {
    for (const call of [braces.compile, braces.expand, braces.stringify]) {
      assert.throws(() => call(ast), error => error instanceof SyntaxError && error.message.includes("maximum depth"));
    }
  }
});

test("Electron Builder loads the supported downloader API without the vulnerable cache chain", () => {
  const require = createRequire(import.meta.url);
  const builderRequire = createRequire(require.resolve("app-builder-lib"));
  const get = builderRequire("@electron/get");
  assert.equal(typeof get.downloadArtifact, "function");
  assert.equal(typeof get.ElectronDownloadCacheMode.ReadWrite, "number");
  assert.doesNotThrow(() => builderRequire("./util/electronGet.js"));
  assert.throws(() => builderRequire.resolve("http-cache-semantics"), { code: "MODULE_NOT_FOUND" });
});
