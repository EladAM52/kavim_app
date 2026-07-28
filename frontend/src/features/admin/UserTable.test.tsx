/**
 * The user table.
 *
 * Two things here are easy to get wrong and expensive to notice late: a locked
 * account rendering as merely "active", and the self-edit rules being discovered
 * only by receiving a 409.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { setAccessToken, useAuthStore, type UserIdentity } from '@/stores/auth';

import { UserTable } from './UserTable';

const ADMIN: UserIdentity = {
  id: 'admin-1',
  email: 'admin@kavim.example.com',
  full_name: 'מנהל מערכת',
  locale: 'he',
  roles: ['SYSTEM_ADMIN'],
  permissions: ['user:manage'],
};

const IN_TEN_MINUTES = new Date(Date.now() + 10 * 60_000).toISOString();

const PAGE = {
  items: [
    {
      id: 'admin-1',
      email: 'admin@kavim.example.com',
      full_name: 'מנהל מערכת',
      status: 'active',
      locale: 'he',
      roles: ['SYSTEM_ADMIN'],
      last_login_at: '2026-07-27T06:14:00Z',
      created_at: '2026-01-01T00:00:00Z',
      locked_until: null,
    },
    {
      id: 'worker-9',
      email: 'worker1@kavim.example.com',
      full_name: 'עובד ראשון',
      status: 'active',
      locale: 'he',
      roles: ['WORKER'],
      last_login_at: null,
      created_at: '2026-02-01T00:00:00Z',
      // Still `active`; simply cannot sign in for the next ten minutes (FR-109).
      locked_until: IN_TEN_MINUTES,
    },
  ],
  next_cursor: null,
};

/**
 * A row, found by email.
 *
 * Not by name: "מנהל מערכת" is the administrator's name, the label of the
 * SYSTEM_ADMIN option in the role filter, *and* the contents of that user's role
 * cell. The address is the only value on the screen guaranteed to appear once.
 */
async function findRow(email: string): Promise<HTMLElement> {
  const table = await screen.findByRole('table', { name: 'משתמשים' });
  const cell = await within(table).findByText(email);
  const row = cell.closest('tr');
  if (!row) throw new Error(`no row for ${email}`);
  return row;
}

const ADMIN_ROW = 'admin@kavim.example.com';
const WORKER_ROW = 'worker1@kavim.example.com';

function renderTable(): void {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <UserTable />
    </QueryClientProvider>,
  );
}

describe('UserTable', () => {
  beforeEach(() => {
    setAccessToken('test-token');
    useAuthStore.setState({ status: 'authenticated', user: ADMIN });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(PAGE), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('separates a temporary lockout from a deactivated account', async () => {
    renderTable();

    const lockedRow = await findRow(WORKER_ROW);
    expect(within(lockedRow).getByText(/נעול זמנית/)).toBeInTheDocument();

    const activeRow = await findRow(ADMIN_ROW);
    expect(within(activeRow).getByText('פעיל')).toBeInTheDocument();
  });

  it('marks the signed-in administrator and never shows a login they never made', async () => {
    renderTable();

    const ownRow = await findRow(ADMIN_ROW);
    expect(within(ownRow).getByText('(אתה)')).toBeInTheDocument();

    const workerRow = await findRow(WORKER_ROW);
    expect(within(workerRow).getByText('מעולם לא')).toBeInTheDocument();
  });

  it('disables role and status on your own account rather than letting the server refuse it', async () => {
    const user = userEvent.setup();
    renderTable();

    const ownRow = await findRow(ADMIN_ROW);
    await user.click(within(ownRow).getByRole('button', { name: 'עריכה' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText('תפקיד')).toBeDisabled();
    expect(within(dialog).getByLabelText('סטטוס')).toBeDisabled();
    expect(dialog).toHaveTextContent('לא ניתן לשנות את התפקיד של עצמך');
  });

  it('closes a confirmation on Escape', async () => {
    const user = userEvent.setup();
    renderTable();

    const workerRow = await findRow(WORKER_ROW);
    await user.click(within(workerRow).getByRole('button', { name: 'ניתוק מכל המכשירים' }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
