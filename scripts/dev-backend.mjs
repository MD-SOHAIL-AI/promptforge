import { spawn } from "node:child_process";
import path from "node:path";

const port = process.env.FORGEX_BACKEND_PORT ?? "8000";
const pythonExecutable = process.env.FORGEX_PYTHON ?? process.env.PYTHON ?? "python";
const settingsPath = process.env.FORGEX_SETTINGS_PATH ?? path.join(process.cwd(), ".promptforge", "settings", "settings.json");

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
      FORGEX_BACKEND_URL: `http://127.0.0.1:${port}`,
      FORGEX_SETTINGS_PATH: settingsPath,
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
