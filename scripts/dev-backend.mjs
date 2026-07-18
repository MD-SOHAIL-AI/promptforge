import { spawn } from "node:child_process";
import os from "node:os";
import path from "node:path";

const port = process.env.FORGEX_BACKEND_PORT ?? process.env.PROMPTFORGE_BACKEND_PORT ?? "8000";
const pythonExecutable = process.env.FORGEX_PYTHON ?? process.env.PYTHON ?? "python";
const dataRoot = process.env.PROMPTFORGE_DATA_ROOT
  ?? process.env.PROMPTFORGE_RUNTIME_ROOT
  ?? process.env.PROMPTFORGE_ROOT
  ?? path.join(process.env.LOCALAPPDATA ?? os.tmpdir(), "ForgeX", "desktop", "backend-data");

const child = spawn(
  pythonExecutable,
  [
    "-m",
    "uvicorn",
    "backend.api.app:create_app",
    "--factory",
    "--host",
    "127.0.0.1",
    "--port",
    port,
  ],
  {
    cwd: process.cwd(),
    env: {
      ...process.env,
      FORGEX_BACKEND_PORT: port,
      PROMPTFORGE_BACKEND_PORT: port,
      PROMPTFORGE_BACKEND_URL: `http://127.0.0.1:${port}`,
      PROMPTFORGE_DATA_ROOT: dataRoot,
    },
    stdio: "inherit",
    windowsHide: true,
  },
);

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});
