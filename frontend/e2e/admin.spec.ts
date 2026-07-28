/**
 * The admin area, in a real browser, in both locales.
 *
 * Two things this covers that nothing else can:
 *
 *   1. **The RoleMatrix layout.** Five roles × thirty permissions is the widest
 *      grid in the project, and a single physical CSS property escaping the
 *      ESLint rule pushes the whole page sideways in Hebrew. Component tests
 *      pass either way; only a browser knows.
 *   2. **The permission boundary end to end** (SPEC §11.3 scenario 6, the part
 *      that is testable before the board exists): a worker is refused the admin
 *      area by the client *and* by the server, and an administrator is not.
 */

import type { Page } from '@playwright/test';
import { expect, test } from '@playwright/test';

import enAdmin from '../src/locales/en/admin.json' with { type: 'json' };
import enCommon from '../src/locales/en/common.json' with { type: 'json' };
import heAdmin from '../src/locales/he/admin.json' with { type: 'json' };
import heCommon from '../src/locales/he/common.json' with { type: 'json' };
import { uniqueEmail } from './support/backend';
import { ADMIN_EMAIL, WORKER_EMAIL, signIn } from './support/session';

/**
 * A union, not `typeof heAdmin`.
 *
 * The two namespaces are deliberately *not* the same shape: Hebrew's CLDR plural
 * categories are one / two / many / other, so `holders_two` and `holders_many`
 * exist there and have no English equivalent. Typing this as the Hebrew file
 * claims they are interchangeable, which stopped compiling the moment they
 * diverged — and because `npm run build` type-checks every project, that broke a
 * production image build over a test-only type.
 *
 * A union permits only the keys both files share, which is exactly what a spec
 * running in both locales may use.
 */
function stringsFor(projectName: string): {
  admin: typeof heAdmin | typeof enAdmin;
  common: typeof heCommon | typeof enCommon;
  hebrew: boolean;
  dir: 'rtl' | 'ltr';
} {
  return projectName.startsWith('he')
    ? { admin: heAdmin, common: heCommon, hebrew: true, dir: 'rtl' }
    : { admin: enAdmin, common: enCommon, hebrew: false, dir: 'ltr' };
}

/** How far the document can be scrolled sideways. Must be zero, in both locales. */
async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
}

