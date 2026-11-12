import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for the Phase-9 e2e suite (SPEC §12.4). The
 * frontend is fixture-backed on this branch, so e2e tests don't
 * need the Python backend — the Vite dev server serves the
 * fixtures and the hooks replay them.
 *
 * Phase-5 merge: flip `webServer.command` to `npm run dev`
 * (already correct), start the backend separately, and `tests
 * /e2e/*.spec.ts` becomes a true integration suite.
 *
 * Browsers aren't installed by `npm install`. Run
 *   `npx playwright install chromium`
 * once per clone. CI should cache `~/.cache/ms-playwright`.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  projects: [
    { name: "chromium", use: devices["Desktop Chrome"] },
  ],
});
