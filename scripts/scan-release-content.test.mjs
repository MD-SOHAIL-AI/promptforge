import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  classifyReleasePath,
  parseZipEntryNames,
  scanReleaseTarget,
} from "./scan-release-content.mjs";

function centralDirectoryOnlyZip(entryNames) {
  const entries = entryNames.map((entryName) => {
    const encodedName = Buffer.from(entryName, "utf8");
    const header = Buffer.alloc(46);
    header.writeUInt32LE(0x02014b50, 0);
    header.writeUInt16LE(20, 4);
    header.writeUInt16LE(20, 6);
    header.writeUInt16LE(0x0800, 8);
    header.writeUInt16LE(encodedName.length, 28);
    return Buffer.concat([header, encodedName]);
  });
  const centralDirectory = Buffer.concat(entries);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(entryNames.length, 8);
  end.writeUInt16LE(entryNames.length, 10);
  end.writeUInt32LE(centralDirectory.length, 12);
  end.writeUInt32LE(0, 16);
  return Buffer.concat([centralDirectory, end]);
}

async function withTemporaryDirectory(callback) {
  const directory = await mkdtemp(path.join(os.tmpdir(), "forgex-release-scan-"));
  try {
    return await callback(directory);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

test("classifies release paths case-insensitively and permits environment templates", () => {
  assert.equal(classifyReleasePath("Vendor/NODE_MODULES/pkg.js").code, "dependency_tree");
  assert.equal(classifyReleasePath("output/FIRMWARE.UF2").code, "firmware_output");
  assert.equal(classifyReleasePath("../outside.txt").code, "unsafe_path");
  assert.equal(classifyReleasePath(".env.example"), null);
  assert.equal(classifyReleasePath("workspace/artifacts/.gitkeep"), null);
  assert.equal(classifyReleasePath("backend/workspace/workspace_manager.py"), null);
  assert.equal(classifyReleasePath("frontend/src/workspace/view.tsx"), null);
});

test("a clean staging directory passes, including workspace placeholders", async () => {
  await withTemporaryDirectory(async (directory) => {
    await mkdir(path.join(directory, "src"), { recursive: true });
    await mkdir(path.join(directory, "workspace", "artifacts"), { recursive: true });
    await writeFile(path.join(directory, "src", "index.js"), "export {};\n");
    await writeFile(path.join(directory, ".env.example"), "KEY=replace-me\n");
    await writeFile(path.join(directory, "workspace", ".gitkeep"), "");
    await writeFile(path.join(directory, "workspace", "artifacts", ".gitkeep"), "");

    const report = await scanReleaseTarget(directory);
    assert.deepEqual(report.findings, []);
  });
});

test("a staging directory reports every prohibited content category", async () => {
  await withTemporaryDirectory(async (directory) => {
    const directories = ["node_modules", ".next", "dist", ".venv", ".pytest_cache", "hardware-signoff-reports", "workspace"];
    await Promise.all(directories.map((name) => mkdir(path.join(directory, name), { recursive: true })));
    await writeFile(path.join(directory, ".env"), "TOKEN=secret\n");
    await writeFile(path.join(directory, "workspace", "session.json"), "{}");
    await writeFile(path.join(directory, "firmware.bin"), "firmware");
    await writeFile(path.join(directory, "forgex-review.zip"), "review");

    const report = await scanReleaseTarget(directory);
    assert.deepEqual(
      new Set(report.findings.map((finding) => finding.code)),
      new Set([
        "dependency_tree",
        "frontend_build",
        "distribution_output",
        "release_evidence_output",
        "virtual_environment",
        "cache",
        "environment_file",
        "workspace_runtime",
        "firmware_output",
        "nested_review_archive",
      ]),
    );
  });
});

test("source-tree mode ignores local generated artifacts without weakening strict staging scans", async () => {
  await withTemporaryDirectory(async (directory) => {
    const localDirectories = [
      ".forgex/electron-user-data/Cache",
      ".pytest_cache",
      ".venv",
      "backend/__pycache__",
      "backend/workspace",
      "dist",
      "frontend/.next",
      "frontend/node_modules",
      "hardware-signoff-reports",
      "node_modules",
      "workspace",
    ];
    await Promise.all(localDirectories.map((name) => mkdir(path.join(directory, name), { recursive: true })));
    await writeFile(path.join(directory, ".env"), "TOKEN=secret\n");
    await writeFile(path.join(directory, "backend", "workspace", "workspace_manager.py"), "class WorkspaceManager: pass\n");
    await writeFile(path.join(directory, "workspace", "external-projects.json"), "[]");
    await writeFile(path.join(directory, "forgex-review.zip"), "review");

    const strictReport = await scanReleaseTarget(directory);
    assert.ok(strictReport.findings.length > 0);

    const sourceTreeReport = await scanReleaseTarget(directory, { sourceTreeMode: true });
    assert.deepEqual(sourceTreeReport.findings, []);
  });
});

test("ZIP scanning reads the central directory and rejects nested release debris", async () => {
  const names = ["README.md", "src/app.py", "node_modules/pkg.js", ".env.production", "nested/forgex-review.zip"];
  const archive = centralDirectoryOnlyZip(names);
  assert.deepEqual(parseZipEntryNames(archive), names);

  await withTemporaryDirectory(async (directory) => {
    const archivePath = path.join(directory, "release.zip");
    await writeFile(archivePath, archive);
    const report = await scanReleaseTarget(archivePath);
    assert.deepEqual(
      report.findings.map((finding) => finding.code),
      ["environment_file", "nested_review_archive", "dependency_tree"],
    );
  });
});

test("malformed archives fail closed", () => {
  assert.throws(() => parseZipEntryNames(Buffer.from("not a zip")), /valid ZIP file/);
});

test("local runtime state is excluded from source scans and rejected in release staging", async () => {
  await withTemporaryDirectory(async directory => {
    await mkdir(path.join(directory, ".promptforge", "state"), {recursive:true});
    await writeFile(path.join(directory, ".promptforge", "state", "runtime.json"), "{}");
    assert.deepEqual((await scanReleaseTarget(directory, {sourceTreeMode:true})).findings, []);
    assert.ok((await scanReleaseTarget(directory)).findings.some(finding => finding.code === "workspace_runtime"));
  });
});