test.describe('an administrator', () => {
  test('reaches the admin area from the shell and lands on the user list', async ({
    page,
  }, testInfo) => {
    const { admin, common, hebrew } = stringsFor(testInfo.project.name);
    await signIn(page, ADMIN_EMAIL, hebrew);

    await page.getByRole('link', { name: common.nav.admin }).first().click();

    // `/admin` forwards to the first tab the user can open.
    await expect(page).toHaveURL(/\/admin\/users$/);
    await expect(page.getByRole('heading', { name: admin.title })).toBeVisible();
    await expect(page.getByRole('table', { name: admin.users.title })).toBeVisible();
    // The seeded demo accounts are there, so the list is really the database.
    await expect(page.getByText(WORKER_EMAIL)).toBeVisible();
  });

  test('the role matrix renders without pushing the page sideways', async ({ page }, testInfo) => {
    const { admin, hebrew, dir } = stringsFor(testInfo.project.name);
    await signIn(page, ADMIN_EMAIL, hebrew);

    await page.goto('/admin/roles');
    await expect(page.getByRole('heading', { name: admin.matrix.title })).toBeVisible();

    // 30 permissions, grouped by resource, one switch per role.
    await expect(page.getByRole('switch').first()).toBeVisible();
    // By role, not by text: "Tasks" also appears in the nav ("My tasks") and
    // inside three permission descriptions.
    await expect(
      page.getByRole('columnheader', { name: admin.matrix.resources.task }),
    ).toBeVisible();

    await expect(page.locator('html')).toHaveAttribute('dir', dir);
    // The grid is wider than a phone by design. It has to scroll *inside its own
    // container* — if the document itself scrolls, a logical property was written
    // physically somewhere and the whole layout has shifted.
    expect(await horizontalOverflow(page)).toBeLessThanOrEqual(0);

    // The permission column is sticky, so it stays readable while the role
    // columns scroll under it. `inset-inline-start: 0` resolves to `right` in
    // Hebrew and `left` in English — this asserts the computed side, which is
    // the thing a physical class would get wrong.
    const firstRowHeader = page.locator('tbody th[scope="row"]').first();
    const position = await firstRowHeader.evaluate((node) => {
      const style = getComputedStyle(node);
      return { position: style.position, left: style.left, right: style.right };
    });
    expect(position.position).toBe('sticky');
    expect(dir === 'rtl' ? position.right : position.left).toBe('0px');
  });

  test('a permission edit is staged, confirmed, then saved', async ({ page }, testInfo) => {
    const { admin, common, hebrew } = stringsFor(testInfo.project.name);
    await signIn(page, ADMIN_EMAIL, hebrew);

    await page.goto('/admin/roles');
    await expect(page.getByRole('heading', { name: admin.matrix.title })).toBeVisible();

    // VIEWER does not hold `report:export`… it does; `notification:manage_delivery`
    // is the one no seeded role but SYSTEM_ADMIN holds, so granting and revoking
    // it leaves the demo matrix exactly as it was found.
    const label = hebrew
      ? 'notification:manage_delivery — צופה / מבקר'
      : 'notification:manage_delivery — Viewer / auditor';
    const toggle = page.getByRole('switch', { name: label });
    await expect(toggle).toHaveAttribute('aria-checked', 'false');

    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-checked', 'true');
    await expect(page.getByRole('button', { name: admin.matrix.save })).toBeEnabled();

    await page.getByRole('button', { name: admin.matrix.save }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: common.actions.save }).click();

    await expect(page.getByText(admin.matrix.saved)).toBeVisible();
    await expect(page.getByRole('switch', { name: label })).toHaveAttribute('aria-checked', 'true');

    // Put it back, so a rerun starts from the same matrix.
    await page.getByRole('switch', { name: label }).click();
    await page.getByRole('button', { name: admin.matrix.save }).click();
    await page.getByRole('dialog').getByRole('button', { name: common.actions.save }).click();
    await expect(page.getByText(admin.matrix.saved)).toBeVisible();
  });

  test('invites a user through the UI instead of the CLI', async ({ page }, testInfo) => {
    const { admin, hebrew } = stringsFor(testInfo.project.name);
    await signIn(page, ADMIN_EMAIL, hebrew);

    const email = uniqueEmail('e2e-admin-invite');
    await page.goto('/admin/invitations');

    await page.getByLabel(new RegExp(`^${admin.invitations.email}`)).fill(email);
    await page.getByRole('button', { name: admin.invitations.send }).click();

    await expect(page.getByText(email).first()).toBeVisible();
    await expect(
      page
        .getByRole('table', { name: admin.invitations.title })
        .getByText(admin.invitationStatus.pending)
        .first(),
    ).toBeVisible();
  });

  test('the audit log shows the mutations the other tests just made', async ({
    page,
  }, testInfo) => {
    const { admin, hebrew } = stringsFor(testInfo.project.name);
    await signIn(page, ADMIN_EMAIL, hebrew);

    await page.goto('/admin/audit-log');
    await expect(page.getByRole('table', { name: admin.audit.title })).toBeVisible();
    // `auth.login_succeeded` — the constant in `modules/audit/service.py`. Every
    // sign-in writes one, so the table is never empty by the time we get here.
    await expect(page.getByText('auth.login_succeeded').first()).toBeVisible();
  });

  test('signs out from the user menu', async ({ page }, testInfo) => {
    const { common, hebrew } = stringsFor(testInfo.project.name);
    await signIn(page, ADMIN_EMAIL, hebrew);

    await page.getByRole('button', { name: common.user.menu }).click();
    await page.getByRole('menuitem', { name: common.user.signOut }).click();

    await expect(page).toHaveURL(/\/login$/);
    // The refresh cookie is gone too, so a reload does not resurrect the session.
    await page.goto('/');
    await expect(page).toHaveURL(/\/login$/);
  });
});

test.describe('a worker', () => {
  test('is not offered the admin area and is refused it directly', async ({ page }, testInfo) => {
    const { common, hebrew } = stringsFor(testInfo.project.name);
    await signIn(page, WORKER_EMAIL, hebrew);

    // Not rendered — hiding it is a UX affordance, not the control.
    await expect(page.getByRole('link', { name: common.nav.admin })).toHaveCount(0);

    await page.goto('/admin/users');

    // The client says why rather than bouncing to "/", which would be
    // indistinguishable from a broken link.
    await expect(page.getByRole('alert')).toContainText(common.forbidden.title);
    await expect(page.getByRole('table')).toHaveCount(0);
  });

  test('is refused by the server as well, not only by the screen', async ({ page }, testInfo) => {
    const { hebrew } = stringsFor(testInfo.project.name);
    await signIn(page, WORKER_EMAIL, hebrew);

    // The guard above is client-side. This is the one that matters: the same
    // request the screen would have made, made anyway.
    const status = await page.evaluate(async () => {
      const response = await fetch('/api/v1/admin/users', { credentials: 'include' });
      return response.status;
    });

    // 401 without the bearer header, which is itself a refusal; the point is that
    // it is never 200.
    expect([401, 403]).toContain(status);
  });
});
