import { existsSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const candidate = path.join(root, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const python = process.env.FORGEX_PYTHON ?? (existsSync(candidate) ? candidate : "python");
const result = spawnSync(python, ["-B", path.join(root, "scripts/test-conversations.py"), ...process.argv.slice(2)], {
  cwd: root, env: process.env, stdio: "inherit", windowsHide: true, timeout: 780_000,
});
if (result.error) console.error(`Conversation test runner failed: ${result.error.message}`);
process.exit(result.status ?? 1);
