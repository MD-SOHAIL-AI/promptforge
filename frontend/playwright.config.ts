import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45000,
  workers: 1,
  use: {
    baseURL: process.env.FORGEX_BROWSER_BASE_URL ?? "http://127.0.0.1:3012",
    channel: "msedge",
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
  },
  webServer: process.env.FORGEX_BROWSER_BASE_URL ? undefined : {
    command: "npm run dev -- -p 3012",
    env: { FORGEX_NEXT_DIST_DIR: ".next-e2e" },
    url: "http://127.0.0.1:3012",
    reuseExistingServer: false,
    timeout: 120000,
  },
});
