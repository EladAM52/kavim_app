import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end configuration.
 *
 * These tests exist for one reason the unit suite cannot cover: **layout**. The
 * 33 component tests assert behaviour and accessibility, and pass identically
 * whether the Hebrew UI renders right-to-left or collapses into a left-aligned
 * mess. A real browser is the only thing that knows the difference, and RTL is
 * this project's central risk (SPEC §10.3).
 *
 * Requires a running backend on :8000 with a seeded database. Playwright starts
 * the Vite dev server itself, but deliberately not the backend — a suite that
 * silently boots an API against whatever database happens to be configured is
 * how you destroy development data with a test run.
 */

const BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:5173';

export default defineConfig({
  testDir: './e2e',
  // The auth flow is inherently sequential — an invitation is consumed exactly
  // once — and the suite is small. Parallel workers here would buy seconds and
  // cost cross-test interference on shared rate-limit counters.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  // One retry in CI only. Locally a flake should be seen, not smoothed over.
  retries: process.env.CI ? 1 : 0,
  timeout: 30_000,
  expect: { timeout: 5_000 },

  reporter: process.env.CI
    ? [['github'], ['html', { open: 'never' }]]
    : [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: BASE_URL,
    // Traces and screenshots on first retry only: a failure nobody can reproduce
    // is barely better than no test, and a trace for every green run is noise.
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 10_000,
  },

  projects: [
    {
      // Hebrew is the primary locale, so it runs first and at a phone width —
      // the worker on the line is the primary user (SPEC §10.4).
      name: 'he-mobile',
      use: {
        ...devices['Pixel 7'],
        locale: 'he-IL',
        timezoneId: 'Asia/Jerusalem',
        extraHTTPHeaders: { 'Accept-Language': 'he-IL,he;q=0.9' },
      },
    },
    {
      name: 'en-desktop',
      use: {
        ...devices['Desktop Chrome'],
        locale: 'en-US',
        timezoneId: 'Asia/Jerusalem',
        extraHTTPHeaders: { 'Accept-Language': 'en-US,en;q=0.9' },
      },
    },
  ],

  webServer: {
    command: 'npm run dev',
    url: BASE_URL,
    // Reuse a dev server that is already up. Starting a second one would bind a
    // different port and every baseURL-relative navigation would miss it.
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
});
