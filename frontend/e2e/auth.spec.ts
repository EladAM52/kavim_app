import type { Page } from '@playwright/test';
import { expect, test } from '@playwright/test';

// `with { type: 'json' }` is required: Playwright loads specs as real ESM, where
// a bare JSON import is a TypeError rather than a bundler convenience.
import en from '../src/locales/en/auth.json' with { type: 'json' };
import he from '../src/locales/he/auth.json' with { type: 'json' };
import { TEST_PASSWORD, createInvitation, latestOtp, uniqueEmail } from './support/backend';

/**
 * The auth flow, in a real browser, in both locales (SPEC §11.3 scenario 1).
 *
 * The component suite already asserts that these screens behave. What it cannot
 * see is how they *look*: every one of those 33 tests passes whether the Hebrew
 * UI renders right-to-left or collapses to the left. That gap is the reason this
 * file exists.
 *
 * Copy is imported from the app's own locale files rather than hard-coded. A
 * wording change then does not break the test — but a **missing translation**
 * does, which is the failure worth catching.
 */

/**
 * A field by its label, tolerating the required marker.
 *
 * `Field` renders `<label>סיסמה *</label>`, so an exact match misses it. Plain
 * substring matching is worse: "סיסמה" is contained in "אימות סיסמה", so it
 * would resolve to two elements and pick the wrong one. Anchoring at the start
 * distinguishes them.
 */
function field(page: Page, label: string) {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return page.getByLabel(new RegExp(`^${escaped}`));
}

// The locale a project runs under, derived from its name so the two stay in step.
function stringsFor(projectName: string): { t: typeof he; dir: 'rtl' | 'ltr'; lang: string } {
  return projectName.startsWith('he')
    ? { t: he, dir: 'rtl', lang: 'he' }
    : { t: en, dir: 'ltr', lang: 'en' };
}

test.describe('invitation → OTP → register → login', () => {
  test('a worker can accept an invitation and reach the app', async ({ page }, testInfo) => {
    const { t, dir, lang } = stringsFor(testInfo.project.name);
    const email = uniqueEmail('e2e');
    const invitation = await createInvitation(email);

    // ── 1. the landing screen ──────────────────────────────────────────────
    await page.goto(`/invite/${invitation.token}`);

    await expect(page.getByRole('heading', { name: t.invitation.title })).toBeVisible();
    // The address is shown, never offered as an input — the API takes it from the
    // invitation and rejects a submitted one.
    await expect(page.getByText(email)).toBeVisible();
    await expect(page.locator('input[name="email"]')).toHaveCount(0);

    // The document direction must be settled before first paint, not corrected
    // after it — a visible flip on load is the thing `<html dir>` exists to avoid.
    await expect(page.locator('html')).toHaveAttribute('dir', dir);
    await expect(page.locator('html')).toHaveAttribute('lang', lang);

    await page.getByRole('button', { name: t.invitation.start }).click();

    // ── 2. the code ────────────────────────────────────────────────────────
    await expect(page.getByRole('heading', { name: t.otp.title })).toBeVisible();

    const codeInput = field(page, t.otp.code);
    // A six-digit code rendered RTL reads back in the wrong order, so the user
    // types what they see and it is wrong.
    await expect(codeInput).toHaveAttribute('dir', 'ltr');

    const code = await latestOtp(email);
    expect(code).toMatch(/^\d{6}$/);
    await codeInput.fill(code);
    await page.getByRole('button', { name: t.otp.submit }).click();

    // ── 3. registration ────────────────────────────────────────────────────
    await expect(page.getByRole('heading', { name: t.register.title })).toBeVisible();
    // Still no email field: the address comes from the invitation the ticket names.
    await expect(page.locator('input[type="email"]')).toHaveCount(0);

    await field(page, t.register.fullName).fill('עובד בדיקה');
    await field(page, t.register.password).fill(TEST_PASSWORD);
    await field(page, t.register.passwordConfirm).fill(TEST_PASSWORD);
    await page.getByRole('button', { name: t.register.submit }).click();

    // ── 4. signed in ───────────────────────────────────────────────────────
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole('heading', { name: t.invitation.title })).toHaveCount(0);

    // ── 5. the session survives a reload ───────────────────────────────────
    // The access token lives in a module closure and dies with the page. Only the
    // httpOnly refresh cookie can bring it back, so a reload that keeps the user
    // signed in is the one proof that the boot refresh works.
    await page.reload();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole('heading', { name: t.login.title })).toHaveCount(0);

    // ── 6. and the account works from the login screen ─────────────────────
    await page.context().clearCookies();
    await page.goto('/login');
    await expect(page.getByRole('heading', { name: t.login.title })).toBeVisible();

    await field(page, t.login.email).fill(email);
    await field(page, t.login.password).fill(TEST_PASSWORD);
    await page.getByRole('button', { name: t.login.submit }).click();

    await expect(page).toHaveURL(/\/$/);
  });

  test('a spent invitation says so rather than failing silently', async ({ page }, testInfo) => {
    const { t } = stringsFor(testInfo.project.name);
    const email = uniqueEmail('e2e-spent');
    const first = await createInvitation(email);
    // Re-inviting supersedes the first link (FR-111).
    await createInvitation(email);

    await page.goto(`/invite/${first.token}`);

    await expect(page.getByText(t.invitation.expired)).toBeVisible();
  });

  test('a nonsense token is a clear message, not a blank screen', async ({ page }, testInfo) => {
    const { t } = stringsFor(testInfo.project.name);

    await page.goto('/invite/definitely-not-a-real-token-000000000000');

    await expect(page.getByText(t.invitation.notFound)).toBeVisible();
  });
});

