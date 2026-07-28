/**
 * Signing in through the real login screen.
 *
 * Not by injecting a token: the access token lives in a module closure and the
 * refresh token is an httpOnly cookie, so there is nothing a test could set from
 * the outside without reimplementing the session — and a session built by a
 * fixture is exactly the one that stops resembling production.
 *
 * The accounts are the seeded demo users (`app/scripts/seed.py`), which is what
 * `docs/PROGRESS.md` tells a human to log in with.
 */

import type { Page } from '@playwright/test';

import en from '../../src/locales/en/auth.json' with { type: 'json' };
import he from '../../src/locales/he/auth.json' with { type: 'json' };

export const DEMO_PASSWORD = 'KavimDemo2026!';
export const ADMIN_EMAIL = 'admin@kavim.example.com';
export const WORKER_EMAIL = 'worker1@kavim.example.com';

export async function signIn(page: Page, email: string, hebrew: boolean): Promise<void> {
  const t = hebrew ? he : en;

  await page.goto('/login');
  await page.getByLabel(new RegExp(`^${escapeForLabel(t.login.email)}`)).fill(email);
  await page.getByLabel(new RegExp(`^${escapeForLabel(t.login.password)}`)).fill(DEMO_PASSWORD);
  await page.getByRole('button', { name: t.login.submit }).click();
  await page.waitForURL(/\/$/);
}

/** `Field` renders "label *", so the matcher anchors at the start. */
function escapeForLabel(label: string): string {
  return label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}
