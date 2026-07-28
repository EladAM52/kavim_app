/**
 * Permission reads for rendering decisions.
 *
 * **This is a UX affordance and nothing more** (CLAUDE.md rule 2). The list it
 * reads came from the network into a store the user can edit from their own
 * devtools, so a `true` here is not authorization and a `false` here is not
 * security. Every mutation declares `require_permission(...)` on the server, and
 * that declaration is what actually holds — `tests/security/
 * test_all_routes_declare_permission.py` fails CI if a route omits it.
 *
 * What these hooks are *for* is not showing a worker a button that would return
 * 403 if they pressed it.
 */

import { useAuthStore } from '@/stores/auth';

/** Reactive: re-renders when the identity changes, e.g. after a role edit. */
export function usePermission(permission: string): boolean {
  return useAuthStore((state) => state.user?.permissions.includes(permission) ?? false);
}

/**
 * True when the user holds **any** of these.
 *
 * The admin area is entered on any one of `user:manage`, `user:manage_permissions`,
 * `user:invite`, or `audit:read`, because the four screens have four different
 * requirements and an auditor holding only `audit:read` still has somewhere to go.
 *
 * The selector returns a boolean, so zustand's default reference equality is
 * enough — returning a filtered array here would produce a new array on every
 * store write and re-render the whole shell.
 */
export function useAnyPermission(permissions: readonly string[]): boolean {
  return useAuthStore((state) => {
    const held = state.user?.permissions;
    if (!held) return false;
    return permissions.some((permission) => held.includes(permission));
  });
}
