import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:3012",
    channel: "msedge",
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npm run dev -- -p 3012",
    url: "http://127.0.0.1:3012",
    reuseExistingServer: true,
    timeout: 120000,
    env: { FORGEX_NEXT_DIST_DIR: ".next-design-preview" },
  },
});
