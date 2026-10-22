import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const venvDir = path.join(repoRoot, ".venv");
const isWindows = process.platform === "win32";
const venvPython = path.join(venvDir, isWindows ? "Scripts/python.exe" : "bin/python");
const venvPio = path.join(venvDir, isWindows ? "Scripts/pio.exe" : "bin/pio");

main();

function main() {
  if (!fs.existsSync(venvPython)) {
    const python = resolvePython();
    console.log(`[setup:platformio] Creating repo-local Python environment at ${venvDir}`);
    run(python.command, [...python.args, "-m", "venv", venvDir]);
  }

  console.log("[setup:platformio] Installing PlatformIO into .venv");
  run(venvPython, ["-m", "pip", "install", "--upgrade", "platformio==6.2.0"]);

  if (!fs.existsSync(venvPio)) {
    throw new Error(`PlatformIO install completed but executable was not found at ${venvPio}`);
  }

  console.log(`[setup:platformio] PlatformIO ready at ${venvPio}`);
  run(venvPio, ["--version"]);
}

function resolvePython() {
  if (process.env.PYTHON && process.env.PYTHON.trim()) {
    return { command: process.env.PYTHON.trim(), args: [] };
  }
  if (isWindows) {
    const py = spawnSync("py", ["-3", "--version"], { stdio: "ignore", windowsHide: true });
    if (py.status === 0) return { command: "py", args: ["-3"] };
  }
  for (const command of ["python3", "python"]) {
    const result = spawnSync(command, ["--version"], { stdio: "ignore", windowsHide: true });
    if (result.status === 0) return { command, args: [] };
  }
  throw new Error("Python 3 was not found. Install Python 3, then run `npm run setup:platformio` again.");
}

function run(command, args) {
  const result = spawnSync(command, args, {
    cwd: repoRoot,
    stdio: "inherit",
    windowsHide: true,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(" ")} failed with exit code ${result.status}`);
  }
}
