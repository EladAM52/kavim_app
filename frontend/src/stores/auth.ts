/**
 * Session state (SPEC §8.2).
 *
 * **The access token lives in memory and nowhere else.** Not `localStorage`, not
 * `sessionStorage`, not a non-httpOnly cookie. An XSS payload can read every one
 * of those; it cannot read a module-scoped JavaScript variable it has no
 * reference to. The cost is that a page reload starts with no token — which is
 * why the app calls `/auth/refresh` on boot, using the httpOnly cookie the
 * browser holds but scripts cannot see.
 *
 * The token is deliberately kept *outside* the Zustand store's persisted surface
 * and readable synchronously, because `api/client.ts` needs it on every request
 * and is not a React component.
 */

import { create } from 'zustand';

import type { components } from '@/api/generated/types';

export type UserIdentity = components['schemas']['UserIdentity'];

/**
 * The token, held in a closure.
 *
 * A module variable rather than store state: the HTTP client reads it on every
 * request, and routing a synchronous read through a React store risks reading a
 * stale render's value mid-refresh.
 */
let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export type AuthStatus =
  /** Boot: a refresh cookie may or may not exist, and we have not asked yet. */
  'unknown' | 'authenticated' | 'anonymous';

interface AuthState {
  status: AuthStatus;
  user: UserIdentity | null;
  /** Session established — from login, registration, or a boot refresh. */
  signIn: (token: string, user: UserIdentity) => void;
  /** Session gone. Clears the token first, so nothing can race a render. */
  signOut: () => void;
  /** Boot refresh came back negative. Distinct from `signOut`: nothing was lost. */
  markAnonymous: () => void;
  hasPermission: (permission: string) => boolean;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  status: 'unknown',
  user: null,

  signIn: (token, user) => {
    setAccessToken(token);
    set({ status: 'authenticated', user });
  },

  signOut: () => {
    setAccessToken(null);
    set({ status: 'anonymous', user: null });
  },

  markAnonymous: () => {
    setAccessToken(null);
    set({ status: 'anonymous', user: null });
  },

  /**
   * UX affordance only — it hides buttons.
   *
   * The server re-checks every mutation (CLAUDE.md rule 2). Never treat a `true`
   * here as authorization, and never treat a `false` as security: the permission
   * list arrived from the network and the user can edit their own memory.
   */
  hasPermission: (permission) => get().user?.permissions.includes(permission) ?? false,
}));

/** Non-reactive read, for code outside React. */
export const currentUser = (): UserIdentity | null => useAuthStore.getState().user;
