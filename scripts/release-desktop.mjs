import { spawn } from "node:child_process";
import { readFile, writeFile, mkdir, unlink } from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";
import { scanPackage } from "./scan-packaged-content.mjs";
import { createReleaseShell } from "./release-shell.mjs";

const testOnly = process.argv.includes("--unsigned-test");
if (process.platform !== "win32" || process.arch !== "x64") throw new Error("Desktop release requires Windows x64");
if (!testOnly && !process.env.CSC_LINK) throw new Error("RELEASE_SIGNING_CERTIFICATE_REQUIRED: configure CSC_LINK and CSC_KEY_PASSWORD");
async function run(command, args, env = process.env) {
  await new Promise((resolve, reject) => {
    const child = spawn(command, args, { env, stdio: "inherit", windowsHide: true });
    child.on("error", reject);
    child.on("close", code => code === 0 ? resolve() : reject(new Error(`${command} exited ${code}`)));
  });
}
const npm = process.env.npm_execpath;
if (!npm) throw new Error("Run via npm run package:desktop");
const version = JSON.parse(await readFile("package.json", "utf8")).version;
if (!/^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$/.test(version)) throw new Error("RELEASE_VERSION_INVALID");
await run(process.execPath, [npm, "run", "test:release-scripts"]);
await run(process.execPath, [npm, "run", "verify:beta"]);
await run(process.execPath, [npm, "audit", "--audit-level=moderate"]);
await run(process.execPath, [npm, "audit", "--prefix", "frontend", "--audit-level=moderate"]);
const python = process.env.FORGEX_RELEASE_PYTHON || "python";
const auditPython = process.env.FORGEX_AUDIT_PYTHON || python;
await run(auditPython, ["-m", "pip_audit", "-r", "requirements-runtime.lock", "--disable-pip", "--no-deps"]);
await run(process.execPath, [npm, "run", "build:frontend"]);
await run(python, ["scripts/prepare-release.py"]);
const stage = JSON.parse(await readFile(".forgex/release-stage.json", "utf8")).stage;
const scan = await scanPackage(path.join(stage, "forgex"));
if (!scan.passed) throw new Error(`PACKAGED_CONTENT_REJECTED: ${scan.errors.join("; ")}`);
await run(python, ["scripts/smoke-packaged.py", "--root", path.join(stage, "forgex")]);
await run(path.join(stage, "forgex/python/python.exe"), ["-B", "scripts/smoke-live-log-reliability.py",
  "--bundle", path.join(stage, "forgex"), "--output", ".forgex/reliability-package-smoke/staged"]);
const shellDir = await createReleaseShell();
// Electron Builder trusts intermediate archive timestamps. An interrupted
// compressor can leave a newer, incomplete archive; always generate it anew.
try {
  await unlink(path.resolve(`.forgex/installers/forgex-desktop-${version}-x64.nsis.7z`));
} catch (error) {
  if (error.code !== "ENOENT") throw error;
}
await run(process.execPath, ["node_modules/electron-builder/out/cli/cli.js", "--win", "--x64", "--config", "electron-builder.config.cjs",
  `--config.directories.app=${shellDir}`],
  { ...process.env, FORGEX_UNSIGNED_TEST_BUILD: testOnly ? "1" : "0" });
const packagedRoot = path.resolve(".forgex/installers/win-unpacked/resources/forgex");
const packagedScan = await scanPackage(packagedRoot);
if (!packagedScan.passed) throw new Error(`FINAL_PACKAGE_REJECTED: ${packagedScan.errors.join("; ")}`);
await run(python, ["scripts/smoke-packaged.py", "--root", packagedRoot]);
await run(path.join(packagedRoot, "python/python.exe"), ["-B", "scripts/smoke-live-log-reliability.py",
  "--bundle", packagedRoot, "--output", ".forgex/reliability-package-smoke/final"]);
await run(python, ["scripts/smoke-desktop.py"]);
await run(python, ["scripts/smoke-desktop.py", "--closed-output-pipes", "--screenshot", "test-results/packaged-desktop-closed-pipes.png"]);
const installer = path.resolve(`.forgex/installers/ForgeX-${version}-windows-x64-setup.exe`);
if (!testOnly) {
  await run("powershell.exe", ["-NoProfile", "-File", "scripts/verify-installer-signature.ps1", "-Installer", installer]);
}
const sha256 = createHash("sha256").update(await readFile(installer)).digest("hex");
const resourceManifestSha256 = createHash("sha256").update(await readFile(path.join(packagedRoot, "release-manifest.json"))).digest("hex");
await mkdir(".forgex/installers", { recursive: true });
await writeFile(installer + ".evidence.json", JSON.stringify({ schemaVersion: 1, version, sha256, resourceManifestSha256, signed: !testOnly, testOnly, packagedFiles: scan.files, hardwareSignoff: "required", cleanMachineSignoff: "required", agentPromptQualification: "required", agentQualityQualification:"required", maintenanceQualification:"required" }, null, 2));
console.log(`${testOnly ? "UNSIGNED TEST BUILD" : "SIGNED BUILD"}: ${installer}; distribution requires hardware and clean-machine sign-off.`);
