import type { EmailLocale, InvitationStatus, RoleKey, UserStatus } from '@/api/admin';

/**
 * The enum values, mirrored from the generated schema.
 *
 * `RoleKey` and friends are TypeScript union *types* — they have no runtime
 * value to iterate for a `<select>`. These arrays are typed as the union, so
 * adding a role on the backend and regenerating the schema makes any list that
 * has fallen behind a compile error rather than a silently short dropdown.
 */
export const ROLE_KEYS: readonly RoleKey[] = [
  'SYSTEM_ADMIN',
  'LINE_MANAGER',
  'SHIFT_SUPERVISOR',
  'WORKER',
  'VIEWER',
];

export const USER_STATUSES: readonly UserStatus[] = ['invited', 'active', 'deactivated'];

/** The languages an invitation email can be sent in. */
export const EMAIL_LOCALES: readonly EmailLocale[] = ['he', 'en'];

export const INVITATION_STATUSES: readonly InvitationStatus[] = [
  'pending',
  'consumed',
  'revoked',
  'expired',
];

export interface AdminTab {
  path: string;
  labelKey: string;
  permission: string;
}

/**
 * The admin tab strip.
 *
 * Here rather than in `AdminLayout` because `AppShell` needs
 * `ADMIN_PERMISSIONS` to decide whether to render the nav link at all, and a
 * component file that also exports constants breaks Fast Refresh.
 */
export const ADMIN_TABS: readonly AdminTab[] = [
  { path: '/admin/users', labelKey: 'admin:tabs.users', permission: 'user:manage' },
  { path: '/admin/roles', labelKey: 'admin:tabs.roles', permission: 'user:manage_permissions' },
  { path: '/admin/invitations', labelKey: 'admin:tabs.invitations', permission: 'user:invite' },
  { path: '/admin/audit-log', labelKey: 'admin:tabs.audit', permission: 'audit:read' },
];

/** Any one of these opens the admin area. */
export const ADMIN_PERMISSIONS: readonly string[] = ADMIN_TABS.map((tab) => tab.permission);
