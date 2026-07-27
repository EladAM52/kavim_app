/**
 * The login screen.
 *
 * Asserts behaviour a worker would notice: the form submits, a wrong password
 * shows translated Hebrew copy rather than the backend's English, and the token
 * reaches the in-memory store rather than storage.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getAccessToken, setAccessToken, useAuthStore } from '@/stores/auth';

import Login from './Login';

function renderLogin(): void {
  // retry off: a failed login must surface immediately, and React Query's default
  // backoff would make the test wait for retries that production also does not do.
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/login']}>
        <Login />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const TOKEN_RESPONSE = {
  access_token: 'granted-token',
  token_type: 'bearer',
  expires_in_seconds: 900,
  user: {
    id: 'u1',
    email: 'worker@example.com',
    full_name: 'Worker',
    locale: 'he',
    roles: ['WORKER'],
    permissions: [],
  },
};

describe('Login', () => {
  beforeEach(() => {
    setAccessToken(null);
    useAuthStore.setState({ status: 'unknown', user: null });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('signs in and puts the token in memory only', async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(TOKEN_RESPONSE), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    );

    renderLogin();

    await user.type(screen.getByLabelText(/כתובת מייל/), 'worker@example.com');
    await user.type(screen.getByLabelText(/^סיסמה/), 'a-long-enough-passphrase');
    await user.click(screen.getByRole('button', { name: 'כניסה' }));

    await waitFor(() => {
      expect(useAuthStore.getState().status).toBe('authenticated');
    });
    expect(getAccessToken()).toBe('granted-token');
    expect(localStorage.getItem('kavim.token')).toBeNull();
  });

  it('shows translated copy for a wrong password, not the server message', async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            status: 401,
            code: 'unauthenticated',
            title: 'Authentication required',
            // The English the backend actually returns. It must not reach the user.
            detail: 'Email or password is incorrect.',
            type: 'x',
          }),
          { status: 401, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );

    renderLogin();

    await user.type(screen.getByLabelText(/כתובת מייל/), 'worker@example.com');
    await user.type(screen.getByLabelText(/^סיסמה/), 'wrong');
    await user.click(screen.getByRole('button', { name: 'כניסה' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('כתובת המייל או הסיסמה שגויים');
    expect(alert).not.toHaveTextContent('incorrect');
    expect(useAuthStore.getState().status).toBe('unknown');
  });

  it('explains a lockout with the remaining minutes', async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            status: 403,
            code: 'account_locked',
            title: 'Account temporarily locked',
            detail: 'locked',
            type: 'x',
            retry_after_seconds: 540,
          }),
          { status: 403, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );

    renderLogin();

    await user.type(screen.getByLabelText(/כתובת מייל/), 'worker@example.com');
    await user.type(screen.getByLabelText(/^סיסמה/), 'whatever12345');
    await user.click(screen.getByRole('button', { name: 'כניסה' }));

    // 540s rounds up to 9 minutes — never "0 minutes".
    expect(await screen.findByRole('alert')).toHaveTextContent('9');
  });

  it('keeps email and password inputs left-to-right inside the RTL form', () => {
    renderLogin();

    // A password or address rendered RTL reads back in the wrong order, which for
    // a credential means the user cannot check what they typed.
    expect(screen.getByLabelText(/כתובת מייל/)).toHaveAttribute('dir', 'ltr');
    expect(screen.getByLabelText(/^סיסמה/)).toHaveAttribute('dir', 'ltr');
  });
});
