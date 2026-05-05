/**
 * Playwright configuration — Wave 5-C (E2E).
 *
 * Targets:
 *   - frontend full-flow (login → schedule → DnD → allocate → diff → approve)
 *   - DnD performance smoke (FPS sampling on 50-visit grid)
 *   - iOS Safari (WebKit + iPhone 14 viewport) touch-DnD compatibility
 *
 * Process model: a single Next.js dev server is started by Playwright via
 * `webServer`. Tests assume the backend is reachable at NEXT_PUBLIC_API_BASE
 * (default http://localhost:8000) — start it manually or in CI before running.
 *
 * Parallelism: workers=1 because DnD interactions mutate shared backend state
 * and screenshots can interleave. Within a single worker, files run in the
 * order they are listed and tests within a file run sequentially.
 */
import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.PLAYWRIGHT_PORT ?? 3000);
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? `http://localhost:${PORT}`;

export default defineConfig({
  testDir: './e2e',
  // Per-action timeout (click / fill / waitFor selector). Keep short so flakes
  // surface fast; the spec-level timeout below absorbs page-load latency.
  expect: { timeout: 5_000 },
  // Spec-level timeout (covers all steps in one `test(...)` block).
  timeout: 90_000,
  // Hard cap on the whole run (CI safety net).
  globalTimeout: 30 * 60 * 1000,
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI
    ? [['list'], ['html', { outputFolder: 'e2e/.report', open: 'never' }]]
    : [['list']],

  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    // Only capture screenshots on failure to avoid bloating CI artifacts.
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 5_000,
    navigationTimeout: 15_000,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },
    {
      // iOS WebKit project for the touch-DnD spec (P-25).
      name: 'mobile-webkit',
      testMatch: /touch-dnd\.spec\.ts$/,
      use: { ...devices['iPhone 14'] },
    },
  ],

  webServer: process.env.PLAYWRIGHT_SKIP_WEBSERVER
    ? undefined
    : {
        command: 'pnpm dev',
        url: BASE_URL,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
        stdout: 'ignore',
        stderr: 'pipe',
      },

  outputDir: 'e2e/.artifacts',
});
