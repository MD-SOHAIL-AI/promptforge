import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { localDependencyProblems } from "./check-local-node-dependencies.mjs";

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "forgex local dependencies "));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const manifest = { dependencies: { example: "^1.0.0" } };
  const lock = { lockfileVersion: 3, packages: {
    "": manifest,
    "node_modules/example": { version: "1.2.0" },
    "node_modules/example/node_modules/child": { version: "2.0.0" },
  } };
  const write = (relative, value) => {
    const target = path.join(root, relative);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, JSON.stringify(value));
  };
  write("package.json", manifest);
  write("package-lock.json", lock);
  write("node_modules/example/package.json", { version: "1.2.0" });
  write("node_modules/example/node_modules/child/package.json", { version: "2.0.0" });
  return { root, manifest, lock, write };
}

test("a complete installed dependency graph is reusable without npm", t => {
  const { root } = fixture(t);
  assert.deepEqual(localDependencyProblems(root), []);
});

test("missing transitive dependencies and different locked versions require installation", t => {
  const { root, write } = fixture(t);
  fs.unlinkSync(path.join(root, "node_modules/example/node_modules/child/package.json"));
  write("node_modules/example/package.json", { version: "1.1.0" });
  const problems = localDependencyProblems(root);
  assert.ok(problems.some(value => value.includes("expected 1.2.0, found 1.1.0")));
  assert.ok(problems.some(value => value.includes("node_modules/example/node_modules/child")));
});

test("optional and incompatible-platform packages do not cause repeat installs", t => {
  const { root, lock, write } = fixture(t);
  lock.packages["node_modules/optional"] = { version: "1.0.0", optional: true };
  lock.packages["node_modules/linux-only"] = { version: "1.0.0", os: ["linux"], cpu: ["x64"] };
  lock.packages["node_modules/arm-only"] = { version: "1.0.0", cpu: ["arm64"] };
  lock.packages["node_modules/not-windows"] = { version: "1.0.0", os: ["!win32"] };
  write("package-lock.json", lock);
  assert.deepEqual(localDependencyProblems(root, "win32", "x64"), []);
});

test("changed requirements invalidate an otherwise complete local installation", t => {
  const { root, write } = fixture(t);
  write("package.json", { dependencies: { example: "^2.0.0" } });
  assert.ok(localDependencyProblems(root).some(value => value.includes("does not match package.json")));
});

test("linked local packages and their executable files are checked", t => {
  const { root, lock, write } = fixture(t);
  lock.packages["vendor/local"] = { version: "3.0.0" };
  lock.packages["node_modules/local"] = { link: true, resolved: "vendor/local" };
  write("package-lock.json", lock);
  write("node_modules/local/package.json", { version: "3.0.0", bin: "cli.js" });
  assert.ok(localDependencyProblems(root).some(value => value.includes("missing executable cli.js")));
  fs.writeFileSync(path.join(root, "node_modules/local/cli.js"), "");
  assert.ok(localDependencyProblems(root).some(value => value.includes("missing command shim local")));
  fs.mkdirSync(path.join(root, "node_modules/.bin"), { recursive: true });
  fs.writeFileSync(path.join(root, "node_modules/.bin/local" + (process.platform === "win32" ? ".cmd" : "")), "");
  assert.deepEqual(localDependencyProblems(root), []);
});

test("the installer never invokes npm when all packages already exist", { skip: process.platform !== "win32" }, t => {
  const { root } = fixture(t);
  const projectRoot = fileURLToPath(new URL("../", import.meta.url));
  const npmLog = path.join(root, "npm-was-invoked.txt");
  const fakeNpm = path.join(root, "npm.cmd");
  fs.writeFileSync(fakeNpm, '@echo off\r\necho called>>"%FORGEX_TEST_NPM_LOG%"\r\nexit /b 19\r\n');
  const result = spawnSync("powershell.exe", ["-NoProfile", "-Command", `
    $ErrorActionPreference = 'Stop'
    $projectRoot = $env:FORGEX_TEST_PROJECT
    $nodeExecutable = $env:FORGEX_TEST_NODE
    $npmExecutable = $env:FORGEX_TEST_NPM
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $projectRoot 'scripts/install-dependencies.ps1'), [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
    foreach ($definition in $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
      Invoke-Expression $definition.Extent.Text
    }
    Install-NodeDependencies $env:FORGEX_TEST_FIXTURE 'fixture'
    if (Test-Path -LiteralPath $env:FORGEX_TEST_NPM_LOG) { throw 'npm was called despite a complete local installation' }
  `], {
    encoding: "utf8", timeout: 30_000, windowsHide: true,
    env: { ...process.env, FORGEX_TEST_PROJECT: projectRoot, FORGEX_TEST_NODE: process.execPath,
      FORGEX_TEST_NPM: fakeNpm, FORGEX_TEST_NPM_LOG: npmLog, FORGEX_TEST_FIXTURE: root },
  });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  assert.match(result.stdout, /no download or reinstall/);
  assert.equal(fs.existsSync(npmLog), false);
});

test("Python reuses satisfied requirements and only installs after a local miss", { skip: process.platform !== "win32" }, t => {
  const { root } = fixture(t);
  const projectRoot = fileURLToPath(new URL("../", import.meta.url));
  const pythonLog = path.join(root, "python-calls.txt");
  const fakePython = path.join(root, "python.cmd");
  fs.writeFileSync(fakePython, [
    "@echo off", 'echo %*>>"%FORGEX_TEST_PYTHON_LOG%"',
    'if not "%FORGEX_TEST_PYTHON_MISSING%"=="1" exit /b 0',
    'for %%A in (%*) do if "%%~A"=="--no-index" goto missing',
    "exit /b 0", ":missing", "echo Expected missing dependency 1>&2", "exit /b 1", "",
  ].join("\r\n"));
  const result = spawnSync("powershell.exe", ["-NoProfile", "-Command", `
    $ErrorActionPreference = 'Stop'
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $env:FORGEX_TEST_PROJECT 'scripts/install-dependencies.ps1'), [ref]$tokens, [ref]$errors)
    foreach ($definition in $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
      Invoke-Expression $definition.Extent.Text
    }
    $env:FORGEX_TEST_PYTHON_MISSING = '0'
    Install-PythonDependencies $env:FORGEX_TEST_PYTHON (Join-Path $env:FORGEX_TEST_FIXTURE 'present.log')
    $env:FORGEX_TEST_PYTHON_MISSING = '1'
    Install-PythonDependencies $env:FORGEX_TEST_PYTHON (Join-Path $env:FORGEX_TEST_FIXTURE 'missing.log')
  `], {
    encoding: "utf8", timeout: 30_000, windowsHide: true,
    env: { ...process.env, FORGEX_TEST_PROJECT: projectRoot, FORGEX_TEST_PYTHON: fakePython,
      FORGEX_TEST_PYTHON_LOG: pythonLog, FORGEX_TEST_FIXTURE: root },
  });
  assert.equal(result.status, 0, result.stdout + result.stderr);
  const calls = fs.readFileSync(pythonLog, "utf8").trim().split(/\r?\n/);
  assert.equal(calls.length, 3);
  assert.match(calls[0], /--no-index/);
  assert.match(calls[1], /--no-index/);
  assert.doesNotMatch(calls[2], /--no-index/);
  assert.doesNotMatch(calls.join("\n"), /--upgrade|--force-reinstall|--ignore-installed/);
});