test.describe('login', () => {
  test('a wrong password shows translated copy, never the server English', async ({
    page,
  }, testInfo) => {
    const { t, lang } = stringsFor(testInfo.project.name);

    await page.goto('/login');
    await field(page, t.login.email).fill('admin@kavim.example.com');
    await field(page, t.login.password).fill('definitely-wrong');
    await page.getByRole('button', { name: t.login.submit }).click();

    const alert = page.getByRole('alert');
    await expect(alert).toContainText(t.login.invalid);

    if (lang === 'he') {
      // Only checkable in Hebrew. The English translation reads "Email or
      // password is incorrect", so a leak of the backend's own wording is
      // indistinguishable from correct output — the two agree by coincidence.
      // In Hebrew any Latin text in this alert means the client rendered
      // `problem.detail` instead of looking the code up in `errors`.
      await expect(alert).not.toContainText(/[A-Za-z]{4,}/);
    }
  });

  test('credentials read left-to-right inside the form', async ({ page }, testInfo) => {
    const { t } = stringsFor(testInfo.project.name);

    await page.goto('/login');

    await expect(field(page, t.login.email)).toHaveAttribute('dir', 'ltr');
    await expect(field(page, t.login.password)).toHaveAttribute('dir', 'ltr');
  });

  test('an unauthenticated visit to the app is sent to login', async ({ page }, testInfo) => {
    const { t } = stringsFor(testInfo.project.name);

    await page.goto('/');

    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole('heading', { name: t.login.title })).toBeVisible();
  });
});

test.describe('layout', () => {
  test('nothing overflows the viewport and touch targets stay reachable', async ({
    page,
  }, testInfo) => {
    const { t } = stringsFor(testInfo.project.name);
    // 320px is the narrowest width in the NFR-04 matrix — an older phone held by
    // a worker in gloves, which is the actual deployment.
    await page.setViewportSize({ width: 320, height: 640 });
    await page.goto('/login');
    await expect(page.getByRole('heading', { name: t.login.title })).toBeVisible();

    // A horizontal scrollbar on a login form is the classic RTL regression: one
    // physical margin escapes the ESLint rule and the layout pushes sideways.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);

    // 44px is the token in `@theme`, and the reason is gloves.
    const submit = page.getByRole('button', { name: t.login.submit });
    const box = await submit.boundingBox();
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
  });
});
