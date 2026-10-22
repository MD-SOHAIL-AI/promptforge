import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { scanPackage } from "./scan-packaged-content.mjs";

async function fixture(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), "forgex-package-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const files = {};
  for (const name of ["backend/api/app.py", "backend/workspace/workspace_manager.py", "frontend/server.js", "frontend/.next/static/app.js", "node_modules/next/server.js", "python/python.exe", "dist/harness/server.js", "requirements-runtime.lock", "sbom-python.json", "sbom-desktop.json", "sbom-frontend.json"]) {
    const file = path.join(root, name);
    await mkdir(path.dirname(file), { recursive: true });
    await writeFile(file, "trusted runtime");
    files[name] = createHash("sha256").update("trusted runtime").digest("hex");
  }
  await writeFile(path.join(root, "release-manifest.json"), JSON.stringify({ schemaVersion: 1, platform: "win32-x64", files }));
  return root;
}
test("packaged scanner permits declared bundled runtime resources", async t => {
  assert.equal((await scanPackage(await fixture(t))).passed, true);
});
test("packaged scanner rejects added files and modified runtime", async t => {
  const root = await fixture(t);
  await writeFile(path.join(root, ".env"), "PRIVATE_TOKEN=example");
  await writeFile(path.join(root, "frontend/server.js"), "modified");
  const result = await scanPackage(root);
  assert.equal(result.passed, false);
  assert.ok(result.errors.some(error => error.includes(".env")));
  assert.ok(result.errors.some(error => error.includes("frontend/server.js")));
});

async function declareFile(root, name, contents) {
  const file = path.join(root, name);
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, contents);
  const manifestPath = path.join(root, "release-manifest.json");
  const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
  manifest.files[name] = createHash("sha256").update(contents).digest("hex");
  await writeFile(manifestPath, JSON.stringify(manifest));
}

test("packaged scanner allows only the known OpenSSH parser delimiter", async t => {
  const root = await fixture(t);
  const name = "python/Lib/site-packages/cryptography/hazmat/primitives/serialization/ssh.py";
  const constant = '_SK_START = b"-----BEGIN OPENSSH PRIVATE KEY-----"\r\n';
  await declareFile(root, name, constant);
  assert.equal((await scanPackage(root)).passed, true);
  await declareFile(root, name, constant + "-----BEGIN OPENSSH PRIVATE KEY-----\nexample-key-material\n-----END OPENSSH PRIVATE KEY-----\n");
  assert.ok((await scanPackage(root)).errors.includes(`${name}: private key material`));
});

test("packaged scanner rejects private key markers outside the exact parser constant", async t => {
  const root = await fixture(t);
  for (const kind of ["", "RSA ", "EC ", "ENCRYPTED ", "OPENSSH "]) {
    await declareFile(root, "backend/private.py", `value = "-----BEGIN ${kind}PRIVATE KEY-----"`);
    assert.ok((await scanPackage(root)).errors.includes("backend/private.py: private key material"));
  }
});
