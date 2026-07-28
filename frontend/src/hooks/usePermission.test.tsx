/**
 * The permission hooks and the route guard.
 *
 * What these pin down is the *rendering* decision, which is all the client side
 * of authorization is allowed to be (CLAUDE.md rule 2). The equivalent server
 * assertion lives in `tests/security/test_all_routes_declare_permission.py`, and
 * that one is the security control.
 */

import { act, render, renderHook, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';

import { RequirePermission } from '@/features/auth/RequirePermission';
import { useAnyPermission, usePermission } from '@/hooks/usePermission';
import { useAuthStore, type UserIdentity } from '@/stores/auth';

function signIn(permissions: string[]): void {
  const user: UserIdentity = {
    id: 'u1',
    email: 'manager@kavim.example.com',
    full_name: 'מנהלת קו',
    locale: 'he',
    roles: ['LINE_MANAGER'],
    permissions,
  };
  useAuthStore.setState({ status: 'authenticated', user });
}

describe('usePermission', () => {
  beforeEach(() => {
    useAuthStore.setState({ status: 'unknown', user: null });
  });

  it('is false with no user at all', () => {
    const { result } = renderHook(() => usePermission('user:manage'));
    expect(result.current).toBe(false);
  });

  it('reads the identity the server sent', () => {
    signIn(['user:invite']);

    expect(renderHook(() => usePermission('user:invite')).result.current).toBe(true);
    expect(renderHook(() => usePermission('user:manage')).result.current).toBe(false);
  });

  it('useAnyPermission needs only one of the list', () => {
    signIn(['audit:read']);

    const { result } = renderHook(() =>
      useAnyPermission(['user:manage', 'user:invite', 'audit:read']),
    );
    expect(result.current).toBe(true);
  });

  it('re-renders when a role change replaces the identity', () => {
    signIn([]);
    const { result, rerender } = renderHook(() => usePermission('user:manage'));
    expect(result.current).toBe(false);

    // FR-202: a matrix edit takes effect on the next request, and the refreshed
    // identity has to move the UI with it rather than waiting for a reload.
    act(() => {
      signIn(['user:manage']);
    });
    rerender();
    expect(result.current).toBe(true);
  });
});

describe('RequirePermission', () => {
  beforeEach(() => {
    useAuthStore.setState({ status: 'unknown', user: null });
  });

  function renderGuard(): void {
    render(
      <MemoryRouter initialEntries={['/admin']}>
        <Routes>
          <Route element={<RequirePermission anyOf={['user:manage']} />}>
            <Route path="/admin" element={<p>admin content</p>} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );
  }

  it('renders the screen for a user who holds the permission', () => {
    signIn(['user:manage']);
    renderGuard();
    expect(screen.getByText('admin content')).toBeInTheDocument();
  });

  it('explains the denial instead of redirecting silently', () => {
    signIn(['task:read']);
    renderGuard();

    // A bounce to "/" is indistinguishable from a broken link.
    expect(screen.queryByText('admin content')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('אין לך גישה למסך הזה');
  });
});
