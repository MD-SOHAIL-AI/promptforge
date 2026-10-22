import { createHash } from "node:crypto";
import { lstat, readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

function containsPrivateKeyMaterial(relative, bytes) {
  let text = bytes.toString("latin1");
  // cryptography's OpenSSH parser declares a delimiter, without any key bytes.
  // Permit this exact constant once in that module; still scan everything else
  // in the file, including additional delimiters or any appended key material.
  if (relative === "python/Lib/site-packages/cryptography/hazmat/primitives/serialization/ssh.py") {
    text = text.replace(/^_SK_START = b"-----BEGIN OPENSSH PRIVATE KEY-----"\r?$/m, "");
  }
  return /-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----/.test(text);
}

export async function scanPackage(root) {
  root = path.resolve(root);
  const manifest = JSON.parse(await readFile(path.join(root, "release-manifest.json"), "utf8"));
  if (manifest.schemaVersion !== 1 || manifest.platform !== "win32-x64") throw new Error("RELEASE_MANIFEST_INVALID");
  const found = new Set();
  const errors = [];
  async function walk(directory) {
    for (const entry of await readdir(directory)) {
      const file = path.join(directory, entry);
      const relative = path.relative(root, file).replaceAll("\\", "/");
      const info = await lstat(file);
      if (info.isSymbolicLink()) { errors.push(`${relative}: unsafe link`); continue; }
      if (info.isDirectory()) {
        if ([".promptforge", ".forgex", ".pio", "__pycache__", ".git", ".venv"].includes(entry.toLowerCase()) || relative === "workspace") errors.push(`${relative}: runtime/developer data`);
        await walk(file);
        continue;
      }
      if (!info.isFile()) { errors.push(`${relative}: non-regular file`); continue; }
      if (relative === "release-manifest.json") continue;
      found.add(relative);
      const bytes = await readFile(file);
      const actual = createHash("sha256").update(bytes).digest("hex");
      if (manifest.files[relative] !== actual) errors.push(`${relative}: absent/changed manifest entry`);
      if (/^\.env(?:\.|$)/i.test(entry) || /\.(?:sqlite3?|db|pfx|p12|key|log|pyc)$/i.test(entry)) errors.push(`${relative}: private/runtime file`);
      if (containsPrivateKeyMaterial(relative, bytes)) errors.push(`${relative}: private key material`);
    }
  }
  await walk(root);
  for (const relative of Object.keys(manifest.files)) {
    if (relative.startsWith("/") || relative.includes("\\") || relative.split("/").includes("..") || !found.has(relative)) errors.push(`${relative}: missing/unsafe manifest file`);
  }
  for (const required of ["backend/api/app.py", "frontend/server.js", "python/python.exe", "dist/harness/server.js", "requirements-runtime.lock", "sbom-python.json", "sbom-desktop.json", "sbom-frontend.json"]) {
    if (!found.has(required)) errors.push(`${required}: required resource missing`);
  }
  return { passed: errors.length === 0, files: found.size, errors };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const root = process.argv[2] ?? path.join(JSON.parse(await readFile(".forgex/release-stage.json", "utf8")).stage, "forgex");
  const result = await scanPackage(root);
  console.log(JSON.stringify(result, null, 2));
  if (!result.passed) process.exitCode = 1;
}
