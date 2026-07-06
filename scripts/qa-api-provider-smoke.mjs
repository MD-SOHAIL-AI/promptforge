import { spawnSync } from "node:child_process";

const result = spawnSync("python", ["-m", "backend.agent_runtime.api_provider_smoke", ...process.argv.slice(2)], {
  cwd: process.cwd(),
  encoding: "utf8",
  shell: false,
  env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" },
});

if (result.stdout) process.stdout.write(result.stdout);
if (result.stderr) process.stderr.write(result.stderr);
process.exit(result.status ?? 1);
