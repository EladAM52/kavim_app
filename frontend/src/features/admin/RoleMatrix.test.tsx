/**
 * The matrix screen.
 *
 * The behaviour worth pinning is the staging: a toggle must not reach the
 * server, and saving must send the *whole* set for the role, because the
 * endpoint is a replace and a delta would silently strip everything it omitted.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { setAccessToken } from '@/stores/auth';

import { RoleMatrix } from './RoleMatrix';

const PERMISSIONS = [
  {
    key: 'task:create',
    resource: 'task',
    description_he: 'יצירת משימה',
    description_en: 'Create a task',
  },
  {
    key: 'task:delete',
    resource: 'task',
    description_he: 'מחיקת משימה',
    description_en: 'Delete a task',
  },
  {
    key: 'audit:read',
    resource: 'audit',
    description_he: 'צפייה ביומן פעולות',
    description_en: 'View the audit log',
  },
];

const ROLES = [
  {
    id: 'role-worker',
    key: 'WORKER',
    label_he: 'עובד',
    label_en: 'Worker',
    rank: 40,
    is_system: true,
    permission_keys: ['task:create'],
    user_count: 3,
  },
];

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** Routes each admin call to its fixture and records the PUTs. */
function stubApi(): { puts: { url: string; body: unknown }[] } {
  const puts: { url: string; body: unknown }[] = [];

  vi.stubGlobal(
    'fetch',
    // Typed to what `api/client.ts` actually sends — a string URL and a string
    // body — rather than to the full `fetch` signature it never uses.
    vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === 'PUT') {
        puts.push({ url, body: JSON.parse(init.body as string) as unknown });
        return Promise.resolve(jsonResponse({ ...ROLES[0], permission_keys: [] }));
      }
      if (url.includes('/admin/permissions')) return Promise.resolve(jsonResponse(PERMISSIONS));
      if (url.includes('/admin/roles')) return Promise.resolve(jsonResponse(ROLES));
      throw new Error(`unexpected request: ${url}`);
    }),
  );

  return { puts };
}

function renderMatrix(): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <RoleMatrix />
    </QueryClientProvider>,
  );
}

describe('RoleMatrix', () => {
  beforeEach(() => {
    setAccessToken('test-token');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('groups permissions by resource and shows how many people hold each role', async () => {
    stubApi();
    renderMatrix();

    expect(await screen.findByText('משימות')).toBeInTheDocument();
    expect(screen.getByText('ביקורת')).toBeInTheDocument();
    expect(screen.getByText('3 משתמשים')).toBeInTheDocument();
  });

  it('stages a toggle instead of saving it, then sends the complete set', async () => {
    const { puts } = stubApi();
    const user = userEvent.setup();
    renderMatrix();

    const grant = await screen.findByRole('switch', { name: 'task:delete — עובד' });
    expect(grant).toHaveAttribute('aria-checked', 'false');

    await user.click(grant);

    // Staged: still nothing written.
    expect(grant).toHaveAttribute('aria-checked', 'true');
    expect(puts).toHaveLength(0);
    expect(screen.getByText('שינוי אחד לא נשמר')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'שמירת שינויים' }));

    // The confirmation names the role and its holders before anything happens.
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('עובד');
    expect(dialog).toHaveTextContent('3 משתמשים');

    await user.click(within(dialog).getByRole('button', { name: 'שמירה' }));

    await waitFor(() => {
      expect(puts).toHaveLength(1);
    });
    expect(puts[0]?.url).toContain('/admin/roles/role-worker/permissions');
    // The whole set, not the one that changed — the endpoint replaces.
    expect(puts[0]?.body).toEqual({ permission_keys: ['task:create', 'task:delete'] });
  });

  it('counts two holders in Hebrew, which is its own plural category', async () => {
    // Hebrew CLDR has one / two / many / other, so `_one` and `_other` alone
    // leave `count: 2` with no match and i18next renders the raw key —
    // "מנהל מערכתmatrix.holders" on a live screen, spotted by a user. The
    // original tests used 1 and 3, the exact two categories that were covered.
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        if (url.includes('/admin/permissions')) return Promise.resolve(jsonResponse(PERMISSIONS));
        return Promise.resolve(jsonResponse([{ ...ROLES[0], user_count: 2 }]));
      }),
    );
    renderMatrix();

    expect(await screen.findByText('שני משתמשים')).toBeInTheDocument();
    expect(screen.queryByText(/matrix.holders/)).not.toBeInTheDocument();
  });

  it('counts two staged changes in Hebrew', async () => {
    stubApi();
    const user = userEvent.setup();
    renderMatrix();

    await user.click(await screen.findByRole('switch', { name: 'task:delete — עובד' }));
    await user.click(screen.getByRole('switch', { name: 'audit:read — עובד' }));

    expect(screen.getByText('שני שינויים לא נשמרו')).toBeInTheDocument();
  });

  it('discards staged edits without touching the server', async () => {
    const { puts } = stubApi();
    const user = userEvent.setup();
    renderMatrix();

    const grant = await screen.findByRole('switch', { name: 'task:delete — עובד' });
    await user.click(grant);
    await user.click(screen.getByRole('button', { name: 'ביטול השינויים' }));

    expect(grant).toHaveAttribute('aria-checked', 'false');
    expect(puts).toHaveLength(0);
  });
});
