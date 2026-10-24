import { defineConfig, devices } from "@playwright/test";

// Browser tests of the built app (frontend/e2e). They need the API serving the built
// frontend with demo data (CLAUDE.md → «Команды»; CI — the «ui» job):
//   DEMO_PASSWORD=… npx playwright test
export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8000",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
