/**
 * Session store (SPEC §8.2).
 *
 * The assertion that matters most is the negative one: the access token must not
 * be findable in any browser storage. That is the whole reason it lives in a
 * closure, and it is exactly the kind of property that quietly regresses when
 * someone later adds `persist` to the store for convenience.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { getAccessToken, setAccessToken, useAuthStore, type UserIdentity } from '@/stores/auth';

const USER: UserIdentity = {
  id: 'user-1',
  email: 'worker@example.com',
  full_name: 'מאיה עובדת קו',
  locale: 'he',
  roles: ['WORKER'],
  permissions: ['task:read', 'task:update:status'],
};

describe('auth store', () => {
  beforeEach(() => {
    setAccessToken(null);
    useAuthStore.setState({ status: 'unknown', user: null });
    localStorage.clear();
    sessionStorage.clear();
  });

  it('starts unknown, because a refresh cookie may exist but has not been tried', () => {
    // Not `anonymous`: the difference is what stops a reload from bouncing every
    // signed-in user to the login screen before the boot refresh answers.
    expect(useAuthStore.getState().status).toBe('unknown');
    expect(getAccessToken()).toBeNull();
  });

  it('holds the token in memory and never in browser storage', () => {
    useAuthStore.getState().signIn('secret-access-token', USER);

    expect(getAccessToken()).toBe('secret-access-token');

    // The property an XSS payload would exploit. Scan every value, not just known
    // keys, so a differently-named key still fails the test.
    const stored = [
      ...Object.keys(localStorage).map((key) => localStorage.getItem(key)),
      ...Object.keys(sessionStorage).map((key) => sessionStorage.getItem(key)),
      document.cookie,
    ].join('|');

    expect(stored).not.toContain('secret-access-token');
  });

  it('clears the token on sign-out', () => {
    useAuthStore.getState().signIn('secret-access-token', USER);
    useAuthStore.getState().signOut();

    expect(getAccessToken()).toBeNull();
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().status).toBe('anonymous');
  });

  it('reports permissions the user holds, and denies the rest', () => {
    useAuthStore.getState().signIn('token', USER);
    const { hasPermission } = useAuthStore.getState();

    expect(hasPermission('task:update:status')).toBe(true);
    expect(hasPermission('user:manage')).toBe(false);
  });

  it('denies every permission when nobody is signed in', () => {
    expect(useAuthStore.getState().hasPermission('task:read')).toBe(false);
  });

  it('preserves a Hebrew display name unchanged', () => {
    useAuthStore.getState().signIn('token', USER);
    expect(useAuthStore.getState().user?.full_name).toBe('מאיה עובדת קו');
  });
});
