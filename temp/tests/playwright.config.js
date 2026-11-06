import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  workers: 1,
  outputDir: "../../work/test-results",
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:43817",
    headless: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "node temp/tests/e2e/server.mjs",
    cwd: "../..",
    url: "http://127.0.0.1:43817/",
    reuseExistingServer: false,
    timeout: 15_000,
  },
});
