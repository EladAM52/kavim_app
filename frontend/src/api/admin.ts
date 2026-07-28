/**
 * Administration endpoints (SPEC §6.4).
 *
 * Every type comes from the generated OpenAPI schema, so a backend field rename
 * is a compile error here rather than an `undefined` at render time.
 *
 * Pagination is keyset, not offset: the server returns `next_cursor` and no
 * total (SPEC §9.1). There is deliberately no page-number API to build on.
 */

import { api } from '@/api/client';
import type { components } from '@/api/generated/types';

type Schemas = components['schemas'];

export type AdminUserRow = Schemas['AdminUserRow'];
export type AdminUserUpdate = Schemas['AdminUserUpdate'];
export type AuditRow = Schemas['AuditRow'];
/** The language an invitation email is rendered in — not the UI language. */
export type EmailLocale = Schemas['Locale'];
export type ColumnVerdict = Schemas['ColumnVerdict'];
export type EffectivePermissionsTrace = Schemas['EffectivePermissionsTrace'];
export type InvitationCreate = Schemas['InvitationCreate'];
export type InvitationRow = Schemas['InvitationRow'];
export type InvitationStatus = Schemas['InvitationStatus'];
export type MessageResponse = Schemas['MessageResponse'];
export type PermissionRow = Schemas['PermissionRow'];
export type RoleKey = Schemas['RoleKey'];
export type RoleRow = Schemas['RoleRow'];
export type UserStatus = Schemas['UserStatus'];

export type UserPage = Schemas['Page_AdminUserRow_'];
export type InvitationPage = Schemas['Page_InvitationRow_'];
export type AuditPage = Schemas['Page_AuditRow_'];

/** Drops empty values, so an unset filter never becomes `?role=`. */
function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === '') continue;
    search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : '';
}

export interface UserListParams {
  limit?: number;
  cursor?: string | undefined;
  q?: string | undefined;
  role?: string | undefined;
  status?: string | undefined;
}

export interface InvitationListParams {
  limit?: number;
  cursor?: string | undefined;
  status?: string | undefined;
}

export interface AuditListParams {
  limit?: number;
  cursor?: string | undefined;
  action?: string | undefined;
  entity_type?: string | undefined;
  actor_id?: string | undefined;
  since?: string | undefined;
  until?: string | undefined;
}

export const adminApi = {
  // ── roles and the matrix (FR-203) ───────────────────────────────────────
  listPermissions: (): Promise<PermissionRow[]> => api.get<PermissionRow[]>('/admin/permissions'),

  listRoles: (): Promise<RoleRow[]> => api.get<RoleRow[]>('/admin/roles'),

  /**
   * The complete set, not a delta.
   *
   * The endpoint is a PUT for the same reason the screen is a grid: the
   * administrator means "this is the state I want", and applying it atomically is
   * what stops a half-saved matrix from existing at all.
   */
  replaceRolePermissions: (roleId: string, permissionKeys: string[]): Promise<RoleRow> =>
    api.put<RoleRow>(`/admin/roles/${roleId}/permissions`, { permission_keys: permissionKeys }),

  // ── users (FR-201, FR-202, FR-206, FR-207, FR-210) ──────────────────────
  listUsers: (params: UserListParams = {}): Promise<UserPage> =>
    api.get<UserPage>(`/admin/users${query({ ...params })}`),

  updateUser: (userId: string, payload: AdminUserUpdate): Promise<AdminUserRow> =>
    api.patch<AdminUserRow>(`/admin/users/${userId}`, payload),

  forceLogout: (userId: string): Promise<MessageResponse> =>
    api.post<MessageResponse>(`/admin/users/${userId}/force-logout`),

  effectivePermissions: (userId: string, projectId?: string): Promise<EffectivePermissionsTrace> =>
    api.get<EffectivePermissionsTrace>(
      `/admin/users/${userId}/effective-permissions${query({ project_id: projectId })}`,
    ),

  // ── invitations (FR-101, FR-111) ────────────────────────────────────────
  createInvitation: (payload: InvitationCreate): Promise<InvitationRow> =>
    api.post<InvitationRow>('/admin/invitations', payload),

  listInvitations: (params: InvitationListParams = {}): Promise<InvitationPage> =>
    api.get<InvitationPage>(`/admin/invitations${query({ ...params })}`),

  /** Issues a new link and kills the old one. Rate limited per address. */
  resendInvitation: (invitationId: string): Promise<InvitationRow> =>
    api.post<InvitationRow>(`/admin/invitations/${invitationId}/resend`),

  revokeInvitation: (invitationId: string): Promise<MessageResponse> =>
    api.delete<MessageResponse>(`/admin/invitations/${invitationId}`),

  // ── audit log (FR-208) ──────────────────────────────────────────────────
  listAuditLog: (params: AuditListParams = {}): Promise<AuditPage> =>
    api.get<AuditPage>(`/admin/audit-log${query({ ...params })}`),
};
