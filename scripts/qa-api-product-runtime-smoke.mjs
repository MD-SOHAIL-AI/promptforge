import { spawnSync } from "node:child_process";

const result = spawnSync("python", ["-m", "backend.agent_runtime.api_product_runtime_smoke", ...process.argv.slice(2)], {
  cwd: process.cwd(), encoding: "utf8", env: { ...process.env }, shell: false,
});
if (result.stdout) process.stdout.write(result.stdout);
if (result.stderr) process.stderr.write(result.stderr);
process.exit(result.status ?? 2);
